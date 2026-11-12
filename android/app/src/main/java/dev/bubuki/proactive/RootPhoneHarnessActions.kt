package dev.bubuki.proactive

import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.PowerManager
import android.os.SystemClock
import kotlinx.coroutines.flow.update
import org.json.JSONArray
import org.json.JSONObject

/**
 * [Module] RootPhoneHarnessActions
 * [Responsibility] High-level phone workspace actions, Accessibility UI tree node resolution &
 *                  interaction (`ui_tree`, `tap_node`, `type_node`, `tap_and_type`), launcher
 *                  package/component discovery & alias mapping, stateful shell execution,
 *                  file transfer (`list_files`, `pull_file`, `push_file`), notifications dump,
 *                  and device telemetry (`device_info`).
 * [Public API]
 *   - RootPhoneHarness.buildDeviceInfo(base)
 *   - RootPhoneHarness.executeListFiles(input) / executePullFile(base, input) / executePushFile(base, input)
 *   - RootPhoneHarness.executeShellAction(base, input) / executeNotificationsAction(base, input)
 *   - RootPhoneHarness.resolveGridOrNormCoords(operation, cmdObj, dispW, dispH)
 *   - RootPhoneHarness.executeTapAndTypeAction / executeTapNodeAction / executeTypeNodeAction / resolveLongPressNodeCoords
 *   - RootPhoneHarness.readUiTreeForDisplay(displayId, dispW, dispH, forceRefresh)
 *   - RootPhoneHarness.queryLauncherApps(base, forceRefresh) / resolveLaunchComponent(base, query)
 */

internal fun RootPhoneHarness.buildDeviceInfo(base: Context): JSONObject {
    val out = JSONObject().put("ok", true).put("op", "device_info")
    runCatching {
        val bm = base.getSystemService(android.os.BatteryManager::class.java)
        val intent = base.registerReceiver(null, android.content.IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        val level = bm?.getIntProperty(android.os.BatteryManager.BATTERY_PROPERTY_CAPACITY)
            ?: intent?.getIntExtra(android.os.BatteryManager.EXTRA_LEVEL, -1) ?: -1
        val statusInt = intent?.getIntExtra(android.os.BatteryManager.EXTRA_STATUS, -1) ?: -1
        val charging = statusInt == android.os.BatteryManager.BATTERY_STATUS_CHARGING ||
            statusInt == android.os.BatteryManager.BATTERY_STATUS_FULL ||
            (bm?.isCharging == true)
        val tempTenths = intent?.getIntExtra(android.os.BatteryManager.EXTRA_TEMPERATURE, 0) ?: 0
        out.put("battery", JSONObject()
            .put("level", level)
            .put("charging", charging)
            .put("temp_c", if (tempTenths > 0) tempTenths / 10.0 else 0.0)
        )
    }
    runCatching {
        val am = base.getSystemService(android.app.ActivityManager::class.java)
        val mi = android.app.ActivityManager.MemoryInfo()
        am?.getMemoryInfo(mi)
        out.put("memory", JSONObject()
            .put("total_mb", mi.totalMem / (1024 * 1024))
            .put("avail_mb", mi.availMem / (1024 * 1024))
            .put("low_memory", mi.lowMemory)
        )
    }
    runCatching {
        val stat = android.os.StatFs(android.os.Environment.getExternalStorageDirectory().path)
        val freeGb = (stat.availableBytes.toDouble() / (1024.0 * 1024.0 * 1024.0) * 10.0).toInt() / 10.0
        val totalGb = (stat.totalBytes.toDouble() / (1024.0 * 1024.0 * 1024.0) * 10.0).toInt() / 10.0
        out.put("storage", JSONObject().put("free_gb", freeGb).put("total_gb", totalGb))
    }
    runCatching {
        val cm = base.getSystemService(android.net.ConnectivityManager::class.java)
        val activeCaps = cm?.activeNetwork?.let { cm.getNetworkCapabilities(it) }
        val allCaps = runCatching {
            cm?.allNetworks?.mapNotNull { cm.getNetworkCapabilities(it) }.orEmpty()
        }.getOrDefault(emptyList())
        val hasWifi = activeCaps?.hasTransport(android.net.NetworkCapabilities.TRANSPORT_WIFI) == true ||
            allCaps.any { it.hasTransport(android.net.NetworkCapabilities.TRANSPORT_WIFI) && it.hasCapability(android.net.NetworkCapabilities.NET_CAPABILITY_INTERNET) }
        val hasCellular = activeCaps?.hasTransport(android.net.NetworkCapabilities.TRANSPORT_CELLULAR) == true ||
            allCaps.any { it.hasTransport(android.net.NetworkCapabilities.TRANSPORT_CELLULAR) && it.hasCapability(android.net.NetworkCapabilities.NET_CAPABILITY_INTERNET) }
        val hasVpn = activeCaps?.hasTransport(android.net.NetworkCapabilities.TRANSPORT_VPN) == true ||
            allCaps.any { it.hasTransport(android.net.NetworkCapabilities.TRANSPORT_VPN) }
        val netType = when {
            activeCaps == null && allCaps.isEmpty() -> "none"
            hasWifi -> "wifi"
            hasCellular -> "cellular"
            else -> "other"
        }
        out.put("network", JSONObject()
            .put("connected", activeCaps != null || allCaps.isNotEmpty())
            .put("type", netType)
            .put("vpn", hasVpn)
        )
    }
    runCatching {
        val pm = base.getSystemService(PowerManager::class.java)
        val km = base.getSystemService(android.app.KeyguardManager::class.java)
        val audio = base.getSystemService(android.media.AudioManager::class.java)
        val ringer = when (audio?.ringerMode) {
            android.media.AudioManager.RINGER_MODE_SILENT -> "silent"
            android.media.AudioManager.RINGER_MODE_VIBRATE -> "vibrate"
            else -> "normal"
        }
        out.put("screen", JSONObject()
            .put("interactive", pm?.isInteractive == true)
            .put("locked", km?.isKeyguardLocked == true)
            .put("ringer_mode", ringer)
            .put("music_active", audio?.isMusicActive == true)
        )
    }
    val suPaths = listOf("/system/bin/su", "/system/xbin/su", "/sbin/su", "/debug_ramdisk/su", "/data/adb/magisk", "/data/adb/ksu", "/data/adb/ap")
    val suBinaryPresent = suPaths.any { runCatching { java.io.File(it).exists() }.getOrDefault(false) }
    val rootReady = privilege?.let { priv ->
        if (priv.isRootGrantedFast()) true
        else runCatching { priv.probe().optBoolean("root", false) }.getOrDefault(false)
    } ?: false
    out.put("device", JSONObject()
        .put("brand", Build.BRAND)
        .put("model", Build.MODEL)
        .put("sdk", Build.VERSION.SDK_INT)
        .put("root", rootReady)
        .put("su_binary_present", suBinaryPresent || rootReady)
    )
    return out
}

internal fun RootPhoneHarness.resolveLatestPhoneFile(): java.io.File? {
    val dirs = listOf(
        java.io.File("/sdcard/Download/DeepSeek Claw"),
        java.io.File("/sdcard/Download/WeiXin"),
        java.io.File("/sdcard/Download"),
        java.io.File("/sdcard/Pictures/Screenshots"),
        java.io.File("/sdcard/DCIM/Screenshots"),
        java.io.File("/sdcard/Android/data/com.tencent.mm/MicroMsg/Download")
    )
    val candidates = mutableListOf<java.io.File>()
    for (d in dirs) {
        if (d.exists() && d.isDirectory) {
            d.listFiles()?.filter { it.isFile && it.length() > 0 && !it.name.startsWith(".") }?.let { candidates.addAll(it) }
        }
    }
    return candidates.maxByOrNull { it.lastModified() }
}

internal fun RootPhoneHarness.executeListFiles(input: JSONObject): JSONObject {
    val dirPath = input.optString("path", "/sdcard/Download").trim().ifBlank { "/sdcard/Download" }
    val limit = input.optInt("limit", 25).coerceIn(1, 100)
    val dir = java.io.File(dirPath)
    if (dir.exists() && !dir.isDirectory) {
        return JSONObject().put("ok", false).put("op", "list_files").put("path", dirPath).put("error", "目标路径不是目录: $dirPath")
    }
    val arr = JSONArray()
    val files = if (dir.exists() && dir.isDirectory) dir.listFiles()?.sortedByDescending { it.lastModified() } else null
    if (files != null) {
        for (f in files.take(limit)) {
            arr.put(JSONObject()
                .put("name", f.name)
                .put("path", f.absolutePath)
                .put("is_dir", f.isDirectory)
                .put("bytes", if (f.isFile) f.length() else 0L)
                .put("mtime", f.lastModified() / 1000)
            )
        }
        return JSONObject().put("ok", true).put("op", "list_files").put("path", dirPath).put("files", arr)
    }
    if (privilege!!.isRootGrantedFast() || runCatching { privilege!!.probe().optBoolean("root") }.getOrDefault(false)) {
        val safeP = "'" + dirPath.replace("'", "'\\''") + "'"
        val checkRes = privilege!!.execute(JSONObject().put("op", "shell").put("command", "[ -d $safeP ] && echo DIR_OK || echo DIR_MISSING"))
        if (!checkRes.optString("output", "").contains("DIR_OK")) {
            return JSONObject().put("ok", false).put("op", "list_files").put("path", dirPath).put("error", "目录不存在: $dirPath")
        }
        val lsRes = privilege!!.execute(JSONObject().put("op", "shell").put("command", "ls -lt $safeP | head -n ${limit + 1}"))
        return JSONObject().put("ok", true).put("op", "list_files").put("path", dirPath).put("ls_output", lsRes.optString("output", ""))
    }
    return JSONObject().put("ok", false).put("op", "list_files").put("path", dirPath).put("error", "目录不存在或无法访问: $dirPath")
}

internal fun RootPhoneHarness.executePullFile(base: Context, input: JSONObject): JSONObject {
    val rawPath = input.optString("path", input.optString("target", "latest")).trim()
    val source = input.optString("source", "workspace").trim().lowercase().let { if (it == "computer") "computer" else "workspace" }
    val isLatestAlias = rawPath.isBlank() || rawPath.equals("latest", ignoreCase = true) || rawPath.equals("latest_download", ignoreCase = true)
    var resolvedPhonePath = rawPath
    val targetFile = if (isLatestAlias) {
        resolveLatestPhoneFile()?.also { resolvedPhonePath = it.absolutePath }
    } else {
        java.io.File(rawPath)
    }
    var stagedCopy: java.io.File? = null
    try {
        val uploadFile = if (targetFile != null && targetFile.exists() && targetFile.canRead() && targetFile.length() > 0) {
            targetFile
        } else if (privilege!!.isRootGrantedFast() || runCatching { privilege!!.probe().optBoolean("root") }.getOrDefault(false)) {
            if (isLatestAlias && (targetFile == null || !targetFile.exists())) {
                val findCmd = "ls -1dt /sdcard/Download/* '/sdcard/Download/DeepSeek Claw/'* /sdcard/Pictures/Screenshots/* /sdcard/DCIM/Screenshots/* 2>/dev/null | while IFS= read -r f; do [ -f \"\$f\" ] && [ -s \"\$f\" ] && echo \"\$f\" && break; done"
                val findRes = privilege!!.execute(JSONObject().put("op", "shell").put("command", findCmd))
                val foundLine = findRes.optString("output", "").lineSequence().map { it.trim() }.firstOrNull { it.startsWith("/") }
                if (!foundLine.isNullOrBlank()) {
                    resolvedPhonePath = foundLine
                }
            }
            if (resolvedPhonePath.isBlank() || resolvedPhonePath.equals("latest", ignoreCase = true) || resolvedPhonePath.equals("latest_download", ignoreCase = true)) {
                throw Exception("手机端 /sdcard/Download 与截图目录中未找到可拉取的最新文件")
            }
            val baseName = resolvedPhonePath.substringAfterLast('/').ifBlank { "pulled_file" }
            val tmp = java.io.File(base.cacheDir, baseName)
            val safeSrc = "'" + resolvedPhonePath.replace("'", "'\\''") + "'"
            val safeDst = "'" + tmp.absolutePath.replace("'", "'\\''") + "'"
            privilege!!.execute(JSONObject().put("op", "shell").put("command", "cp $safeSrc $safeDst && chmod 644 $safeDst"))
            if (!tmp.exists() || tmp.length() == 0L) throw Exception("手机端未找到或无法读取文件: $resolvedPhonePath")
            stagedCopy = tmp
            tmp
        } else {
            throw Exception("手机端未找到可读取文件: ${resolvedPhonePath.ifBlank { "/sdcard/Download" }}")
        }
        val receipt = CloudFiles.upload(base, android.net.Uri.fromFile(uploadFile), source)
        return JSONObject()
            .put("ok", true)
            .put("op", "pull_file")
            .put("phone_path", targetFile?.absolutePath ?: resolvedPhonePath)
            .put("workspace_path", receipt.optString("path"))
            .put("name", receipt.optString("name", uploadFile.name))
            .put("bytes", receipt.optLong("bytes", uploadFile.length()))
            .put("sha256", receipt.optString("sha256"))
    } finally {
        stagedCopy?.delete()
    }
}

internal fun RootPhoneHarness.executePushFile(base: Context, input: JSONObject): JSONObject {
    val rawPath = input.optString("path", input.optString("target", "")).trim()
    if (rawPath.isEmpty()) throw Exception("push_file 需要提供 path")
    val source = input.optString("source", "workspace").trim().lowercase().let { if (it == "computer") "computer" else "workspace" }
    val receipt = CloudFiles.download(base, source, rawPath)
    val name = receipt.optString("name")
    val phonePath = receipt.optString("path").ifBlank { "/sdcard/Download/DeepSeek Claw/$name" }
    return JSONObject()
        .put("ok", true)
        .put("op", "push_file")
        .put("name", name)
        .put("bytes", receipt.optLong("bytes"))
        .put("uri", receipt.optString("uri"))
        .put("phone_path", phonePath)
}

private fun sandboxShellCommandToDisplay(rawCmd: String, displayId: Int): String {
    var s = rawCmd
    s = s.replace(Regex("""--display\s+0\b"""), "--display $displayId")
    s = s.replace(Regex("""\bam\s+start(?:-activity)?\b(?![^;&|\n]*--display\b)""")) { "${it.value} --display $displayId" }
    s = s.replace(Regex("""\bcmd\s+activity\s+start(?:-activity)?\b(?![^;&|\n]*--display\b)""")) { "${it.value} --display $displayId" }
    s = s.replace(Regex("""\binput\s+-d\s+0\b"""), "input -d $displayId")
    s = s.replace(Regex("""\binput\s+(?!-d\b)(tap|swipe|keyevent|text|motionevent|press|roll|draganddrop)\b""")) {
        "input -d $displayId ${it.groupValues[1]}"
    }
    s = s.replace(
        Regex("""\binput(?:\s+-d\s+\d+)?\s+keyevent\s+(?:--longpress\s+)?(?:3|KEYCODE_HOME|187|KEYCODE_APP_SWITCH)\b"""),
        "am start --display $displayId -f 0x14000000 -n dev.bubuki.proactive/.DisplayTestActivity"
    )
    s = s.replace(Regex("""\bscreencap\s+-d\s+0\b"""), "screencap -d $displayId")
    s = s.replace(Regex("""\bscreencap\b(?!\s+-d\b)"""), "screencap -d $displayId")
    return s
}

internal fun RootPhoneHarness.executeShellAction(base: Context, input: JSONObject): JSONObject {
    var cmdStr = input.optString("command", input.optString("cmd", input.optString("text", ""))).trim()
    if (cmdStr.isEmpty()) throw Exception("请提供要执行的 shell command")
    val waitingForeground = state.value.phase == "waiting_user"
    if (Regex("""\bcmd\s+uimode\b|\bservice\s+call\s+uimode\b|\bsettings\s+put\s+(?:system|secure|global)\s+(?:ui_night_mode|screen_brightness|screen_off_timeout|font_scale|user_rotation|accelerometer_rotation|default_input_method|enabled_input_methods)\b""", RegexOption.IGNORE_CASE).containsMatchIn(cmdStr)) {
        throw Exception("安全沙箱已拦截全局系统设置修改指令（严禁修改主屏深浅色 Night mode、亮度、字体缩放或输入法等系统全局设置）")
    }
    if (!waitingForeground && Regex("""\bmonkey\b""", RegexOption.IGNORE_CASE).containsMatchIn(cmdStr)) {
        throw Exception("安全沙箱已拦截 monkey 指令（monkey 会直接操作主屏幕 Display 0），请改用 phone_workspace(op=\"open\") 或 am start")
    }
    if (!waitingForeground && Regex("""(?:--display|-d)\s+0\b""", RegexOption.IGNORE_CASE).containsMatchIn(cmdStr)) {
        throw Exception("安全沙箱已拦截指向主屏幕 (Display 0) 的指令，严禁在未接管状态下操作或启动应用到 Display 0")
    }
    var dispObj = runCatching { JSONObject(state.value.display) }.getOrNull()
    var currentDisp = if (dispObj?.optBoolean("created", false) == true) dispObj.optInt("displayId", -1) else -1
    val touchesDisplayUi = Regex(
        """\b(input|screencap|uiautomator)\b|\bam\s+(start|display)\b|\bcmd\s+activity\s+start|\bwm\s+(size|density|overscan|user-rotation)\b""",
        RegexOption.IGNORE_CASE
    ).containsMatchIn(cmdStr)
    if (!waitingForeground && touchesDisplayUi && currentDisp <= 0) {
        runCatching {
            val woke = command(base, JSONObject().put("op", "create").put("skip_home", false))
            currentDisp = woke.optInt("displayId", -1)
            dispObj = runCatching { JSONObject(state.value.display) }.getOrNull()
        }
        if (currentDisp <= 0) {
            throw Exception("虚拟屏尚未就绪 (displayId=$currentDisp)，安全沙箱已禁止 shell 指令操作或截取主屏幕 (Display 0)")
        }
    }
    val dW = dispObj?.optInt("width", 720)?.coerceAtLeast(1) ?: 720
    val dH = dispObj?.optInt("height", 1600)?.coerceAtLeast(1) ?: 1600
    val isBareUiautomatorDump = cmdStr.startsWith("uiautomator", ignoreCase = true) &&
        cmdStr.contains("dump", ignoreCase = true) &&
        !cmdStr.contains("&&") && !cmdStr.contains(";") && !cmdStr.contains("|")
    if (isBareUiautomatorDump && currentDisp > 0) {
        val treeJson = readUiTreeForDisplay(currentDisp, dW, dH, forceRefresh = true)
        return JSONObject()
            .put("ok", true)
            .put("uid", 0)
            .put("exit_code", 0)
            .put("output", treeJson.optString("summary", ""))
            .put("cwd", shellCwd)
    }
    if (!waitingForeground && currentDisp > 0) {
        if (Regex("""\bwm\s+(size|density|overscan|user-rotation)\b""", RegexOption.IGNORE_CASE).containsMatchIn(cmdStr) &&
            !Regex("""-d\s+$currentDisp\b""").containsMatchIn(cmdStr)
        ) {
            throw Exception("安全沙箱已拦截未绑定虚拟屏 (-d $currentDisp) 的 wm 显示参数修改指令，禁止修改主屏幕分辨率/DPI")
        }
        cmdStr = sandboxShellCommandToDisplay(cmdStr, currentDisp)
    }
    val safeCwd = "'" + shellCwd.replace("'", "'\\''") + "'"
    val wrappedCmd = "cd $safeCwd 2>/dev/null || cd /sdcard; $cmdStr; echo ''; echo '__PULSE_CWD__:'\$(pwd)"
    val hasRoot = privilege!!.isRootGrantedFast() || runCatching { privilege!!.probe().optBoolean("root") }.getOrDefault(false)
    val rawRes = if (hasRoot) {
        try {
            privilege!!.execute(JSONObject().put("op", "shell").put("command", wrappedCmd))
        } catch (rpcErr: Throwable) {
            // Fallback to direct `su -c` if the RootDisplayServer RPC pipe is restarting
            val proc = ProcessBuilder("su", "-c", wrappedCmd).redirectErrorStream(true).start()
            val finished = proc.waitFor(20, java.util.concurrent.TimeUnit.SECONDS)
            if (!finished) proc.destroyForcibly()
            val outTxt = runCatching { proc.inputStream.bufferedReader().readText() }.getOrDefault("")
            JSONObject().put("uid", 0).put("exit_code", if (finished) proc.exitValue() else 124).put("output", outTxt).put("direct_su_fallback", true)
        }
    } else {
        val proc = ProcessBuilder("sh", "-c", wrappedCmd).redirectErrorStream(true).start()
        val finished = proc.waitFor(15, java.util.concurrent.TimeUnit.SECONDS)
        if (!finished) proc.destroyForcibly()
        val outTxt = runCatching { proc.inputStream.bufferedReader().readText() }.getOrDefault("")
        JSONObject().put("uid", android.os.Process.myUid()).put("exit_code", if (finished) proc.exitValue() else 124).put("output", outTxt)
    }
    var rawOut = rawRes.optString("output", "")
    val markerIdx = rawOut.lastIndexOf("__PULSE_CWD__:")
    if (markerIdx >= 0) {
        val newCwd = rawOut.substring(markerIdx + "__PULSE_CWD__:".length).trim().lineSequence().firstOrNull()?.trim().orEmpty()
        if (newCwd.startsWith("/")) shellCwd = newCwd
        rawOut = rawOut.substring(0, markerIdx).trimEnd()
    }
    if (rawOut.length > 16000) {
        rawOut = rawOut.take(12000) + "\n...[输出过长已截断，原长 ${rawOut.length} 字符]...\n" + rawOut.takeLast(3000)
    }
    val timedOut = rawRes.optBoolean("timeout", false)
    val outJson = JSONObject()
        .put("ok", true)
        .put("uid", rawRes.optInt("uid", 0))
        .put("exit_code", rawRes.optInt("exit_code", 0))
        .put("timeout", timedOut)
        .put("output", rawOut)
        .put("cwd", shellCwd)
    if (timedOut) {
        outJson.put("partial_output", rawOut)
    }
    return outJson
}

internal fun RootPhoneHarness.executeNotificationsAction(base: Context, input: JSONObject): JSONObject {
    val filterQ = input.optString("app", input.optString("query", input.optString("package", ""))).trim()
    val limit = input.optInt("limit", 25).coerceIn(1, 80)
    if (!AgentNotificationListener.isConnected()) {
        runCatching {
            if (privilege!!.isRootGrantedFast() || privilege!!.probe().optBoolean("root")) {
                privilege!!.execute(
                    JSONObject()
                        .put("op", "shell")
                        .put("command", "cmd notification allow_listener ${base.packageName}/${base.packageName}.AgentNotificationListener")
                )
                Thread.sleep(180)
            }
        }
    }
    var items = AgentNotificationListener.getRecentNotifications(base, filterQ, limit)
    if (items.length() == 0) {
        runCatching {
            if (privilege!!.isRootGrantedFast() || privilege!!.probe().optBoolean("root")) {
                val dumpRes = privilege!!.execute(
                    JSONObject()
                        .put("op", "shell")
                        .put("command", "dumpsys notification --noredact | grep -E 'NotificationRecord\\(|pkg=|android\\.title=|android\\.text=|android\\.bigText=' | head -n 260")
                )
                val rawOut = dumpRes.optString("output", dumpRes.optString("stdout", ""))
                val parsed = JSONArray()
                var curPkg = ""
                var curTitle = ""
                var curText = ""
                fun flushRecord() {
                    if (curPkg.isNotBlank() && curPkg != base.packageName && curPkg != "android" && curPkg != "com.android.systemui" && (curTitle.isNotBlank() || curText.isNotBlank())) {
                        val appLabel = AgentNotificationListener.resolveAppName(base, curPkg)
                        val hay = "$appLabel $curPkg $curTitle $curText".lowercase()
                        if (filterQ.isBlank() || hay.contains(filterQ.lowercase())) {
                            parsed.put(
                                JSONObject()
                                    .put("package", curPkg)
                                    .put("app", appLabel)
                                    .put("title", curTitle)
                                    .put("text", curText)
                                    .put("active", true)
                            )
                        }
                    }
                    curTitle = ""
                    curText = ""
                }
                for (line in rawOut.lineSequence()) {
                    val s = line.trim()
                    if (s.contains("NotificationRecord(")) {
                        flushRecord()
                        val m = Regex("pkg=([^\\s:]+)").find(s)
                        curPkg = m?.groupValues?.get(1) ?: ""
                    } else if (s.startsWith("android.title=")) {
                        curTitle = s.substringAfter("android.title=").removePrefix("String (").removeSuffix(")").trim()
                    } else if (s.startsWith("android.bigText=") && s != "android.bigText=null") {
                        val bt = s.substringAfter("android.bigText=").removePrefix("String (").removeSuffix(")").trim()
                        if (bt.isNotBlank() && bt != "null") curText = bt
                    } else if (s.startsWith("android.text=") && curText.isBlank()) {
                        val tx = s.substringAfter("android.text=").removePrefix("String (").removeSuffix(")").trim()
                        if (tx != "null") curText = tx
                    }
                    if (parsed.length() >= limit) break
                }
                flushRecord()
                if (parsed.length() > 0) items = parsed
            }
        }
    }
    return JSONObject()
        .put("ok", true)
        .put("listener_connected", AgentNotificationListener.isConnected())
        .put("count", items.length())
        .put("notifications", items)
}

internal fun RootPhoneHarness.resolveGridOrNormCoords(operation: String, cmdObj: JSONObject, dispW: Int, dispH: Int) {
    if (cmdObj.has("start_x") && !cmdObj.has("x1")) cmdObj.put("x1", cmdObj.optInt("start_x"))
    if (cmdObj.has("start_y") && !cmdObj.has("y1")) cmdObj.put("y1", cmdObj.optInt("start_y"))
    if (cmdObj.has("end_x") && !cmdObj.has("x2")) cmdObj.put("x2", cmdObj.optInt("end_x"))
    if (cmdObj.has("end_y") && !cmdObj.has("y2")) cmdObj.put("y2", cmdObj.optInt("end_y"))
    val dir = cmdObj.optString("direction", "").trim().lowercase()
    if (operation == "swipe" && dir.isNotEmpty() && !cmdObj.has("x2")) {
        val cx = dispW / 2
        val cy = dispH / 2
        when (dir) {
            "up" -> { cmdObj.put("x1", cx); cmdObj.put("y1", (dispH * 0.72).toInt()); cmdObj.put("x2", cx); cmdObj.put("y2", (dispH * 0.25).toInt()) }
            "down" -> { cmdObj.put("x1", cx); cmdObj.put("y1", (dispH * 0.28).toInt()); cmdObj.put("x2", cx); cmdObj.put("y2", (dispH * 0.75).toInt()) }
            "left" -> { cmdObj.put("x1", (dispW * 0.80).toInt()); cmdObj.put("y1", cy); cmdObj.put("x2", (dispW * 0.20).toInt()); cmdObj.put("y2", cy) }
            "right" -> { cmdObj.put("x1", (dispW * 0.20).toInt()); cmdObj.put("y1", cy); cmdObj.put("x2", (dispW * 0.80).toInt()); cmdObj.put("y2", cy) }
        }
    }
    val grid = cmdObj.optString("grid", "").trim().uppercase()
    if (grid.length in 2..3 && grid[0] in 'A'..'F') {
        val colIdx = grid[0] - 'A'
        val rowIdx = (grid.substring(1).toIntOrNull() ?: 1).coerceIn(1, 10) - 1
        cmdObj.put("x", ((colIdx + 0.5) / 6.0 * dispW).toInt().coerceIn(1, (dispW - 2).coerceAtLeast(1)))
        cmdObj.put("y", ((rowIdx + 0.5) / 10.0 * dispH).toInt().coerceIn(1, (dispH - 2).coerceAtLeast(1)))
        return
    }
    val isNormFlag = cmdObj.optBoolean("normalized", false) || cmdObj.optString("coord_space", "") == "normalized"
    if (cmdObj.has("nx") && cmdObj.has("ny")) {
        val nx = cmdObj.optDouble("nx", 500.0).coerceIn(0.0, 1000.0)
        val ny = cmdObj.optDouble("ny", 500.0).coerceIn(0.0, 1000.0)
        cmdObj.put("x", (nx / 1000.0 * dispW).toInt().coerceIn(1, (dispW - 2).coerceAtLeast(1)))
        cmdObj.put("y", (ny / 1000.0 * dispH).toInt().coerceIn(1, (dispH - 2).coerceAtLeast(1)))
    } else if (!cmdObj.has("grid") && cmdObj.has("x") && cmdObj.has("y")) {
        val rx = cmdObj.optInt("x", 0)
        val ry = cmdObj.optInt("y", 0)
        if (isNormFlag || ((rx > dispW || ry > dispH) && rx <= 1000 && ry <= 1000)) {
            cmdObj.put("x", (rx / 1000.0 * dispW).toInt().coerceIn(1, (dispW - 2).coerceAtLeast(1)))
            cmdObj.put("y", (ry / 1000.0 * dispH).toInt().coerceIn(1, (dispH - 2).coerceAtLeast(1)))
        } else {
            cmdObj.put("x", rx.coerceIn(0, (dispW - 1).coerceAtLeast(0)))
            cmdObj.put("y", ry.coerceIn(0, (dispH - 1).coerceAtLeast(0)))
        }
    }
    if (cmdObj.has("nx1") && cmdObj.has("ny1")) {
        cmdObj.put("x1", (cmdObj.optDouble("nx1", 500.0).coerceIn(0.0, 1000.0) / 1000.0 * dispW).toInt().coerceIn(1, (dispW - 2).coerceAtLeast(1)))
        cmdObj.put("y1", (cmdObj.optDouble("ny1", 500.0).coerceIn(0.0, 1000.0) / 1000.0 * dispH).toInt().coerceIn(1, (dispH - 2).coerceAtLeast(1)))
    } else if (cmdObj.has("x1") && cmdObj.has("y1")) {
        val rx1 = cmdObj.optInt("x1", 0)
        val ry1 = cmdObj.optInt("y1", 0)
        if (isNormFlag || ((rx1 > dispW || ry1 > dispH) && rx1 <= 1000 && ry1 <= 1000)) {
            cmdObj.put("x1", (rx1 / 1000.0 * dispW).toInt().coerceIn(1, (dispW - 2).coerceAtLeast(1)))
            cmdObj.put("y1", (ry1 / 1000.0 * dispH).toInt().coerceIn(1, (dispH - 2).coerceAtLeast(1)))
        } else {
            cmdObj.put("x1", rx1.coerceIn(0, (dispW - 1).coerceAtLeast(0)))
            cmdObj.put("y1", ry1.coerceIn(0, (dispH - 1).coerceAtLeast(0)))
        }
    }
    if (cmdObj.has("nx2") && cmdObj.has("ny2")) {
        cmdObj.put("x2", (cmdObj.optDouble("nx2", 500.0).coerceIn(0.0, 1000.0) / 1000.0 * dispW).toInt().coerceIn(1, (dispW - 2).coerceAtLeast(1)))
        cmdObj.put("y2", (cmdObj.optDouble("ny2", 500.0).coerceIn(0.0, 1000.0) / 1000.0 * dispH).toInt().coerceIn(1, (dispH - 2).coerceAtLeast(1)))
    } else if (cmdObj.has("x2") && cmdObj.has("y2")) {
        val rx2 = cmdObj.optInt("x2", 0)
        val ry2 = cmdObj.optInt("y2", 0)
        if (isNormFlag || ((rx2 > dispW || ry2 > dispH) && rx2 <= 1000 && ry2 <= 1000)) {
            cmdObj.put("x2", (rx2 / 1000.0 * dispW).toInt().coerceIn(1, (dispW - 2).coerceAtLeast(1)))
            cmdObj.put("y2", (ry2 / 1000.0 * dispH).toInt().coerceIn(1, (dispH - 2).coerceAtLeast(1)))
        } else {
            cmdObj.put("x2", rx2.coerceIn(0, (dispW - 1).coerceAtLeast(0)))
            cmdObj.put("y2", ry2.coerceIn(0, (dispH - 1).coerceAtLeast(0)))
        }
    }
}

internal fun RootPhoneHarness.formatInteractiveNodesBrief(displayId: Int): String {
    val snap = PulseAccessibilityService.getCachedSnapshot(displayId) ?: return "无节点"
    val candidates = snap.nodes.filter { it.editable || it.clickable || it.role.equals("EditText", ignoreCase = true) }.take(8)
    if (candidates.isEmpty()) return "无节点"
    return candidates.joinToString("; ") { n ->
        val lbl = listOf(n.text, n.desc, n.hint).firstOrNull { it.isNotBlank() }.orEmpty()
        "${n.ref} [${n.role}]" +
            (if (lbl.isNotEmpty()) " \"$lbl\"" else "") +
            (if (n.resourceId.isNotEmpty()) " id=${n.resourceId}" else "") +
            " (${n.centerX},${n.centerY})"
    }
}

internal fun RootPhoneHarness.executeTapAndTypeAction(
    base: Context,
    input: JSONObject,
    currentDisplay: Int,
    dispW: Int,
    dispH: Int
): JSONObject {
    if (currentDisplay <= 0) throw Exception("先创建独立工作区")
    val ref = input.optString("ref", input.optString("node", "")).trim()
    val targetLabel = input.optString("target", input.optString("label", input.optString("hint", ""))).trim()
    val resId = input.optString("id", input.optString("view_id", input.optString("resource_id", ""))).trim()
    val txt = input.optString("text", input.optString("value", ""))
    var resolvedNode: PulseAccessibilityService.CachedNode? = null
    if (!input.has("x") || !input.has("y")) {
        if (ref.isNotEmpty() || targetLabel.isNotEmpty() || resId.isNotEmpty()) {
            resolvedNode = PulseAccessibilityService.getCachedSnapshot(currentDisplay)?.let {
                PulseAccessibilityService.matchNodeInSnapshot(it, ref, targetLabel, resId, preferEditable = true)
            } ?: PulseAccessibilityService.resolveTargetNode(currentDisplay, dispW, dispH, ref, targetLabel, resId, preferEditable = true)
            if (resolvedNode == null) {
                readUiTreeForDisplay(currentDisplay, dispW, dispH, forceRefresh = true)
                resolvedNode = PulseAccessibilityService.resolveTargetNode(currentDisplay, dispW, dispH, ref, targetLabel, resId, preferEditable = true)
            }
        }
        if (resolvedNode == null) {
            var snap = PulseAccessibilityService.getCachedSnapshot(currentDisplay)
            if (snap == null || snap.nodes.isEmpty()) {
                readUiTreeForDisplay(currentDisplay, dispW, dispH, forceRefresh = true)
                snap = PulseAccessibilityService.getCachedSnapshot(currentDisplay)
            }
            resolvedNode = snap?.nodes?.firstOrNull { n ->
                n.editable || n.role.equals("EditText", ignoreCase = true)
            } ?: snap?.nodes?.firstOrNull { n ->
                n.text.contains("搜索") || n.text.contains("输入") ||
                    n.hint.contains("搜索") || n.hint.contains("输入") ||
                    n.resourceId.contains("search", ignoreCase = true) ||
                    n.resourceId.contains("input", ignoreCase = true) ||
                    n.resourceId.contains("edit", ignoreCase = true)
            }
        }
    }
    val brief = formatInteractiveNodesBrief(currentDisplay)
    val tx = if (input.has("x")) input.optInt("x") else (resolvedNode?.centerX ?: throw Exception("未识别到输入框节点 (ref=$ref, target=$targetLabel, id=$resId)；当前可交互节点: $brief"))
    val ty = if (input.has("y")) input.optInt("y") else (resolvedNode?.centerY ?: throw Exception("未识别到输入框节点 (ref=$ref, target=$targetLabel, id=$resId)；当前可交互节点: $brief"))
    var a11yOk = false
    if (txt.isNotEmpty() && resolvedNode != null) {
        a11yOk = PulseAccessibilityService.performAccessibilityAction(currentDisplay, resolvedNode, "set_text", txt)
    }
    if (!a11yOk) {
        privilege!!.execute(JSONObject().put("op", "tap").put("display_id", currentDisplay).put("x", tx).put("y", ty))
        state.update { it.copy(lastTouchX = tx, lastTouchY = ty, lastTouchTime = System.currentTimeMillis()) }
        FloatingVirtualDisplay.onWorkspaceStateChanged(base.applicationContext, state.value)
        Thread.sleep(320)
        if (txt.isNotEmpty()) {
            readUiTreeForDisplay(currentDisplay, dispW, dispH, forceRefresh = true, waitMs = 450L)
            val newEdit = PulseAccessibilityService.getCachedSnapshot(currentDisplay)?.nodes?.firstOrNull { it.editable || it.role.equals("EditText", ignoreCase = true) } ?: resolvedNode
            if (newEdit != null) {
                a11yOk = PulseAccessibilityService.performAccessibilityAction(currentDisplay, newEdit, "set_text", txt)
            }
        }
    }
    val typeRes = if (txt.isEmpty()) {
        JSONObject()
    } else if (a11yOk) {
        JSONObject().put("ok", true).put("typed", true).put("method", "accessibility_set_text")
    } else {
        privilege!!.execute(JSONObject().put("op", "text").put("display_id", currentDisplay).put("text", txt))
    }
    if (input.optBoolean("submit", false) || input.optBoolean("press_enter", false) || input.optBoolean("send", false)) {
        Thread.sleep(200)
        privilege!!.execute(JSONObject().put("op", "key").put("display_id", currentDisplay).put("key", "enter"))
    }
    val afterTree = readUiTreeForDisplay(currentDisplay, dispW, dispH, forceRefresh = true, waitMs = 450L)
    val summary = afterTree.optString("summary", "")
    val aHash = afterTree.optString("treeHash", "")
    state.update { it.copy(lastTreeSummary = summary) }
    return JSONObject()
        .put("uid", 0)
        .put("ok", true)
        .put("created", true)
        .put("displayId", currentDisplay)
        .put("display_id", currentDisplay)
        .put("width", dispW)
        .put("height", dispH)
        .put("densityDpi", afterTree.optInt("densityDpi", ((dispW * 160) / 360).coerceIn(240, 640)))
        .put("screenWidthDp", afterTree.optInt("screenWidthDp", 360))
        .put("fontScale", 1.0)
        .put("op", "tap_and_type")
        .put("ref", resolvedNode?.ref ?: ref)
        .put("x", tx)
        .put("y", ty)
        .put("tapped", JSONObject().put("x", tx).put("y", ty))
        .put("typed", typeRes)
        .put("package", afterTree.optString("package", "unknown"))
        .put("treeHash", aHash)
        .put("tree_hash", aHash)
        .put("nodeCount", afterTree.optInt("nodeCount", 0))
        .put("layoutStable", afterTree.optBoolean("layoutStable", false))
        .put("settled", afterTree.optBoolean("settled", false))
        .put("settle_ms", afterTree.optLong("settle_ms", 0L))
        .put("layout_signature", afterTree.optString("layout_signature", ""))
        .put("ui_tree", summary)
        .also { attachLatestDisplayFrame(it, currentDisplay) }
}

internal fun RootPhoneHarness.executeTapNodeAction(
    base: Context,
    input: JSONObject,
    currentDisplay: Int,
    dispW: Int,
    dispH: Int
): JSONObject {
    if (currentDisplay <= 0) throw Exception("先创建独立工作区")
    val ref = input.optString("ref", input.optString("node", "")).trim()
    val text = input.optString("text", input.optString("target", input.optString("label", ""))).trim()
    val resId = input.optString("id", input.optString("resource_id", "")).trim()
    var node = PulseAccessibilityService.getCachedSnapshot(currentDisplay)?.let {
        PulseAccessibilityService.matchNodeInSnapshot(it, ref, text, resId)
    } ?: PulseAccessibilityService.resolveTargetNode(currentDisplay, dispW, dispH, ref, text, resId)
    if (node == null) {
        readUiTreeForDisplay(currentDisplay, dispW, dispH, forceRefresh = true)
        node = PulseAccessibilityService.resolveTargetNode(currentDisplay, dispW, dispH, ref, text, resId)
    }
    if (node == null) {
        val brief = formatInteractiveNodesBrief(currentDisplay)
        throw Exception("在当前无障碍节点树中未找到目标节点 (ref=$ref, text=$text, id=$resId)；当前可交互节点: $brief")
    }
    val prevSnap = PulseAccessibilityService.getCachedSnapshot(currentDisplay)
    val beforeHash = prevSnap?.treeHash.orEmpty()
    val beforePkg = prevSnap?.activePackage.orEmpty()
    val a11yClicked = PulseAccessibilityService.performAccessibilityAction(currentDisplay, node, "click")
    var needCenterTap = !a11yClicked
    var settledTree: JSONObject? = null
    if (a11yClicked) {
        try { Thread.sleep(240L) } catch (ignored: InterruptedException) {}
        val checkTree = readUiTreeForDisplay(currentDisplay, dispW, dispH, forceRefresh = true, waitMs = 550L)
        val checkPkg = checkTree.optString("package")
        val checkHash = checkTree.optString("treeHash")
        if (checkPkg == beforePkg && checkHash == beforeHash) {
            needCenterTap = true
        } else {
            settledTree = checkTree
        }
    }
    val tapRes = if (needCenterTap) {
        val rawTap = privilege!!.execute(JSONObject().put("op", "tap").put("display_id", currentDisplay).put("x", node.centerX).put("y", node.centerY))
        try { Thread.sleep(260L) } catch (ignored: InterruptedException) {}
        settledTree = readUiTreeForDisplay(currentDisplay, dispW, dispH, forceRefresh = true, waitMs = 650L)
        rawTap
    } else {
        JSONObject().put("uid", 0).put("created", true).put("displayId", currentDisplay).put("display_id", currentDisplay).put("clicked", true)
    }
    var finalTree = settledTree ?: readUiTreeForDisplay(currentDisplay, dispW, dispH, forceRefresh = true, waitMs = 550L)
    if (beforePkg == "dev.bubuki.proactive" && !node.editable && finalTree.optString("package") == "dev.bubuki.proactive") {
        // Tapped an app icon on VirtualDisplay Home Screen (`DisplayTestActivity`); wait for async app launch to settle
        try { Thread.sleep(480L) } catch (ignored: InterruptedException) {}
        finalTree = readUiTreeForDisplay(currentDisplay, dispW, dispH, forceRefresh = true, waitMs = 850L)
    }
    val summary = finalTree.optString("summary", "")
    val fHash = finalTree.optString("treeHash", "")
    val nowTs = System.currentTimeMillis()
    state.update { it.copy(lastTouchX = node.centerX, lastTouchY = node.centerY, lastTouchTime = nowTs, lastTreeSummary = summary) }
    FloatingVirtualDisplay.onWorkspaceStateChanged(base.applicationContext, state.value)
    return tapRes
        .put("created", true)
        .put("displayId", currentDisplay)
        .put("display_id", currentDisplay)
        .put("width", dispW)
        .put("height", dispH)
        .put("densityDpi", afterTree.optInt("densityDpi", ((dispW * 160) / 360).coerceIn(240, 640)))
        .put("screenWidthDp", afterTree.optInt("screenWidthDp", 360))
        .put("fontScale", 1.0)
        .put("clicked", true)
        .put("method", if (!needCenterTap) "accessibility_node" else "node_center_tap")
        .put("ref", node.ref)
        .put("role", node.role)
        .put("label", node.text.ifBlank { node.desc })
        .put("x", node.centerX)
        .put("y", node.centerY)
        .put("package", finalTree.optString("package", "unknown"))
        .put("treeHash", fHash)
        .put("tree_hash", fHash)
        .put("nodeCount", finalTree.optInt("nodeCount", 0))
        .put("layoutStable", finalTree.optBoolean("layoutStable", false))
        .put("settled", finalTree.optBoolean("settled", false))
        .put("settle_ms", finalTree.optLong("settle_ms", 0L))
        .put("likely_blank", finalTree.optBoolean("likely_blank", false))
        .put("layout_signature", finalTree.optString("layout_signature", ""))
        .put("ui_tree", summary)
        .also { attachLatestDisplayFrame(it, currentDisplay) }
}

internal fun RootPhoneHarness.executeTypeNodeAction(
    input: JSONObject,
    currentDisplay: Int,
    dispW: Int,
    dispH: Int
): JSONObject {
    if (currentDisplay <= 0) throw Exception("先创建独立工作区")
    val ref = input.optString("ref", input.optString("node", "")).trim()
    val targetLabel = input.optString("target", input.optString("label", input.optString("hint", ""))).trim()
    val resId = input.optString("id", input.optString("resource_id", "")).trim()
    val contentText = input.optString("text", input.optString("value", ""))
    if (contentText.isEmpty()) throw Exception("text/type_node 需要提供要输入的 text")
    val hasExplicitQuery = ref.isNotEmpty() || targetLabel.isNotEmpty() || resId.isNotEmpty()
    var node = if (hasExplicitQuery) {
        PulseAccessibilityService.getCachedSnapshot(currentDisplay)?.let {
            PulseAccessibilityService.matchNodeInSnapshot(it, ref, targetLabel, resId, preferEditable = true)
        } ?: PulseAccessibilityService.resolveTargetNode(currentDisplay, dispW, dispH, ref, targetLabel, resId, preferEditable = true)
    } else {
        PulseAccessibilityService.getCachedSnapshot(currentDisplay)?.nodes?.firstOrNull { it.editable || it.role.equals("EditText", ignoreCase = true) }
    }
    if (node == null) {
        readUiTreeForDisplay(currentDisplay, dispW, dispH, forceRefresh = true)
        node = if (hasExplicitQuery) {
            PulseAccessibilityService.resolveTargetNode(currentDisplay, dispW, dispH, ref, targetLabel, resId, preferEditable = true)
        } else {
            PulseAccessibilityService.getCachedSnapshot(currentDisplay)?.nodes?.firstOrNull { it.editable || it.role.equals("EditText", ignoreCase = true) }
        }
    }
    if (node == null) {
        node = PulseAccessibilityService.getCachedSnapshot(currentDisplay)?.nodes?.firstOrNull {
            it.editable || it.role.equals("EditText", ignoreCase = true) ||
                it.resourceId.contains("search", ignoreCase = true) ||
                it.resourceId.contains("input", ignoreCase = true) ||
                it.resourceId.contains("edit", ignoreCase = true) ||
                it.resourceId.contains("form", ignoreCase = true)
        }
    }
    if (node != null) {
        var a11yTyped = PulseAccessibilityService.performAccessibilityAction(currentDisplay, node, "set_text", contentText)
        if (!a11yTyped) {
            runCatching {
                privilege!!.execute(JSONObject().put("op", "tap").put("display_id", currentDisplay).put("x", node.centerX).put("y", node.centerY))
                Thread.sleep(240L)
            }
            readUiTreeForDisplay(currentDisplay, dispW, dispH, forceRefresh = true, waitMs = 450L)
            val focusedEdit = PulseAccessibilityService.getCachedSnapshot(currentDisplay)?.nodes?.firstOrNull {
                it.editable || it.role.equals("EditText", ignoreCase = true)
            } ?: node
            a11yTyped = PulseAccessibilityService.performAccessibilityAction(currentDisplay, focusedEdit, "set_text", contentText)
            if (a11yTyped) node = focusedEdit
        }
        if (a11yTyped) {
            if (input.optBoolean("submit", false) || input.optBoolean("press_enter", false) || input.optBoolean("send", false)) {
                Thread.sleep(200)
                privilege!!.execute(JSONObject().put("op", "key").put("display_id", currentDisplay).put("key", "enter"))
            }
            val afterTree = readUiTreeForDisplay(currentDisplay, dispW, dispH, forceRefresh = true, waitMs = 450L)
            val summary = afterTree.optString("summary", "")
            val aHash = afterTree.optString("treeHash", "")
            state.update { it.copy(lastTreeSummary = summary) }
            return JSONObject()
                .put("uid", 0)
                .put("ok", true)
                .put("created", true)
                .put("displayId", currentDisplay)
                .put("display_id", currentDisplay)
                .put("width", dispW)
                .put("height", dispH)
                .put("densityDpi", ((dispW * 160) / 420).coerceIn(240, 640))
                .put("screenWidthDp", 420)
                .put("fontScale", 1.0)
                .put("typed", true)
                .put("method", "accessibility_set_text")
                .put("ref", node.ref)
                .put("role", node.role)
                .put("characters", contentText.length)
                .put("package", afterTree.optString("package", "unknown"))
                .put("treeHash", aHash)
                .put("tree_hash", aHash)
                .put("nodeCount", afterTree.optInt("nodeCount", 0))
                .put("layoutStable", afterTree.optBoolean("layoutStable", false))
                .put("settled", afterTree.optBoolean("settled", false))
                .put("settle_ms", afterTree.optLong("settle_ms", 0L))
                .put("layout_signature", afterTree.optString("layout_signature", ""))
                .put("ui_tree", summary)
        }
    }
    if (node == null && hasExplicitQuery) {
        val brief = formatInteractiveNodesBrief(currentDisplay)
        throw Exception("未找到输入框节点 (ref=$ref, target=$targetLabel, id=$resId)；当前可交互节点: $brief")
    }
    val pasteRes = privilege!!.execute(JSONObject().put("op", "text").put("display_id", currentDisplay).put("text", contentText))
    if (input.optBoolean("submit", false) || input.optBoolean("press_enter", false) || input.optBoolean("send", false)) {
        Thread.sleep(200)
        privilege!!.execute(JSONObject().put("op", "key").put("display_id", currentDisplay).put("key", "enter"))
    }
    val afterTree = readUiTreeForDisplay(currentDisplay, dispW, dispH, forceRefresh = true, waitMs = 450L)
    val summary = afterTree.optString("summary", "")
    val pHash = afterTree.optString("treeHash", "")
    state.update { it.copy(lastTreeSummary = summary) }
    return pasteRes
        .put("ok", true)
        .put("created", true)
        .put("displayId", currentDisplay)
        .put("display_id", currentDisplay)
        .put("width", dispW)
        .put("height", dispH)
        .put("densityDpi", afterTree.optInt("densityDpi", ((dispW * 160) / 360).coerceIn(240, 640)))
        .put("screenWidthDp", afterTree.optInt("screenWidthDp", 360))
        .put("fontScale", 1.0)
        .put("typed", true)
        .put("method", "clipboard_paste")
        .put("ref", node?.ref.orEmpty())
        .put("package", afterTree.optString("package", "unknown"))
        .put("treeHash", pHash)
        .put("tree_hash", pHash)
        .put("nodeCount", afterTree.optInt("nodeCount", 0))
        .put("layoutStable", afterTree.optBoolean("layoutStable", false))
        .put("settled", afterTree.optBoolean("settled", false))
        .put("settle_ms", afterTree.optLong("settle_ms", 0L))
        .put("layout_signature", afterTree.optString("layout_signature", ""))
        .put("ui_tree", summary)
        .also { attachLatestDisplayFrame(it, currentDisplay) }
}

internal fun RootPhoneHarness.resolveLongPressNodeCoords(
    input: JSONObject,
    currentDisplay: Int,
    dispW: Int,
    dispH: Int
) {
    if (currentDisplay <= 0) throw Exception("先创建独立工作区")
    val ref = input.optString("ref", input.optString("node", "")).trim()
    val text = input.optString("text", input.optString("target", input.optString("label", ""))).trim()
    val resId = input.optString("id", input.optString("resource_id", "")).trim()
    var node = PulseAccessibilityService.resolveTargetNode(currentDisplay, dispW, dispH, ref, text, resId)
    if (node == null) {
        readUiTreeForDisplay(currentDisplay, dispW, dispH, forceRefresh = true)
        node = PulseAccessibilityService.resolveTargetNode(currentDisplay, dispW, dispH, ref, text, resId)
    }
    if (node == null) {
        throw Exception("在当前无障碍节点树中未找到长按目标节点 (ref=$ref, text=$text, id=$resId)")
    }
    input.put("x", node.centerX).put("y", node.centerY)
}

internal fun RootPhoneHarness.attachLatestDisplayFrame(target: JSONObject, displayId: Int): JSONObject {
    if (displayId <= 0 || target.optString("image").isNotBlank()) return target
    runCatching {
        val snap = privilege?.execute(JSONObject().put("op", "preview").put("since", -1).put("fresh", true)) ?: return@runCatching
        val img = snap.optString("image")
        if (img.isNotBlank()) {
            target.put("image", img)
            if (snap.optInt("width", 0) > 0) target.put("width", snap.optInt("width"))
            if (snap.optInt("height", 0) > 0) target.put("height", snap.optInt("height"))
            val fh = snap.optString("frameHash")
            if (fh.isNotBlank()) target.put("frameHash", fh)
        }
    }
    return target
}

internal fun RootPhoneHarness.readUiTreeForDisplay(
    displayId: Int,
    dispW: Int,
    dispH: Int,
    forceRefresh: Boolean,
    waitMs: Long = 750L
): JSONObject {
    if (!PulseAccessibilityService.isConnected()) {
        runCatching { privilege?.probe() }
        val waitDeadline = SystemClock.uptimeMillis() + 300L
        while (!PulseAccessibilityService.isConnected() && SystemClock.uptimeMillis() < waitDeadline) {
            try { Thread.sleep(60L) } catch (ignored: InterruptedException) { break }
        }
    }
    PulseAccessibilityService.dumpDisplayTree(displayId, dispW, dispH, forceRefresh, waitMs = waitMs)?.let {
        return attachLatestDisplayFrame(it.put("uid", 0).put("source", "accessibility_windows"), displayId)
    }
    val emptySnap = PulseAccessibilityService.updateSnapshotFromExternal(displayId, emptyList(), "", dispW, dispH, layoutStable = false)
    return attachLatestDisplayFrame(
        PulseAccessibilityService.snapshotToJson(emptySnap, fromCache = false)
            .put("uid", 0)
            .put("source", "empty"),
        displayId
    )
}

internal fun RootPhoneHarness.queryLauncherApps(base: Context, forceRefresh: Boolean = false): List<PhoneApp> {
    val now = SystemClock.elapsedRealtime()
    if (!forceRefresh && cachedLauncherApps.isNotEmpty() && now - appsCachedAtElapsed < APPS_CACHE_TTL_MS) {
        return cachedLauncherApps
    }
    val launcher = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
    val fresh = base.packageManager.queryIntentActivities(launcher, 0)
        .filter { it.activityInfo.packageName != base.packageName }
        .map { PhoneApp(it.loadLabel(base.packageManager).toString(), it.activityInfo.packageName + "/" + it.activityInfo.name) }
        .distinctBy { it.component }
        .sortedBy { it.name }
    cachedLauncherApps = fresh
    appsCachedAtElapsed = now
    return fresh
}

internal fun RootPhoneHarness.resolveLaunchComponent(base: Context, query: String): String {
    if (query.isBlank()) throw Exception("请提供要打开的应用名或组件名")
    val qTrim = query.trim()
    resolvedComponentCache[qTrim]?.let { return it }
    if (qTrim.contains("/") && qTrim.matches(Regex("[A-Za-z0-9_.$]+/[A-Za-z0-9_.$]+"))) {
        resolvedComponentCache[qTrim] = qTrim
        return qTrim
    }
    val apps = state.value.apps.ifEmpty { queryLauncherApps(base).also { list -> state.update { it.copy(apps = list) } } }
    val qLower = qTrim.lowercase()
    val stripped = qLower
        .replace(Regex("^(oppo|vivo|小米|miui|hyperos|华为|huawei|荣耀|honor|一加|oneplus|realme|真我|红米|redmi|三星|samsung|系统|手机|自带)\\s*"), "")
        .replace(Regex("(app|客户端|应用)$"), "")
        .trim()
        .ifBlank { qLower }

    val aliasPackages = mapOf(
        "微信" to "com.tencent.mm", "wechat" to "com.tencent.mm",
        "b站" to "tv.danmaku.bili", "bilibili" to "tv.danmaku.bili", "哔哩哔哩" to "tv.danmaku.bili",
        "qq" to "com.tencent.mobileqq",
        "网易云" to "com.netease.cloudmusic", "网易云音乐" to "com.netease.cloudmusic",
        "qq音乐" to "com.tencent.qqmusic",
        "健康" to "com.heytap.health", "运动健康" to "com.heytap.health",
        "淘宝" to "com.taobao.taobao", "支付宝" to "com.eg.android.AlipayGphone",
        "高德" to "com.autonavi.minimap", "高德地图" to "com.autonavi.minimap",
        "美团" to "com.sankuai.meituan", "小红书" to "com.xingin.xhs",
        "抖音" to "com.ss.android.ugc.aweme", "知乎" to "com.zhihu.android"
    )
    val aliasPkg = aliasPackages[qLower] ?: aliasPackages[stripped]
    if (aliasPkg != null) {
        apps.firstOrNull { it.component.substringBefore("/").equals(aliasPkg, ignoreCase = true) }?.let {
            resolvedComponentCache[qTrim] = it.component
            return it.component
        }
    }

    apps.firstOrNull {
        val pkg = it.component.substringBefore("/").lowercase()
        val nm = it.name.lowercase()
        pkg == qLower || nm == qLower || nm == stripped
    }?.let {
        resolvedComponentCache[qTrim] = it.component
        return it.component
    }
    apps.firstOrNull {
        val nm = it.name.lowercase()
        val comp = it.component.lowercase()
        nm.contains(qLower) || nm.contains(stripped) ||
            (nm.length >= 2 && (qLower.contains(nm) || stripped.contains(nm))) ||
            comp.contains(qLower)
    }?.let {
        resolvedComponentCache[qTrim] = it.component
        return it.component
    }
    base.packageManager.getLaunchIntentForPackage(qTrim)?.component?.let {
        val comp = it.packageName + "/" + it.className
        resolvedComponentCache[qTrim] = comp
        return comp
    }
    val sampleApps = apps.take(25).joinToString("、") { it.name }
    throw Exception("未在手机上找到应用「$qTrim」；已安装的部分应用有：$sampleApps（也可调用 op=\"apps\" 搜索）")
}
