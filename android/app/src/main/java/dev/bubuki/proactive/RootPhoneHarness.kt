package dev.bubuki.proactive

import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.PowerManager
import android.os.SystemClock
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import org.json.JSONArray
import org.json.JSONObject
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
    val currentPackage: String?
        get() = PulseAccessibilityService.getCachedSnapshot(displayId)?.activePackage?.takeIf { it.isNotBlank() }
            ?: runCatching { JSONObject(display).optString("package", "").takeIf { it.isNotBlank() } }.getOrNull()
}

/**
 * [Module] RootPhoneHarness
 * [Responsibility] Root virtual display workspace state machine, lifecycle & power management
 *                  (`KeepAwake`, idle watchdog, auto-release), live preview polling, sub-2ms
 *                  1:1 touch streaming, command router (`command`), and local phone agent loop
 *                  (`submit`, `stop`, `resume`).
 *                  Delegates high-level Accessibility node actions, file transfers, shell,
 *                  notifications, and launcher package resolution to RootPhoneHarnessActions.kt.
 * [Public API]
 *   - RootPhoneHarness.changes: StateFlow<RootPhoneState>
 *   - initialize(base) / status(base) / history(base) / deviceInfo(base)
 *   - command(base, input) / sendLiveTouch(base, action, x, y) / scheduleAutoRelease(base, delayMs, toForeground)
 *   - submit(base, bot, goal, history, recordUserMessage) / submitFromBridge(base, bot, goal)
 *   - stop(base) / resume(base) / isVirtualDisplayActive() / currentDisplayId()
 */
object RootPhoneHarness {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    internal val state = MutableStateFlow(RootPhoneState())
    internal val changes: StateFlow<RootPhoneState> = state.asStateFlow()
    private var context: Context? = null
    internal var privilege: PrivilegeManager? = null
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

    // Multi-layer caches for fast 0ms lookups (shared with RootPhoneHarnessActions)
    @Volatile internal var cachedLauncherApps: List<PhoneApp> = emptyList()
    @Volatile internal var appsCachedAtElapsed: Long = 0L
    @Volatile internal var shellCwd: String = "/sdcard"
    internal const val APPS_CACHE_TTL_MS = 5 * 60 * 1000L
    private const val IDLE_AUTO_RELEASE_MS = 45_000L
    internal val resolvedComponentCache = ConcurrentHashMap<String, String>()

    @JvmStatic fun isVirtualDisplayActive(): Boolean {
        val st = state.value
        return st.displayCreated || st.active
    }

    @JvmStatic fun currentDisplayId(): Int {
        return state.value.displayId
    }

    @JvmStatic fun launchFromVirtualHome(base: Context, displayId: Int, component: String) {
        initialize(base)
        runCatching {
            command(
                base.applicationContext,
                JSONObject()
                    .put("op", "launch")
                    .put("component", component)
                    .put("manual", true)
            )
        }
    }

    internal fun listLaunchableApps(base: Context, forceRefresh: Boolean = false): List<PhoneApp> {
        initialize(base)
        val apps = queryLauncherApps(base.applicationContext, forceRefresh)
        state.update { it.copy(apps = apps) }
        return apps
    }

    internal fun shallowCopyWithoutImage(src: JSONObject): JSONObject {
        val out = JSONObject()
        val keys = src.keys()
        while (keys.hasNext()) {
            val k = keys.next()
            if (k == "image" || k == "_image_b64") continue
            out.put(k, src.opt(k))
        }
        return out
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
                releaseKeepAwake()
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

    @JvmStatic fun sendLiveTouch(base: Context, action: String, x: Int, y: Int) {
        initialize(base)
        val now = SystemClock.elapsedRealtime()
        lastActivityElapsed = now
        lastLiveTouchElapsed = now
        autoReleaseJob?.cancel()
        startLivePreview(base.applicationContext)
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
                if (disp == null || (!disp.optBoolean("created") && !disp.optBoolean("allocated"))) break
                if (cur.active) {
                    lastActivityElapsed = SystemClock.elapsedRealtime()
                    continue
                }
                val idleMs = SystemClock.elapsedRealtime() - lastActivityElapsed
                if (idleMs >= IDLE_AUTO_RELEASE_MS) {
                    releaseKeepAwake()
                }
            }
        }
    }

    @JvmStatic fun deviceInfo(base: Context): JSONObject = buildDeviceInfo(base)

    @JvmStatic fun status(base: Context): JSONObject {
        initialize(base); val current = state.value
        var status = privilege!!.status()
        if (!status.optBoolean("root")) {
            status = runCatching { privilege!!.probe() }.getOrDefault(status)
        }
        val saved = SecurePrefs.readFast(base); val config = modelConfig(saved)
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
            "close", "destroy", "stop_display" -> "release"
            "promote", "expand_to_foreground", "to_foreground", "fullscreen" -> "promote"
            "pull_back", "to_virtual", "pull_to_virtual" -> "pull_back"
            "dump", "tree", "nodes", "read_screen" -> "ui_tree"
            "click_node" -> "tap_node"
            "input_node", "set_text", "type" -> "type_node"
            "exec", "cmd", "bash" -> "shell"
            "phone_info", "battery" -> "device_info"
            "files" -> "list_files"
            "open_url" -> "open_uri"
            "wait", "sleep" -> "delay"
            "get_notifications", "recent_notifications", "notifs", "notification" -> "notifications"
            "get_clipboard", "read_clipboard" -> "clipboard_get"
            "set_clipboard", "write_clipboard" -> "clipboard_set"
            else -> rawOp
        }
        when (operation) {
            "device_info" -> return deviceInfo(base)
            "list_files" -> return executeListFiles(input)
            "pull_file" -> return executePullFile(base, input)
            "push_file" -> return executePushFile(base, input)
            "shell" -> return executeShellAction(base, input)
            "notifications" -> return executeNotificationsAction(base, input)
            "clipboard", "clipboard_get", "clipboard_set" -> {
                if (!privilege!!.isRootGrantedFast()) {
                    val probed = privilege!!.probe()
                    if (!probed.optBoolean("root")) throw Exception("尚未取得 Root 授权，请在手机的 Root 管理器中允许此应用")
                }
                return privilege!!.execute(JSONObject(input.toString()).put("op", operation))
            }
            "delay" -> {
                val ms = input.optLong("delay_ms", input.optLong("ms", 600L)).coerceIn(50L, 5000L)
                try { Thread.sleep(ms) } catch (ignored: InterruptedException) {}
                return JSONObject().put("uid", 0).put("ok", true).put("op", "delay").put("delay_ms", ms)
            }
            "batch" -> {
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
            "auto_release" -> {
                val curDisp = runCatching { JSONObject(state.value.display).optInt("displayId", -1) }.getOrDefault(-1)
                if (!state.value.displayCreated && curDisp <= 0) {
                    return JSONObject().put("uid", 0).put("ok", true).put("noop", true).put("created", false)
                }
                val toFg = input.optBoolean("to_foreground", false)
                scheduleAutoRelease(base, input.optLong("delay_ms", 2500L), toFg)
                return JSONObject().put("scheduled_release", true).put("to_foreground", toFg)
            }
            "release" -> {
                val keepAlive = input.optBoolean("keep_alive", false)
                val shouldDestroy = !keepAlive || input.optBoolean("user_close", false) || input.optBoolean("destroy", false) || input.optBoolean("manual", false)
                val curDisp = runCatching { JSONObject(state.value.display).optInt("displayId", -1) }.getOrDefault(-1)
                if (!shouldDestroy) {
                    releaseKeepAwake()
                    if (state.value.displayCreated || curDisp > 0) {
                        startLivePreview(base.applicationContext)
                    }
                    return runCatching { JSONObject(state.value.display) }.getOrDefault(JSONObject())
                        .put("uid", 0)
                        .put("ok", true)
                        .put("kept_alive", true)
                        .put("released", false)
                        .put("display_active", state.value.displayCreated || curDisp > 0)
                        .put("created", state.value.displayCreated || curDisp > 0)
                        .put("displayId", curDisp)
                }
                if (!state.value.displayCreated && curDisp <= 0) {
                    autoReleaseJob?.cancel()
                    idleWatchJob?.cancel()
                    stopLivePreview()
                    releaseKeepAwake()
                    FloatingVirtualDisplay.hide()
                    return JSONObject()
                        .put("uid", 0)
                        .put("ok", true)
                        .put("noop", true)
                        .put("released", true)
                        .put("destroyed", true)
                        .put("already_released", true)
                        .put("display_active", false)
                        .put("created", false)
                        .put("displayId", -1)
                }
                input.put("destroy", true)
                input.put("user_close", true)
            }
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
        if (state.value.phase in listOf("running", "waiting_user") && input.optBoolean("manual") && operation !in listOf("capture", "resize", "create", "apps", "pull_back")) {
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
        val virtualDisplayOps = setOf("ui_tree", "tap", "tap_node", "tap_and_type", "swipe", "text", "type_node", "key", "long_press", "capture", "launch", "open_uri", "resize", "pull_back")
        if (currentDisplay <= 0 && operation in virtualDisplayOps) {
            runCatching {
                val woke = command(base, JSONObject().put("op", "create").put("skip_home", false))
                currentDisplay = woke.optInt("displayId", -1)
            }
            if (currentDisplay <= 0) {
                throw Exception("虚拟屏尚未创建成功 (display_not_created, displayId=$currentDisplay)，请检查 Root 权限后重试")
            }
        }
        var dispInfo = JSONObject(state.value.display.ifBlank { state.value.imageInfo })
        var dispW = dispInfo.optInt("width", 1472).coerceAtLeast(1)
        var dispH = dispInfo.optInt("height", 3232).coerceAtLeast(1)
        var dispDpi = dispInfo.optInt("densityDpi", 480).coerceAtLeast(160)

        resolveGridOrNormCoords(operation, input, dispW, dispH)

        if (operation == "tap_and_type") {
            return executeTapAndTypeAction(base, input, currentDisplay, dispW, dispH)
        }
        if (operation == "ui_tree") {
            if (currentDisplay <= 0) throw Exception("虚拟屏尚未就绪，请先调用 launch 打开目标应用")
            lastCaptureRotation = null
            val treeJson = readUiTreeForDisplay(currentDisplay, dispW, dispH, forceRefresh = input.optBoolean("refresh", true))
            state.update { it.copy(lastTreeSummary = treeJson.optString("summary", "")) }
            return treeJson.put("created", true).put("displayId", currentDisplay).put("display_id", currentDisplay)
        }
        if (operation == "tap_node" || (operation == "tap" && !input.has("x") && (input.has("ref") || input.has("text") || input.has("id") || input.has("target")))) {
            return executeTapNodeAction(base, input, currentDisplay, dispW, dispH)
        }
        if (operation == "type_node" || operation == "text") {
            return executeTypeNodeAction(input, currentDisplay, dispW, dispH)
        }
        if (operation == "long_press" && !input.has("x") && (input.has("ref") || input.has("text") || input.has("id") || input.has("target"))) {
            resolveLongPressNodeCoords(input, currentDisplay, dispW, dispH)
        }

        val command = JSONObject(input.toString())
        command.put("op", operation)
        command.remove("manual")
        if (operation == "launch") {
            val rawComp = command.optString("component").ifBlank { command.optString("package").ifBlank { command.optString("app").ifBlank { command.optString("text") } } }.trim()
            val resolved = resolveLaunchComponent(base, rawComp)
            command.put("component", resolved)
            if (currentDisplay <= 0) {
                val created = command(base, JSONObject().put("op", "create").put("skip_home", false))
                currentDisplay = created.optInt("displayId", -1)
                dispInfo = JSONObject(state.value.display.ifBlank { state.value.imageInfo })
                dispW = dispInfo.optInt("width", 1472).coerceAtLeast(1)
                dispH = dispInfo.optInt("height", 3232).coerceAtLeast(1)
                dispDpi = dispInfo.optInt("densityDpi", 480).coerceAtLeast(160)
            }
            lastCaptureRotation = null
        } else if (operation == "open_uri") {
            val uri = command.optString("uri", command.optString("url", command.optString("text", ""))).trim()
            if (uri.isEmpty()) throw Exception("请提供要打开的 URI/URL")
            if (currentDisplay <= 0) {
                val created = command(base, JSONObject().put("op", "create").put("skip_home", false))
                currentDisplay = created.optInt("displayId", -1)
            }
            lastCaptureRotation = null
        } else if (operation in listOf("create", "release")) {
            lastCaptureRotation = null
        }
        if (operation in listOf("capture", "tap", "long_press", "swipe", "text", "key", "launch", "open_uri", "resize")) {
            if (currentDisplay <= 0) throw Exception("虚拟屏尚未启动任何应用 (display_not_created)，请先调用 launch 打开目标应用")
            if (command.has("display_id") && command.getInt("display_id") != currentDisplay) throw Exception("displayId 不属于当前工作区")
            command.put("display_id", currentDisplay)
            if (operation in listOf("tap", "long_press", "swipe") && !input.optBoolean("manual")) lastCaptureRotation?.let { command.put("expected_rotation", it) }
        }
        val beforeKeyHash = if (operation == "key" && currentDisplay > 0) {
            (PulseAccessibilityService.getCachedSnapshot(currentDisplay)
                ?: PulseAccessibilityService.dumpDisplayTree(currentDisplay, dispW, dispH, forceRefresh = false, waitMs = 150L)?.let {
                    PulseAccessibilityService.getCachedSnapshot(currentDisplay)
                })?.treeHash.orEmpty()
        } else ""
        try {
            val result = privilege!!.execute(command)
            if (result.optInt("uid", -1) != 0) throw Exception("拒绝非 Root 的手机执行结果")
            if (operation == "promote" && result.optBoolean("promoted", false)) {
                state.update { it.copy(phase = "waiting_user") }
                FloatingVirtualDisplay.minimizeToSidePill(base.applicationContext)
            } else if (operation == "pull_back" && result.optBoolean("pulled_back", false)) {
                FloatingVirtualDisplay.show(base.applicationContext, forceExpand = true)
            }
            if (operation == "key" && currentDisplay > 0) {
                try { Thread.sleep(240L) } catch (ignored: InterruptedException) {}
                val afterTree = readUiTreeForDisplay(currentDisplay, dispW, dispH, forceRefresh = true, waitMs = 450L)
                val afterHash = afterTree.optString("treeHash")
                val consumed = beforeKeyHash.isEmpty() || afterHash != beforeKeyHash
                val summary = afterTree.optString("summary", "")
                state.update { it.copy(lastTreeSummary = summary) }
                result.put("key_consumed", consumed)
                    .put("tree_hash_before", beforeKeyHash)
                    .put("tree_hash_after", afterHash)
                    .put("treeHash", afterHash)
                    .put("tree_hash", afterHash)
                    .put("package", afterTree.optString("package", "unknown"))
                    .put("nodeCount", afterTree.optInt("nodeCount", 0))
                    .put("layoutStable", afterTree.optBoolean("layoutStable", false))
                    .put("settled", afterTree.optBoolean("settled", false))
                    .put("settle_ms", afterTree.optLong("settle_ms", 0L))
                    .put("densityDpi", dispDpi)
                    .put("screenWidthDp", (dispW * 160 / dispDpi).coerceAtLeast(360))
                    .put("fontScale", 1.0)
                    .put("ui_tree", summary)
            } else if ((operation == "launch" || operation == "open_uri") && currentDisplay > 0) {
                val isManualLaunch = input.optBoolean("manual", false)
                val targetComp = command.optString("component", "")
                val expectExternalApp = operation == "launch" && targetComp.isNotBlank() && !targetComp.startsWith("dev.bubuki.proactive/")
                try { Thread.sleep(if (isManualLaunch) 140L else 280L) } catch (ignored: InterruptedException) {}
                var launchTree = readUiTreeForDisplay(currentDisplay, dispW, dispH, forceRefresh = true, waitMs = if (isManualLaunch) 320L else 950L)
                val firstPkg = launchTree.optString("package", "unknown")
                if (!isManualLaunch && (launchTree.optInt("nodeCount", 0) == 0 || firstPkg == "unknown" || (expectExternalApp && firstPkg == "dev.bubuki.proactive"))) {
                    try { Thread.sleep(320L) } catch (ignored: InterruptedException) {}
                    launchTree = readUiTreeForDisplay(currentDisplay, dispW, dispH, forceRefresh = true, waitMs = 750L)
                }
                val pkg = launchTree.optString("package", "unknown")
                val cnt = launchTree.optInt("nodeCount", 0)
                val summary = launchTree.optString("summary", "")
                val lHash = launchTree.optString("treeHash", "")
                state.update { it.copy(lastTreeSummary = summary) }
                result.put("launched", pkg.isNotBlank() && pkg != "unknown" && cnt > 0 && (!expectExternalApp || pkg != "dev.bubuki.proactive"))
                    .put("package", pkg)
                    .put("nodeCount", cnt)
                    .put("treeHash", lHash)
                    .put("tree_hash", lHash)
                    .put("layoutStable", launchTree.optBoolean("layoutStable", false))
                    .put("settled", launchTree.optBoolean("settled", false))
                    .put("settle_ms", launchTree.optLong("settle_ms", 0L))
                    .put("densityDpi", dispDpi)
                    .put("screenWidthDp", (dispW * 160 / dispDpi).coerceAtLeast(360))
                    .put("fontScale", 1.0)
                    .put("likely_blank", launchTree.optBoolean("likely_blank", cnt == 0))
                    .put("layout_signature", launchTree.optString("layout_signature", ""))
                    .put("ui_tree", summary)
            } else if (operation in listOf("tap", "swipe", "long_press", "capture") && currentDisplay > 0 && !input.optBoolean("manual", false)) {
                val waitForSettle = if (operation == "capture") 280L else 450L
                if (operation != "capture") {
                    try { Thread.sleep(280L) } catch (ignored: InterruptedException) {}
                }
                val afterTree = readUiTreeForDisplay(currentDisplay, dispW, dispH, forceRefresh = true, waitMs = waitForSettle)
                val summary = afterTree.optString("summary", "")
                val aHash = afterTree.optString("treeHash", "")
                state.update { it.copy(lastTreeSummary = summary) }
                result.put("package", afterTree.optString("package", "unknown"))
                    .put("nodeCount", afterTree.optInt("nodeCount", 0))
                    .put("treeHash", aHash)
                    .put("tree_hash", aHash)
                    .put("layoutStable", afterTree.optBoolean("layoutStable", false))
                    .put("settled", afterTree.optBoolean("settled", false))
                    .put("settle_ms", afterTree.optLong("settle_ms", 0L))
                    .put("densityDpi", dispDpi)
                    .put("screenWidthDp", (dispW * 160 / dispDpi).coerceAtLeast(360))
                    .put("fontScale", 1.0)
                    .put("likely_blank", afterTree.optBoolean("likely_blank", false))
                    .put("layout_signature", afterTree.optString("layout_signature", ""))
                    .put("ui_tree", summary)
                attachLatestDisplayFrame(result, currentDisplay)
            }
            if (operation in listOf("launch", "open_uri", "key") && currentDisplay > 0) {
                attachLatestDisplayFrame(result, currentDisplay)
            }
            val image = result.optString("image").ifBlank { result.optString("_image_b64") }
            val clean = shallowCopyWithoutImage(result)
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
                    image = if (operation == "release") "" else image.ifBlank { it.image },
                    imageInfo = if (operation == "release") "" else if (image.isNotBlank() || operation == "resize") clean.toString() else it.imageInfo,
                    lastTreeSummary = if (operation == "release") "" else it.lastTreeSummary,
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
                if (input.optBoolean("manual", false)) {
                    FloatingVirtualDisplay.show(base.applicationContext)
                } else {
                    FloatingVirtualDisplay.showAuto(base.applicationContext)
                }
            } else if (operation == "release") {
                autoReleaseJob?.cancel()
                idleWatchJob?.cancel()
                stopLivePreview()
                releaseKeepAwake()
                FloatingVirtualDisplay.hide()
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

    private fun startLivePreview(appContext: Context) {
        if (previewJob?.isActive == true) return
        lastPreviewSequence = -1L
        previewJob = scope.launch {
            while (isActive) {
                val sinceTouch = SystemClock.elapsedRealtime() - lastLiveTouchElapsed
                val sinceAct = SystemClock.elapsedRealtime() - lastActivityElapsed
                val pipVisible = FloatingVirtualDisplay.isPreviewVisible()
                if (!pipVisible) {
                    delay(1800L)
                    val disp = runCatching { JSONObject(state.value.display) }.getOrNull()
                    if (disp == null || !disp.optBoolean("created")) break
                    continue
                }
                val pollInterval = when {
                    sinceTouch < 1000L -> 70L
                    sinceAct < 3000L -> 550L
                    else -> 1500L
                }
                delay(pollInterval)
                val disp = runCatching { JSONObject(state.value.display) }.getOrNull()
                if (disp == null || !disp.optBoolean("created")) break
                if (!FloatingVirtualDisplay.isPreviewVisible()) continue
                runCatching {
                    val res = privilege?.execute(JSONObject().put("op", "preview").put("since", lastPreviewSequence)) ?: return@runCatching
                    if (!res.optBoolean("created")) return@runCatching
                    val seq = res.optLong("frameSequence", -1L)
                    if (seq >= 0) lastPreviewSequence = seq
                    val img = res.optString("image").ifBlank { res.optString("_image_b64") }
                    if (img.isNotBlank() && img != state.value.image) {
                        val clean = shallowCopyWithoutImage(res)
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
                    state.update { it.copy(phase = "idle", result = "手机执行已停止。") }
                    append(message(bot, "assistant", "手机执行已停止。", "phone_stopped", state.value.trace))
                }
            } catch (error: Exception) {
                state.update { it.copy(phase = "failed", error = MainActivity.safeError(error), result = "手机执行中断：${MainActivity.safeError(error)}") }
                append(message(bot, "assistant", state.value.result, "phone_stopped", state.value.trace))
            } finally {
                withContext(NonCancellable) {
                    persist(); broadcast()
                    if (!waiting) {
                        scheduleAutoRelease(base, 2500L)
                        withContext(Dispatchers.Main) { base.stopService(Intent(base, RootPhoneService::class.java)) }
                    }
                }
            }
        }
    }

    @JvmStatic @JvmOverloads fun stop(base: Context, closeDisplay: Boolean = false) {
        initialize(base)
        if (closeDisplay) {
            FloatingVirtualDisplay.hide()
        }
        val active = job
        val curDisp = runCatching { JSONObject(state.value.display).optInt("displayId", -1) }.getOrDefault(-1)
        if (active?.isActive != true && !state.value.active && (!closeDisplay || (!state.value.displayCreated && curDisp <= 0))) {
            return
        }
        state.update { it.copy(phase = if (closeDisplay) "stopping" else "idle") }
        if (active?.isActive == true) { active.cancel(); agent?.cancel() }
        if (closeDisplay) {
            scope.launch {
                if (state.value.displayCreated || curDisp > 0) {
                    runCatching { command(base, JSONObject().put("op", "release").put("user_close", true).put("destroy", true)) }
                }
                state.update { it.copy(phase = "stopped") }
                persist(); broadcast()
                base.stopService(Intent(base, RootPhoneService::class.java))
            }
        } else {
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
