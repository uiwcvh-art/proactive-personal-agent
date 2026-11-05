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
    val active: Boolean get() = phase in listOf("running", "waiting_user", "stopping")
    val displayCreated: Boolean get() = runCatching { JSONObject(display).optBoolean("created") }.getOrDefault(false)
    val displayId: Int get() = runCatching { JSONObject(display).optInt("display_id", 0) }.getOrDefault(0)
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
    private var agent: RootPhoneAgent? = null
    private var lastCaptureRotation: Int? = null
    private var screenWakeLock: PowerManager.WakeLock? = null
    private val setupLock = Any()
    private val loaded = CompletableDeferred<Unit>()
    private val submitLock = Mutex()

    // Multi-layer caches for fast 0ms lookups
    @Volatile private var cachedLauncherApps: List<PhoneApp> = emptyList()
    @Volatile private var appsCachedAtElapsed: Long = 0L
    private const val APPS_CACHE_TTL_MS = 5 * 60 * 1000L
    private const val IDLE_AUTO_RELEASE_MS = 45_000L
    private val resolvedComponentCache = ConcurrentHashMap<String, String>()

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

    @Suppress("DEPRECATION")
    private fun acquireKeepAwake(base: Context) {
        runCatching {
            synchronized(setupLock) {
                val wl = screenWakeLock ?: base.applicationContext.getSystemService(PowerManager::class.java)
                    .newWakeLock(PowerManager.SCREEN_DIM_WAKE_LOCK or PowerManager.ON_AFTER_RELEASE, "Pulse:VirtualDisplay")
                    .apply { setReferenceCounted(false) }
                    .also { screenWakeLock = it }
                if (!wl.isHeld) wl.acquire(30 * 60 * 1000L)
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

    @JvmStatic fun scheduleAutoRelease(base: Context, delayMs: Long = 2500L) {
        autoReleaseJob?.cancel()
        autoReleaseJob = scope.launch {
            delay(delayMs.coerceIn(500L, 15000L))
            val cur = state.value
            if (!cur.active) {
                runCatching { command(base.applicationContext, JSONObject().put("op", "release")) }
            }
        }
    }

    private fun touchActivity() {
        lastActivityElapsed = SystemClock.elapsedRealtime()
        autoReleaseJob?.cancel()
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
            "dump", "tree", "nodes", "read_screen" -> "ui_tree"
            "click_node" -> "tap_node"
            "input_node", "set_text" -> "type_node"
            "exec", "cmd" -> "shell"
            else -> rawOp
        }
        if (operation == "auto_release") {
            scheduleAutoRelease(base, input.optLong("delay_ms", 2500L))
            return JSONObject().put("scheduled_release", true)
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
        if (state.value.phase in listOf("running", "waiting_user") && input.optBoolean("manual") && operation != "capture") {
            job?.cancel(); agent?.cancel()
            state.update { it.copy(phase = "idle") }
        }
        if (!privilege!!.isRootGrantedFast()) {
            val probed = privilege!!.probe()
            state.update { it.copy(root = probed.optBoolean("root")) }
            if (!probed.optBoolean("root")) throw Exception("尚未取得 Root 授权，请在手机的 Root 管理器中允许此应用")
        }

        val currentDisplay = JSONObject(state.value.display).optInt("displayId", -1)
        val dispInfo = JSONObject(state.value.imageInfo.ifBlank { state.value.display })
        val dispW = dispInfo.optInt("width", 1080).coerceAtLeast(1)
        val dispH = dispInfo.optInt("height", 2400).coerceAtLeast(1)

        // Direct Root shell execution for external callers
        if (operation == "shell") {
            val cmdStr = input.optString("command", input.optString("cmd", input.optString("text", ""))).trim()
            if (cmdStr.isEmpty()) throw Exception("请提供要执行的 shell command")
            // Intercept any uiautomator dump command so it never dumps Display 0 (the user's main screen)!
            if (cmdStr.contains("uiautomator", ignoreCase = true) && cmdStr.contains("dump", ignoreCase = true)) {
                if (currentDisplay <= 0) throw Exception("先创建独立工作区")
                val treeJson = readUiTreeForDisplay(currentDisplay, dispW, dispH, forceRefresh = true)
                return treeJson.put("output", treeJson.optString("summary", ""))
            }
            return privilege!!.execute(JSONObject().put("op", "shell").put("command", cmdStr))
        }

        // 1. Primary operation: Read Accessibility / UI Node Tree (`ui_tree`)
        if (operation == "ui_tree") {
            if (currentDisplay <= 0) throw Exception("先创建独立工作区")
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

        // 3. Node-based text input (`type_node`)
        if (operation == "type_node" || (operation == "text" && (input.has("ref") || input.has("target") || input.has("id")))) {
            if (currentDisplay <= 0) throw Exception("先创建独立工作区")
            val ref = input.optString("ref", input.optString("node", "")).trim()
            val targetLabel = input.optString("target", input.optString("hint", "")).trim()
            val resId = input.optString("id", input.optString("resource_id", "")).trim()
            val contentText = input.optString("text", input.optString("value", ""))
            if (contentText.isEmpty()) throw Exception("type_node 需要提供要输入的 text")
            var node = PulseAccessibilityService.resolveTargetNode(currentDisplay, dispW, dispH, ref, targetLabel, resId)
            if (node == null) {
                readUiTreeForDisplay(currentDisplay, dispW, dispH, forceRefresh = true)
                node = PulseAccessibilityService.resolveTargetNode(currentDisplay, dispW, dispH, ref, targetLabel, resId)
            }
            if (node != null) {
                // Tap center to focus first
                runCatching {
                    privilege!!.execute(JSONObject().put("op", "tap").put("display_id", currentDisplay).put("x", node.centerX).put("y", node.centerY))
                }
                val a11yTyped = PulseAccessibilityService.performAccessibilityAction(currentDisplay, node, "set_text", contentText)
                if (a11yTyped) {
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
            return pasteRes.put("method", "clipboard_paste").put("ref", node?.ref.orEmpty())
        }

        val command = JSONObject(input.toString())
        command.put("op", operation)
        command.remove("manual")
        if (operation == "launch") {
            val rawComp = command.optString("component").ifBlank { command.optString("package").ifBlank { command.optString("app").ifBlank { command.optString("text") } } }.trim()
            val resolved = resolveLaunchComponent(base, rawComp)
            command.put("component", resolved)
        }
        if (operation in listOf("capture", "tap", "swipe", "text", "key", "launch")) {
            if (currentDisplay <= 0) throw Exception("先创建独立工作区")
            if (command.has("display_id") && command.getInt("display_id") != currentDisplay) throw Exception("displayId 不属于当前工作区")
            command.put("display_id", currentDisplay)
            if (operation in listOf("tap", "swipe", "text", "key") && !input.optBoolean("manual")) lastCaptureRotation?.let { command.put("expected_rotation", it) }
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
                    image = if (operation == "release") "" else image.ifBlank { it.image },
                    imageInfo = if (image.isNotBlank()) clean.toString() else it.imageInfo,
                    error = "",
                    lastTouchX = touchX,
                    lastTouchY = touchY,
                    lastTouchTime = touchTs
                )
            }
            if (operation == "create" && result.optBoolean("created")) {
                acquireKeepAwake(base)
                touchActivity()
                if (state.value.apps.isEmpty()) state.update { it.copy(apps = queryLauncherApps(base, false)) }
                startLivePreview(base.applicationContext)
                startIdleWatchdog(base.applicationContext)
                FloatingVirtualDisplay.show(base.applicationContext)
                runCatching {
                    android.os.Handler(android.os.Looper.getMainLooper()).post {
                        runCatching { base.applicationContext.startForegroundService(Intent(base.applicationContext, RootPhoneService::class.java)) }
                    }
                }
            } else if (operation == "release") {
                autoReleaseJob?.cancel()
                idleWatchJob?.cancel()
                stopLivePreview()
                releaseKeepAwake()
                FloatingVirtualDisplay.onWorkspaceStateChanged(base.applicationContext, state.value)
                if (!state.value.active) {
                    runCatching {
                        android.os.Handler(android.os.Looper.getMainLooper()).post {
                            runCatching { base.applicationContext.stopService(Intent(base.applicationContext, RootPhoneService::class.java)) }
                        }
                    }
                }
            } else {
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
            val waitDeadline = SystemClock.uptimeMillis() + 1200L
            while (!PulseAccessibilityService.isConnected() && SystemClock.uptimeMillis() < waitDeadline) {
                try { Thread.sleep(80L) } catch (ignored: InterruptedException) { break }
            }
        }
        // Strictly read Accessibility windows on the virtual display (`displayId > 0`), waiting up to 1.6s if transitioning
        PulseAccessibilityService.dumpDisplayTree(displayId, dispW, dispH, forceRefresh, waitMs = 1600L)?.let {
            return it.put("uid", 0).put("source", "accessibility_windows")
        }
        // If no Accessibility nodes exist on `displayId` (e.g. WeChat on VirtualDisplay), fall back to Root `dumpsys activity top` hierarchy on `displayId`!
        val raw = runCatching {
            privilege?.execute(JSONObject().put("op", "ui_tree_xml").put("display_id", displayId))
        }.getOrNull() ?: JSONObject()
        val topPkg = raw.optString("topPackage", "")
        val xml = raw.optString("xml", "")
        val fallbackNodes = if (xml.isNotBlank()) parseUiautomatorXml(xml, dispW, dispH) else emptyList()
        val snap = PulseAccessibilityService.updateSnapshotFromExternal(displayId, fallbackNodes, topPkg)
        return PulseAccessibilityService.snapshotToJson(snap, fromCache = false)
            .put("uid", 0)
            .put("source", if (fallbackNodes.isNotEmpty()) "root_view_hierarchy" else "empty")
    }

    private fun parseUiautomatorXml(xml: String, screenW: Int, screenH: Int): List<PulseAccessibilityService.CachedNode> {
        val startIdx = xml.indexOf("<hierarchy")
        if (startIdx < 0) return emptyList()
        val cleanXml = xml.substring(startIdx)
        val list = ArrayList<PulseAccessibilityService.CachedNode>(64)
        var nextIdx = 1
        val boundsRegex = Regex("\\[(\\d+),(\\d+)]\\[(\\d+),(\\d+)]")
        runCatching {
            val parser = Xml.newPullParser()
            parser.setInput(StringReader(cleanXml))
            var event = parser.eventType
            while (event != XmlPullParser.END_DOCUMENT && list.size < 120) {
                if (event == XmlPullParser.START_TAG && parser.name == "node") {
                    val cls = parser.getAttributeValue(null, "class").orEmpty()
                    val text = parser.getAttributeValue(null, "text")?.trim().orEmpty()
                    val desc = parser.getAttributeValue(null, "content-desc")?.trim().orEmpty()
                    val resId = parser.getAttributeValue(null, "resource-id")?.substringAfterLast('/')?.trim().orEmpty()
                    val pkg = parser.getAttributeValue(null, "package").orEmpty()
                    val clickable = parser.getAttributeValue(null, "clickable") == "true" || parser.getAttributeValue(null, "long-clickable") == "true"
                    val scrollable = parser.getAttributeValue(null, "scrollable") == "true"
                    val checkable = parser.getAttributeValue(null, "checkable") == "true"
                    val checked = parser.getAttributeValue(null, "checked") == "true"
                    val editable = cls.contains("EditText", ignoreCase = true)
                    val bStr = parser.getAttributeValue(null, "bounds").orEmpty()
                    val m = boundsRegex.find(bStr)
                    if (m != null) {
                        val l = m.groupValues[1].toIntOrNull() ?: 0
                        val t = m.groupValues[2].toIntOrNull() ?: 0
                        val r = m.groupValues[3].toIntOrNull() ?: 0
                        val b = m.groupValues[4].toIntOrNull() ?: 0
                        val rect = Rect(l, t, r, b)
                        if (rect.width() > 2 && rect.height() > 2 && (clickable || editable || scrollable || text.isNotEmpty() || desc.isNotEmpty())) {
                            val cx = rect.centerX().coerceIn(0, (screenW - 1).coerceAtLeast(0))
                            val cy = rect.centerY().coerceIn(0, (screenH - 1).coerceAtLeast(0))
                            val duplicate = list.any { prev ->
                                !clickable && !editable && prev.text == text && prev.desc == desc &&
                                    kotlin.math.abs(prev.centerX - cx) <= 6 && kotlin.math.abs(prev.centerY - cy) <= 6
                            }
                            if (!duplicate) {
                                list.add(
                                    PulseAccessibilityService.CachedNode(
                                        ref = "@e${nextIdx++}",
                                        role = PulseAccessibilityService.simplifyRole(cls, editable, checkable),
                                        text = text.take(120),
                                        desc = desc.take(120),
                                        hint = "",
                                        resourceId = resId,
                                        pkg = pkg,
                                        bounds = rect,
                                        centerX = cx,
                                        centerY = cy,
                                        clickable = clickable,
                                        editable = editable,
                                        scrollable = scrollable,
                                        checkable = checkable,
                                        checked = checked
                                    )
                                )
                            }
                        }
                    }
                }
                event = parser.next()
            }
        }
        return list
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
        // 1. Exact package match or exact name match
        apps.firstOrNull { it.component.substringBefore("/").lowercase() == qLower || it.name.lowercase() == qLower }?.let {
            resolvedComponentCache[qTrim] = it.component
            return it.component
        }
        // 2. Substring match on app name or package name
        apps.firstOrNull { it.name.lowercase().contains(qLower) || it.component.lowercase().contains(qLower) }?.let {
            resolvedComponentCache[qTrim] = it.component
            return it.component
        }
        // 3. Fallback via PackageManager getLaunchIntentForPackage
        base.packageManager.getLaunchIntentForPackage(qTrim)?.component?.let {
            val comp = it.packageName + "/" + it.className
            resolvedComponentCache[qTrim] = comp
            return comp
        }
        return qTrim
    }

    private fun startLivePreview(appContext: Context) {
        if (previewJob?.isActive == true) return
        lastPreviewSequence = -1L
        previewJob = scope.launch {
            while (isActive) {
                delay(72L)
                val disp = runCatching { JSONObject(state.value.display) }.getOrNull()
                if (disp == null || !disp.optBoolean("created")) break
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
        initialize(base); state.update { it.copy(phase = "stopping") }; val active = job
        if (active?.isActive == true) { active.cancel(); agent?.cancel() }
        else scope.launch {
            runCatching { command(base, JSONObject().put("op", "release")) }
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
    private fun append(message: PulseMessage) { state.update { it.copy(messages = (it.messages + message).takeLast(600)) }; broadcast() }
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
