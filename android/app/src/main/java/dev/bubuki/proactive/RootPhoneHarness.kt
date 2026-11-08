package dev.bubuki.proactive

import android.content.Context
import android.content.Intent
import android.graphics.Rect
import android.os.Build
import android.os.PowerManager
import android.os.SystemClock
import android.util.Xml
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import org.json.JSONArray
import org.json.JSONObject
import org.xmlpull.v1.XmlPullParser
import java.io.StringReader
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap

internal data class PhoneApp(val name: String, val component: String)
internal data class RootPhoneState(
    val root: Boolean = false, val sdk: Int = Build.VERSION.SDK_INT, val display: String = "{}", val image: String = "",
    val phase: String = "idle", val botId: String? = null, val goal: String = "", val result: String = "", val error: String = "",
    val trace: String = "[]", val messages: List<PulseMessage> = emptyList(), val apps: List<PhoneApp> = emptyList(), val model: String = "",
    val modelReady: Boolean = false, val imageInfo: String = "{}", val notifications: Boolean = false,
    val overlay: Boolean = false, val listener: Boolean = false, val background: Boolean = false,
    val lastTouchX: Int = -1, val lastTouchY: Int = -1, val lastTouchTime: Long = 0L,
    val lastTreeSummary: String = ""
) {
    val active: Boolean get() = phase in listOf("running", "waiting_user")
    val displayCreated: Boolean get() = runCatching { JSONObject(display).optBoolean("created") }.getOrDefault(false)
    val displayId: Int get() = runCatching { JSONObject(display).let { it.optInt("displayId", it.optInt("display_id", -1)) } }.getOrDefault(-1)
}

/**
 * Root virtual display workspace with:
 * - Accessibility UI Tree first (`ui_tree`, `tap_node`, `type_node`) and coordinate hard-tap (`tap`) last
 * - Screen-dim wake lock (`KeepAwake`) while display is active so the phone/virtual display never auto-locks
 * - Automatic virtual display release 2.5s after task completion or after 45s of inactivity
 * - Multi-layer caching for launcher apps, component resolution, and UI tree snapshots
 */
object RootPhoneHarness {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val state = MutableStateFlow(RootPhoneState())
    internal val changes: StateFlow<RootPhoneState> = state.asStateFlow()
    private var context: Context? = null
    private var privilege: PrivilegeManager? = null
    private var initialized = false
    private var job: Job? = null
    private var previewJob: Job? = null
    private var idleWatchJob: Job? = null
    private var autoReleaseJob: Job? = null
    private var lastPreviewSequence: Long = -1L
    @Volatile private var lastActivityElapsed: Long = SystemClock.elapsedRealtime()
    @Volatile private var lastLiveTouchElapsed: Long = 0L
    private val liveTouchExecutor = java.util.concurrent.Executors.newSingleThreadExecutor { r ->
        Thread(r, "pulse-live-touch").apply { priority = Thread.MAX_PRIORITY; isDaemon = true }
    }
    private var agent: RootPhoneAgent? = null
    private var lastCaptureRotation: Int? = null
    private var screenWakeLock: PowerManager.WakeLock? = null
    private val setupLock = Any()
    private val loaded = CompletableDeferred<Unit>()
    private val submitLock = Mutex()

    // Multi-layer caches for fast 0ms lookups
    @Volatile private var cachedLauncherApps: List<PhoneApp> = emptyList()
    @Volatile private var appsCachedAtElapsed: Long = 0L
    @Volatile private var shellCwd: String = "/sdcard"
    private const val APPS_CACHE_TTL_MS = 5 * 60 * 1000L
    private const val IDLE_AUTO_RELEASE_MS = 45_000L
    private val resolvedComponentCache = ConcurrentHashMap<String, String>()

    @JvmStatic fun isVirtualDisplayActive(): Boolean {
        val st = state.value
        return st.displayCreated || st.active
    }

    @JvmStatic fun initialize(base: Context) {
        synchronized(setupLock) {
            if (initialized) return
            context = base.applicationContext; privilege = PrivilegeManager(context!!); initialized = true
        }
        scope.launch {
            try {
                val saved = SecurePrefs.read(context!!)
                val history = saved.optJSONArray("phoneHistory")?.objects()?.mapNotNull { runCatching { PulseMessage.from(it) }.getOrNull() }.orEmpty()
                val config = modelConfig(saved)
                val initialRoot = runCatching {
                    val st = privilege!!.status()
                    if (st.optBoolean("root")) true else privilege!!.probe().optBoolean("root")
                }.getOrDefault(false)
                state.update { it.copy(root = initialRoot, messages = history, model = config.optString("model"), modelReady = ready(config)) }
                val interrupted = saved.optJSONObject("phoneTask")
                if (interrupted?.optString("phase") in listOf("running", "waiting_user", "stopping")) {
                    val bot = interrupted!!.optString("botId", "companion")
                    append(message(bot, "assistant", "上次手机任务已中断，工作区不会自动重启。查看记录后可以重新开始。", "phone_stopped"))
                    persist()
                }
                loaded.complete(Unit)
            } catch (error: Exception) { loaded.completeExceptionally(error) }
        }
    }

    private fun modelConfig(saved: JSONObject): JSONObject {
        val config = saved.optJSONObject("config") ?: JSONObject()
        val big = config.optJSONObject("big_brain") ?: JSONObject()
        val small = config.optJSONObject("small_brain") ?: JSONObject()
        return JSONObject()
            .put("base_url", big.optString("base_url").ifBlank { config.optString("api_base_url").ifBlank { small.optString("base_url", "https://api.openai.com/v1") } })
            .put("api_key", big.optString("api_key").ifBlank { config.optString("api_key").ifBlank { small.optString("api_key") } })
            .put("model", big.optString("model").ifBlank { config.optString("default_model").ifBlank { small.optString("model") } })
    }
    private fun ready(config: JSONObject) = config.optString("api_key").isNotBlank() && config.optString("model").isNotBlank() && config.optString("base_url").isNotBlank()

    private fun acquireKeepAwake(base: Context) {
        runCatching {
            synchronized(setupLock) {
                // Strictly use PARTIAL_WAKE_LOCK (CPU only) so virtual display tasks NEVER touch or brighten the physical screen!
                val wl = screenWakeLock ?: base.applicationContext.getSystemService(PowerManager::class.java)
                    .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "Pulse:VirtualDisplayCpu")
                    .apply { setReferenceCounted(false) }
                    .also { screenWakeLock = it }
                wl.acquire(90_000L)
            }
        }
    }

    private fun releaseKeepAwake() {
        runCatching {
            synchronized(setupLock) {
                screenWakeLock?.let { if (it.isHeld) it.release() }
            }
        }
    }

    private fun isMusicActiveInWorkspace(base: Context): Boolean {
        return runCatching {
            val audio = base.getSystemService(android.media.AudioManager::class.java)
            audio?.isMusicActive == true
        }.getOrDefault(false)
    }

    @JvmStatic @JvmOverloads fun scheduleAutoRelease(base: Context, delayMs: Long = 1800L, toForeground: Boolean = false) {
        autoReleaseJob?.cancel()
        autoReleaseJob = scope.launch {
            delay(delayMs.coerceIn(500L, 15000L))
            val cur = state.value
            if (!cur.active) {
                val keepForMusic = isMusicActiveInWorkspace(base)
                runCatching {
                    command(
                        base.applicationContext,
                        JSONObject().put("op", "release").put("destroy", !keepForMusic)
                    )
                }
            }
        }
    }

    private fun touchActivity() {
        lastActivityElapsed = SystemClock.elapsedRealtime()
        autoReleaseJob?.cancel()
        runCatching {
            synchronized(setupLock) {
                val wl = screenWakeLock
                if (wl != null && wl.isHeld) wl.acquire(90_000L)
            }
        }
    }

    /**
     * Sub-2ms 1:1 live finger touch stream (`down` / `move` / `up` / `cancel`) for zero-latency scrolling!
     */
    @JvmStatic fun sendLiveTouch(base: Context, action: String, x: Int, y: Int) {
        initialize(base)
        val now = SystemClock.elapsedRealtime()
        lastActivityElapsed = now
        lastLiveTouchElapsed = now
        autoReleaseJob?.cancel()
        if (action == "down") {
            val nowWall = System.currentTimeMillis()
            state.update { it.copy(lastTouchX = x, lastTouchY = y, lastTouchTime = nowWall) }
        }
        liveTouchExecutor.execute {
            runCatching {
                privilege?.execute(
                    JSONObject()
                        .put("op", "touch")
                        .put("action", action)
                        .put("x", x)
                        .put("y", y)
                )
            }
        }
    }

    private fun startIdleWatchdog(appContext: Context) {
        if (idleWatchJob?.isActive == true) return
        idleWatchJob = scope.launch {
            while (isActive) {
                delay(5000L)
                val cur = state.value
                val disp = runCatching { JSONObject(cur.display) }.getOrNull()
                if (disp == null || !disp.optBoolean("created")) break
                if (cur.active) {
                    lastActivityElapsed = SystemClock.elapsedRealtime()
                    continue
                }
                val idleMs = SystemClock.elapsedRealtime() - lastActivityElapsed
                if (idleMs >= IDLE_AUTO_RELEASE_MS) {
                    runCatching { command(appContext, JSONObject().put("op", "release")) }
                    break
                }
            }
        }
    }

    @JvmStatic fun deviceInfo(base: Context): JSONObject {
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
            val caps = cm?.activeNetwork?.let { cm.getNetworkCapabilities(it) }
            val netType = when {
                caps == null -> "none"
                caps.hasTransport(android.net.NetworkCapabilities.TRANSPORT_WIFI) -> "wifi"
                caps.hasTransport(android.net.NetworkCapabilities.TRANSPORT_CELLULAR) -> "cellular"
                else -> "other"
            }
            out.put("network", JSONObject().put("connected", caps != null).put("type", netType))
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
        out.put("device", JSONObject()
            .put("brand", Build.BRAND)
            .put("model", Build.MODEL)
            .put("sdk", Build.VERSION.SDK_INT)
            .put("root", privilege?.isRootGrantedFast() == true)
        )
        return out
    }

    private fun resolveLatestPhoneFile(): java.io.File? {
        val dirs = listOf(
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

    @JvmStatic fun status(base: Context): JSONObject {
        initialize(base); val current = state.value
        var status = privilege!!.status()
        if (!status.optBoolean("root")) {
            status = runCatching { privilege!!.probe() }.getOrDefault(status)
        }
        val saved = SecurePrefs.read(base); val config = modelConfig(saved)
        state.update {
            it.copy(
                root = status.optBoolean("root"),
                model = config.optString("model"),
                modelReady = ready(config),
                notifications = status.optBoolean("notifications"),
                overlay = status.optBoolean("overlay"),
                listener = status.optBoolean("listener"),
                background = saved.optBoolean("background")
            )
        }
        return status.put("display", JSONObject(current.display))
            .put("phase", current.phase)
            .put("goal", current.goal)
            .put("result", current.result)
            .put("a11y_connected", PulseAccessibilityService.isConnected())
            .put("model", config.optString("model"))
            .put("model_ready", ready(config))
            .put("device_info", deviceInfo(base))
    }

    @JvmStatic fun history(base: Context): JSONObject {
        initialize(base)
        return JSONObject().put("messages", JSONArray(state.value.messages.map(::json))).put("status", status(base))
    }

    /** Called on IO threads by the UI, cloud bridge, or tool loop. */
    @JvmStatic fun command(base: Context, input: JSONObject): JSONObject {
        initialize(base)
        val rawOp = input.optString("op", "status").trim().lowercase()
        val operation = when (rawOp) {
            "close", "destroy", "stop_display", "promote", "expand_to_foreground", "to_foreground", "fullscreen" -> "release"
            "dump", "tree", "nodes", "read_screen" -> "ui_tree"
            "click_node" -> "tap_node"
            "input_node", "set_text" -> "type_node"
            "exec", "cmd", "bash" -> "shell"
            "phone_info", "battery" -> "device_info"
            "files" -> "list_files"
            "open_url" -> "open_uri"
            "wait", "sleep" -> "delay"
            "get_notifications", "recent_notifications", "notifs", "notification" -> "notifications"
            else -> rawOp
        }
        if (operation == "device_info") {
            return deviceInfo(base)
        }
        if (operation == "list_files") {
            val dirPath = input.optString("path", "/sdcard/Download").trim().ifBlank { "/sdcard/Download" }
            val limit = input.optInt("limit", 25).coerceIn(1, 100)
            val dir = java.io.File(dirPath)
            val arr = JSONArray()
            val files = dir.listFiles()?.sortedByDescending { it.lastModified() }
            if (files != null && files.isNotEmpty()) {
                for (f in files.take(limit)) {
                    arr.put(JSONObject()
                        .put("name", f.name)
                        .put("path", f.absolutePath)
                        .put("is_dir", f.isDirectory)
                        .put("bytes", if (f.isFile) f.length() else 0L)
                        .put("mtime", f.lastModified() / 1000)
                    )
                }
            } else if (privilege!!.isRootGrantedFast() || runCatching { privilege!!.probe().optBoolean("root") }.getOrDefault(false)) {
                val safeP = "'" + dirPath.replace("'", "'\\''") + "'"
                val lsRes = privilege!!.execute(JSONObject().put("op", "shell").put("command", "ls -lt $safeP | head -n ${limit + 1}"))
                return JSONObject().put("ok", true).put("op", "list_files").put("path", dirPath).put("ls_output", lsRes.optString("output", ""))
            }
            return JSONObject().put("ok", true).put("op", "list_files").put("path", dirPath).put("files", arr)
        }
        if (operation == "pull_file") {
            val rawPath = input.optString("path", input.optString("target", "latest")).trim()
            val source = input.optString("source", "workspace").trim().lowercase().let { if (it == "computer") "computer" else "workspace" }
            val targetFile = if (rawPath.isBlank() || rawPath == "latest" || rawPath == "latest_download") {
                resolveLatestPhoneFile()
            } else {
                java.io.File(rawPath)
            }
            var stagedCopy: java.io.File? = null
            try {
                val uploadFile = if (targetFile != null && targetFile.exists() && targetFile.canRead() && targetFile.length() > 0) {
                    targetFile
                } else if (rawPath.isNotBlank() && rawPath != "latest" && (privilege!!.isRootGrantedFast() || runCatching { privilege!!.probe().optBoolean("root") }.getOrDefault(false))) {
                    val baseName = rawPath.substringAfterLast('/').ifBlank { "pulled_file" }
                    val tmp = java.io.File(base.cacheDir, baseName)
                    val safeSrc = "'" + rawPath.replace("'", "'\\''") + "'"
                    val safeDst = "'" + tmp.absolutePath.replace("'", "'\\''") + "'"
                    privilege!!.execute(JSONObject().put("op", "shell").put("command", "cp $safeSrc $safeDst && chmod 644 $safeDst"))
                    if (!tmp.exists() || tmp.length() == 0L) throw Exception("手机端未找到或无法读取文件: $rawPath")
                    stagedCopy = tmp
                    tmp
                } else {
                    throw Exception("手机端未找到可读取文件: ${rawPath.ifBlank { "/sdcard/Download" }}")
                }
                val receipt = CloudFiles.upload(base, android.net.Uri.fromFile(uploadFile), source)
                return JSONObject()
                    .put("ok", true)
                    .put("op", "pull_file")
                    .put("phone_path", targetFile?.absolutePath ?: rawPath)
                    .put("workspace_path", receipt.optString("path"))
                    .put("name", receipt.optString("name", uploadFile.name))
                    .put("bytes", receipt.optLong("bytes", uploadFile.length()))
                    .put("sha256", receipt.optString("sha256"))
            } finally {
                stagedCopy?.delete()
            }
        }
        if (operation == "push_file") {
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
        // Direct Zero-Idle-Battery Root/Local shell execution (stateful cwd, no virtual display creation or wakelock!)
        if (operation == "shell") {
            var cmdStr = input.optString("command", input.optString("cmd", input.optString("text", ""))).trim()
            if (cmdStr.isEmpty()) throw Exception("请提供要执行的 shell command")
            var currentDisp = JSONObject(state.value.display).optInt("displayId", -1)
            val dispInf = JSONObject(state.value.imageInfo.ifBlank { state.value.display })
            val dW = dispInf.optInt("width", 720).coerceAtLeast(1)
            val dH = dispInf.optInt("height", 1600).coerceAtLeast(1)
            if (cmdStr.contains("uiautomator", ignoreCase = true) && cmdStr.contains("dump", ignoreCase = true)) {
                if (currentDisp <= 0) throw Exception("先创建独立工作区")
                val treeJson = readUiTreeForDisplay(currentDisp, dW, dH, forceRefresh = true)
                return treeJson.put("output", treeJson.optString("summary", ""))
            }
            // Guard against bare `am start` or `input keyevent KEYCODE_HOME` hijacking physical Display 0!
            if (Regex("""\bam\s+start\b""").containsMatchIn(cmdStr) && !cmdStr.contains("--display")) {
                if (currentDisp <= 0) {
                    val created = command(base, JSONObject().put("op", "create"))
                    currentDisp = created.optInt("displayId", -1)
                }
                if (currentDisp > 0) {
                    cmdStr = cmdStr.replaceFirst(Regex("""\bam\s+start\b"""), "am start --display $currentDisp")
                }
            }
            val safeCwd = "'" + shellCwd.replace("'", "'\\''") + "'"
            val wrappedCmd = "cd $safeCwd 2>/dev/null || cd /sdcard; $cmdStr; echo ''; echo '__PULSE_CWD__:'\$(pwd)"
            val hasRoot = privilege!!.isRootGrantedFast() || runCatching { privilege!!.probe().optBoolean("root") }.getOrDefault(false)
            val rawRes = if (hasRoot) {
                privilege!!.execute(JSONObject().put("op", "shell").put("command", wrappedCmd))
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
            rawRes.put("output", rawOut).put("cwd", shellCwd).put("ok", true)
            return rawRes
        }
        if (operation == "delay") {
            val ms = input.optLong("delay_ms", input.optLong("ms", 600L)).coerceIn(50L, 5000L)
            try { Thread.sleep(ms) } catch (ignored: InterruptedException) {}
            return JSONObject().put("uid", 0).put("ok", true).put("op", "delay").put("delay_ms", ms)
        }
        if (operation == "batch") {
            val steps = input.optJSONArray("steps") ?: JSONArray()
            var lastRes = JSONObject().put("uid", 0).put("ok", true)
            for (i in 0 until minOf(steps.length(), 10)) {
                val st = steps.optJSONObject(i) ?: continue
                if (st.optString("op") == "batch") continue
                lastRes = command(base, st)
                if (i + 1 < steps.length()) {
                    try { Thread.sleep(220L) } catch (ignored: InterruptedException) {}
                }
            }
            return lastRes
        }
        if (operation == "auto_release") {
            val curDisp = runCatching { JSONObject(state.value.display).optInt("displayId", -1) }.getOrDefault(-1)
            if (!state.value.displayCreated && curDisp <= 0) {
                return JSONObject().put("uid", 0).put("ok", true).put("noop", true).put("created", false)
            }
            val toFg = input.optBoolean("to_foreground", false)
            scheduleAutoRelease(base, input.optLong("delay_ms", 2500L), toFg)
            return JSONObject().put("scheduled_release", true).put("to_foreground", toFg)
        }
        if (operation == "release") {
            val curDisp = runCatching { JSONObject(state.value.display).optInt("displayId", -1) }.getOrDefault(-1)
            if (!state.value.displayCreated && curDisp <= 0) {
                autoReleaseJob?.cancel()
                idleWatchJob?.cancel()
                stopLivePreview()
                releaseKeepAwake()
                return JSONObject().put("uid", 0).put("ok", true).put("noop", true).put("created", false)
            }
        }
        if (operation == "notifications") {
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
        if (operation != "preview" && operation != "status" && operation != "release") {
            touchActivity()
        }
        if (operation == "connect") {
            val status = privilege!!.probe()
            state.update { it.copy(root = status.optBoolean("root"), error = "") }
            if (!status.optBoolean("root")) throw Exception("尚未取得 Root 授权，请在手机的 Root 管理器中允许此应用")
            return status(base)
        }
        if (operation == "status") return status(base)
        if (operation == "apps") {
            val force = input.optBoolean("refresh", false)
            val apps = queryLauncherApps(base, force)
            state.update { it.copy(apps = apps) }
            return JSONObject().put("apps", JSONArray(apps.map { JSONObject().put("name", it.name).put("component", it.component) }))
        }
        if (state.value.phase in listOf("running", "waiting_user") && input.optBoolean("manual") && operation !in listOf("capture", "resize", "create", "apps")) {
            job?.cancel(); agent?.cancel()
            state.update { it.copy(phase = "idle") }
        }
        if (!privilege!!.isRootGrantedFast()) {
            val probed = privilege!!.probe()
            state.update { it.copy(root = probed.optBoolean("root")) }
            if (!probed.optBoolean("root")) throw Exception("尚未取得 Root 授权，请在手机的 Root 管理器中允许此应用")
        }

        var currentDisplay = JSONObject(state.value.display).let {
            if (it.optBoolean("created", false)) it.optInt("displayId", -1) else -1
        }
        if (currentDisplay <= 0 && operation in listOf("ui_tree", "tap", "tap_node", "tap_and_type", "swipe", "text", "type_node", "key", "long_press", "capture")) {
            val dispObj = JSONObject(state.value.display)
            if (dispObj.optBoolean("allocated", false) && dispObj.optInt("displayId", -1) > 0) {
                runCatching {
                    val woke = command(base, JSONObject().put("op", "create"))
                    currentDisplay = woke.optInt("displayId", -1)
                }
            }
        }
        var dispInfo = JSONObject(state.value.imageInfo.ifBlank { state.value.display })
        var dispW = dispInfo.optInt("width", 720).coerceAtLeast(1)
        var dispH = dispInfo.optInt("height", 1600).coerceAtLeast(1)

        fun resolveGridOrNorm(cmdObj: JSONObject) {
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
        resolveGridOrNorm(input)

        if (operation == "tap_and_type") {
            if (currentDisplay <= 0) throw Exception("先创建独立工作区")
            val ref = input.optString("ref", input.optString("node", "")).trim()
            val targetLabel = input.optString("target", input.optString("label", input.optString("hint", ""))).trim()
            val resId = input.optString("id", input.optString("view_id", input.optString("resource_id", ""))).trim()
            val txt = input.optString("text", input.optString("value", ""))
            var resolvedNode: PulseAccessibilityService.CachedNode? = null
            if (!input.has("x") || !input.has("y")) {
                if (ref.isNotEmpty() || targetLabel.isNotEmpty() || resId.isNotEmpty()) {
                    resolvedNode = PulseAccessibilityService.resolveTargetNode(currentDisplay, dispW, dispH, ref, targetLabel, resId)
                    if (resolvedNode == null) {
                        readUiTreeForDisplay(currentDisplay, dispW, dispH, forceRefresh = true)
                        resolvedNode = PulseAccessibilityService.resolveTargetNode(currentDisplay, dispW, dispH, ref, targetLabel, resId)
                    }
                }
                if (resolvedNode == null) {
                    var snap = PulseAccessibilityService.getCachedSnapshot(currentDisplay)
                    if (snap == null || snap.nodes.isEmpty()) {
                        readUiTreeForDisplay(currentDisplay, dispW, dispH, forceRefresh = true)
                        snap = PulseAccessibilityService.getCachedSnapshot(currentDisplay)
                    }
                    resolvedNode = snap?.nodes?.firstOrNull { n ->
                        n.editable || n.role.equals("EditText", ignoreCase = true) ||
                            n.text.contains("搜索") || n.text.contains("输入") ||
                            n.hint.contains("搜索") || n.hint.contains("输入")
                    }
                }
            }
            val tx = if (input.has("x")) input.optInt("x") else (resolvedNode?.centerX ?: throw Exception("未识别到输入框节点，请先调用 ui_tree 或指定输入框 ref/坐标"))
            val ty = if (input.has("y")) input.optInt("y") else (resolvedNode?.centerY ?: throw Exception("未识别到输入框节点，请先调用 ui_tree 或指定输入框 ref/坐标"))
            privilege!!.execute(JSONObject().put("op", "tap").put("display_id", currentDisplay).put("x", tx).put("y", ty))
            state.update { it.copy(lastTouchX = tx, lastTouchY = ty, lastTouchTime = System.currentTimeMillis()) }
            FloatingVirtualDisplay.onWorkspaceStateChanged(base.applicationContext, state.value)
            Thread.sleep(320)
            var typeRes = JSONObject()
            if (txt.isNotEmpty()) {
                var a11yOk = false
                if (resolvedNode != null && resolvedNode.editable) {
                    a11yOk = PulseAccessibilityService.performAccessibilityAction(currentDisplay, resolvedNode, "set_text", txt)
                }
                if (!a11yOk) {
                    readUiTreeForDisplay(currentDisplay, dispW, dispH, forceRefresh = true)
                    val newEdit = PulseAccessibilityService.getCachedSnapshot(currentDisplay)?.nodes?.firstOrNull { it.editable || it.role.equals("EditText", ignoreCase = true) }
                    if (newEdit != null) {
                        a11yOk = PulseAccessibilityService.performAccessibilityAction(currentDisplay, newEdit, "set_text", txt)
                    }
                }
                typeRes = if (a11yOk) {
                    JSONObject().put("ok", true).put("typed", true).put("method", "accessibility_set_text")
                } else {
                    privilege!!.execute(JSONObject().put("op", "text").put("display_id", currentDisplay).put("text", txt))
                }
            }
            if (input.optBoolean("submit", false) || input.optBoolean("press_enter", false) || input.optBoolean("send", false)) {
                Thread.sleep(200)
                privilege!!.execute(JSONObject().put("op", "key").put("display_id", currentDisplay).put("key", "enter"))
            }
            return JSONObject()
                .put("uid", 0)
                .put("ok", true)
                .put("op", "tap_and_type")
                .put("ref", resolvedNode?.ref ?: ref)
                .put("x", tx)
                .put("y", ty)
                .put("tapped", JSONObject().put("x", tx).put("y", ty))
                .put("typed", typeRes)
        }

        // 1. Primary operation: Read Accessibility / UI Node Tree (`ui_tree`)
        if (operation == "ui_tree") {
            if (currentDisplay <= 0) throw Exception("虚拟屏尚未启动任何应用，请先调用 launch 打开目标应用")
            lastCaptureRotation = null
            val treeJson = readUiTreeForDisplay(currentDisplay, dispW, dispH, forceRefresh = input.optBoolean("refresh", true))
            state.update { it.copy(lastTreeSummary = treeJson.optString("summary", "")) }
            return treeJson
        }

        // 2. Primary node-based click (`tap_node`) or `tap` with `ref` / `text` / `id` instead of raw (x,y)
        if (operation == "tap_node" || (operation == "tap" && !input.has("x") && (input.has("ref") || input.has("text") || input.has("id") || input.has("target")))) {
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
                throw Exception("在当前无障碍节点树中未找到目标节点 (ref=$ref, text=$text, id=$resId)；请先调用 ui_tree 核对最新节点，或改用 swipe 滚动页面 / 最后用 tap(x,y) 硬点")
            }
            val beforeHash = PulseAccessibilityService.getCachedSnapshot(currentDisplay)?.treeHash.orEmpty()
            val a11yClicked = PulseAccessibilityService.performAccessibilityAction(currentDisplay, node, "click")
            var needCenterTap = !a11yClicked
            if (a11yClicked) {
                // Verify whether ACTION_CLICK actually changed the UI tree (ListView / custom OnTouchListener rows often ignore ACTION_CLICK)
                try { Thread.sleep(130L) } catch (ignored: InterruptedException) {}
                val checkTree = PulseAccessibilityService.dumpDisplayTree(currentDisplay, dispW, dispH, forceRefresh = true, waitMs = 160L)
                if (checkTree == null || checkTree.optString("treeHash") == beforeHash) {
                    needCenterTap = true
                }
            }
            val tapRes = if (needCenterTap) {
                privilege!!.execute(JSONObject().put("op", "tap").put("display_id", currentDisplay).put("x", node.centerX).put("y", node.centerY))
            } else {
                JSONObject().put("uid", 0).put("displayId", currentDisplay).put("clicked", true)
            }
            val nowTs = System.currentTimeMillis()
            state.update { it.copy(lastTouchX = node.centerX, lastTouchY = node.centerY, lastTouchTime = nowTs) }
            FloatingVirtualDisplay.onWorkspaceStateChanged(base.applicationContext, state.value)
            return tapRes
                .put("clicked", true)
                .put("method", if (!needCenterTap) "accessibility_node" else "node_center_tap")
                .put("ref", node.ref)
                .put("role", node.role)
                .put("label", node.text.ifBlank { node.desc })
                .put("x", node.centerX)
                .put("y", node.centerY)
        }

        // 3. Node-based text input (`type_node`) or `text` with automatic Accessibility `set_text` priority
        if (operation == "type_node" || operation == "text") {
            if (currentDisplay <= 0) throw Exception("先创建独立工作区")
            val ref = input.optString("ref", input.optString("node", "")).trim()
            val targetLabel = input.optString("target", input.optString("hint", "")).trim()
            val resId = input.optString("id", input.optString("resource_id", "")).trim()
            val contentText = input.optString("text", input.optString("value", ""))
            if (contentText.isEmpty()) throw Exception("text/type_node 需要提供要输入的 text")
            var node = if (ref.isNotEmpty() || targetLabel.isNotEmpty() || resId.isNotEmpty()) {
                PulseAccessibilityService.resolveTargetNode(currentDisplay, dispW, dispH, ref, targetLabel, resId)
            } else {
                PulseAccessibilityService.getCachedSnapshot(currentDisplay)?.nodes?.firstOrNull { it.editable }
            }
            if (node == null) {
                readUiTreeForDisplay(currentDisplay, dispW, dispH, forceRefresh = true)
                node = if (ref.isNotEmpty() || targetLabel.isNotEmpty() || resId.isNotEmpty()) {
                    PulseAccessibilityService.resolveTargetNode(currentDisplay, dispW, dispH, ref, targetLabel, resId)
                } else {
                    PulseAccessibilityService.getCachedSnapshot(currentDisplay)?.nodes?.firstOrNull { it.editable }
                }
            }
            if (node != null) {
                if (ref.isNotEmpty() || targetLabel.isNotEmpty() || resId.isNotEmpty()) {
                    runCatching {
                        privilege!!.execute(JSONObject().put("op", "tap").put("display_id", currentDisplay).put("x", node.centerX).put("y", node.centerY))
                        Thread.sleep(150L)
                    }
                }
                val a11yTyped = PulseAccessibilityService.performAccessibilityAction(currentDisplay, node, "set_text", contentText)
                if (a11yTyped) {
                    if (input.optBoolean("submit", false) || input.optBoolean("press_enter", false) || input.optBoolean("send", false)) {
                        Thread.sleep(200)
                        privilege!!.execute(JSONObject().put("op", "key").put("display_id", currentDisplay).put("key", "enter"))
                    }
                    return JSONObject()
                        .put("uid", 0)
                        .put("typed", true)
                        .put("method", "accessibility_set_text")
                        .put("ref", node.ref)
                        .put("characters", contentText.length)
                }
            }
            // Fallback to clipboard paste on display
            val pasteRes = privilege!!.execute(JSONObject().put("op", "text").put("display_id", currentDisplay).put("text", contentText))
            if (input.optBoolean("submit", false) || input.optBoolean("press_enter", false) || input.optBoolean("send", false)) {
                Thread.sleep(200)
                privilege!!.execute(JSONObject().put("op", "key").put("display_id", currentDisplay).put("key", "enter"))
            }
            return pasteRes.put("method", "clipboard_paste").put("ref", node?.ref.orEmpty())
        }

        if (operation == "long_press" && !input.has("x") && (input.has("ref") || input.has("text") || input.has("id") || input.has("target"))) {
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

        val command = JSONObject(input.toString())
        command.put("op", operation)
        command.remove("manual")
        if (operation == "launch") {
            val rawComp = command.optString("component").ifBlank { command.optString("package").ifBlank { command.optString("app").ifBlank { command.optString("text") } } }.trim()
            val resolved = resolveLaunchComponent(base, rawComp)
            command.put("component", resolved)
            if (currentDisplay <= 0) {
                val created = command(base, JSONObject().put("op", "create"))
                currentDisplay = created.optInt("displayId", -1)
                dispInfo = JSONObject(state.value.imageInfo.ifBlank { state.value.display })
                dispW = dispInfo.optInt("width", 720).coerceAtLeast(1)
                dispH = dispInfo.optInt("height", 1600).coerceAtLeast(1)
            }
            lastCaptureRotation = null
        } else if (operation == "open_uri") {
            val uri = command.optString("uri", command.optString("url", command.optString("text", ""))).trim()
            if (uri.isEmpty()) throw Exception("请提供要打开的 URI/URL")
            if (currentDisplay <= 0) {
                val created = command(base, JSONObject().put("op", "create"))
                currentDisplay = created.optInt("displayId", -1)
            }
            lastCaptureRotation = null
        } else if (operation in listOf("create", "release")) {
            lastCaptureRotation = null
        }
        if (operation in listOf("capture", "tap", "long_press", "swipe", "text", "key", "launch", "open_uri", "resize")) {
            if (currentDisplay <= 0) throw Exception("虚拟屏尚未启动任何应用，请先调用 launch 打开目标应用")
            if (command.has("display_id") && command.getInt("display_id") != currentDisplay) throw Exception("displayId 不属于当前工作区")
            command.put("display_id", currentDisplay)
            if (operation in listOf("tap", "long_press", "swipe") && !input.optBoolean("manual")) lastCaptureRotation?.let { command.put("expected_rotation", it) }
        }
        try {
            val result = privilege!!.execute(command)
            if (result.optInt("uid", -1) != 0) throw Exception("拒绝非 Root 的手机执行结果")
            val image = result.optString("image")
            val clean = JSONObject(result.toString()); clean.remove("image")
            if (operation == "capture" && !input.optBoolean("manual")) lastCaptureRotation = result.optInt("rotation")
            val touchX = when (operation) {
                "tap" -> command.optInt("x", -1)
                "swipe" -> command.optInt("x2", -1)
                else -> state.value.lastTouchX
            }
            val touchY = when (operation) {
                "tap" -> command.optInt("y", -1)
                "swipe" -> command.optInt("y2", -1)
                else -> state.value.lastTouchY
            }
            val touchTs = if (operation in listOf("tap", "swipe")) System.currentTimeMillis() else state.value.lastTouchTime
            state.update {
                it.copy(
                    root = true,
                    display = clean.toString(),
                    image = image.ifBlank { it.image },
                    imageInfo = if (image.isNotBlank() || operation == "resize") clean.toString() else it.imageInfo,
                    error = "",
                    lastTouchX = touchX,
                    lastTouchY = touchY,
                    lastTouchTime = touchTs
                )
            }
            if ((operation == "create" || operation == "launch" || operation == "open_uri") && result.optBoolean("created")) {
                acquireKeepAwake(base)
                touchActivity()
                if (state.value.apps.isEmpty()) state.update { it.copy(apps = queryLauncherApps(base, false)) }
                startLivePreview(base.applicationContext)
                startIdleWatchdog(base.applicationContext)
                FloatingVirtualDisplay.show(base.applicationContext)
            } else if (operation == "release") {
                autoReleaseJob?.cancel()
                idleWatchJob?.cancel()
                stopLivePreview()
                releaseKeepAwake()
                FloatingVirtualDisplay.onWorkspaceStateChanged(base.applicationContext, state.value)
            } else {
                if (result.optBoolean("created")) {
                    startLivePreview(base.applicationContext)
                    startIdleWatchdog(base.applicationContext)
                }
                FloatingVirtualDisplay.onWorkspaceStateChanged(base.applicationContext, state.value)
            }
            return result
        } catch (error: Exception) {
            state.update { it.copy(error = MainActivity.safeError(error)) }; throw error
        }
    }

    private fun readUiTreeForDisplay(displayId: Int, dispW: Int, dispH: Int, forceRefresh: Boolean): JSONObject {
        if (!PulseAccessibilityService.isConnected()) {
            runCatching { privilege?.probe() }
            val waitDeadline = SystemClock.uptimeMillis() + 300L
            while (!PulseAccessibilityService.isConnected() && SystemClock.uptimeMillis() < waitDeadline) {
                try { Thread.sleep(60L) } catch (ignored: InterruptedException) { break }
            }
        }
        // Strictly read Accessibility windows on the virtual display (`displayId > 0`) with a 420ms anti-redraw bound
        // (NEVER fall back to `dumpsys activity top`, which freezes Android's system_server and foreground UI thread!)
        PulseAccessibilityService.dumpDisplayTree(displayId, dispW, dispH, forceRefresh, waitMs = 420L)?.let {
            return it.put("uid", 0).put("source", "accessibility_windows")
        }
        val emptySnap = PulseAccessibilityService.updateSnapshotFromExternal(displayId, emptyList(), "", dispW, dispH)
        return PulseAccessibilityService.snapshotToJson(emptySnap, fromCache = false)
            .put("uid", 0)
            .put("source", "empty")
    }

    private fun queryLauncherApps(base: Context, forceRefresh: Boolean = false): List<PhoneApp> {
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

    private fun resolveLaunchComponent(base: Context, query: String): String {
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

        // 1. Exact package match or exact name match (raw or brand-stripped)
        apps.firstOrNull {
            val pkg = it.component.substringBefore("/").lowercase()
            val nm = it.name.lowercase()
            pkg == qLower || nm == qLower || nm == stripped
        }?.let {
            resolvedComponentCache[qTrim] = it.component
            return it.component
        }
        // 2. Bidirectional substring match on app name or package name
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
        // 3. Fallback via PackageManager getLaunchIntentForPackage
        base.packageManager.getLaunchIntentForPackage(qTrim)?.component?.let {
            val comp = it.packageName + "/" + it.className
            resolvedComponentCache[qTrim] = comp
            return comp
        }
        val sampleApps = apps.take(25).joinToString("、") { it.name }
        throw Exception("未在手机上找到应用「$qTrim」；已安装的部分应用有：$sampleApps（也可调用 op=\"apps\" 搜索）")
    }

    private fun startLivePreview(appContext: Context) {
        if (previewJob?.isActive == true) return
        lastPreviewSequence = -1L
        previewJob = scope.launch {
            while (isActive) {
                val sinceTouch = SystemClock.elapsedRealtime() - lastLiveTouchElapsed
                val sinceAct = SystemClock.elapsedRealtime() - lastActivityElapsed
                val pipVisible = FloatingVirtualDisplay.isPreviewVisible()
                val pollInterval = when {
                    !pipVisible && !state.value.active -> 1500L
                    sinceTouch < 1200L -> 48L  // ~20fps responsive mode while user finger is dragging inside PiP
                    sinceAct < 3500L -> 140L   // ~7fps during agent operations (prevents IPC & main-thread contention!)
                    else -> 380L               // ~2.6fps low-overhead idle preview
                }
                delay(pollInterval)
                val disp = runCatching { JSONObject(state.value.display) }.getOrNull()
                if (disp == null || !disp.optBoolean("created")) break
                if (!FloatingVirtualDisplay.isPreviewVisible() && !state.value.active) continue
                runCatching {
                    val res = privilege?.execute(JSONObject().put("op", "preview").put("since", lastPreviewSequence)) ?: return@runCatching
                    if (!res.optBoolean("created")) return@runCatching
                    val seq = res.optLong("frameSequence", -1L)
                    if (seq >= 0) lastPreviewSequence = seq
                    val img = res.optString("image")
                    if (img.isNotBlank() && img != state.value.image) {
                        val clean = JSONObject(res.toString()).apply { remove("image") }
                        state.update { it.copy(image = img, imageInfo = clean.toString()) }
                    }
                }
            }
        }
    }

    private fun stopLivePreview() {
        previewJob?.cancel()
        previewJob = null
        lastPreviewSequence = -1L
    }

    internal suspend fun submit(base: Context, bot: String, goal: String, history: List<PulseMessage>, recordUserMessage: Boolean = true): String = withContext(Dispatchers.IO) {
        initialize(base); loaded.await(); submitLock.withLock {
            require(goal.isNotBlank() && goal.length <= 6000) { "请交代一件 6000 字以内的手机任务" }
            if (state.value.active || job?.isActive == true) {
                job?.cancel(); agent?.cancel()
                withTimeoutOrNull(1500L) { job?.join() }
                state.update { it.copy(phase = "idle") }
            }
            val config = modelConfig(SecurePrefs.read(base)); require(ready(config)) { "先在设置中保存 API Key 并选择模型" }
            if (!privilege!!.status().optBoolean("root")) privilege!!.probe()
            require(privilege!!.status().optBoolean("root")) { "尚未取得 Root 授权，请在 Root 管理器中允许此应用" }
            val workspace = command(base, JSONObject().put("op", "create")); require(workspace.optInt("displayId", -1) > 0 && workspace.getInt("uid") == 0)
            val id = "phone-" + UUID.randomUUID()
            state.update { it.copy(phase = "running", botId = bot, goal = goal, result = "", error = "", trace = "[]", model = config.optString("model"), modelReady = true) }
            if (recordUserMessage) {
                append(PulseMessage(id, bot, "user", goal, "phone_task", "root-phone", System.currentTimeMillis() / 1000.0, "", "[]"))
            }
            persist()
            try { withContext(Dispatchers.Main) { base.startForegroundService(Intent(base, RootPhoneService::class.java)) } }
            catch (error: Exception) { state.update { it.copy(phase = "failed", error = MainActivity.safeError(error)) }; persist(); throw error }
            if (state.value.phase != "running") throw CancellationException("手机任务已取消")
            startLoop(base.applicationContext, bot, goal, config, workspace, history)
            id
        }
    }

    @JvmStatic fun submitFromBridge(base: Context, bot: String, goal: String): JSONObject = runBlocking {
        JSONObject().put("accepted", true).put("id", submit(base, bot, goal, state.value.messages.filter { it.botId == bot }, recordUserMessage = false))
    }

    private fun startLoop(base: Context, bot: String, goal: String, config: JSONObject, workspace: JSONObject, history: List<PulseMessage>) {
        agent = RootPhoneAgent()
        job = scope.launch {
            var waiting = false
            try {
                val outcome = agent!!.run(goal, config, workspace, history,
                    execute = { name, arguments ->
                        if (state.value.phase != "running") throw CancellationException("手机任务已停止")
                        val op = when (name) {
                            "phone_apps" -> "apps"
                            "phone_status" -> "status"
                            "phone_launch" -> "launch"
                            "phone_ui_tree" -> "ui_tree"
                            "phone_tap_node" -> "tap_node"
                            "phone_type_node" -> "type_node"
                            "phone_capture" -> "capture"
                            "phone_tap" -> "tap"
                            "phone_swipe" -> "swipe"
                            "phone_text" -> "text"
                            "phone_key" -> "key"
                            else -> throw Exception("未知手机工具：$name")
                        }
                        command(base, JSONObject(arguments.toString()).put("op", op))
                    },
                    onStep = { trace ->
                        if (trace.optString("tool") in listOf("phone_text", "phone_type_node")) {
                            val args = trace.getJSONObject("arguments")
                            args.put("characters", args.optString("text").length)
                            args.remove("text")
                        }
                        val all = JSONArray(state.value.trace).put(trace)
                        state.update { it.copy(trace = all.toString()) }
                        persist(); broadcast()
                    })
                waiting = outcome.needsUser
                state.update { it.copy(phase = if (waiting) "waiting_user" else "done", result = outcome.text) }
                append(message(bot, "assistant", outcome.text, if (waiting) "phone_question" else "phone_result", state.value.trace))
            } catch (cancelled: CancellationException) {
                withContext(NonCancellable) {
                    runCatching { command(base, JSONObject().put("op", "release")) }
                    state.update { it.copy(phase = "stopped", result = "手机执行已停止。") }
                    append(message(bot, "assistant", "手机执行已停止。", "phone_stopped", state.value.trace))
                }
            } catch (error: Exception) {
                state.update { it.copy(phase = "failed", error = MainActivity.safeError(error), result = "手机执行中断：${MainActivity.safeError(error)}") }
                append(message(bot, "assistant", state.value.result, "phone_stopped", state.value.trace))
            } finally {
                withContext(NonCancellable) {
                    persist(); broadcast()
                    if (!waiting) {
                        // Automatically close the virtual display 2.5s after task completion (like Mika!)
                        scheduleAutoRelease(base, 2500L)
                        withContext(Dispatchers.Main) { base.stopService(Intent(base, RootPhoneService::class.java)) }
                    }
                }
            }
        }
    }

    @JvmStatic fun stop(base: Context) {
        initialize(base)
        FloatingVirtualDisplay.hide()
        val active = job
        val curDisp = runCatching { JSONObject(state.value.display).optInt("displayId", -1) }.getOrDefault(-1)
        if (active?.isActive != true && !state.value.active && !state.value.displayCreated && curDisp <= 0) {
            return
        }
        state.update { it.copy(phase = "stopping") }
        if (active?.isActive == true) { active.cancel(); agent?.cancel() }
        else scope.launch {
            if (state.value.displayCreated || curDisp > 0) {
                runCatching { command(base, JSONObject().put("op", "release")) }
            }
            state.update { it.copy(phase = "stopped") }
            persist(); broadcast()
            base.stopService(Intent(base, RootPhoneService::class.java))
        }
    }

    @JvmStatic fun resume(base: Context) {
        initialize(base); require(state.value.phase == "waiting_user") { "当前没有等待继续的手机任务" }
        val old = state.value
        scope.launch {
            job?.join()
            state.update { it.copy(phase = "idle") }
            runCatching {
                submit(base, old.botId ?: "companion", "继续完成上次的任务：${old.goal}\n用户已处理好需要接管的事项，请先调用 phone_ui_tree 核对当前界面节点。", old.messages.filter { it.botId == old.botId })
            }.onFailure { error ->
                state.update { it.copy(phase = "waiting_user", error = MainActivity.safeError(error as? Exception ?: Exception(error))) }
            }
        }
    }

    private fun message(bot: String, role: String, text: String, kind: String, trace: String = "[]") =
        PulseMessage("phone-" + UUID.randomUUID(), bot, role, text, kind, "root-phone", System.currentTimeMillis() / 1000.0, "", trace)
    private fun append(message: PulseMessage) {
        state.update { cur ->
            val cleanTxt = message.text.trim()
            val lastSameBot = cur.messages.lastOrNull { it.botId == message.botId && it.role == message.role }
            if (lastSameBot != null && lastSameBot.text.trim() == cleanTxt && kotlin.math.abs(message.created - lastSameBot.created) < 30.0) {
                cur
            } else {
                cur.copy(messages = (cur.messages + message).takeLast(600))
            }
        }
        broadcast()
    }
    private fun json(message: PulseMessage) = JSONObject().put("id", message.id).put("bot_id", message.botId).put("role", message.role).put("text", message.text).put("kind", message.kind).put("engine", message.engine).put("created_at", message.created).put("decision", message.decision).put("trace", JSONArray(message.trace))
    private fun persist() {
        val base = context ?: return; val snapshot = state.value
        synchronized(SecurePrefs::class.java) {
            val current = SecurePrefs.read(base)
            current.put("phoneHistory", JSONArray(snapshot.messages.map(::json))).put("phoneTask", JSONObject().put("phase", snapshot.phase).put("botId", snapshot.botId).put("goal", snapshot.goal))
            SecurePrefs.save(base, current)
        }
    }
    private fun broadcast() { context?.let { it.sendBroadcast(Intent(it.packageName + ".UPDATE").setPackage(it.packageName)) } }
}
