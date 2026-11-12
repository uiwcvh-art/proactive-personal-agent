package dev.bubuki.proactive

import android.content.Intent
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.util.Locale

/**
 * [Module] PulseActivityBotOps
 * [Responsibility]
 * Bot與記憶庫管理領域服務：封裝 Bot CRUD（創建、編輯、刪除、置頂/隱藏、主助理切換）、
 * 全局設置與模型拉取、客户端 OTA 檢查與 Root/標準 APK 安裝、透明記憶庫 (Obsidian Vault) 讀寫、
 * 釘住記憶 (Memory Pins)、定時任務 (Routines/Schedules) 與哨兵 (Watchdogs) 管控，
 * 以及上下文 Token 統計與流式濃縮歸檔 (`compactBotContext`)。
 *
 * [Public API]
 * - `NativePulseModel.openSettings()`, `saveSettings()`, `updateSettingsField(...)`, `fetchModels(...)`
 * - `NativePulseModel.checkForUpdates(...)`, `downloadLatestApk()`
 * - `NativePulseModel.openBotEditor(...)`, `updateBotEditField(...)`, `saveBotEditor()`, `setPrimaryBot(...)`, `deleteBot(...)`, `placement(...)`
 * - `NativePulseModel.openVault(...)`, `selectVaultFile(...)`, `updateVaultContent(...)`, `saveVaultFile()`
 * - `NativePulseModel.openSchedules()`, `toggleRoutineItem(...)`, `runRoutineItem(...)`
 * - `NativePulseModel.loadMemoryPins()`, `addMemoryPin(...)`, `removeMemoryPin(...)`, `loadWatchdogs()`, `stopWatchdog(...)`
 * - `NativePulseModel.parseContextByBot(...)`, `contextPerfFor(...)`, `contextStatsFor(...)`, `contextCacheFor(...)`, `formatTokenCount(...)`, `compactBotContext(...)`
 */

internal fun NativePulseModel.closePanel() {
    cloudStreamJob?.cancel()
    ui = ui.copy(activePanel = null)
}

internal fun NativePulseModel.openSettings() {
    dismissMenu()
    val saved = runCatching { SecurePrefs.read(activity) }.getOrElse { JSONObject() }
    val localCfg = saved.optJSONObject("config") ?: JSONObject()
    val storedKey = localCfg.optString("api_key").ifBlank {
        localCfg.optJSONObject("big_brain")?.optString("api_key").orEmpty().ifBlank {
            localCfg.optJSONObject("small_brain")?.optString("api_key").orEmpty()
        }
    }
    val storedUrl = localCfg.optString("api_base_url").ifBlank {
        localCfg.optJSONObject("big_brain")?.optString("base_url").orEmpty().ifBlank { "https://api.openai.com/v1" }
    }
    val storedModel = localCfg.optString("default_model").ifBlank {
        localCfg.optJSONObject("big_brain")?.optString("model").orEmpty().ifBlank {
            localCfg.optJSONObject("small_brain")?.optString("model").orEmpty()
        }
    }
    val storedFbEnabled = localCfg.optBoolean("fallback_enabled", false)
    val storedFbUrl = localCfg.optString("fallback_base_url", "")
    val storedFbKey = localCfg.optString("fallback_api_key", "")
    val storedFbModel = localCfg.optString("fallback_model", "")
    val savedApp = saved.optString("appearance", "dark").let { if (it == "light") "light" else "dark" }
    val savedCompact = localCfg.optInt("auto_compact_tokens", 0).takeIf { it >= 8000 }?.toString() ?: ui.autoCompactTokens
    ui = ui.copy(
        activePanel = "settings",
        serverUrl = saved.optString("serverUrl", SecureTransport.DEFAULT_SERVER),
        apiBaseUrl = storedUrl,
        apiKeyInput = storedKey,
        defaultModel = storedModel.ifBlank { ui.defaultModel },
        fallbackEnabled = storedFbEnabled,
        fallbackBaseUrl = storedFbUrl.ifBlank { ui.fallbackBaseUrl },
        fallbackApiKeyInput = storedFbKey,
        fallbackModel = storedFbModel.ifBlank { ui.fallbackModel },
        autoCompactTokens = savedCompact,
        backgroundService = saved.optBoolean("background", true),
        appearance = savedApp,
        dark = savedApp != "light"
    )
    checkForUpdates(silent = true)
    perform {
        val remote = request("GET", "/api/config")
        val rUrl = remote.optString("api_base_url").ifBlank { remote.optJSONObject("big_brain")?.optString("base_url").orEmpty() }
        val rModel = remote.optString("default_model").ifBlank { remote.optJSONObject("big_brain")?.optString("model").orEmpty() }
        val rFbEnabled = remote.optBoolean("fallback_enabled", false)
        val rFbUrl = remote.optString("fallback_base_url", "")
        val rFbModel = remote.optString("fallback_model", "")
        val rCompact = remote.optInt("auto_compact_tokens", 120000).coerceIn(8000, 1000000)
        ui = ui.copy(
            apiBaseUrl = if (ui.apiBaseUrl == "https://api.openai.com/v1" && rUrl.isNotBlank()) rUrl else ui.apiBaseUrl,
            defaultModel = ui.defaultModel.ifBlank { rModel },
            hasApiKey = remote.optBoolean("has_api_key", storedKey.isNotBlank()),
            fallbackEnabled = rFbEnabled,
            fallbackBaseUrl = rFbUrl.ifBlank { ui.fallbackBaseUrl },
            fallbackModel = rFbModel.ifBlank { ui.fallbackModel },
            hasFallbackApiKey = remote.optBoolean("has_fallback_api_key", storedFbKey.isNotBlank()),
            curiosityEnabledGlobal = remote.optBoolean("enabled", true),
            curiosityMinutes = remote.optInt("curiosity_interval_minutes", 90).toString(),
            autoCompactTokens = rCompact.toString()
        )
    }
}

internal fun NativePulseModel.checkForUpdates(silent: Boolean = false) {
    if (ui.checkingUpdate) return
    ui = ui.copy(checkingUpdate = true)
    scope.launch {
        try {
            val base = ui.serverUrl.trim().ifBlank { SecureTransport.DEFAULT_SERVER }.trimEnd('/')
            val releaseObj = withContext(Dispatchers.IO) {
                val conn = (java.net.URL("$base/download/release.json?t=${System.currentTimeMillis()}").openConnection() as java.net.HttpURLConnection).apply {
                    connectTimeout = 8000
                    readTimeout = 8000
                    useCaches = false
                }
                try {
                    conn.inputStream.bufferedReader().use { JSONObject(it.readText()) }
                } finally {
                    conn.disconnect()
                }
            }
            val ver = releaseObj.optString("versionName")
                .ifBlank { releaseObj.optString("version_name") }
                .ifBlank { releaseObj.optString("version") }
                .ifBlank { releaseObj.optString("tag_name") }
                .removePrefix("v")
                .trim()
            val remoteCode = releaseObj.optInt(
                "versionCode",
                releaseObj.optInt("version_code", releaseObj.optInt("build", 0))
            )
            val notes = releaseObj.optString("notes").ifBlank { releaseObj.optString("body") }
            val curVer = BuildConfig.VERSION_NAME
            val curCode = BuildConfig.VERSION_CODE
            val hasNew = (remoteCode > curCode) || (ver.isNotBlank() && ver != curVer && (remoteCode <= 0 || remoteCode >= curCode))
            ui = ui.copy(latestReleaseVersion = ver.ifBlank { curVer }, latestReleaseNotes = notes, updateAvailable = hasNew)
            if (!silent) {
                toast(
                    if (hasNew) "发现新版本 v$ver (Build $remoteCode)，点击下方按钮即可一键下载安装"
                    else "当前已是最新版本 v$curVer (Build $curCode)"
                )
            }
        } catch (e: Exception) {
            if (!silent) toast("检查更新失败 (${e.message ?: "网络异常"})，可直接点击下方按钮下载最新安装包")
        } finally {
            ui = ui.copy(checkingUpdate = false)
        }
    }
}

internal fun NativePulseModel.downloadLatestApk() {
    if (ui.downloadingApk) {
        toast("正在下载安装包 (${ui.downloadProgress}%)，请稍候…")
        return
    }
    val base = ui.serverUrl.trim().ifBlank { SecureTransport.DEFAULT_SERVER }.trimEnd('/')
    val apkUrl = "$base/download/proactive-agent.apk?t=${System.currentTimeMillis()}"
    ui = ui.copy(downloadingApk = true, downloadProgress = 0)
    toast("正在应用内高速下载最新 APK…")
    scope.launch {
        try {
            val apkFile = withContext(Dispatchers.IO) {
                val dir = java.io.File(activity.cacheDir, "avatar-capture").apply { mkdirs() }
                val target = java.io.File(dir, "proactive-agent-update.apk")
                val part = java.io.File(dir, "proactive-agent-update.apk.part")
                val conn = (java.net.URL(apkUrl).openConnection() as java.net.HttpURLConnection).apply {
                    connectTimeout = 15000
                    readTimeout = 60000
                    useCaches = false
                }
                try {
                    val total = conn.contentLengthLong
                    conn.inputStream.use { input ->
                        part.outputStream().use { out ->
                            val buf = ByteArray(32 * 1024)
                            var downloaded = 0L
                            var lastPct = -1
                            while (true) {
                                val n = input.read(buf)
                                if (n <= 0) break
                                out.write(buf, 0, n)
                                downloaded += n
                                if (total > 0) {
                                    val pct = ((downloaded * 100L) / total).toInt().coerceIn(0, 99)
                                    if (pct != lastPct) {
                                        lastPct = pct
                                        withContext(Dispatchers.Main) {
                                            ui = ui.copy(downloadProgress = pct)
                                        }
                                    }
                                }
                            }
                        }
                    }
                } finally {
                    conn.disconnect()
                }
                if (part.length() < 1_000_000L) {
                    throw IllegalStateException("安装包体积异常 (${part.length()} B)")
                }
                if (target.exists()) target.delete()
                part.renameTo(target)
                target
            }
            ui = ui.copy(downloadProgress = 100)
            val rootInstalled = withContext(Dispatchers.IO) {
                try {
                    val tmpPath = "/data/local/tmp/proactive-agent-update.apk"
                    val p = ProcessBuilder(
                        "su", "-c",
                        "cp '${apkFile.absolutePath}' $tmpPath && chmod 644 $tmpPath && pm install -r -d $tmpPath && rm -f $tmpPath"
                    ).redirectErrorStream(true).start()
                    val finished = p.waitFor(45, java.util.concurrent.TimeUnit.SECONDS)
                    val out = if (finished) p.inputStream.bufferedReader().readText() else ""
                    if (!finished) p.destroyForcibly()
                    finished && p.exitValue() == 0 && out.contains("Success", ignoreCase = true)
                } catch (_: Throwable) {
                    false
                }
            }
            if (rootInstalled) {
                toast("Root 静默覆盖安装成功！")
                return@launch
            }
            val uri = androidx.core.content.FileProvider.getUriForFile(activity, "${activity.packageName}.avatar-files", apkFile)
            val installIntent = Intent(Intent.ACTION_VIEW).apply {
                setDataAndType(uri, "application/vnd.android.package-archive")
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            toast("下载完成，正在拉起系统安装器…")
            activity.startActivity(installIntent)
        } catch (e: Exception) {
            toast("应用内下载受阻，正在用浏览器打开下载链接…")
            openLink(apkUrl)
        } finally {
            ui = ui.copy(downloadingApk = false, downloadProgress = 0)
        }
    }
}

internal fun NativePulseModel.updateSettingsField(
    serverUrl: String = ui.serverUrl,
    apiBaseUrl: String = ui.apiBaseUrl,
    apiKeyInput: String = ui.apiKeyInput,
    defaultModel: String = ui.defaultModel,
    fallbackEnabled: Boolean = ui.fallbackEnabled,
    fallbackBaseUrl: String = ui.fallbackBaseUrl,
    fallbackApiKeyInput: String = ui.fallbackApiKeyInput,
    fallbackModel: String = ui.fallbackModel,
    curiosityEnabled: Boolean = ui.curiosityEnabledGlobal,
    curiosityMinutes: String = ui.curiosityMinutes,
    autoCompactTokens: String = ui.autoCompactTokens,
    backgroundService: Boolean = ui.backgroundService,
    appearance: String = ui.appearance,
    motion: Boolean = ui.motion,
    haptics: Boolean = ui.haptics
) {
    val nextApp = if (appearance == "light") "light" else "dark"
    val nextDark = nextApp != "light"
    if (nextDark != ui.dark) {
        activity.systemBarsFor(nextDark)
    }
    ui = ui.copy(
        serverUrl = serverUrl, apiBaseUrl = apiBaseUrl, apiKeyInput = apiKeyInput,
        defaultModel = defaultModel,
        fallbackEnabled = fallbackEnabled,
        fallbackBaseUrl = fallbackBaseUrl, fallbackApiKeyInput = fallbackApiKeyInput, fallbackModel = fallbackModel,
        curiosityEnabledGlobal = curiosityEnabled,
        curiosityMinutes = curiosityMinutes, autoCompactTokens = autoCompactTokens,
        backgroundService = backgroundService,
        appearance = nextApp, dark = nextDark, motion = motion, haptics = haptics
    )
    draftWriter.submit {
        synchronized(SecurePrefs::class.java) {
            val s = SecurePrefs.read(activity)
            s.put("appearance", nextApp).put("motion", motion).put("haptics", haptics)
            SecurePrefs.save(activity, s)
            UiPreferences.update(activity, JSONObject().put("appearance", nextApp).put("motion", motion).put("haptics", haptics))
        }
    }
}

internal fun NativePulseModel.fetchModels(forBotEditor: Boolean = false) {
    if (ui.fetchingModels) return
    ui = ui.copy(fetchingModels = true)
    perform {
        try {
            withContext(Dispatchers.IO) {
                val s = SecurePrefs.read(activity)
                s.put("serverUrl", ui.serverUrl.trim().ifBlank { SecureTransport.DEFAULT_SERVER })
                SecurePrefs.save(activity, s)
            }
            val payload = JSONObject().put("base_url", ui.apiBaseUrl.trim())
            if (ui.apiKeyInput.trim().isNotBlank()) payload.put("api_key", ui.apiKeyInput.trim())
            val res = request("POST", "/api/models", payload)
            val arr = res.optJSONArray("models") ?: JSONArray()
            val list = (0 until arr.length()).map { arr.getString(it) }.filter { it.isNotBlank() }
            val pickedDefault = if (ui.defaultModel.isBlank() && list.isNotEmpty()) list.first() else ui.defaultModel
            val pickedBotModel = if (forBotEditor && ui.editBotModel.isBlank() && list.isNotEmpty()) list.first() else ui.editBotModel
            ui = ui.copy(availableModels = list, defaultModel = pickedDefault, editBotModel = pickedBotModel)
            toast(if (list.isEmpty()) "接口未返回模型列表，可手动填写模型名" else "已获取 ${list.size} 个可用模型！")
        } finally {
            ui = ui.copy(fetchingModels = false)
        }
    }
}

internal fun NativePulseModel.saveSettings() {
    if (ui.savingSettings) return
    val cleanServer = ui.serverUrl.trim().ifBlank { SecureTransport.DEFAULT_SERVER }.trimEnd('/')
    val cleanBase = ui.apiBaseUrl.trim().ifBlank { "https://api.openai.com/v1" }.trimEnd('/')
    val cleanKey = ui.apiKeyInput.trim()
    val cleanModel = ui.defaultModel.trim()
    val cleanFbBase = ui.fallbackBaseUrl.trim().trimEnd('/')
    val cleanFbKey = ui.fallbackApiKeyInput.trim()
    val cleanFbModel = ui.fallbackModel.trim()
    val minutes = (ui.curiosityMinutes.toIntOrNull() ?: 90).coerceIn(5, 1440)
    val compactTokens = (ui.autoCompactTokens.toIntOrNull() ?: 120000).coerceIn(8000, 1000000)
    ui = ui.copy(savingSettings = true, autoCompactTokens = compactTokens.toString())
    perform {
        try {
            withContext(Dispatchers.IO) {
                val s = SecurePrefs.read(activity)
                s.put("serverUrl", cleanServer)
                s.put("deviceToken", s.optString("deviceToken").ifBlank { SecureTransport.defaultToken() })
                s.put("background", ui.backgroundService)
                val normApp = if (ui.appearance == "light") "light" else "dark"
                s.put("appearance", normApp)
                UiPreferences.update(activity, JSONObject().put("appearance", normApp).put("motion", ui.motion).put("haptics", ui.haptics))
                val localCfg = s.optJSONObject("config") ?: JSONObject()
                localCfg.put("api_base_url", cleanBase)
                localCfg.put("auto_compact_tokens", compactTokens)
                if (cleanKey.isNotBlank()) localCfg.put("api_key", cleanKey)
                if (cleanModel.isNotBlank()) localCfg.put("default_model", cleanModel)
                localCfg.put("fallback_enabled", ui.fallbackEnabled)
                localCfg.put("fallback_base_url", cleanFbBase)
                if (cleanFbKey.isNotBlank()) localCfg.put("fallback_api_key", cleanFbKey)
                localCfg.put("fallback_model", cleanFbModel)
                val brainObj = JSONObject().put("base_url", cleanBase).put("model", cleanModel)
                if (cleanKey.isNotBlank()) brainObj.put("api_key", cleanKey)
                else {
                    val oldKey = localCfg.optJSONObject("big_brain")?.optString("api_key").orEmpty()
                    if (oldKey.isNotBlank()) brainObj.put("api_key", oldKey)
                }
                localCfg.put("small_brain", JSONObject(brainObj.toString()))
                localCfg.put("big_brain", JSONObject(brainObj.toString()))
                s.put("config", localCfg)
                SecurePrefs.save(activity, s)
                prefs = s
            }
            val updatePayload = JSONObject()
                .put("fallback_enabled", ui.fallbackEnabled)
                .put("fallback_base_url", cleanFbBase)
                .put("fallback_model", cleanFbModel)
                .put("enabled", ui.curiosityEnabledGlobal)
                .put("curiosity_interval_minutes", minutes)
                .put("auto_compact_tokens", compactTokens)
            if (cleanBase.isNotBlank() && (cleanBase != "https://api.openai.com/v1" || cleanKey.isNotBlank())) {
                updatePayload.put("api_base_url", cleanBase)
            }
            if (cleanKey.isNotBlank()) updatePayload.put("api_key", cleanKey)
            if (cleanModel.isNotBlank()) updatePayload.put("default_model", cleanModel)
            if (cleanFbKey.isNotBlank()) updatePayload.put("fallback_api_key", cleanFbKey)
            val cfgRes = request("POST", "/api/config", updatePayload)
            val (ctxTokensMap, ctxMsgsMap, ctxCachedMap) = parseContextByBot(cfgRes.optJSONObject("context_by_bot"))
            val dark = ui.appearance != "light"
            activity.systemBarsFor(dark)
            RootPhoneHarness.initialize(activity)
            if (ui.backgroundService && !AgentForegroundService.running) {
                runCatching { activity.startForegroundService(Intent(activity, AgentForegroundService::class.java)) }
            } else if (!ui.backgroundService) {
                activity.stopService(Intent(activity, AgentForegroundService::class.java))
            }
            ui = ui.copy(
                dark = dark,
                smallModel = cleanModel,
                bigModel = cleanModel,
                defaultModel = cleanModel,
                fallbackBaseUrl = cleanFbBase,
                fallbackModel = cleanFbModel,
                hasApiKey = cleanKey.isNotBlank() || ui.hasApiKey,
                hasFallbackApiKey = cleanFbKey.isNotBlank() || ui.hasFallbackApiKey,
                contextTokensByBot = if (ctxTokensMap.isNotEmpty()) ctxTokensMap else ui.contextTokensByBot,
                contextCachedByBot = if (ctxCachedMap.isNotEmpty()) ctxCachedMap else ui.contextCachedByBot,
                contextMsgsByBot = if (ctxMsgsMap.isNotEmpty()) ctxMsgsMap else ui.contextMsgsByBot,
                activePanel = null
            )
            refresh()
            toast("已保存并同步到云端！Harness 已就绪。")
        } finally {
            ui = ui.copy(savingSettings = false)
        }
    }
}

internal fun NativePulseModel.openBotEditor(botId: String? = null) {
    dismissMenu()
    val existing = botId?.let { bot(it) }
    ui = ui.copy(
        activePanel = "bot_edit",
        editingBotId = existing?.id,
        editBotName = existing?.name ?: "",
        editBotDesc = existing?.description ?: "",
        editBotModel = existing?.model ?: "",
        editBotInstructions = existing?.instructions ?: "",
        editBotColor = existing?.color ?: "#B568F7",
        editBotShape = existing?.shape ?: "flower",
        editBotCuriosity = existing?.curiosityEnabled ?: false,
        editBotNotify = existing?.notify ?: true
    )
    loadMemoryPins()
    fetchStickerPacks()
}

internal fun NativePulseModel.updateBotEditField(
    name: String = ui.editBotName,
    desc: String = ui.editBotDesc,
    modelName: String = ui.editBotModel,
    instructions: String = ui.editBotInstructions,
    color: String = ui.editBotColor,
    shape: String = ui.editBotShape,
    curiosity: Boolean = ui.editBotCuriosity,
    notify: Boolean = ui.editBotNotify
) {
    ui = ui.copy(
        editBotName = name, editBotDesc = desc, editBotModel = modelName,
        editBotInstructions = instructions, editBotColor = color, editBotShape = shape,
        editBotCuriosity = curiosity, editBotNotify = notify
    )
}

internal fun NativePulseModel.saveBotEditor() {
    val name = ui.editBotName.trim()
    if (name.isEmpty()) { toast("请填写 Bot 名字"); return }
    if (ui.savingBot) return
    ui = ui.copy(savingBot = true)
    perform {
        try {
            val myNodeSource = SecureTransport.getNodeSource(activity)
            val payload = JSONObject()
                .put("name", name)
                .put("description", ui.editBotDesc.trim())
                .put("model", ui.editBotModel.trim())
                .put("instructions", ui.editBotInstructions.trim())
                .put("color", ui.editBotColor)
                .put("shape", ui.editBotShape)
                .put("curiosity_enabled", ui.editBotCuriosity)
                .put("notify", ui.editBotNotify)
                .put("source", myNodeSource)
                .put("engine", "small")
            val editingId = ui.editingBotId
            val savedBot = if (editingId == null) {
                PulseBot.from(request("POST", "/api/bots", payload), myNodeSource)
            } else {
                PulseBot.from(request("PUT", "/api/bots/$editingId", payload), myNodeSource)
            }
            revision++
            val nextBots = if (editingId == null) ui.bots + savedBot else ui.bots.map { if (it.id == editingId) savedBot else it }
            ui = ui.copy(bots = nextBots, botId = savedBot.id, chatOpen = true, activePanel = null)
            cacheRoster(ui.bots, ui.primary)
            toast(if (editingId == null) "已创建新 Bot：${savedBot.name}" else "已更新 Bot：${savedBot.name}")
            refresh()
        } finally {
            ui = ui.copy(savingBot = false)
        }
    }
}

internal fun NativePulseModel.setPrimaryBot(id: String) {
    dismissMenu()
    perform {
        request("PUT", "/api/bots/primary", JSONObject().put("bot_id", id))
        revision++
        ui = ui.copy(primary = id)
        cacheRoster(ui.bots, id)
        toast("已将「${bot(id)?.name ?: id}」设为主助理")
        refresh()
    }
}

internal fun NativePulseModel.deleteBot(id: String) {
    dismissMenu()
    perform {
        request("DELETE", "/api/bots/$id")
        revision++
        val remaining = ui.bots.filterNot { it.id == id }
        ui = ui.copy(bots = remaining, botId = ui.primary, chatOpen = false, activePanel = null)
        cacheRoster(remaining, ui.primary)
        toast("已删除该 Bot")
        refresh()
    }
}

internal fun NativePulseModel.placement(id: String, key: String, value: Boolean) {
    dismissMenu()
    if (!ensurePaired()) return
    perform {
        val myNodeSource = SecureTransport.getNodeSource(activity)
        val updated = PulseBot.from(request("PATCH", "/api/bots/$id/placement", JSONObject().put(key, value).put("source", myNodeSource)), myNodeSource)
        revision++
        ui = ui.copy(bots = ui.bots.map { if (it.id == id) updated else it }, chatOpen = if (key == "hidden" && value && ui.botId == id) false else ui.chatOpen)
        cacheRoster(ui.bots, ui.primary)
        toast(if (key == "hidden") if (value) "已隐藏。" else "已恢复显示。" else if (value) "已置顶到首页。" else "已取消置顶。")
    }
}

internal fun NativePulseModel.openVault(initialPath: String = "INDEX.md") {
    dismissMenu()
    ui = ui.copy(activePanel = "vault", selectedVaultPath = initialPath, vaultLoading = true)
    perform {
        try {
            val listRes = request("GET", "/api/vault")
            val filesArr = listRes.optJSONArray("files") ?: JSONArray()
            val paths = (0 until filesArr.length()).map { filesArr.getJSONObject(it).getString("path") }
            val target = if (initialPath in paths) initialPath else (paths.firstOrNull() ?: "INDEX.md")
            val docRes = request("GET", "/api/vault", query = JSONObject().put("path", target))
            ui = ui.copy(vaultFiles = paths, selectedVaultPath = target, vaultContent = docRes.optString("content", ""))
        } finally {
            ui = ui.copy(vaultLoading = false)
        }
    }
}

internal fun NativePulseModel.selectVaultFile(path: String) {
    ui = ui.copy(selectedVaultPath = path, vaultLoading = true)
    perform {
        try {
            val docRes = request("GET", "/api/vault", query = JSONObject().put("path", path))
            ui = ui.copy(vaultContent = docRes.optString("content", ""))
        } finally {
            ui = ui.copy(vaultLoading = false)
        }
    }
}

internal fun NativePulseModel.updateVaultContent(content: String) { ui = ui.copy(vaultContent = content) }

internal fun NativePulseModel.saveVaultFile() {
    if (ui.vaultSaving) return
    ui = ui.copy(vaultSaving = true)
    perform {
        try {
            request("PUT", "/api/vault", JSONObject().put("path", ui.selectedVaultPath).put("content", ui.vaultContent))
            toast("已保存记忆：${ui.selectedVaultPath}")
        } finally {
            ui = ui.copy(vaultSaving = false)
        }
    }
}

internal fun NativePulseModel.askPrimaryToHealError(errorMsg: PulseMessage) {
    val primaryId = ui.primary
    val targetBot = bot(errorMsg.botId)?.name ?: errorMsg.botId
    val healPrompt = "Bot「$targetBot (${errorMsg.botId})」刚刚报错了：\n${errorMsg.text}\n\n请直接调用 list_models 查看可用模型，再用 manage_bot(action='update', bot_id='${errorMsg.botId}', model=...) 修复它的模型配置，然后用 send_message 让它重试刚才的任务！"
    open(primaryId)
    setDraft(healPrompt)
    send()
}

internal fun NativePulseModel.openSchedules() {
    dismissMenu()
    ui = ui.copy(activePanel = "schedules", schedulesLoading = true)
    perform {
        try {
            val rRes = runCatching { request("GET", "/api/routines") }.getOrNull()
            val dRes = runCatching { request("GET", "/api/deadlines") }.getOrNull()
            val wRes = runCatching { request("GET", "/api/watchdogs") }.getOrNull()
            val pRes = runCatching { request("GET", "/api/memories/pins") }.getOrNull()
            val merged = JSONObject()
                .put("routines", rRes?.optJSONArray("routines") ?: JSONArray())
                .put("deadlines", dRes?.optJSONArray("deadlines") ?: JSONArray())
                .put("timers", rRes?.optJSONArray("timers") ?: JSONArray())
            ui = ui.copy(
                schedulesData = merged,
                watchdogs = wRes?.optJSONArray("watchdogs")?.objects() ?: ui.watchdogs,
                pinnedMemories = pRes?.optJSONArray("pins")?.objects() ?: ui.pinnedMemories,
                schedulesLoading = false
            )
        } finally {
            ui = ui.copy(schedulesLoading = false)
        }
    }
}

internal fun NativePulseModel.toggleRoutineItem(id: String, enabled: Boolean) {
    perform {
        request("PUT", "/api/routines/$id", JSONObject().put("enabled", enabled))
        toast(if (enabled) "已启用该例行任务" else "已暂停该例行任务")
        openSchedules()
    }
}

internal fun NativePulseModel.runRoutineItem(id: String) {
    perform {
        toast("已触发执行例行任务…")
        closePanel()
        request("POST", "/api/routines/$id/run")
        refresh()
    }
}

internal fun NativePulseModel.parseContextByBot(obj: JSONObject?): Triple<Map<String, Int>, Map<String, Int>, Map<String, Int>> {
    if (obj == null) return Triple(emptyMap(), emptyMap(), emptyMap())
    val tokMap = mutableMapOf<String, Int>()
    val msgMap = mutableMapOf<String, Int>()
    val cacheMap = mutableMapOf<String, Int>()
    val ttftMap = mutableMapOf<String, Int>()
    val tpsMap = mutableMapOf<String, Double>()
    val compMap = mutableMapOf<String, Int>()
    obj.keys().forEach { bid ->
        val sub = obj.optJSONObject(bid) ?: return@forEach
        val cur = sub.optInt("current_tokens", 0)
        val cnt = sub.optInt("message_count", 0)
        val cached = sub.optInt("cached_tokens", 0)
        val ttft = sub.optInt("ttft_ms", 0)
        val tps = sub.optDouble("tps", 0.0).let { if (it.isNaN()) 0.0 else it }
        val comp = sub.optInt("completion_tokens", 0)
        if (cur > 0) tokMap[bid] = cur
        if (cnt >= 0) msgMap[bid] = cnt
        if (cached >= 0) cacheMap[bid] = cached
        if (ttft > 0) ttftMap[bid] = ttft
        if (tps > 0.0) tpsMap[bid] = tps
        if (comp > 0) compMap[bid] = comp
    }
    if (ttftMap.isNotEmpty() || tpsMap.isNotEmpty() || compMap.isNotEmpty()) {
        ui = ui.copy(
            contextTtftByBot = ui.contextTtftByBot + ttftMap,
            contextTpsByBot = ui.contextTpsByBot + tpsMap,
            contextCompTokensByBot = ui.contextCompTokensByBot + compMap
        )
    }
    return Triple(tokMap, msgMap, cacheMap)
}

internal fun NativePulseModel.contextPerfFor(botId: String = ui.botId): Triple<Int, Double, Int> {
    val lastBotMsg = ui.messages.lastOrNull { it.botId == botId && it.role == "assistant" && (it.ttftMs > 0 || it.tps > 0.0) }
    val ttft = (ui.contextTtftByBot[botId] ?: lastBotMsg?.ttftMs ?: 0).coerceAtLeast(0)
    val tps = (ui.contextTpsByBot[botId] ?: lastBotMsg?.tps ?: 0.0).coerceAtLeast(0.0)
    val comp = (ui.contextCompTokensByBot[botId] ?: lastBotMsg?.completionTokens ?: 0).coerceAtLeast(0)
    return Triple(ttft, tps, comp)
}

internal fun NativePulseModel.estimateLocalTextTokens(text: String): Int {
    if (text.isEmpty()) return 0
    var cjk = 0
    for (ch in text) {
        if (ch in '\u4e00'..'\u9fff') cjk++
    }
    val other = (text.length - cjk).coerceAtLeast(0)
    return cjk + (other / 3)
}

internal fun NativePulseModel.contextStatsFor(botId: String = ui.botId): Triple<Int, Int, Int> {
    val thresh = (ui.autoCompactTokens.toIntOrNull() ?: 120000).coerceIn(8000, 1000000)
    val localMsgs = ui.messages.filter { it.botId == botId }
    val localEst = 14800 + localMsgs.sumOf { estimateLocalTextTokens(it.text) + 12 }
    val serverTok = ui.contextTokensByBot[botId] ?: 0
    val curTok = if (serverTok > 0) serverTok else localEst
    val msgCnt = ui.contextMsgsByBot[botId] ?: localMsgs.size
    return Triple(curTok, thresh, msgCnt)
}

internal fun NativePulseModel.contextCacheFor(botId: String = ui.botId): Pair<Int, Int> {
    val (curTok, _, _) = contextStatsFor(botId)
    val cachedTok = (ui.contextCachedByBot[botId] ?: 0).coerceIn(0, curTok.coerceAtLeast(0))
    val pct = if (curTok > 0 && cachedTok > 0) ((cachedTok.toDouble() / curTok.toDouble()) * 100.0).toInt().coerceIn(0, 100) else 0
    return cachedTok to pct
}

internal fun NativePulseModel.formatTokenCount(tokens: Int): String {
    return when {
        tokens >= 10_000 -> String.format(Locale.US, "%.1fK", tokens / 1000.0).removeSuffix(".0K") + (if (String.format(Locale.US, "%.1fK", tokens / 1000.0).endsWith(".0K")) "K" else "")
        tokens >= 1_000 -> String.format(Locale.US, "%.1fK", tokens / 1000.0)
        else -> tokens.toString()
    }
}

internal fun NativePulseModel.applyContextWindowTokens(tokens: Int, botId: String = ui.editingBotId ?: ui.botId) {
    val cleanTokens = tokens.coerceIn(8_000, 1_000_000)
    ui = ui.copy(autoCompactTokens = cleanTokens.toString())
    draftWriter.submit {
        synchronized(SecurePrefs::class.java) {
            val s = SecurePrefs.read(activity)
            val localCfg = s.optJSONObject("config") ?: JSONObject()
            localCfg.put("auto_compact_tokens", cleanTokens)
            s.put("config", localCfg)
            SecurePrefs.save(activity, s)
            prefs = s
        }
    }
    perform {
        val mySource = SecureTransport.getNodeSource(activity)
        val res = request(
            "POST",
            "/api/config",
            JSONObject().put("auto_compact_tokens", cleanTokens),
            JSONObject().put("source", mySource)
        )
        val (ctxTokensMap, ctxMsgsMap, ctxCachedMap) = parseContextByBot(res.optJSONObject("context_by_bot"))
        val serverCompact = res.optInt("auto_compact_tokens", cleanTokens).coerceIn(8_000, 1_000_000)
        ui = ui.copy(
            autoCompactTokens = serverCompact.toString(),
            contextTokensByBot = if (ctxTokensMap.isNotEmpty()) ctxTokensMap else ui.contextTokensByBot,
            contextCachedByBot = if (ctxCachedMap.isNotEmpty()) ctxCachedMap else ui.contextCachedByBot,
            contextMsgsByBot = if (ctxMsgsMap.isNotEmpty()) ctxMsgsMap else ui.contextMsgsByBot
        )
        val compactedArr = res.optJSONArray("compacted_bots")
        val autoCompacted = compactedArr != null && compactedArr.length() > 0
        if (autoCompacted) {
            refresh(force = true)
            toast("上下文窗口已调至 ${formatTokenCount(serverCompact)} Tokens，并自动浓缩超限对话")
        } else {
            val (curTok, _, _) = contextStatsFor(botId)
            if (curTok > serverCompact && !ui.compactingContext) {
                toast("上下文窗口已调至 ${formatTokenCount(serverCompact)} Tokens，正在浓缩超限历史…")
                compactBotContext(botId)
            } else {
                toast("上下文窗口已实时调节为 ${formatTokenCount(serverCompact)} Tokens")
            }
        }
    }
}

internal fun NativePulseModel.compactBotContext(botId: String = ui.botId) {
    if (ui.compactingContext) return
    dismissMenu()
    val thresh = (ui.autoCompactTokens.toIntOrNull() ?: 120000).coerceIn(8000, 1000000)
    ui = ui.copy(
        compactingContext = true,
        activePanel = null,
        botId = botId,
        chatOpen = true,
        workingBot = botId,
        streamBotId = botId,
        streamText = "📦 正在流式压缩上下文，请查看实时进度..."
    )
    toast("📦 正在流式压缩上下文，请查看实时进度...")
    perform {
        try {
            val payload = JSONObject()
                .put("bot_id", botId)
                .put("auto_compact_tokens", thresh)
            val res = request("POST", "/api/context/compact", payload)
            val (ctxTokensMap, ctxMsgsMap, ctxCachedMap) = parseContextByBot(res.optJSONObject("context_by_bot"))
            if (ctxTokensMap.isNotEmpty()) {
                ui = ui.copy(contextTokensByBot = ctxTokensMap, contextCachedByBot = ctxCachedMap, contextMsgsByBot = ctxMsgsMap)
            }
            val alreadyCompacted = res.optBoolean("already_compacted", false) ||
                res.optJSONObject("result")?.optBoolean("already_compacted", false) == true
            val compacted = res.optBoolean("compacted", false) ||
                res.optJSONObject("result")?.optBoolean("compacted", false) == true ||
                (res.optJSONObject("result")?.optInt("compacted_messages", 0) ?: 0) > 0
            val archivedCount = res.optInt(
                "archived_count",
                res.optJSONObject("result")?.optInt(
                    "archived_count",
                    res.optJSONObject("result")?.optInt("compacted_messages", 0) ?: 0
                ) ?: 0
            )
            if (alreadyCompacted) {
                toast("当前上下文已是最新压缩状态，无需重复压缩")
            } else if (compacted) {
                val msgResp = request("GET", "/api/messages", null, JSONObject().put("limit", 120))
                val freshMsgs = msgResp.getJSONArray("messages").objects().map(PulseMessage::from)
                mergeRemoteMessages(freshMsgs, fullReplace = true)
                ui = ui.copy(messages = combinedMessages())
                toast("✅ 上下文压缩完成 (归档 $archivedCount 条消息)")
            } else {
                toast(res.optString("message").ifBlank { "当前对话条数较少，暂无需压缩" })
            }
        } finally {
            ui = ui.copy(
                compactingContext = false,
                workingBot = if (ui.workingBot == botId) null else ui.workingBot,
                streamBotId = if (ui.streamBotId == botId && ui.streamText.startsWith("📦")) null else ui.streamBotId,
                streamText = if (ui.streamBotId == botId && ui.streamText.startsWith("📦")) "" else ui.streamText
            )
        }
    }
}

internal fun NativePulseModel.loadMemoryPins() {
    perform {
        val res = request("GET", "/api/memories/pins")
        ui = ui.copy(pinnedMemories = res.optJSONArray("pins")?.objects() ?: emptyList())
    }
}

internal fun NativePulseModel.addMemoryPin(text: String) {
    val clean = text.trim()
    if (clean.isEmpty()) return
    perform {
        val res = request("POST", "/api/memories/pins", JSONObject().put("action", "add").put("text", clean))
        ui = ui.copy(pinnedMemories = res.optJSONArray("pins")?.objects() ?: ui.pinnedMemories)
        toast("已钉住记忆")
    }
}

internal fun NativePulseModel.removeMemoryPin(pinId: String) {
    perform {
        val res = request("POST", "/api/memories/pins", JSONObject().put("action", "remove").put("pin_id", pinId))
        ui = ui.copy(pinnedMemories = res.optJSONArray("pins")?.objects() ?: emptyList())
        toast("已取消钉住")
    }
}

internal fun NativePulseModel.loadWatchdogs() {
    perform {
        val res = request("GET", "/api/watchdogs")
        ui = ui.copy(watchdogs = res.optJSONArray("watchdogs")?.objects() ?: emptyList())
    }
}

internal fun NativePulseModel.stopWatchdog(watchdogId: String) {
    perform {
        request("POST", "/api/watchdogs/$watchdogId/stop")
        loadWatchdogs()
        toast("已停止哨兵监控：$watchdogId")
    }
}

internal fun NativePulseModel.switchAdjacentBot(delta: Int) {
    val list = visibleBots().ifEmpty { ui.bots.filter { !it.hidden } }
    if (list.size <= 1) return
    val curIdx = list.indexOfFirst { it.id == ui.botId }.coerceAtLeast(0)
    val nextIdx = (curIdx + delta + list.size) % list.size
    val nextBot = list[nextIdx]
    ui = ui.copy(lastSwitchDir = if (delta >= 0) 1 else -1)
    open(nextBot.id)
}

internal fun NativePulseModel.togglePinBot(targetBotId: String) {
    val target = bot(targetBotId) ?: return
    placement(target.id, "pinned", !target.pinned)
}

internal fun NativePulseModel.toggleAppearanceQuick() {
    val next = if (ui.appearance == "light") "dark" else "light"
    updateSettingsField(appearance = next)
}

internal fun NativePulseModel.stopBotById(targetBotId: String) {
    stop(targetBotId)
}
