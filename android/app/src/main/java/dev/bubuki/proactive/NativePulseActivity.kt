package dev.bubuki.proactive

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.content.res.Configuration
import android.net.Uri
import android.graphics.BitmapFactory
import android.os.Bundle
import android.os.SystemClock
import android.util.Base64
import android.window.BackEvent
import android.window.OnBackAnimationCallback
import android.window.OnBackInvokedCallback
import android.window.OnBackInvokedDispatcher
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import kotlinx.coroutines.*
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.concurrent.Executors
import java.util.concurrent.Future

/** Private native app root; public launch parameters are filtered by LauncherActivity. */
class NativePulseActivity : ComponentActivity() {
    internal lateinit var model: NativePulseModel
    private var resumed = false
    private var poll: Job? = null
    private var backHandler: OnBackInvokedCallback? = null
    private val documentPicker = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri -> model.documentPicked(uri) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (android.os.Build.VERSION.SDK_INT >= 33 && checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) != android.content.pm.PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(android.Manifest.permission.POST_NOTIFICATIONS), 1001)
        }
        model = NativePulseModel(this, savedInstanceState?.getString("chat_bot") ?: intent.getStringExtra("openBot"))
        model.restorePicker(savedInstanceState)
        setContent { NativePulseScreen(model) }
        if (android.os.Build.VERSION.SDK_INT >= 33) {
            val callback = if (android.os.Build.VERSION.SDK_INT >= 34) object : OnBackAnimationCallback {
                override fun onBackStarted(event: BackEvent) { if (model.ui.chatOpen && model.ui.menu == null && model.ui.activePanel == null) model.predictiveBack = 0f }
                override fun onBackProgressed(event: BackEvent) { if (model.predictiveBack != null) model.predictiveBack = event.progress }
                override fun onBackCancelled() { model.cancelBack() }
                override fun onBackInvoked() { handleNativeBack() }
            } else OnBackInvokedCallback { handleNativeBack() }
            backHandler = callback
            getOnBackInvokedDispatcher().registerOnBackInvokedCallback(OnBackInvokedDispatcher.PRIORITY_DEFAULT, callback)
        } else onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() { handleNativeBack() }
        })
    }
    fun pickDocument() { documentPicker.launch(arrayOf("*/*")) }
    override fun onNewIntent(intent: Intent) { super.onNewIntent(intent); setIntent(intent); intent.getStringExtra("openBot")?.let(model::requestOpenBot) }
    @Deprecated("Legacy detail-page result bridge")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == 740 && resultCode == RESULT_OK) data?.getStringExtra("createdBot")?.let(model::requestOpenBot)
    }

    private fun handleNativeBack() {
        when {
            model.ui.menu != null -> model.dismissMenu()
            model.ui.activePanel != null -> model.closePanel()
            model.ui.searching -> model.search(null)
            model.ui.chatOpen -> model.back()
            else -> finish()
        }
    }

    override fun onResume() {
        super.onResume(); resumed = true
        model.resume()
        poll?.cancel()
        poll = model.scope.launch {
            var tick = 0
            while (isActive && resumed) {
                val wsOnline = AgentForegroundService.connected
                val fast = model.ui.busyBot != null || model.ui.workingBot != null || model.ui.streamBotId != null || model.ui.activePanel == "cloud_desktop"
                if (model.ui.activePanel == "cloud_desktop") {
                    model.refreshStreamOnly()
                    delay(360L)
                } else if (fast) {
                    model.refreshStreamOnly()
                    delay(if (wsOnline) 1500L else 420L)
                } else {
                    if (tick % 3 == 0) {
                        model.refresh()
                    } else {
                        model.refreshStreamOnly()
                    }
                    tick++
                    delay(if (wsOnline) 3200L else 1800L)
                }
            }
        }
    }

    override fun onPause() { resumed = false; poll?.cancel(); model.pause(); model.persistDrafts(); super.onPause() }
    override fun onSaveInstanceState(outState: Bundle) { if (model.ui.chatOpen) outState.putString("chat_bot", model.ui.botId); model.savePicker(outState); super.onSaveInstanceState(outState) }
    override fun onDestroy() {
        if (android.os.Build.VERSION.SDK_INT >= 33) backHandler?.let { getOnBackInvokedDispatcher().unregisterOnBackInvokedCallback(it) }
        model.close(); super.onDestroy()
    }
}

internal data class PulseBot(
    val id: String, val name: String, val description: String, val color: String, val shape: String,
    val engine: String, val pinned: Boolean, val hidden: Boolean, val pinnedAt: Double, val avatarVersion: String,
    val instructions: String = "", val model: String = "", val curiosityEnabled: Boolean = false, val notify: Boolean = true
) {
    fun json() = JSONObject().put("id", id).put("name", name).put("description", description).put("color", color).put("shape", shape)
        .put("engine", engine).put("pinned", pinned).put("hidden", hidden).put("pinned_at", pinnedAt).put("avatar_version", avatarVersion)
        .put("instructions", instructions).put("model", model).put("curiosity_enabled", curiosityEnabled).put("notify", notify)
    companion object {
        fun from(j: JSONObject) = PulseBot(
            j.getString("id"), j.optString("name"), j.optString("description"),
            j.optString("color", "#7C5CFF"), j.optString("shape", "circle"), j.optString("engine", "small"),
            j.optBoolean("pinned"), j.optBoolean("hidden"), j.optDouble("pinned_at", 0.0), j.optString("avatar_version"),
            j.optString("instructions", ""), j.optString("model", ""), j.optBoolean("curiosity_enabled", j.optString("id") == "companion"),
            j.optBoolean("notify", true)
        )
    }
}

internal data class PulseAttachment(val name: String, val path: String, val source: String, val bytes: Long = 0, val sha256: String = "") {
    fun json() = JSONObject().put("name", name).put("path", path).put("source", source).put("bytes", bytes).put("sha256", sha256)
    companion object { fun from(j: JSONObject) = PulseAttachment(j.optString("name"), j.getString("path"), j.optString("source", "workspace"), j.optLong("bytes"), j.optString("sha256")) }
}

internal data class PulseMessage(
    val id: String, val botId: String, val role: String, val text: String, val kind: String, val engine: String,
    val created: Double, val decision: String, val trace: String, val optimistic: Boolean = false,
    val fromBotId: String = "", val fromBotName: String = ""
) {
    companion object {
        fun from(j: JSONObject) = PulseMessage(
            j.getString("id"), j.optString("bot_id", "companion"), j.optString("role"),
            j.optString("text"), j.optString("kind"), j.optString("engine"), j.optDouble("created_at"),
            j.optString("decision"), (j.optJSONArray("trace") ?: JSONArray()).toString(), false,
            j.optString("from_bot_id", ""), j.optString("from_bot_name", "")
        )
    }
}

internal data class PulseMenuItem(val title: String, val description: String = "", val icon: String = "next", val run: () -> Unit)
internal data class PulseMenu(val title: String, val items: List<PulseMenuItem>)

internal data class NativePulseState(
    val bots: List<PulseBot> = emptyList(), val messages: List<PulseMessage> = emptyList(), val unread: Set<String> = emptySet(),
    val online: Boolean = false, val paired: Boolean = true, val latency: Long = 0, val primary: String = "companion",
    val botId: String = "companion", val chatOpen: Boolean = false, val busyBot: String? = null,
    val workingBot: String? = null, val computerBot: String? = null, val workingText: String = "", val mode: String = "auto", val draft: String = "",
    val reply: PulseMessage? = null, val attachments: List<PulseAttachment> = emptyList(), val uploading: Boolean = false,
    val searching: Boolean = false, val query: String = "", val snoozed: Boolean = false,
    val smallReady: Boolean = false, val bigReady: Boolean = false, val smallModel: String = "", val bigModel: String = "",
    val defaultModel: String = "",
    val dark: Boolean = false, val motion: Boolean = true, val haptics: Boolean = true, val routineCount: Int = 0,
    val avatars: Map<String, ImageBitmap> = emptyMap(), val menu: PulseMenu? = null, val toast: String = "", val toastId: Long = 0,
    // Native panels: null | "settings" | "bot_edit" | "vault"
    val activePanel: String? = null,
    // Settings panel state
    val serverUrl: String = SecureTransport.DEFAULT_SERVER,
    val apiBaseUrl: String = "https://api.openai.com/v1",
    val apiKeyInput: String = "",
    val hasApiKey: Boolean = false,
    val availableModels: List<String> = emptyList(),
    val fetchingModels: Boolean = false,
    val savingSettings: Boolean = false,
    val curiosityEnabledGlobal: Boolean = true,
    val curiosityMinutes: String = "90",
    val backgroundService: Boolean = true,
    val appearance: String = "system",
    // Bot Editor state
    val editingBotId: String? = null,
    val editBotName: String = "",
    val editBotDesc: String = "",
    val editBotModel: String = "",
    val editBotInstructions: String = "",
    val editBotColor: String = "#7C5CFF",
    val editBotShape: String = "flower",
    val editBotCuriosity: Boolean = false,
    val editBotNotify: Boolean = true,
    val savingBot: Boolean = false,
    // Vault panel state
    val vaultFiles: List<String> = emptyList(),
    val selectedVaultPath: String = "INDEX.md",
    val vaultContent: String = "",
    val vaultLoading: Boolean = false,
    val vaultSaving: Boolean = false,
    // Live SSE streaming state
    val streamBotId: String? = null,
    val streamSpeakerId: String? = null,
    val streamSpeakerName: String = "",
    val streamText: String = "",
    val streamThought: String = "",
    val streamTool: String = "",
    val streamTrace: List<JSONObject> = emptyList(),
    val streamStartedAt: Long = 0L,
    val activeTypewriterMsgId: String? = null,
    val activeTypewriterChars: Int = Int.MAX_VALUE,
    // Native Cloud Desktop state
    val cloudDesktopFrame: ImageBitmap? = null,
    val cloudDesktopHash: String = "",
    val cloudDesktopUrl: String = "",
    val cloudDesktopTitle: String = "",
    val cloudDesktopLoading: Boolean = false,
    val cloudDesktopTapNorm: Pair<Float, Float>? = null,
    val cloudDesktopLiveBotId: String? = null,
    val cloudDesktopLiveOp: String = "",
    val cloudDesktopPipVisible: Boolean = false,
    // Native Schedules & Automation panel state
    val schedulesData: JSONObject? = null,
    val schedulesLoading: Boolean = false
)

internal class NativePulseModel(private val activity: NativePulseActivity, restoredBot: String?) {
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    var ui by mutableStateOf(NativePulseState(botId = restoredBot ?: "companion", chatOpen = restoredBot != null)); private set
    var predictiveBack by mutableStateOf<Float?>(null)
    var backFrom: Float? = null
    var backSettlement by mutableStateOf(0)
    var navigationRunning = false
    private var prefs = JSONObject()
    private val drafts = mutableMapOf<String, String>()
    private val attachmentDrafts = mutableMapOf<String, List<PulseAttachment>>()
    private var localRevision = 0
    private var pendingOpenBot: String? = restoredBot
    private var pickerBot: String? = null
    private var pickerSource: String? = null
    private val draftLock = Any()
    private val draftWriter = Executors.newSingleThreadExecutor { task -> Thread(task, "native-drafts").apply { priority = Thread.MIN_PRIORITY } }
    private var draftJob: Job? = null
    private var revision = 0
    private var active = false
    private val refreshLock = Mutex()
    private val streamLock = Mutex()
    private var lastStreamSeq: Long = 0L
    private var currentTurnId: String = ""
    private val completedTurnIds = LinkedHashSet<String>()
    private var sendGuardUntilUptime: Long = 0L
    private var sendGuardBotId: String? = null
    private var typewriterJob: Job? = null
    private var streamPacerJob: Job? = null
    private var targetStreamFullText: String = ""
    private val avatarLoading = mutableSetOf<String>()
    private val avatarVersions = mutableMapOf<String, String>()
    private val optimisticList = mutableListOf<PulseMessage>()
    private var inFlightSends = 0
    private var remoteMessages: List<PulseMessage> = emptyList()
    private var remoteWorkingBot: String? = null
    private var remoteWorkingText = ""
    private val clock = SimpleDateFormat("HH:mm", Locale.CHINA).apply { timeZone = TimeZone.getTimeZone("Asia/Shanghai") }
    private val calendar = SimpleDateFormat("M月d日", Locale.CHINA).apply { timeZone = TimeZone.getTimeZone("Asia/Shanghai") }

    companion object {
        @Volatile private var activeInstance: NativePulseModel? = null
        @JvmStatic fun dispatchRealtimeEvent(event: JSONObject) {
            activeInstance?.onRealtimeEvent(event)
        }
    }

    init {
        activeInstance = this
        prefs = runCatching { UiPreferences.decorate(activity, SecurePrefs.read(activity)) }.getOrElse { JSONObject() }
        val stored = prefs.optJSONObject("drafts") ?: JSONObject()
        stored.keys().forEach { drafts[it] = stored.optString(it) }
        readAttachments(prefs)
        val cached = homeCache(prefs)
        remoteMessages = cached.third
        val initialBot = cached.first.firstOrNull { it.id == restoredBot }?.id ?: cached.second
        val dark = when (prefs.optString("appearance", "system")) {
            "dark" -> true; "light" -> false
            else -> activity.resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK == Configuration.UI_MODE_NIGHT_YES
        }
        val cfg = prefs.optJSONObject("config") ?: JSONObject()
        val defModel = cfg.optString("default_model").ifBlank { cfg.optJSONObject("big_brain")?.optString("model").orEmpty().ifBlank { cfg.optJSONObject("small_brain")?.optString("model").orEmpty() } }
        val defUrl = cfg.optString("api_base_url").ifBlank { cfg.optJSONObject("big_brain")?.optString("base_url").orEmpty().ifBlank { "https://api.openai.com/v1" } }
        ui = ui.copy(
            bots = cached.first, primary = cached.second, botId = initialBot,
            messages = combinedMessages(),
            chatOpen = restoredBot != null && cached.first.any { it.id == restoredBot },
            dark = dark, paired = prefs.optString("deviceToken", SecureTransport.defaultToken()).isNotBlank(),
            draft = draft(initialBot), attachments = attachments(initialBot), mode = draftMode(initialBot),
            defaultModel = defModel, apiBaseUrl = defUrl, serverUrl = prefs.optString("serverUrl", SecureTransport.DEFAULT_SERVER),
            backgroundService = prefs.optBoolean("background", true), appearance = prefs.optString("appearance", "system"),
            motion = prefs.optBoolean("motion", true) && !animationsOff(activity), haptics = prefs.optBoolean("haptics", true)
        )
        activity.systemBarsFor(dark)
        RootPhoneHarness.initialize(activity)
        scope.launch { RootPhoneHarness.changes.collect { applyPhoneState() } }
    }

    /**
     * Strictly preserves server chronological order for remoteMessages and appends in-flight optimisticList
     * items at the tail so clock skew between phone and VPS NEVER causes AI messages to jump above user messages.
     */
    private fun combinedMessages(): List<PulseMessage> {
        val phoneMsgs = RootPhoneHarness.changes.value.messages.filter { it.role != "user" && it.kind != "phone_task" }
        val baseList = if (phoneMsgs.isEmpty()) {
            remoteMessages
        } else {
            (remoteMessages + phoneMsgs).distinctBy { it.id }.sortedBy { it.created }
        }
        if (optimisticList.isEmpty()) return baseList
        val existingIds = baseList.asSequence().map { it.id }.toHashSet()
        val tailOptimistic = optimisticList.filter { opt ->
            opt.id !in existingIds && baseList.takeLast(12).none {
                it.role == "user" && it.botId == opt.botId && it.text.trim() == opt.text.trim()
            }
        }
        return baseList + tailOptimistic
    }

    private fun applyPhoneState() {
        val phone = RootPhoneHarness.changes.value
        ui = ui.copy(messages = combinedMessages(), workingBot = if (phone.active) phone.botId else remoteWorkingBot,
            workingText = if (phone.active) when (phone.phase) { "waiting_user" -> "手机工作区需要你接管"; "stopping" -> "正在停止手机执行…"; else -> "正在操作独立手机工作区…" } else remoteWorkingText)
    }

    private fun scopeOf(settings: JSONObject): String {
        val value = settings.optString("serverUrl", SecureTransport.DEFAULT_SERVER).trim().trimEnd('/') + "\u0000" + settings.optString("publicKey") + "\u0000" + settings.optString("deviceToken", SecureTransport.defaultToken())
        return java.security.MessageDigest.getInstance("SHA-256").digest(value.toByteArray()).joinToString("") { "%02x".format(it.toInt() and 255) }
    }
    private fun homeCache(settings: JSONObject): Triple<List<PulseBot>, String, List<PulseMessage>> {
        val cache = settings.optJSONObject("nativeHomeCache")
        if (cache?.optString("scope") == scopeOf(settings)) runCatching {
            val bots = cache.getJSONArray("bots").objects().map(PulseBot::from)
            val primary = cache.getString("primary")
            val msgs = (cache.optJSONArray("messages") ?: JSONArray()).objects().map(PulseMessage::from)
            return Triple(bots, primary, msgs)
        }
        return Triple(
            listOf(
                PulseBot("companion", "白kawa", "主助理 · 聊聊近况、把关全局，并统筹安排其它 Bot 干活。", "#7C5CFF", "flower", "small", false, false, 0.0, "", curiosityEnabled = true),
                PulseBot("research", "研究助手", "负责查资料、跑脚本、写文件，带着可验收成果汇报。", "#FF8A3D", "circle", "big", false, false, 0.0, "", curiosityEnabled = false)
            ),
            "companion",
            emptyList()
        )
    }
    private fun cacheRoster(bots: List<PulseBot>, primary: String, serverScope: String = scopeOf(prefs)) {
        val recentMsgsJson = JSONArray()
        remoteMessages.takeLast(80).forEach { m ->
            recentMsgsJson.put(
                JSONObject()
                    .put("id", m.id).put("bot_id", m.botId).put("role", m.role)
                    .put("text", m.text).put("kind", m.kind).put("engine", m.engine)
                    .put("created_at", m.created).put("decision", m.decision)
                    .put("trace", runCatching { JSONArray(m.trace) }.getOrDefault(JSONArray()))
                    .put("from_bot_id", m.fromBotId).put("from_bot_name", m.fromBotName)
            )
        }
        val cache = JSONObject().put("scope", serverScope).put("primary", primary)
            .put("bots", JSONArray(bots.map { it.json() }))
            .put("messages", recentMsgsJson)
        draftWriter.submit { synchronized(SecurePrefs::class.java) {
            val fresh = SecurePrefs.read(activity)
            if (scopeOf(fresh) == serverScope) SecurePrefs.save(activity, fresh.put("nativeHomeCache", cache))
        } }
    }
    private fun readAttachments(settings: JSONObject) {
        val stored = settings.optJSONObject("attachmentDrafts") ?: JSONObject()
        synchronized(draftLock) { stored.keys().forEach { id -> attachmentDrafts[id] = (stored.optJSONArray(id) ?: JSONArray()).objects().map(PulseAttachment::from) } }
    }
    private fun attachments(id: String) = synchronized(draftLock) { attachmentDrafts[id] ?: emptyList() }
    private fun draftMode(id: String): String = attachments(id).firstOrNull()?.let { if (it.source == "computer") "computer" else "auto" } ?: "auto"

    private suspend fun request(method: String, path: String, body: JSONObject? = null, query: JSONObject? = null) =
        withContext(Dispatchers.IO) { SecureTransport(activity).request(method, path, body, query) }

    fun resume() {
        active = true
        activeInstance = this
        scope.launch {
            val before = localRevision; val previousScope = scopeOf(prefs)
            val settings = withContext(Dispatchers.IO) { UiPreferences.decorate(activity, SecurePrefs.read(activity)) }
            prefs = settings
            if (before == localRevision) synchronized(draftLock) {
                val stored = settings.optJSONObject("drafts") ?: JSONObject()
                stored.keys().forEach { drafts[it] = stored.optString(it) }
                readAttachments(settings)
            }
            if (previousScope != scopeOf(settings)) {
                val cached = homeCache(settings); avatarVersions.clear()
                remoteMessages = cached.third
                ui = ui.copy(bots = cached.first, primary = cached.second, botId = cached.second, messages = combinedMessages(), chatOpen = false, avatars = emptyMap())
            }
            val dark = when (settings.optString("appearance", "system")) {
                "dark" -> true; "light" -> false
                else -> activity.resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK == Configuration.UI_MODE_NIGHT_YES
            }
            val config = settings.optJSONObject("config") ?: JSONObject()
            val smallM = config.optJSONObject("small_brain")?.optString("model") ?: ""
            val bigM = config.optJSONObject("big_brain")?.optString("model") ?: ""
            val defM = config.optString("default_model").ifBlank { bigM.ifBlank { smallM } }
            ui = ui.copy(
                paired = settings.optString("deviceToken", SecureTransport.defaultToken()).isNotBlank(), dark = dark,
                motion = settings.optBoolean("motion", true) && !animationsOff(activity), haptics = settings.optBoolean("haptics", true),
                smallModel = smallM, bigModel = bigM, defaultModel = defM,
                draft = draft(ui.botId), attachments = attachments(ui.botId),
                mode = if (attachments(ui.botId).isNotEmpty()) draftMode(ui.botId) else ui.mode
            )
            activity.systemBarsFor(dark)
            if (active) {
                if (settings.optBoolean("background", true) && ui.paired && !AgentForegroundService.running) {
                    try { activity.startForegroundService(Intent(activity, AgentForegroundService::class.java)) } catch (e: Exception) { toast(MainActivity.safeError(e)) }
                } else if (!settings.optBoolean("background", true)) activity.stopService(Intent(activity, AgentForegroundService::class.java))
            }
            refresh()
        }
    }

    private fun isStreamPlaceholder(t: String): Boolean {
        return t.isBlank() || t == "正在思考…" || t == "正在思考与执行任务…" || t == "正在整理回复…" || t.startsWith("正在调用 ") || t.startsWith("⏰ 正在执行") || t.startsWith("📦 ")
    }

    private fun markTurnCompleted(turnId: String) {
        if (turnId.isBlank()) return
        completedTurnIds.add(turnId)
        while (completedTurnIds.size > 240) {
            val first = completedTurnIds.firstOrNull() ?: break
            completedTurnIds.remove(first)
        }
    }

    /**
     * Atomically merges incoming messages (from full `/api/messages`, `/api/status` `recent_tail`,
     * `/api/chat` response, or WebSocket `message` push) so newly arrived messages are NEVER lost
     * if an older high-latency (3000ms+) HTTP response lands out of order.
     */
    private fun mergeRemoteMessages(incoming: List<PulseMessage>, fullReplace: Boolean = false) {
        if (incoming.isEmpty() && !fullReplace) return
        val oldIds = remoteMessages.asSequence().map { it.id }.toHashSet()
        val merged = if (fullReplace) {
            val incomingIds = incoming.asSequence().map { it.id }.toHashSet()
            val maxIncomingCreated = incoming.maxOfOrNull { it.created } ?: 0.0
            val newerLocal = remoteMessages.filter { local ->
                local.id !in incomingIds && local.created >= maxIncomingCreated - 1.0
            }
            incoming + newerLocal
        } else {
            val byId = LinkedHashMap<String, PulseMessage>(remoteMessages.size + incoming.size)
            remoteMessages.forEach { byId[it.id] = it }
            incoming.forEach { byId[it.id] = it }
            byId.values.toList()
        }
        remoteMessages = merged
        optimisticList.removeAll { opt ->
            merged.takeLast(18).any { it.role == "user" && it.botId == opt.botId && it.text.trim() == opt.text.trim() }
        }
        // If a brand-new assistant message just arrived for the active bot (or finished a stream under 3000ms latency),
        // smoothly typewriter-reveal any unrevealed suffix so it NEVER pops in as an abrupt wall of text!
        if (!fullReplace && oldIds.isNotEmpty()) {
            val newestAssistant = incoming.lastOrNull { it.role == "assistant" && it.id !in oldIds && it.text.length > 6 }
            if (newestAssistant != null && newestAssistant.id == merged.lastOrNull { it.botId == newestAssistant.botId }?.id) {
                if (sendGuardBotId == newestAssistant.botId) {
                    sendGuardBotId = null
                    sendGuardUntilUptime = 0L
                }
                val curStream = ui.streamText
                val alreadyRevealed = if (!isStreamPlaceholder(curStream) && curStream.isNotBlank() &&
                    newestAssistant.text.startsWith(curStream.take(minOf(16, curStream.length)))) {
                    curStream.length.coerceAtMost(newestAssistant.text.length)
                } else {
                    0
                }
                if (alreadyRevealed < newestAssistant.text.length - 6) {
                    startMessageTypewriter(newestAssistant.id, newestAssistant.text.length, alreadyRevealed.coerceAtLeast(2))
                }
            }
        }
    }

    private fun startMessageTypewriter(msgId: String, totalLength: Int, startChars: Int) {
        streamPacerJob?.cancel()
        targetStreamFullText = ""
        typewriterJob?.cancel()
        if (totalLength - startChars <= 14) {
            ui = ui.copy(activeTypewriterMsgId = null, activeTypewriterChars = Int.MAX_VALUE)
            return
        }
        var current = startChars.coerceIn(1, totalLength)
        ui = ui.copy(activeTypewriterMsgId = msgId, activeTypewriterChars = current)
        typewriterJob = scope.launch {
            while (isActive && current < totalLength && ui.activeTypewriterMsgId == msgId) {
                val remaining = totalLength - current
                val step = when {
                    remaining <= 10 -> 1
                    remaining <= 28 -> 2
                    remaining <= 70 -> 4
                    else -> minOf(12, remaining / 8 + 2)
                }
                current = (current + step).coerceAtMost(totalLength)
                ui = ui.copy(activeTypewriterChars = current)
                delay(16L)
            }
            if (ui.activeTypewriterMsgId == msgId) {
                ui = ui.copy(activeTypewriterMsgId = null, activeTypewriterChars = Int.MAX_VALUE)
            }
        }
    }

    private fun applyPacedStreamText(sBot: String?, resolvedText: String): String {
        if (sBot == null) {
            streamPacerJob?.cancel()
            targetStreamFullText = ""
            return ""
        }
        if (isStreamPlaceholder(resolvedText)) {
            if (targetStreamFullText.isNotEmpty() && !isStreamPlaceholder(targetStreamFullText)) {
                return ui.streamText.takeIf { !isStreamPlaceholder(it) && it.isNotBlank() } ?: targetStreamFullText.take(2)
            }
            streamPacerJob?.cancel()
            targetStreamFullText = ""
            return resolvedText
        }
        if (resolvedText.length >= targetStreamFullText.length || !targetStreamFullText.startsWith(resolvedText)) {
            targetStreamFullText = resolvedText
        }
        val curShown = if (isStreamPlaceholder(ui.streamText)) "" else ui.streamText
        val initialLen = curShown.length.coerceAtLeast(minOf(2, targetStreamFullText.length))
        if (streamPacerJob?.isActive != true) {
            streamPacerJob = scope.launch {
                while (isActive && ui.streamBotId != null && targetStreamFullText.isNotEmpty()) {
                    val cur = if (isStreamPlaceholder(ui.streamText)) 0 else ui.streamText.length
                    val target = targetStreamFullText
                    if (cur >= target.length) {
                        delay(16L)
                        continue
                    }
                    val rem = target.length - cur
                    val step = when {
                        rem <= 8 -> 1
                        rem <= 24 -> 2
                        rem <= 60 -> 3
                        else -> minOf(10, rem / 8 + 2)
                    }
                    val nextLen = (cur + step).coerceAtMost(target.length)
                    ui = ui.copy(streamText = target.take(nextLen))
                    delay(16L)
                }
            }
        }
        return targetStreamFullText.take(initialLen)
    }

    /**
     * Prevents sentences from popping up and disappearing during multi-step tool calls or 3000ms latency jitter.
     */
    private fun resolveStreamText(targetBot: String?, turnId: String, rawStreamText: String): String {
        if (targetBot == null) return ""
        val prevBot = ui.streamBotId ?: ui.workingBot
        val prevFull = targetStreamFullText.takeIf { it.isNotBlank() && !isStreamPlaceholder(it) } ?: ui.streamText
        val sameTurn = turnId.isBlank() || currentTurnId.isBlank() || turnId == currentTurnId || currentTurnId.startsWith("pending-")
        if (turnId.isNotBlank() && turnId != currentTurnId) {
            if (!currentTurnId.startsWith("pending-")) {
                targetStreamFullText = ""
            }
            currentTurnId = turnId
        }
        if (isStreamPlaceholder(rawStreamText)) {
            return if (prevBot == targetBot && sameTurn && !isStreamPlaceholder(prevFull)) prevFull else rawStreamText
        }
        val isThought = rawStreamText.startsWith("💭 ")
        val prevWasThought = prevFull.startsWith("💭 ")
        if (prevBot == targetBot && sameTurn && !isStreamPlaceholder(prevFull) && !prevWasThought) {
            if (isThought) return prevFull
            if (rawStreamText.length < prevFull.length && prevFull.startsWith(rawStreamText)) {
                return prevFull
            }
        }
        return rawStreamText
    }

    fun onRealtimeEvent(event: JSONObject) {
        scope.launch {
            val type = event.optString("type")
            when (type) {
                "cloud_desktop_frame" -> {
                    val frameObj = event.optJSONObject("frame") ?: return@launch
                    val botId = event.optString("bot_id").takeIf { it.isNotBlank() }
                    val op = event.optString("op", "")
                    applyCloudDesktopPacket(frameObj)
                    ui = ui.copy(
                        cloudDesktopLiveBotId = botId ?: ui.cloudDesktopLiveBotId,
                        cloudDesktopLiveOp = op.ifBlank { ui.cloudDesktopLiveOp },
                        cloudDesktopPipVisible = true
                    )
                }
                "stream" -> {
                    val stream = event.optJSONObject("stream") ?: return@launch
                    val seq = stream.optLong("seq", 0L)
                    val turnId = stream.optString("turn_id", "")
                    if (turnId.isNotBlank() && turnId in completedTurnIds) return@launch
                    if (seq in 1 until lastStreamSeq) return@launch
                    if (seq > lastStreamSeq) lastStreamSeq = seq
                    val sBot = stream.optString("bot_id").takeIf { it.isNotBlank() } ?: return@launch
                    val toolName = stream.optString("tool", "")
                    val trace = stream.optJSONArray("trace")?.objects() ?: ui.streamTrace
                    val usesCloudDesktop = toolName == "cloud_desktop" || trace.any { it.optString("tool") == "cloud_desktop" }
                    if (usesCloudDesktop) {
                        ui = ui.copy(cloudDesktopLiveBotId = sBot, cloudDesktopPipVisible = true)
                        launch { refreshCloudDesktopFrame(forceFull = false) }
                    }
                    // If user is currently viewing a specific Bot's chat, ignore unrelated background Bot stream events
                    if (ui.chatOpen && sBot != ui.botId && ui.streamBotId == ui.botId) return@launch
                    val rawText = stream.optString("text", "")
                    val thought = stream.optString("thought", "")
                    val preserved = resolveStreamText(sBot, turnId, rawText)
                    val paced = applyPacedStreamText(sBot, preserved)
                    val startedMs = ui.streamStartedAt.takeIf { it > 0L } ?: System.currentTimeMillis()
                    ui = ui.copy(
                        online = true,
                        streamBotId = sBot,
                        streamSpeakerId = stream.optString("speaker_id").takeIf { it.isNotBlank() },
                        streamSpeakerName = stream.optString("speaker_name", ""),
                        streamText = paced,
                        streamThought = thought,
                        streamTool = toolName,
                        streamTrace = trace,
                        streamStartedAt = startedMs
                    )
                }
                "execution" -> {
                    val exec = event.optJSONObject("execution")
                    val working = exec?.optString("bot_id")?.takeIf { it.isNotBlank() }
                    val tool = exec?.optString("tool").orEmpty()
                    val trace = exec?.optJSONArray("trace")?.objects() ?: ui.streamTrace
                    val usesCloudDesktop = tool == "cloud_desktop" || trace.any { it.optString("tool") == "cloud_desktop" }
                    if (usesCloudDesktop) {
                        ui = ui.copy(cloudDesktopLiveBotId = working ?: ui.botId, cloudDesktopPipVisible = true)
                        launch { refreshCloudDesktopFrame(forceFull = false) }
                    }
                    if (ui.chatOpen && working != null && working != ui.botId && ui.workingBot == ui.botId) return@launch
                    remoteWorkingBot = working
                    remoteWorkingText = if (tool.isNotBlank()) "正在调用 $tool…" else "正在思考…"
                    val activeBot = working ?: ui.workingBot ?: ui.streamBotId ?: sendGuardBotId
                    ui = ui.copy(
                        online = true,
                        workingBot = activeBot,
                        streamBotId = ui.streamBotId ?: activeBot,
                        workingText = remoteWorkingText,
                        streamText = ui.streamText.ifBlank { remoteWorkingText },
                        streamTool = tool,
                        streamTrace = trace
                    )
                }
                "message" -> {
                    val mObj = event.optJSONObject("message") ?: return@launch
                    val msg = runCatching { PulseMessage.from(mObj) }.getOrNull() ?: return@launch
                    val seq = event.optLong("seq", mObj.optLong("seq", 0L))
                    val turnId = mObj.optString("turn_id", "")
                    if (seq > lastStreamSeq) lastStreamSeq = seq
                    if (turnId.isNotBlank()) markTurnCompleted(turnId)
                    mergeRemoteMessages(listOf(msg), fullReplace = false)
                    val finishedStreamHere = msg.role == "assistant" && (ui.streamBotId == msg.botId || ui.workingBot == msg.botId || sendGuardBotId == msg.botId) &&
                        (turnId.isBlank() || currentTurnId.isBlank() || turnId == currentTurnId || msg.kind in listOf("chat", "result", "curiosity", "error", "stopped"))
                    if (finishedStreamHere) {
                        remoteWorkingBot = null
                        sendGuardBotId = null
                        sendGuardUntilUptime = 0L
                        streamPacerJob?.cancel()
                        targetStreamFullText = ""
                        ui = ui.copy(
                            messages = combinedMessages(),
                            workingBot = null,
                            streamBotId = null,
                            streamText = "",
                            streamThought = "",
                            streamTool = "",
                            streamTrace = emptyList(),
                            streamStartedAt = 0L
                        )
                    } else {
                        ui = ui.copy(messages = combinedMessages())
                    }
                    acknowledge()
                }
            }
        }
    }

    suspend fun refresh(force: Boolean = false) {
        if (!ui.paired || (!force && refreshLock.isLocked)) return
        refreshLock.withLock {
            val before = revision; val started = SystemClock.uptimeMillis(); val serverScope = scopeOf(prefs)
            try {
                val fetched = coroutineScope {
                    val b = async { request("GET", "/api/bots") }
                    val h = async { request("GET", "/api/messages") }
                    val s = async { request("GET", "/api/status") }
                    val c = async { runCatching { request("GET", "/api/config") }.getOrNull() }
                    listOf(b.await(), h.await(), s.await(), c.await())
                }
                if (serverScope != withContext(Dispatchers.IO) { scopeOf(SecurePrefs.read(activity)) }) return@withLock
                val roster = fetched[0]!!
                val history = fetched[1]!!
                val status = fetched[2]!!
                val remoteCfg = fetched[3]
                val pending = history.optJSONObject("pending") ?: JSONObject()
                val unread = pending.keys().asSequence().toSet()
                val messages = history.getJSONArray("messages").objects().map(PulseMessage::from)
                mergeRemoteMessages(messages, fullReplace = true)
                status.optJSONArray("recent_tail")?.objects()?.mapNotNull { runCatching { PulseMessage.from(it) }.getOrNull() }?.let {
                    mergeRemoteMessages(it, fullReplace = false)
                }
                val lastDone = status.optJSONObject("last_completed_stream")
                val lastDoneTurn = lastDone?.optString("turn_id").orEmpty()
                if (lastDoneTurn.isNotBlank()) markTurnCompleted(lastDoneTurn)

                val statusSeq = status.optLong("seq", 0L)
                val isStaleStatus = statusSeq in 1 until lastStreamSeq
                if (statusSeq > lastStreamSeq) lastStreamSeq = statusSeq

                val execsByBot = status.optJSONObject("executions_by_bot")
                val streamsByBot = status.optJSONObject("streams_by_bot")
                val execution = if (isStaleStatus) null else (if (ui.chatOpen && execsByBot != null) execsByBot.optJSONObject(ui.botId) else status.optJSONObject("execution"))
                val stream = if (isStaleStatus) null else (if (ui.chatOpen && streamsByBot != null) streamsByBot.optJSONObject(ui.botId) else status.optJSONObject("stream"))
                val streamTurnId = stream?.optString("turn_id").orEmpty()
                val streamAlreadyDone = streamTurnId.isNotBlank() && streamTurnId in completedTurnIds

                val computer = status.optJSONObject("computer")
                val rawWorking = if (streamAlreadyDone) null else execution?.optString("bot_id")?.takeIf { it.isNotBlank() }
                val rawSBot = if (streamAlreadyDone) null else stream?.optString("bot_id")?.takeIf { it.isNotBlank() }

                // Bridge gap if stream just finished on server but final_message_id hasn't landed in remoteMessages yet
                val finalMsgId = lastDone?.optString("final_message_id").orEmpty()
                val holdDoneStream = rawSBot == null && rawWorking == null && finalMsgId.isNotBlank() && remoteMessages.none { it.id == finalMsgId } &&
                    (!ui.chatOpen || lastDone?.optString("bot_id") == ui.botId)
                val inSendGuard = sendGuardBotId != null && SystemClock.uptimeMillis() < sendGuardUntilUptime &&
                    (lastDoneTurn.isBlank() || currentTurnId.isBlank() || lastDoneTurn != currentTurnId) &&
                    (!ui.chatOpen || sendGuardBotId == ui.botId)
                val guardBot = if (inSendGuard) sendGuardBotId else null
                val effectiveSBot = if (isStaleStatus) ui.streamBotId else if (holdDoneStream) lastDone?.optString("bot_id")?.takeIf { it.isNotBlank() } else (rawSBot ?: rawWorking ?: guardBot)
                val effectiveWorking = if (isStaleStatus) ui.workingBot else (rawWorking ?: guardBot)

                remoteWorkingBot = effectiveWorking
                remoteWorkingText = execution?.optString("tool")?.takeIf { it.isNotBlank() }?.let { "正在调用 $it…" } ?: "正在思考…"
                val defM = remoteCfg?.optString("default_model")?.takeIf { it.isNotBlank() } ?: ui.defaultModel
                val hasKey = remoteCfg?.optBoolean("has_api_key") ?: ui.hasApiKey
                val rawStreamText = if (holdDoneStream) lastDone?.optString("text").orEmpty() else stream?.optString("text").orEmpty()
                val preservedText = if (isStaleStatus) ui.streamText else resolveStreamText(effectiveSBot ?: effectiveWorking, streamTurnId, rawStreamText)
                val pacedText = if (isStaleStatus) ui.streamText else applyPacedStreamText(effectiveSBot ?: effectiveWorking, preservedText)
                val thoughtText = if (isStaleStatus) ui.streamThought else stream?.optString("thought").orEmpty()
                val execTrace = if (isStaleStatus) ui.streamTrace else ((execution?.optJSONArray("trace") ?: stream?.optJSONArray("trace"))?.objects() ?: emptyList())
                val curTool = if (isStaleStatus) ui.streamTool else (stream?.optString("tool")?.takeIf { it.isNotBlank() } ?: execution?.optString("tool").orEmpty())
                val usesCloudDesktop = curTool == "cloud_desktop" || execTrace.any { it.optString("tool") == "cloud_desktop" }
                val startedSec = execution?.optDouble("started_at", 0.0) ?: 0.0
                val startedMs = if (startedSec > 0) (startedSec * 1000).toLong() else if (effectiveSBot != null || effectiveWorking != null) (ui.streamStartedAt.takeIf { it > 0L } ?: System.currentTimeMillis()) else 0L
                val routinesCount = (status.optInt("routine_count", -1)).let { if (it >= 0) it else ui.routineCount }
                ui = ui.copy(
                    bots = if (before == revision) roster.getJSONArray("bots").objects().map(PulseBot::from) else ui.bots,
                    primary = if (before == revision) roster.optString("primary_bot_id", "companion") else ui.primary,
                    messages = combinedMessages(), unread = unread,
                    online = true, latency = SystemClock.uptimeMillis() - started, workingBot = effectiveWorking, computerBot = null,
                    workingText = remoteWorkingText,
                    streamBotId = effectiveSBot,
                    streamSpeakerId = if (isStaleStatus) ui.streamSpeakerId else stream?.optString("speaker_id")?.takeIf { it.isNotBlank() },
                    streamSpeakerName = if (isStaleStatus) ui.streamSpeakerName else stream?.optString("speaker_name").orEmpty(),
                    streamText = pacedText,
                    streamThought = thoughtText,
                    streamTool = curTool,
                    streamTrace = execTrace,
                    streamStartedAt = startedMs,
                    cloudDesktopTitle = computer?.optString("title")?.takeIf { it.isNotBlank() } ?: ui.cloudDesktopTitle,
                    cloudDesktopUrl = computer?.optString("url")?.takeIf { it.isNotBlank() } ?: ui.cloudDesktopUrl,
                    cloudDesktopLiveBotId = if (usesCloudDesktop) (effectiveSBot ?: effectiveWorking) else ui.cloudDesktopLiveBotId,
                    cloudDesktopPipVisible = ui.cloudDesktopPipVisible || usesCloudDesktop,
                    routineCount = routinesCount,
                    snoozed = status.optDouble("snooze_until", 0.0) * 1000 > System.currentTimeMillis(),
                    smallReady = status.optBoolean("small_ready"), bigReady = status.optBoolean("big_ready"),
                    defaultModel = defM, hasApiKey = hasKey
                )
                if (ui.activePanel == "cloud_desktop" || usesCloudDesktop || ui.cloudDesktopPipVisible) refreshCloudDesktopFrame()
                if (before == revision) cacheRoster(ui.bots, ui.primary, serverScope)
                val requested = pendingOpenBot
                if (requested != null) { pendingOpenBot = null; if (bot(requested) != null) open(requested) }
                else if (ui.chatOpen && bot() == null) ui = ui.copy(chatOpen = false, botId = ui.primary, draft = draft(ui.primary), attachments = attachments(ui.primary))
                applyPhoneState(); loadAvatars(); acknowledge()
            } catch (e: Exception) { if (e is CancellationException) throw e; ui = ui.copy(online = false) }
        }
    }

    suspend fun refreshStreamOnly() {
        if (!ui.paired || streamLock.isLocked) return
        streamLock.withLock {
            runCatching {
                val started = SystemClock.uptimeMillis()
                val status = request("GET", "/api/status")
                val rtt = SystemClock.uptimeMillis() - started
                // 1. Always merge recent_tail immediately so completed messages appear in 0ms without an extra RTT!
                status.optJSONArray("recent_tail")?.objects()?.mapNotNull { runCatching { PulseMessage.from(it) }.getOrNull() }?.let {
                    mergeRemoteMessages(it, fullReplace = false)
                }
                val lastDone = status.optJSONObject("last_completed_stream")
                val lastDoneTurn = lastDone?.optString("turn_id").orEmpty()
                if (lastDoneTurn.isNotBlank()) markTurnCompleted(lastDoneTurn)

                val statusSeq = status.optLong("seq", 0L)
                if (statusSeq in 1 until lastStreamSeq) {
                    // Out-of-order stale response over high-latency (3000ms) link; update messages only, keep newer stream!
                    ui = ui.copy(online = true, latency = rtt, messages = combinedMessages())
                    return@withLock
                }
                if (statusSeq > lastStreamSeq) lastStreamSeq = statusSeq

                val execsByBot = status.optJSONObject("executions_by_bot")
                val streamsByBot = status.optJSONObject("streams_by_bot")
                val execution = if (ui.chatOpen && execsByBot != null) execsByBot.optJSONObject(ui.botId) else status.optJSONObject("execution")
                val stream = if (ui.chatOpen && streamsByBot != null) streamsByBot.optJSONObject(ui.botId) else status.optJSONObject("stream")
                val streamTurnId = stream?.optString("turn_id").orEmpty()
                val streamAlreadyDone = streamTurnId.isNotBlank() && streamTurnId in completedTurnIds

                val working = if (streamAlreadyDone) null else execution?.optString("bot_id")?.takeIf { it.isNotBlank() }
                val sBot = if (streamAlreadyDone) null else stream?.optString("bot_id")?.takeIf { it.isNotBlank() }

                val finalMsgId = lastDone?.optString("final_message_id").orEmpty()
                val holdDoneStream = sBot == null && working == null && finalMsgId.isNotBlank() && remoteMessages.none { it.id == finalMsgId } &&
                    (!ui.chatOpen || lastDone?.optString("bot_id") == ui.botId)
                val inSendGuard = sendGuardBotId != null && SystemClock.uptimeMillis() < sendGuardUntilUptime &&
                    (lastDoneTurn.isBlank() || currentTurnId.isBlank() || lastDoneTurn != currentTurnId) &&
                    (!ui.chatOpen || sendGuardBotId == ui.botId)
                val guardBot = if (inSendGuard) sendGuardBotId else null
                val effectiveWorking = working ?: guardBot
                val effectiveSBot = if (holdDoneStream) lastDone?.optString("bot_id")?.takeIf { it.isNotBlank() } else (sBot ?: effectiveWorking)

                remoteWorkingBot = effectiveWorking
                remoteWorkingText = execution?.optString("tool")?.takeIf { it.isNotBlank() }?.let { "正在调用 $it…" } ?: "正在思考…"
                val rawStreamText = if (holdDoneStream) lastDone?.optString("text").orEmpty() else stream?.optString("text").orEmpty().ifBlank { if (effectiveSBot != null) "正在思考…" else "" }
                val preservedText = resolveStreamText(effectiveSBot ?: effectiveWorking, streamTurnId, rawStreamText)
                val pacedText = applyPacedStreamText(effectiveSBot ?: effectiveWorking, preservedText)
                val thoughtText = stream?.optString("thought").orEmpty().ifBlank { if (effectiveSBot != null && stream == null) ui.streamThought else "" }
                val execTrace = (execution?.optJSONArray("trace") ?: stream?.optJSONArray("trace"))?.objects() ?: if (effectiveSBot != null || effectiveWorking != null) ui.streamTrace else emptyList()
                val curTool = stream?.optString("tool")?.takeIf { it.isNotBlank() } ?: execution?.optString("tool").orEmpty()
                val usesCloudDesktop = curTool == "cloud_desktop" || execTrace.any { it.optString("tool") == "cloud_desktop" }
                val startedSec = execution?.optDouble("started_at", 0.0) ?: 0.0
                val startedMs = if (startedSec > 0) (startedSec * 1000).toLong() else if (effectiveSBot != null || effectiveWorking != null) (ui.streamStartedAt.takeIf { it > 0L } ?: System.currentTimeMillis()) else 0L
                ui = ui.copy(
                    online = true,
                    latency = rtt,
                    messages = combinedMessages(),
                    workingBot = effectiveWorking,
                    workingText = remoteWorkingText,
                    streamBotId = effectiveSBot,
                    streamSpeakerId = stream?.optString("speaker_id")?.takeIf { it.isNotBlank() },
                    streamSpeakerName = stream?.optString("speaker_name").orEmpty(),
                    streamText = pacedText,
                    streamThought = thoughtText,
                    streamTool = curTool,
                    streamTrace = execTrace,
                    streamStartedAt = startedMs,
                    cloudDesktopLiveBotId = if (usesCloudDesktop) (effectiveSBot ?: effectiveWorking) else ui.cloudDesktopLiveBotId,
                    cloudDesktopPipVisible = ui.cloudDesktopPipVisible || usesCloudDesktop
                )
                if (ui.activePanel == "cloud_desktop" || usesCloudDesktop || ui.cloudDesktopPipVisible) refreshCloudDesktopFrame()
            }
        }
    }

    private fun loadAvatars() {
        for (bot in ui.bots) {
            if (bot.avatarVersion.isBlank()) {
                if (bot.id in avatarVersions) { avatarVersions.remove(bot.id); ui = ui.copy(avatars = ui.avatars - bot.id) }
                continue
            }
            if (avatarVersions[bot.id] == bot.avatarVersion || bot.id in avatarLoading || avatarLoading.size >= 2) continue
            avatarLoading.add(bot.id)
            scope.launch {
                try {
                    val image = request("GET", "/api/bots/${bot.id}/avatar")
                    val bitmap = withContext(Dispatchers.Default) {
                        val value = image.getString("image"); require(value.startsWith("data:image/png;base64,"))
                        val bytes = Base64.decode(value.substringAfter(','), Base64.DEFAULT)
                        requireNotNull(BitmapFactory.decodeByteArray(bytes, 0, bytes.size)).asImageBitmap()
                    }
                    if (ui.bots.any { it.id == bot.id && it.avatarVersion == image.optString("version") }) {
                        avatarVersions[bot.id] = image.getString("version"); ui = ui.copy(avatars = ui.avatars + (bot.id to bitmap))
                    }
                } catch (e: Exception) { if (e is CancellationException) throw e }
                finally { avatarLoading.remove(bot.id) }
            }
        }
    }

    // --- Pure Compose Panels (Settings, Bot Editor, Vault) ---
    fun closePanel() { ui = ui.copy(activePanel = null) }

    fun openSettings() {
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
        ui = ui.copy(
            activePanel = "settings",
            serverUrl = saved.optString("serverUrl", SecureTransport.DEFAULT_SERVER),
            apiBaseUrl = storedUrl,
            apiKeyInput = storedKey,
            defaultModel = storedModel.ifBlank { ui.defaultModel },
            backgroundService = saved.optBoolean("background", true),
            appearance = saved.optString("appearance", "system")
        )
        perform {
            val remote = request("GET", "/api/config")
            val rUrl = remote.optString("api_base_url").ifBlank { remote.optJSONObject("big_brain")?.optString("base_url").orEmpty() }
            val rModel = remote.optString("default_model").ifBlank { remote.optJSONObject("big_brain")?.optString("model").orEmpty() }
            ui = ui.copy(
                apiBaseUrl = if (ui.apiBaseUrl == "https://api.openai.com/v1" && rUrl.isNotBlank()) rUrl else ui.apiBaseUrl,
                defaultModel = ui.defaultModel.ifBlank { rModel },
                hasApiKey = remote.optBoolean("has_api_key", storedKey.isNotBlank()),
                curiosityEnabledGlobal = remote.optBoolean("enabled", true),
                curiosityMinutes = remote.optInt("curiosity_interval_minutes", 90).toString()
            )
        }
    }

    fun updateSettingsField(
        serverUrl: String = ui.serverUrl,
        apiBaseUrl: String = ui.apiBaseUrl,
        apiKeyInput: String = ui.apiKeyInput,
        defaultModel: String = ui.defaultModel,
        curiosityEnabled: Boolean = ui.curiosityEnabledGlobal,
        curiosityMinutes: String = ui.curiosityMinutes,
        backgroundService: Boolean = ui.backgroundService,
        appearance: String = ui.appearance
    ) {
        ui = ui.copy(
            serverUrl = serverUrl, apiBaseUrl = apiBaseUrl, apiKeyInput = apiKeyInput,
            defaultModel = defaultModel, curiosityEnabledGlobal = curiosityEnabled,
            curiosityMinutes = curiosityMinutes, backgroundService = backgroundService, appearance = appearance
        )
    }

    fun fetchModels(forBotEditor: Boolean = false) {
        if (ui.fetchingModels) return
        ui = ui.copy(fetchingModels = true)
        perform {
            try {
                // Save serverUrl first if changed
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

    fun saveSettings() {
        if (ui.savingSettings) return
        val cleanServer = ui.serverUrl.trim().ifBlank { SecureTransport.DEFAULT_SERVER }.trimEnd('/')
        val cleanBase = ui.apiBaseUrl.trim().ifBlank { "https://api.openai.com/v1" }.trimEnd('/')
        val cleanKey = ui.apiKeyInput.trim()
        val cleanModel = ui.defaultModel.trim()
        val minutes = (ui.curiosityMinutes.toIntOrNull() ?: 90).coerceIn(5, 1440)
        ui = ui.copy(savingSettings = true)
        perform {
            try {
                withContext(Dispatchers.IO) {
                    val s = SecurePrefs.read(activity)
                    s.put("serverUrl", cleanServer)
                    s.put("deviceToken", s.optString("deviceToken").ifBlank { SecureTransport.defaultToken() })
                    s.put("background", ui.backgroundService)
                    s.put("appearance", ui.appearance)
                    val localCfg = s.optJSONObject("config") ?: JSONObject()
                    localCfg.put("api_base_url", cleanBase)
                    if (cleanKey.isNotBlank()) localCfg.put("api_key", cleanKey)
                    if (cleanModel.isNotBlank()) localCfg.put("default_model", cleanModel)
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
                    .put("api_base_url", cleanBase)
                    .put("enabled", ui.curiosityEnabledGlobal)
                    .put("curiosity_interval_minutes", minutes)
                if (cleanKey.isNotBlank()) updatePayload.put("api_key", cleanKey)
                if (cleanModel.isNotBlank()) updatePayload.put("default_model", cleanModel)
                request("POST", "/api/config", updatePayload)
                val dark = when (ui.appearance) {
                    "dark" -> true; "light" -> false
                    else -> activity.resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK == Configuration.UI_MODE_NIGHT_YES
                }
                activity.systemBarsFor(dark)
                RootPhoneHarness.initialize(activity)
                if (ui.backgroundService && !AgentForegroundService.running) {
                    runCatching { activity.startForegroundService(Intent(activity, AgentForegroundService::class.java)) }
                } else if (!ui.backgroundService) {
                    activity.stopService(Intent(activity, AgentForegroundService::class.java))
                }
                ui = ui.copy(dark = dark, smallModel = cleanModel, bigModel = cleanModel, defaultModel = cleanModel, hasApiKey = cleanKey.isNotBlank() || ui.hasApiKey, activePanel = null)
                refresh()
                toast("已保存并同步到云端！Harness 已就绪。")
            } finally {
                ui = ui.copy(savingSettings = false)
            }
        }
    }

    fun openBotEditor(botId: String? = null) {
        dismissMenu()
        val existing = botId?.let { bot(it) }
        ui = ui.copy(
            activePanel = "bot_edit",
            editingBotId = existing?.id,
            editBotName = existing?.name ?: "",
            editBotDesc = existing?.description ?: "",
            editBotModel = existing?.model ?: "",
            editBotInstructions = existing?.instructions ?: "你是一位专业、直接的协作 Bot。使用工作区工具完成任务并汇报。",
            editBotColor = existing?.color ?: "#7C5CFF",
            editBotShape = existing?.shape ?: "circle",
            editBotCuriosity = existing?.curiosityEnabled ?: false,
            editBotNotify = existing?.notify ?: true
        )
    }

    fun updateBotEditField(
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

    fun saveBotEditor() {
        val name = ui.editBotName.trim()
        if (name.isEmpty()) { toast("请填写 Bot 名字"); return }
        if (ui.savingBot) return
        ui = ui.copy(savingBot = true)
        perform {
            try {
                val payload = JSONObject()
                    .put("name", name)
                    .put("description", ui.editBotDesc.trim())
                    .put("model", ui.editBotModel.trim())
                    .put("instructions", ui.editBotInstructions.trim())
                    .put("color", ui.editBotColor)
                    .put("shape", ui.editBotShape)
                    .put("curiosity_enabled", ui.editBotCuriosity)
                    .put("notify", ui.editBotNotify)
                    .put("engine", "small")
                val editingId = ui.editingBotId
                val savedBot = if (editingId == null) {
                    PulseBot.from(request("POST", "/api/bots", payload))
                } else {
                    PulseBot.from(request("PUT", "/api/bots/$editingId", payload))
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

    fun setPrimaryBot(id: String) {
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

    fun deleteBot(id: String) {
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

    fun openVault(initialPath: String = "INDEX.md") {
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

    fun selectVaultFile(path: String) {
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

    fun updateVaultContent(content: String) { ui = ui.copy(vaultContent = content) }

    fun saveVaultFile() {
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

    fun askPrimaryToHealError(errorMsg: PulseMessage) {
        val primaryId = ui.primary
        val targetBot = bot(errorMsg.botId)?.name ?: errorMsg.botId
        val healPrompt = "Bot「$targetBot (${errorMsg.botId})」刚刚报错了：\n${errorMsg.text}\n\n请直接调用 list_models 查看可用模型，再用 manage_bot(action='update', bot_id='${errorMsg.botId}', model=...) 修复它的模型配置，然后用 send_message 让它重试刚才的任务！"
        open(primaryId)
        setDraft(healPrompt)
        send()
    }

    private var cloudStreamJob: Job? = null

    fun dismissCloudDesktopPip() {
        ui = ui.copy(cloudDesktopPipVisible = false)
    }

    fun showCloudDesktopPip() {
        ui = ui.copy(cloudDesktopPipVisible = true)
        scope.launch { refreshCloudDesktopFrame(forceFull = true) }
    }

    fun openCloudDesktop() {
        dismissMenu()
        ui = ui.copy(activePanel = "cloud_desktop", cloudDesktopLoading = ui.cloudDesktopFrame == null)
        cloudStreamJob?.cancel()
        cloudStreamJob = scope.launch {
            refreshCloudDesktopFrame(forceFull = true)
            while (isActive && ui.activePanel == "cloud_desktop") {
                delay(850)
                if (ui.activePanel == "cloud_desktop" && !ui.cloudDesktopLoading) {
                    refreshCloudDesktopFrame(forceFull = false)
                }
            }
        }
    }

    fun openSchedules() {
        dismissMenu()
        ui = ui.copy(activePanel = "schedules", schedulesLoading = true)
        perform {
            try {
                val rRes = runCatching { request("GET", "/api/routines") }.getOrNull()
                val dRes = runCatching { request("GET", "/api/deadlines") }.getOrNull()
                val merged = JSONObject()
                    .put("routines", rRes?.optJSONArray("routines") ?: JSONArray())
                    .put("deadlines", dRes?.optJSONArray("deadlines") ?: JSONArray())
                    .put("timers", rRes?.optJSONArray("timers") ?: JSONArray())
                ui = ui.copy(schedulesData = merged, schedulesLoading = false)
            } finally {
                ui = ui.copy(schedulesLoading = false)
            }
        }
    }

    fun toggleRoutineItem(id: String, enabled: Boolean) {
        perform {
            request("PUT", "/api/routines/$id", JSONObject().put("enabled", enabled))
            toast(if (enabled) "已启用该例行任务" else "已暂停该例行任务")
            openSchedules()
        }
    }

    fun runRoutineItem(id: String) {
        perform {
            toast("已触发执行例行任务…")
            closePanel()
            request("POST", "/api/routines/$id/run")
            refresh()
        }
    }

    fun sendQuickPrompt(prompt: String) {
        if (prompt.isBlank()) return
        setDraft(prompt)
        send()
    }

    fun retryMessage(msg: PulseMessage) {
        if (msg.role == "user") {
            setDraft(msg.text)
            send()
        } else {
            val botMsgs = ui.messages.filter { it.botId == ui.botId }
            val idx = botMsgs.indexOfFirst { it.id == msg.id }
            val prevUser = if (idx > 0) botMsgs.subList(0, idx).lastOrNull { it.role == "user" } else botMsgs.lastOrNull { it.role == "user" }
            if (prevUser != null) {
                setDraft(prevUser.text)
                send()
            } else {
                setDraft("请重新执行并回答上一条请求。")
                send()
            }
        }
    }

    private suspend fun applyCloudDesktopPacket(res: JSONObject) {
        val unchanged = res.optBoolean("unchanged", false)
        val newHash = res.optString("hash", ui.cloudDesktopHash)
        val rawImg = res.optString("image").trim()
        val b64 = if (rawImg.contains(",")) rawImg.substringAfter(",") else rawImg
        val bitmap = if (!unchanged && b64.isNotBlank()) withContext(Dispatchers.Default) {
            val bytes = Base64.decode(b64, Base64.DEFAULT)
            BitmapFactory.decodeByteArray(bytes, 0, bytes.size)?.asImageBitmap()
        } else ui.cloudDesktopFrame
        ui = ui.copy(
            cloudDesktopFrame = bitmap ?: ui.cloudDesktopFrame,
            cloudDesktopHash = newHash.ifBlank { ui.cloudDesktopHash },
            cloudDesktopUrl = res.optString("url").takeIf { it.isNotBlank() } ?: ui.cloudDesktopUrl,
            cloudDesktopTitle = res.optString("title").takeIf { it.isNotBlank() } ?: ui.cloudDesktopTitle,
            cloudDesktopLoading = false
        )
    }

    suspend fun refreshCloudDesktopFrame(forceFull: Boolean = false) {
        runCatching {
            val q = JSONObject().put("quality", 60)
            if (!forceFull && ui.cloudDesktopHash.isNotBlank()) {
                q.put("since", ui.cloudDesktopHash)
            }
            val res = request("GET", "/api/computer/frame", query = q)
            applyCloudDesktopPacket(res)
        }.onFailure { ui = ui.copy(cloudDesktopLoading = false) }
    }

    fun cloudDesktopNavigate(url: String) {
        val clean = url.trim().let { if (it.isNotBlank() && !it.startsWith("http")) "https://$it" else it }
        if (clean.isBlank()) return
        ui = ui.copy(cloudDesktopLoading = true, cloudDesktopUrl = clean)
        perform {
            try {
                val res = request("POST", "/api/computer/navigate", JSONObject().put("url", clean).put("include_frame", true))
                applyCloudDesktopPacket(res)
                if (!res.has("image")) refreshCloudDesktopFrame(forceFull = true)
            } finally {
                ui = ui.copy(cloudDesktopLoading = false)
            }
        }
    }

    fun cloudDesktopClick(normX: Float, normY: Float, kind: String = "click") {
        val clampedX = normX.coerceIn(0f, 1f)
        val clampedY = normY.coerceIn(0f, 1f)
        val x = (clampedX * 1279f).toInt()
        val y = (clampedY * 799f).toInt()
        ui = ui.copy(cloudDesktopTapNorm = clampedX to clampedY)
        perform {
            try {
                val res = request("POST", "/api/computer/input", JSONObject().put("kind", kind).put("x", x).put("y", y).put("include_frame", true))
                applyCloudDesktopPacket(res)
                if (!res.has("image")) refreshCloudDesktopFrame(forceFull = true)
            } finally {
                delay(220)
                ui = ui.copy(cloudDesktopTapNorm = null)
            }
        }
    }

    fun cloudDesktopScroll(normX: Float, normY: Float, direction: String, amount: Int = 4) {
        val x = (normX.coerceIn(0f, 1f) * 1279f).toInt()
        val y = (normY.coerceIn(0f, 1f) * 799f).toInt()
        perform {
            val res = request("POST", "/api/computer/input", JSONObject().put("kind", "scroll").put("x", x).put("y", y).put("direction", direction).put("amount", amount).put("include_frame", true))
            applyCloudDesktopPacket(res)
            if (!res.has("image")) refreshCloudDesktopFrame(forceFull = true)
        }
    }

    fun cloudDesktopLaunchApp(appKey: String, argText: String = "") {
        ui = ui.copy(cloudDesktopLoading = true)
        perform {
            try {
                val payload = JSONObject().put("kind", "launch_app").put("app", appKey).put("include_frame", true)
                if (argText.isNotBlank()) payload.put("text", argText)
                val res = request("POST", "/api/computer/input", payload)
                applyCloudDesktopPacket(res)
                if (!res.has("image")) refreshCloudDesktopFrame(forceFull = true)
            } finally {
                ui = ui.copy(cloudDesktopLoading = false)
            }
        }
    }

    fun cloudDesktopType(text: String) {
        if (text.isEmpty()) return
        perform {
            val steps = JSONArray()
                .put(JSONObject().put("kind", "type").put("text", text))
                .put(JSONObject().put("kind", "key").put("key", "Return"))
            val res = request("POST", "/api/computer/input", JSONObject().put("kind", "batch").put("steps", steps).put("include_frame", true))
            applyCloudDesktopPacket(res)
            if (!res.has("image")) refreshCloudDesktopFrame(forceFull = true)
        }
    }

    fun cloudDesktopKey(key: String) {
        val mappedKey = when (key) {
            "Backspace" -> "BackSpace"
            "Enter" -> "Return"
            "PageDown" -> "Page_Down"
            "PageUp" -> "Page_Up"
            else -> key
        }
        perform {
            val res = request("POST", "/api/computer/input", JSONObject().put("kind", "key").put("key", mappedKey).put("include_frame", true))
            applyCloudDesktopPacket(res)
            if (!res.has("image")) refreshCloudDesktopFrame(forceFull = true)
        }
    }

    fun visibleBots(): List<PulseBot> = ui.bots.filter { bot ->
        !bot.hidden && (ui.query.isBlank() || "${bot.name} ${bot.description} ${ui.messages.lastOrNull { it.botId == bot.id }?.text.orEmpty()}".contains(ui.query, true))
    }.sortedWith(compareByDescending<PulseBot> { it.pinned }.thenByDescending { if (it.pinned) it.pinnedAt else 0.0 }
        .thenByDescending { b -> ui.messages.lastOrNull { it.botId == b.id }?.created ?: 0.0 })
    fun bot(id: String = ui.botId): PulseBot? = ui.bots.find { it.id == id }
    fun time(created: Double): String = clock.format(Date((created * 1000).toLong()))
    fun day(created: Double): String = calendar.format(Date((created * 1000).toLong()))
    fun timeSeparator(created: Double): String {
        val ms = (created * 1000).toLong()
        val nowDay = calendar.format(Date(System.currentTimeMillis()))
        val msgDay = calendar.format(Date(ms))
        return if (nowDay == msgDay) time(created) else "$msgDay ${time(created)}"
    }
    fun open(id: String) {
        if (bot(id) == null) return
        val switchingBot = ui.botId != id
        if (switchingBot) {
            streamPacerJob?.cancel()
            targetStreamFullText = ""
        }
        ui = ui.copy(
            botId = id, chatOpen = true, activePanel = null, mode = draftMode(id), reply = null,
            draft = draft(id), attachments = attachments(id), menu = null,
            streamBotId = if (switchingBot && ui.streamBotId != id) null else ui.streamBotId,
            workingBot = if (switchingBot && ui.workingBot != id) null else ui.workingBot,
            streamText = if (switchingBot && ui.streamBotId != id) "" else ui.streamText,
            streamThought = if (switchingBot && ui.streamBotId != id) "" else ui.streamThought,
            streamTool = if (switchingBot && ui.streamBotId != id) "" else ui.streamTool,
            streamTrace = if (switchingBot && ui.streamBotId != id) emptyList() else ui.streamTrace
        )
        scope.launch { refreshStreamOnly() }
        acknowledge()
    }
    fun requestOpenBot(id: String) { if (id.isBlank()) return; pendingOpenBot = id; if (bot(id) != null) { pendingOpenBot = null; open(id) }; scope.launch { refresh() } }
    fun back() { predictiveBack?.let { backFrom = 1f - it }; ui = ui.copy(chatOpen = false, menu = null, activePanel = null); predictiveBack = null }
    fun cancelBack() { predictiveBack?.let { backFrom = 1f - it }; predictiveBack = null; backSettlement++ }
    fun search(value: String?) { ui = ui.copy(searching = value != null, query = value ?: "") }
    fun setDraft(value: String) {
        if (value.length > 12000) return
        synchronized(draftLock) { drafts[ui.botId] = value }
        localRevision++
        ui = ui.copy(draft = value)
        draftJob?.cancel(); draftJob = scope.launch { delay(600); persistDrafts() }
    }
    private fun draft(id: String) = synchronized(draftLock) { drafts[id] ?: "" }
    fun persistDrafts(): Future<*> {
        val snapshot = synchronized(draftLock) { drafts.toMap() }
        val files = synchronized(draftLock) { attachmentDrafts.toMap() }
        return draftWriter.submit {
            synchronized(SecurePrefs::class.java) {
                val current = SecurePrefs.read(activity); val stored = current.optJSONObject("drafts") ?: JSONObject()
                snapshot.forEach { (id, value) -> stored.put(id, value) }
                val attached = current.optJSONObject("attachmentDrafts") ?: JSONObject()
                files.forEach { (id, value) -> attached.put(id, JSONArray(value.map { it.json() })) }
                current.put("drafts", stored).put("attachmentDrafts", attached); SecurePrefs.save(activity, current)
            }
        }
    }
    fun secondary(view: String, action: String = "", path: String = "") {
        dismissMenu()
        when {
            view in listOf("settings", "config") -> { openSettings(); return }
            view == "vault" -> { openVault(path.ifBlank { "INDEX.md" }); return }
            view == "computer" -> { openCloudDesktop(); return }
            view == "profile" -> { openBotEditor(ui.botId); return }
            action == "createBot" -> { openBotEditor(null); return }
            view in listOf("local", "root") -> {
                activity.startActivity(Intent(activity, NativePhoneActivity::class.java))
                return
            }
        }
        val selected = ui.botId; val selectedMode = ui.mode; val saved = persistDrafts()
        perform {
            withContext(Dispatchers.IO) { saved.get() }
            activity.startActivityForResult(Intent(activity, MainActivity::class.java).putExtra("nativeParent", true)
                .putExtra("entryRoute", view).putExtra("entryBot", selected).putExtra("entryAction", action).putExtra("entryMode", selectedMode).putExtra("entryPath", path), 740)
        }
    }
    fun dismissMenu() { ui = ui.copy(menu = null) }
    private fun menu(title: String, items: List<PulseMenuItem>) { ui = ui.copy(menu = PulseMenu(title, items)) }
    fun newMenu() = menu("工作台", listOf(
        PulseMenuItem("创建新 Bot", "创建专属角色、指令与独立模型", "plus") { openBotEditor(null) },
        PulseMenuItem("手机虚拟屏 (桌面悬浮小窗)", "创建并弹出浮在系统桌面上的独立小窗", "screen") { launchFloatingVirtualDisplay() },
        PulseMenuItem("云端桌面 (Cloud Desktop)", "查看云端浏览器实时画面与触控接管", "cloud") { openCloudDesktop() },
        PulseMenuItem("定时闹钟与例行自动化", "查看和管理每日定点提醒、课表闹钟与巡检", "clock") { openSchedules() },
        PulseMenuItem("透明记忆库 (Obsidian Vault)", "查看或修改 Markdown 记忆与画像", "vault") { openVault("INDEX.md") },
        PulseMenuItem("API 与模型设置", "填入 API Key，一键拉取 /models 并同步", "settings") { openSettings() }))
    fun botMenu(id: String = ui.botId) {
        val b = bot(id) ?: return
        val entries = mutableListOf<PulseMenuItem>()
        if (!ui.chatOpen || ui.botId != id) entries += PulseMenuItem("打开对话", icon = "chat") { open(id) }
        entries += listOf(
            PulseMenuItem("编辑 Bot 设定与模型", "当前模型：${b.model.ifBlank { ui.defaultModel.ifBlank { "跟随全局默认" } }}", "settings") { openBotEditor(id) },
            PulseMenuItem(if (ui.primary == id) "当前为主助理" else "设为主助理", icon = "spark") { setPrimaryBot(id) },
            PulseMenuItem(if (b.pinned) "取消置顶" else "置顶到首页", icon = "pin") { placement(id, "pinned", !b.pinned) },
            PulseMenuItem(if (b.hidden) "在首页显示" else "隐藏此 Bot", "保留对话、记忆和协作能力", "hide") { placement(id, "hidden", !b.hidden) },
            PulseMenuItem("定时闹钟与例行自动化", icon = "clock") { openSchedules() },
            PulseMenuItem("透明记忆库", icon = "vault") { openVault("INDEX.md") }
        )
        if (ui.bots.size > 1 && ui.primary != id) {
            entries += PulseMenuItem("删除此 Bot", "移除该助手", "close") { deleteBot(id) }
        }
        menu(b.name, entries)
    }
    fun actionsMenu() = menu("工作台与工具", listOf(
        PulseMenuItem("上传文件到工作区", "供当前 Bot 读取、编辑或运行脚本", "file") { chooseAttachment() },
        PulseMenuItem("弹出手机虚拟屏悬浮小窗", "在桌面上悬浮独立虚拟屏，点按展开触控", "screen") {
            val raw = ui.draft.trim()
            if (raw.isNotBlank()) sendPhone(raw) else launchFloatingVirtualDisplay()
        },
        PulseMenuItem("打开云端桌面", "查看云端浏览器实时画面或接管", "cloud") { secondary("computer") },
        PulseMenuItem("定时闹钟与例行自动化", "管理每日准点提醒与后台巡检", "clock") { openSchedules() },
        PulseMenuItem("触发好奇心主动搭话", "让主助理结合记忆投递实用情报或追问", "spark") { job("curiosity") },
        PulseMenuItem("打开透明记忆库", "查看或编辑 memory_vault/*.md", "vault") { openVault("INDEX.md") },
        PulseMenuItem("编辑当前 Bot 模型与设定", icon = "settings") { openBotEditor(ui.botId) },
        PulseMenuItem("免打扰 1 小时", icon = "moon") { job("snooze") }))

    fun launchFloatingVirtualDisplay() {
        dismissMenu()
        if (!FloatingVirtualDisplay.canOverlay(activity)) {
            FloatingVirtualDisplay.requestOverlayPermission(activity)
        }
        perform {
            try {
                withContext(Dispatchers.IO) {
                    RootPhoneHarness.initialize(activity)
                    val st = RootPhoneHarness.status(activity)
                    if (!st.optBoolean("root")) {
                        RootPhoneHarness.command(activity, JSONObject().put("op", "connect").put("manual", true))
                    }
                    val disp = st.optJSONObject("display") ?: JSONObject()
                    if (!disp.optBoolean("created")) {
                        RootPhoneHarness.command(activity, JSONObject().put("op", "create").put("manual", true))
                    }
                    runCatching { RootPhoneHarness.command(activity, JSONObject().put("op", "apps").put("manual", true)) }
                }
                FloatingVirtualDisplay.show(activity, forceExpand = true)
                toast(if (FloatingVirtualDisplay.canOverlay(activity)) "虚拟屏悬浮小窗已开启，退回桌面也会浮在屏幕上。" else "虚拟屏已启动（开启悬浮窗权限后可浮在系统桌面上）。")
            } catch (e: Exception) {
                activity.startActivity(Intent(activity, NativePhoneActivity::class.java))
            }
        }
    }

    private fun perform(task: suspend () -> Unit) { scope.launch { try { task() } catch (e: Exception) { if (e is CancellationException) throw e; toast(MainActivity.safeError(e)) } } }
    fun toast(message: String) { ui = ui.copy(toast = message, toastId = ui.toastId + 1) }
    private fun ensurePaired(): Boolean { if (ui.paired) return true; openSettings(); return false }
    private fun placement(id: String, key: String, value: Boolean) {
        dismissMenu(); if (!ensurePaired()) return
        perform {
            val updated = PulseBot.from(request("PATCH", "/api/bots/$id/placement", JSONObject().put(key, value)))
            revision++; ui = ui.copy(bots = ui.bots.map { if (it.id == id) updated else it }, chatOpen = if (key == "hidden" && value && ui.botId == id) false else ui.chatOpen)
            cacheRoster(ui.bots, ui.primary)
            toast(if (key == "hidden") if (value) "已隐藏。" else "已恢复显示。" else if (value) "已置顶到首页。" else "已取消置顶。")
        }
    }
    private fun acknowledge() {
        if (!active || !ui.chatOpen || !ui.online) return
        val ids = ui.messages.filter { it.botId == ui.botId && it.id in ui.unread && it.kind != "escalation_demo" }.map { it.id }
        if (ids.isEmpty()) return
        perform { request("POST", "/api/ack", JSONObject().put("ids", JSONArray(ids))); ui = ui.copy(unread = ui.unread - ids.toSet()) }
    }
    fun pause() { active = false }
    fun job(kind: String) {
        dismissMenu(); if (!ensurePaired() || ui.busyBot != null) return
        val id = if (kind == "curiosity") ui.primary else ui.botId
        ui = ui.copy(busyBot = id)
        perform {
            try {
                when (kind) {
                    "curiosity" -> { open(id); request("POST", "/api/trigger-curiosity") }
                    "research" -> request("POST", "/api/research", JSONObject().put("bot_id", id))
                    "snooze", "wake" -> { request("POST", "/api/snooze", JSONObject().put("minutes", if (kind == "snooze") 60 else 0)); toast(if (kind == "snooze") "好，安静一小时。" else "已恢复主动联系。") }
                }
                refresh()
            } finally { ui = ui.copy(busyBot = null) }
        }
    }
    fun send() {
        val raw = ui.draft.trim().ifEmpty { if (ui.attachments.isNotEmpty()) "请查看这些附件。" else "" }
        if (raw.isEmpty() || ui.uploading) return
        if (!ensurePaired()) return
        val id = ui.botId
        val sentAttachments = ui.attachments
        val withFiles = raw + if (sentAttachments.isEmpty()) "" else "\n\n附件（工作区相对路径，可用 read_file 或 run_bash 读取）：\n" + sentAttachments.joinToString("\n") { it.path.removePrefix("workspace/") }
        val text = ui.reply?.let { "> " + it.text.take(600).replace("\n", "\n> ") + "\n\n" + withFiles } ?: withFiles
        val pending = PulseMessage("pending-${System.currentTimeMillis()}-${optimisticList.size}", id, "user", text, "", "", System.currentTimeMillis() / 1000.0, "", "[]", true)
        optimisticList.add(pending)
        inFlightSends++
        draftJob?.cancel()
        synchronized(draftLock) {
            drafts[id] = ""
            attachmentDrafts[id] = attachments(id).filterNot { a -> sentAttachments.any { it.path == a.path && it.source == a.source } }
        }
        localRevision++
        persistDrafts()
        sendGuardBotId = id
        sendGuardUntilUptime = SystemClock.uptimeMillis() + 15_000L
        currentTurnId = "pending-${System.currentTimeMillis()}"
        ui = ui.copy(
            draft = if (ui.botId == id) "" else ui.draft,
            attachments = if (ui.botId == id) attachments(id) else ui.attachments,
            busyBot = id,
            workingBot = id,
            workingText = "正在思考…",
            streamBotId = id,
            streamText = "正在思考…",
            streamThought = "正在向云端发送指令并建立实时流…",
            streamTool = "",
            streamTrace = emptyList(),
            streamStartedAt = System.currentTimeMillis(),
            reply = null,
            messages = combinedMessages()
        )
        perform {
            try {
                val res = request("POST", "/api/chat", JSONObject().put("text", text).put("bot_id", id).put("mode", "auto").put("async_ack", true))
                val seq = res.optLong("seq", 0L)
                if (seq > lastStreamSeq) lastStreamSeq = seq
                val turnId = res.optString("turn_id", "")
                val isQueued = res.optBoolean("queued", false)
                if (turnId.isNotBlank()) {
                    if (isQueued) {
                        currentTurnId = turnId
                        sendGuardBotId = id
                        sendGuardUntilUptime = SystemClock.uptimeMillis() + 10_000L
                    } else {
                        markTurnCompleted(turnId)
                        sendGuardBotId = null
                        sendGuardUntilUptime = 0L
                    }
                }
                val incoming = mutableListOf<PulseMessage>()
                res.optJSONArray("recent_tail")?.objects()?.mapNotNull { runCatching { PulseMessage.from(it) }.getOrNull() }?.let { incoming.addAll(it) }
                res.optJSONObject("user_message")?.let { uObj ->
                    runCatching { PulseMessage.from(uObj) }.getOrNull()?.let { incoming.add(it) }
                }
                if (res.has("id") && res.has("text")) {
                    runCatching { PulseMessage.from(res) }.getOrNull()?.let { incoming.add(it) }
                }
                if (incoming.isNotEmpty()) {
                    mergeRemoteMessages(incoming, fullReplace = false)
                }
                optimisticList.removeAll { it.id == pending.id }
                if (isQueued) {
                    remoteWorkingBot = id
                    ui = ui.copy(
                        messages = combinedMessages(),
                        workingBot = id,
                        streamBotId = ui.streamBotId ?: id
                    )
                } else {
                    remoteWorkingBot = null
                    ui = ui.copy(
                        messages = combinedMessages(),
                        workingBot = if (ui.workingBot == id) null else ui.workingBot,
                        streamBotId = if (ui.streamBotId == id) null else ui.streamBotId,
                        streamText = if (ui.streamBotId == id) "" else ui.streamText,
                        streamTool = if (ui.streamBotId == id) "" else ui.streamTool,
                        streamTrace = if (ui.streamBotId == id) emptyList() else ui.streamTrace
                    )
                    refresh(force = true)
                }
            } catch (e: Exception) {
                // In 3000ms high-latency environments, the server may have already received the message even if HTTP timed out.
                // Refresh state first and NEVER push already-sent instructions back into the input box!
                runCatching { refresh(force = true) }
                val delivered = remoteMessages.takeLast(16).any { it.role == "user" && it.botId == id && it.text.trim() == text.trim() } ||
                    ui.workingBot == id || ui.streamBotId == id
                if (delivered) {
                    optimisticList.removeAll { it.id == pending.id }
                    ui = ui.copy(messages = combinedMessages())
                } else {
                    // Keep the message visible in the chat list rather than dumping it back into the input box
                    ui = ui.copy(messages = combinedMessages())
                    throw e
                }
            } finally {
                inFlightSends = (inFlightSends - 1).coerceAtLeast(0)
                if (inFlightSends == 0) ui = ui.copy(busyBot = null)
            }
        }
    }
    fun chooseAttachment() {
        if (!ensurePaired() || ui.uploading || pickerBot != null || ui.busyBot != null) return
        pickerBot = ui.botId; pickerSource = "workspace"
        try { activity.pickDocument() } catch (e: Exception) { pickerBot = null; pickerSource = null; toast(MainActivity.safeError(e)) }
    }
    fun restorePicker(state: Bundle?) { pickerBot = state?.getString("upload_bot"); pickerSource = state?.getString("upload_source") }
    fun savePicker(state: Bundle) { pickerBot?.let { state.putString("upload_bot", it); state.putString("upload_source", pickerSource) } }
    fun documentPicked(uri: Uri?) {
        val id = pickerBot; val source = pickerSource; pickerBot = null; pickerSource = null
        if (uri == null || id == null || source == null) return
        ui = ui.copy(uploading = true)
        perform { try {
            val result = withContext(Dispatchers.IO) { CloudFiles.upload(activity, uri, source) }
            val file = PulseAttachment.from(result.put("source", source))
            synchronized(draftLock) { attachmentDrafts[id] = attachments(id) + file }; localRevision++
            if (ui.botId == id) ui = ui.copy(attachments = attachments(id))
            withContext(Dispatchers.IO) { persistDrafts().get() }; toast("已上传：${file.name}")
        } finally { ui = ui.copy(uploading = false) } }
    }
    fun clearAttachments() { synchronized(draftLock) { attachmentDrafts[ui.botId] = emptyList() }; localRevision++; ui = ui.copy(attachments = emptyList()); persistDrafts() }
    fun openLink(value: String) {
        if (value.startsWith("#vault:")) {
            if (!ensurePaired()) return
            val path = Uri.decode(value.removePrefix("#vault:")).let { if (it.endsWith(".md")) it else "$it.md" }
            openVault(path); return
        }
        val target = Uri.parse(value)
        if (target.scheme !in listOf("http", "https") || target.host.isNullOrBlank()) { toast("只支持 HTTP(S) 网页链接。"); return }
        try { activity.startActivity(Intent(Intent.ACTION_VIEW, target)) } catch (e: Exception) { toast("没有可用的浏览器。") }
    }
    private fun sendPhone(text: String) {
        if (text.length > 6000) { toast("手机任务最多 6000 字，请分成几步交代。"); return }
        val selectedBot = ui.botId; val originalDraft = ui.draft; val context = ui.messages.filter { it.botId == selectedBot }.takeLast(12)
        ui = ui.copy(busyBot = selectedBot)
        perform {
            try {
                RootPhoneHarness.submit(activity, selectedBot, text, context)
                if (draft(selectedBot) == originalDraft) {
                    synchronized(draftLock) { drafts[selectedBot] = "" }; localRevision++
                    if (ui.botId == selectedBot) ui = ui.copy(draft = "")
                    withContext(Dispatchers.IO) { persistDrafts().get() }
                }
                applyPhoneState(); toast("手机独立工作台已开始执行，可随时查看或停止。")
            } finally { ui = ui.copy(busyBot = null) }
        }
    }
    fun stop() {
        val phone = RootPhoneHarness.changes.value
        if (phone.active && phone.botId == ui.botId) { RootPhoneHarness.stop(activity); toast("正在停止手机执行…"); return }
        perform { request("POST", if (ui.computerBot == ui.botId) "/api/computer/stop" else "/api/stop"); toast("正在停止执行…"); refresh() }
    }
    fun reply(message: PulseMessage?) { ui = ui.copy(reply = message) }
    fun copy(text: String) { activity.getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newPlainText("Pulse Bot", text)); toast("已复制。") }
    fun confirm(message: PulseMessage, accepted: Boolean) = perform {
        request("POST", "/api/confirm", JSONObject().put("id", message.id).put("accepted", accepted)); refresh()
    }
    fun ackDemo(message: PulseMessage) = perform { request("POST", "/api/ack", JSONObject().put("ids", JSONArray().put(message.id))); refresh(); toast("演练已结束。") }
    fun close() { if (activeInstance === this) activeInstance = null; draftJob?.cancel(); persistDrafts(); draftWriter.shutdown(); scope.cancel() }
}

internal fun JSONArray.objects(): List<JSONObject> = (0 until length()).map { getJSONObject(it) }
