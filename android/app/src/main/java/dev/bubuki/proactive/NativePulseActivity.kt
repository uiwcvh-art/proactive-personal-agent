package dev.bubuki.proactive

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.os.Bundle
import android.os.SystemClock
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
import kotlinx.coroutines.*
import kotlinx.coroutines.sync.Mutex
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.concurrent.Executors
import java.util.concurrent.Future

/**
 * [Module] NativePulseActivity
 * [Responsibility]
 * 原生宿主 Activity、領域數據模型與核心對話會話控制器：管理 Activity 生命周期與預測式返回手勢、
 * 定義不可變 UI 狀態樹 (`NativePulseState`, `PulseBot`, `PulseMessage`, `PulseAttachment`, `PulseChoice`)，
 * 並封裝核心對話會話操作（打開對話、草稿持久化、發送消息、中止回合、編輯重發與重新生成）。
 * 網絡同步與流式狀態機委派至 [PulseActivitySync]，Bot/Vault/設置管理委派至 [PulseActivityBotOps]，
 * 多媒體與雲端桌面/權限交互委派至 [PulseActivityMedia]。
 *
 * [Public API]
 * - `NativePulseActivity`
 * - `PulseBot`, `PulseAttachment`, `PulseChoice`, `PulseMessage`, `PulseMenuItem`, `PulseMenu`, `NativePulseState`
 * - `NativePulseModel`: `open(...)`, `back()`, `setDraft(...)`, `send()`, `stop(...)`, `editMessageAndResend(...)`, `rerollMessage(...)`
 */
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
        if (savedInstanceState == null) {
            model.handleIncomingShareIntent(intent)
        }
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
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        intent.getStringExtra("openBot")?.let(model::requestOpenBot)
        if (intent.getStringExtra("entryRoute") == "settings") model.openSettings()
        model.handleIncomingShareIntent(intent)
    }
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
            while (isActive && (resumed || model.hasInFlightWork())) {
                val wsOnline = AgentForegroundService.connected
                val fast = model.hasInFlightWork() || (resumed && model.ui.activePanel == "cloud_desktop")
                if (resumed && model.ui.activePanel == "cloud_desktop") {
                    if (!wsOnline) {
                        model.refreshStreamOnly()
                    }
                    delay(if (wsOnline) 3000L else 1100L)
                } else if (fast) {
                    if (!wsOnline) {
                        model.refreshStreamOnly()
                    }
                    delay(if (wsOnline) 12_000L else if (!resumed) 4500L else 800L)
                } else {
                    if (!resumed) break
                    if (!wsOnline) {
                        if (tick % 4 == 0) {
                            model.refresh()
                        } else {
                            model.refreshStreamOnly()
                        }
                        tick++
                        delay(5500L)
                    } else {
                        delay(45_000L)
                        if (isActive && resumed) {
                            model.refreshStreamOnly()
                        }
                    }
                }
            }
        }
    }
    override fun onPause() { resumed = false; if (!model.hasInFlightWork()) poll?.cancel(); model.pause(); model.persistDrafts(); super.onPause() }
    override fun onStop() { resumed = false; if (!model.hasInFlightWork()) poll?.cancel(); model.pause(); super.onStop() }
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
        fun from(j: JSONObject, source: String = "phone"): PulseBot {
            val normSrc = source.trim().lowercase().ifBlank { "phone" }
            val nodeCfg = j.optJSONObject("node_settings")?.optJSONObject(normSrc)
            val id = j.getString("id")
            return PulseBot(
                id, j.optString("name"), j.optString("description"),
                j.optString("color", "#B568F7"), j.optString("shape", if (id == "companion") "flower" else "circle"), j.optString("engine", "small"),
                nodeCfg?.optBoolean("pinned", j.optBoolean("pinned")) ?: j.optBoolean("pinned"),
                nodeCfg?.optBoolean("hidden", j.optBoolean("hidden")) ?: j.optBoolean("hidden"),
                nodeCfg?.optDouble("pinned_at", j.optDouble("pinned_at", 0.0)) ?: j.optDouble("pinned_at", 0.0),
                j.optString("avatar_version"),
                j.optString("instructions", ""),
                nodeCfg?.optString("model", j.optString("model", "")) ?: j.optString("model", ""),
                nodeCfg?.optBoolean("curiosity_enabled", j.optBoolean("curiosity_enabled", id == "companion")) ?: j.optBoolean("curiosity_enabled", id == "companion"),
                nodeCfg?.optBoolean("notify", j.optBoolean("notify", true)) ?: j.optBoolean("notify", true)
            )
        }
    }
}

internal data class PulseAttachment(val name: String, val path: String, val source: String, val bytes: Long = 0, val sha256: String = "") {
    fun json() = JSONObject().put("name", name).put("path", path).put("source", source).put("bytes", bytes).put("sha256", sha256)
    companion object { fun from(j: JSONObject) = PulseAttachment(j.optString("name"), j.getString("path"), j.optString("source", "workspace"), j.optLong("bytes"), j.optString("sha256")) }
}

internal data class PulseChoice(val label: String, val value: String) {
    fun json(): JSONObject = JSONObject().put("label", label).put("value", value)
    companion object {
        fun from(j: JSONObject) = PulseChoice(
            j.optString("label", j.optString("value", "")).trim(),
            j.optString("value", j.optString("label", "")).trim()
        )
        fun parseArray(rawChoices: JSONArray?): List<PulseChoice> {
            if (rawChoices == null) return emptyList()
            return (0 until rawChoices.length()).mapNotNull { idx ->
                val item = rawChoices.optJSONObject(idx)
                if (item != null) from(item).takeIf { it.label.isNotBlank() }
                else rawChoices.optString(idx).trim().takeIf { it.isNotBlank() }?.let { PulseChoice(it, it) }
            }
        }
    }
}

internal data class PulseMessage(
    val id: String, val botId: String, val role: String, val text: String, val kind: String, val engine: String,
    val created: Double, val decision: String, val trace: String, val optimistic: Boolean = false,
    val fromBotId: String = "", val fromBotName: String = "",
    val choices: List<PulseChoice> = emptyList(),
    val isSubTurn: Boolean = false,
    val thought: String = "",
    val sticker: String = "",
    val apiCalls: Int = 0,
    val ttftMs: Int = 0,
    val tps: Double = 0.0,
    val promptTokens: Int = 0,
    val cachedTokens: Int = 0,
    val cacheHitPct: Int = 0,
    val completionTokens: Int = 0,
    val totalMs: Int = 0,
    val toBotId: String = "",
    val toBotName: String = "",
    val direction: String = ""
) {
    companion object {
        fun from(j: JSONObject): PulseMessage {
            val parsedChoices = PulseChoice.parseArray(j.optJSONArray("choices"))
            val m = j.optJSONObject("metrics")
            val apiCalls = j.optInt("api_calls", m?.optInt("api_calls", 0) ?: 0)
            return PulseMessage(
                j.getString("id"), j.optString("bot_id", "companion"), j.optString("role"),
                j.optString("text"), j.optString("kind"), j.optString("engine"), j.optDouble("created_at"),
                j.optString("decision"), (j.optJSONArray("trace") ?: JSONArray()).toString(), false,
                j.optString("from_bot_id", ""), j.optString("from_bot_name", ""),
                parsedChoices,
                j.optBoolean("is_sub_turn", false) || j.optString("kind") == "bot_message",
                j.optString("thought", ""),
                j.optString("sticker", ""),
                apiCalls,
                m?.optInt("ttft_ms", 0) ?: 0,
                m?.optDouble("tps", 0.0)?.let { if (it.isNaN()) 0.0 else it } ?: 0.0,
                m?.optInt("prompt_tokens", 0) ?: 0,
                m?.optInt("cached_tokens", 0) ?: 0,
                m?.optInt("cache_hit_pct", 0) ?: 0,
                m?.optInt("completion_tokens", 0) ?: 0,
                m?.optInt("total_ms", 0) ?: 0,
                j.optString("to_bot_id", ""),
                j.optString("to_bot_name", ""),
                j.optString("direction", "")
            )
        }
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
    val dark: Boolean = true, val motion: Boolean = true, val haptics: Boolean = true, val routineCount: Int = 0,
    val avatars: Map<String, ImageBitmap> = emptyMap(), val menu: PulseMenu? = null, val toast: String = "", val toastId: Long = 0,
    val activePanel: String? = null,
    val serverUrl: String = SecureTransport.DEFAULT_SERVER,
    val apiBaseUrl: String = "https://api.openai.com/v1",
    val apiKeyInput: String = "",
    val hasApiKey: Boolean = false,
    val fallbackEnabled: Boolean = false,
    val fallbackBaseUrl: String = "",
    val fallbackApiKeyInput: String = "",
    val fallbackModel: String = "",
    val hasFallbackApiKey: Boolean = false,
    val availableModels: List<String> = emptyList(),
    val fetchingModels: Boolean = false,
    val savingSettings: Boolean = false,
    val curiosityEnabledGlobal: Boolean = true,
    val curiosityMinutes: String = "90",
    val autoCompactTokens: String = "120000",
    val contextTokensByBot: Map<String, Int> = emptyMap(),
    val contextCachedByBot: Map<String, Int> = emptyMap(),
    val contextMsgsByBot: Map<String, Int> = emptyMap(),
    val contextTtftByBot: Map<String, Int> = emptyMap(),
    val contextTpsByBot: Map<String, Double> = emptyMap(),
    val contextCompTokensByBot: Map<String, Int> = emptyMap(),
    val compactingContext: Boolean = false,
    val backgroundService: Boolean = true,
    val appearance: String = "dark",
    val latestReleaseVersion: String = "",
    val latestReleaseNotes: String = "",
    val updateAvailable: Boolean = false,
    val checkingUpdate: Boolean = false,
    val downloadingApk: Boolean = false,
    val downloadProgress: Int = 0,
    val editingBotId: String? = null,
    val editBotName: String = "",
    val editBotDesc: String = "",
    val editBotModel: String = "",
    val editBotInstructions: String = "",
    val editBotColor: String = "#B568F7",
    val editBotShape: String = "flower",
    val editBotCuriosity: Boolean = false,
    val editBotNotify: Boolean = true,
    val savingBot: Boolean = false,
    val vaultFiles: List<String> = emptyList(),
    val selectedVaultPath: String = "INDEX.md",
    val vaultContent: String = "",
    val vaultLoading: Boolean = false,
    val vaultSaving: Boolean = false,
    val streamBotId: String? = null,
    val streamSpeakerId: String? = null,
    val streamSpeakerName: String = "",
    val streamText: String = "",
    val streamThought: String = "",
    val streamTool: String = "",
    val streamTrace: List<JSONObject> = emptyList(),
    val streamChoices: List<PulseChoice> = emptyList(),
    val consumedChoiceIds: Set<String> = emptySet(),
    val streamStartedAt: Long = 0L,
    val activeTypewriterMsgId: String? = null,
    val activeTypewriterChars: Int = Int.MAX_VALUE,
    val cloudDesktopFrame: ImageBitmap? = null,
    val cloudDesktopHash: String = "",
    val cloudDesktopUrl: String = "",
    val cloudDesktopTitle: String = "",
    val cloudDesktopLoading: Boolean = false,
    val cloudDesktopTapNorm: Pair<Float, Float>? = null,
    val cloudDesktopLiveBotId: String? = null,
    val cloudDesktopLiveOp: String = "",
    val cloudDesktopPipVisible: Boolean = false,
    val schedulesData: JSONObject? = null,
    val schedulesLoading: Boolean = false,
    val streamSubAgents: List<JSONObject> = emptyList(),
    val streamTodos: List<JSONObject> = emptyList(),
    val pinnedMemories: List<JSONObject> = emptyList(),
    val watchdogs: List<JSONObject> = emptyList(),
    val stickerPacks: List<JSONObject> = emptyList(),
    val editingMessage: PulseMessage? = null,
    val lastSwitchDir: Int = 1,
    val onboardingStep: Int = 1,
    val dshPeers: List<JSONObject> = emptyList(),
    val testingConnection: Boolean = false,
    val connectionTestOk: Boolean? = null,
    val connectionTestMsg: String = ""
)

internal class NativePulseModel(internal val activity: NativePulseActivity, restoredBot: String?) {
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    var ui by mutableStateOf(NativePulseState(botId = restoredBot ?: "companion", chatOpen = restoredBot != null)); internal set
    var predictiveBack by mutableStateOf<Float?>(null)
    var backFrom: Float? = null
    var backSettlement by mutableStateOf(0)
    var navigationRunning = false
    internal var prefs = JSONObject()
    internal val drafts = mutableMapOf<String, String>()
    internal val attachmentDrafts = mutableMapOf<String, List<PulseAttachment>>()
    internal var localRevision = 0
    internal var pendingOpenBot: String? = restoredBot
    internal var pickerBot: String? = null
    internal var pickerSource: String? = null
    internal val draftLock = Any()
    internal val draftWriter = Executors.newSingleThreadExecutor { task -> Thread(task, "native-drafts").apply { priority = Thread.MIN_PRIORITY } }
    internal var draftJob: Job? = null
    internal var revision = 0
    internal var active = false
    internal val refreshLock = Mutex()
    internal val streamLock = Mutex()
    internal var lastStreamSeq: Long = 0L
    internal var lastMessagesEpoch: Long = 0L
    internal var currentTurnId: String = ""
    internal val completedTurnIds = LinkedHashSet<String>()
    internal var sendGuardUntilUptime: Long = 0L
    internal var sendGuardBotId: String? = null
    internal var typewriterJob: Job? = null
    internal var streamPacerJob: Job? = null
    internal var targetStreamFullText: String = ""
    internal val avatarLoading = mutableSetOf<String>()
    internal val avatarVersions = mutableMapOf<String, String>()
    internal val optimisticList = mutableListOf<PulseMessage>()
    internal var inFlightSends = 0
    internal var remoteMessages: List<PulseMessage> = emptyList()
    internal var remoteWorkingBot: String? = null
    internal var remoteWorkingText = ""
    internal var cloudStreamJob: Job? = null
    internal val stickerCache = android.util.LruCache<String, ImageBitmap>(48)
    internal val clock = SimpleDateFormat("HH:mm", Locale.CHINA).apply { timeZone = TimeZone.getTimeZone("Asia/Shanghai") }
    internal val calendar = SimpleDateFormat("M月d日", Locale.CHINA).apply { timeZone = TimeZone.getTimeZone("Asia/Shanghai") }
    companion object {
        @Volatile private var activeInstance: NativePulseModel? = null
        @JvmStatic internal fun setActiveInstance(inst: NativePulseModel?) {
            activeInstance = inst
        }
        @JvmStatic fun dispatchRealtimeEvent(event: JSONObject) {
            activeInstance?.onRealtimeEvent(event)
        }
        @JvmStatic fun isForeground(): Boolean = activeInstance?.active == true
        @JvmStatic fun syncOnReconnect() {
            activeInstance?.let { inst ->
                inst.scope.launch { runCatching { inst.refreshStreamOnly() } }
            }
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
        val savedAppearance = prefs.optString("appearance", "dark").let { if (it == "light") "light" else "dark" }
        val dark = savedAppearance != "light"
        val cfg = prefs.optJSONObject("config") ?: JSONObject()
        val defModel = cfg.optString("default_model").ifBlank { cfg.optJSONObject("big_brain")?.optString("model").orEmpty().ifBlank { cfg.optJSONObject("small_brain")?.optString("model").orEmpty() } }
        val defUrl = cfg.optString("api_base_url").ifBlank { cfg.optJSONObject("big_brain")?.optString("base_url").orEmpty().ifBlank { "https://api.openai.com/v1" } }
        ui = ui.copy(
            bots = cached.first, primary = cached.second, botId = initialBot,
            messages = combinedMessages(),
            chatOpen = restoredBot != null && cached.first.any { it.id == restoredBot },
            activePanel = null,
            dark = dark, paired = prefs.optString("deviceToken", SecureTransport.defaultToken()).ifBlank { SecureTransport.defaultToken() }.isNotBlank(),
            draft = draft(initialBot), attachments = attachments(initialBot), mode = draftMode(initialBot),
            defaultModel = defModel, apiBaseUrl = defUrl, serverUrl = prefs.optString("serverUrl", SecureTransport.DEFAULT_SERVER),
            backgroundService = prefs.optBoolean("background", true), appearance = savedAppearance,
            motion = prefs.optBoolean("motion", true) && !animationsOff(activity), haptics = prefs.optBoolean("haptics", true)
        )
        activity.systemBarsFor(dark)
        RootPhoneHarness.initialize(activity)
        scope.launch {
            var lastPhoneSig = ""
            RootPhoneHarness.changes.collect { st ->
                val sig = "${st.active}|${st.botId}|${st.phase}|${st.messages.size}|${st.messages.lastOrNull()?.id}|${st.messages.lastOrNull()?.text?.length}"
                if (sig != lastPhoneSig) {
                    lastPhoneSig = sig
                    applyPhoneState()
                }
            }
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
        loadMemoryPins()
        fetchStickerPacks()
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
    internal fun draft(id: String) = synchronized(draftLock) { drafts[id] ?: "" }
    fun draftOf(id: String): String = draft(id)
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
    internal fun menu(title: String, items: List<PulseMenuItem>) { ui = ui.copy(menu = PulseMenu(title, items)) }
    internal fun perform(task: suspend () -> Unit) { scope.launch { try { task() } catch (e: Exception) { if (e is CancellationException) throw e; toast(MainActivity.safeError(e)) } } }
    fun toast(message: String) { ui = ui.copy(toast = message, toastId = ui.toastId + 1) }
    internal fun ensurePaired(): Boolean { if (ui.paired) return true; openSettings(); return false }
    fun sendQuickPrompt(prompt: String) {
        if (prompt.isBlank()) return
        setDraft(prompt)
        send()
    }
    fun consumeChoiceAndSend(messageId: String, prompt: String) {
        if (prompt.isBlank()) return
        ui = ui.copy(
            consumedChoiceIds = ui.consumedChoiceIds + messageId,
            streamChoices = emptyList()
        )
        sendQuickPrompt(prompt)
    }
    fun retryMessage(msg: PulseMessage) {
        rerollMessage(msg)
    }
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
        ui.editingMessage?.let { editing ->
            editMessageAndResend(editing, ui.draft)
            return
        }
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
            streamChoices = emptyList(),
            streamSubAgents = emptyList(),
            streamTodos = emptyList(),
            streamStartedAt = System.currentTimeMillis(),
            reply = null,
            messages = combinedMessages()
        )
        perform {
            try {
                val res = request("POST", "/api/chat", JSONObject().put("text", text).put("bot_id", id).put("mode", "auto").put("async_ack", true).put("client_msg_id", pending.id))
                val respEpoch = res.optLong("messages_epoch", 0L)
                if (respEpoch > 0L) lastMessagesEpoch = respEpoch
                val seq = res.optLong("seq", 0L)
                if (seq > lastStreamSeq) lastStreamSeq = seq
                val turnId = res.optString("turn_id", "")
                val isQueued = res.optBoolean("queued", false)
                if (turnId.isNotBlank()) {
                    if (isQueued) {
                        completedTurnIds.remove(turnId)
                        currentTurnId = turnId
                        sendGuardBotId = id
                        sendGuardUntilUptime = SystemClock.uptimeMillis() + 30_000L
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
                runCatching { refresh(force = true) }
                val delivered = remoteMessages.takeLast(16).any { it.role == "user" && it.botId == id && it.text.trim() == text.trim() } ||
                    ui.workingBot == id || ui.streamBotId == id
                if (delivered) {
                    optimisticList.removeAll { it.id == pending.id }
                    ui = ui.copy(messages = combinedMessages())
                } else {
                    ui = ui.copy(messages = combinedMessages())
                    throw e
                }
            } finally {
                inFlightSends = (inFlightSends - 1).coerceAtLeast(0)
                if (inFlightSends == 0) ui = ui.copy(busyBot = null)
            }
        }
    }
    fun stop(targetBot: String = ui.botId) {
        val phone = RootPhoneHarness.changes.value
        val hasPhoneToolInTurn = (phone.active && phone.botId == targetBot) ||
            (ui.streamBotId == targetBot && (ui.streamTool == "phone_workspace" ||
                ui.streamTrace.any { it.optString("tool") == "phone_workspace" }))
        if (hasPhoneToolInTurn) {
            if (phone.active) {
                RootPhoneHarness.stop(activity, false)
            }
            AgentForegroundService.abortPhoneCommands(activity)
        }
        if (phone.active && phone.botId == targetBot) {
            if (ui.streamBotId != targetBot && remoteWorkingBot != targetBot) {
                return
            }
        }
        val isStreamingTarget = ui.streamBotId == null || ui.streamBotId == targetBot
        val partialText = if (isStreamingTarget) ui.streamText.takeIf { !isStreamPlaceholder(it) && it.isNotBlank() } else null
        if (partialText != null && !(phone.active && phone.botId == targetBot)) {
            val frozenMsg = PulseMessage(
                "stopped-${System.currentTimeMillis()}",
                targetBot,
                "assistant",
                partialText,
                "chat",
                "small",
                System.currentTimeMillis() / 1000.0,
                "",
                JSONArray(ui.streamTrace).toString(),
                false,
                targetBot,
                bot(targetBot)?.name ?: targetBot,
                ui.streamChoices
            )
            mergeRemoteMessages(listOf(frozenMsg), fullReplace = false)
        }
        if (isStreamingTarget) {
            if (currentTurnId.isNotBlank()) markTurnCompleted(currentTurnId)
            streamPacerJob?.cancel()
            targetStreamFullText = ""
        }
        if (remoteWorkingBot == targetBot) remoteWorkingBot = null
        if (sendGuardBotId == targetBot) {
            sendGuardBotId = null
            sendGuardUntilUptime = 0L
        }
        ui = ui.copy(
            messages = combinedMessages(),
            busyBot = if (ui.busyBot == targetBot) null else ui.busyBot,
            workingBot = if (ui.workingBot == targetBot) null else ui.workingBot,
            streamBotId = if (isStreamingTarget) null else ui.streamBotId,
            streamText = if (isStreamingTarget) "" else ui.streamText,
            streamThought = if (isStreamingTarget) "" else ui.streamThought,
            streamTool = if (isStreamingTarget) "" else ui.streamTool,
            streamTrace = if (isStreamingTarget) emptyList() else ui.streamTrace,
            streamChoices = if (isStreamingTarget) emptyList() else ui.streamChoices,
            streamSubAgents = if (isStreamingTarget) emptyList() else ui.streamSubAgents,
            streamTodos = if (isStreamingTarget) emptyList() else ui.streamTodos,
            streamStartedAt = if (isStreamingTarget) 0L else ui.streamStartedAt
        )
        val stopComputerToo = ui.computerBot == targetBot || ui.cloudDesktopLiveBotId == targetBot
        scope.launch {
            runCatching {
                request("POST", "/api/stop", JSONObject().put("bot_id", targetBot))
                if (stopComputerToo) {
                    runCatching { request("POST", "/api/computer/stop") }
                }
                refresh()
            }
        }
    }
    fun startEditingMessage(message: PulseMessage?) {
        val id = ui.botId
        if (message != null) {
            synchronized(draftLock) { drafts[id] = message.text }
            ui = ui.copy(editingMessage = message, draft = message.text)
        } else {
            ui = ui.copy(editingMessage = null)
        }
    }
    fun editMessageAndResend(message: PulseMessage, newText: String) {
        val clean = newText.trim()
        if (clean.isEmpty()) return
        if (!ensurePaired()) return
        val id = message.botId
        draftJob?.cancel()
        synchronized(draftLock) { drafts[id] = "" }
        localRevision++
        persistDrafts()
        val targetIdx = remoteMessages.indexOfFirst { it.id == message.id }
        if (targetIdx >= 0) {
            val updatedList = ArrayList<PulseMessage>(remoteMessages.size)
            for (i in remoteMessages.indices) {
                val cur = remoteMessages[i]
                if (i == targetIdx) {
                    updatedList.add(cur.copy(text = clean))
                } else if (i > targetIdx && cur.botId == id) {
                    continue
                } else {
                    updatedList.add(cur)
                }
            }
            remoteMessages = updatedList
        }
        optimisticList.removeAll { it.botId == id }
        streamPacerJob?.cancel()
        targetStreamFullText = ""
        currentTurnId = "pending-${System.currentTimeMillis()}"
        sendGuardBotId = id
        sendGuardUntilUptime = SystemClock.uptimeMillis() + 15_000L
        ui = ui.copy(
            editingMessage = null,
            draft = if (ui.botId == id) "" else ui.draft,
            busyBot = id,
            workingBot = id,
            workingText = "正在思考…",
            streamBotId = id,
            streamText = "正在思考…",
            streamThought = "",
            streamTool = "",
            streamTrace = emptyList(),
            streamChoices = emptyList(),
            streamSubAgents = emptyList(),
            streamTodos = emptyList(),
            streamStartedAt = System.currentTimeMillis(),
            messages = combinedMessages()
        )
        perform {
            try {
                val res = request(
                    "POST",
                    "/api/messages/edit",
                    JSONObject()
                        .put("message_id", message.id)
                        .put("text", clean)
                        .put("bot_id", id)
                        .put("source", SecureTransport.getNodeSource(activity))
                )
                val newEpoch = res.optLong("messages_epoch", 0L)
                if (newEpoch > 0L) lastMessagesEpoch = newEpoch
                val removedIds = res.optJSONArray("removed_message_ids")?.let { arr ->
                    (0 until arr.length()).mapNotNull { arr.optString(it).takeIf { s -> s.isNotBlank() } }.toHashSet()
                }.orEmpty()
                val editedMsg = res.optJSONObject("edited_message")?.let { runCatching { PulseMessage.from(it) }.getOrNull() }
                if (removedIds.isNotEmpty() || editedMsg != null) {
                    remoteMessages = remoteMessages.mapNotNull { m ->
                        if (m.id in removedIds) null
                        else if (editedMsg != null && m.id == editedMsg.id) editedMsg
                        else m
                    }
                    ui = ui.copy(messages = combinedMessages())
                }
                refresh(force = true)
            } finally {
                if (inFlightSends == 0) ui = ui.copy(busyBot = null)
            }
        }
    }
    fun rerollMessage(message: PulseMessage) {
        if (!ensurePaired()) return
        val id = message.botId
        val msgIdx = remoteMessages.indexOfFirst { it.id == message.id }
        val userIdx = if (msgIdx >= 0) {
            if (remoteMessages[msgIdx].role == "user") msgIdx
            else (msgIdx downTo 0).firstOrNull { remoteMessages[it].botId == id && remoteMessages[it].role == "user" } ?: -1
        } else {
            remoteMessages.indexOfLast { it.botId == id && it.role == "user" }
        }
        if (userIdx >= 0) {
            remoteMessages = remoteMessages.filterIndexed { idx, cur ->
                !(idx > userIdx && cur.botId == id)
            }
        }
        optimisticList.removeAll { it.botId == id }
        streamPacerJob?.cancel()
        targetStreamFullText = ""
        currentTurnId = "pending-${System.currentTimeMillis()}"
        sendGuardBotId = id
        sendGuardUntilUptime = SystemClock.uptimeMillis() + 15_000L
        ui = ui.copy(
            busyBot = id,
            workingBot = id,
            workingText = "正在重新生成…",
            streamBotId = id,
            streamText = "正在重新生成…",
            streamThought = "",
            streamTool = "",
            streamTrace = emptyList(),
            streamChoices = emptyList(),
            streamSubAgents = emptyList(),
            streamTodos = emptyList(),
            streamStartedAt = System.currentTimeMillis(),
            messages = combinedMessages()
        )
        perform {
            try {
                val res = request(
                    "POST",
                    "/api/messages/reroll",
                    JSONObject()
                        .put("bot_id", id)
                        .put("message_id", message.id)
                        .put("source", SecureTransport.getNodeSource(activity))
                )
                val newEpoch = res.optLong("messages_epoch", 0L)
                if (newEpoch > 0L) lastMessagesEpoch = newEpoch
                val removedIds = res.optJSONArray("removed_message_ids")?.let { arr ->
                    (0 until arr.length()).mapNotNull { arr.optString(it).takeIf { s -> s.isNotBlank() } }.toHashSet()
                }.orEmpty()
                if (removedIds.isNotEmpty()) {
                    remoteMessages = remoteMessages.filterNot { it.id in removedIds }
                    ui = ui.copy(messages = combinedMessages())
                }
                refresh(force = true)
            } finally {
                if (inFlightSends == 0) ui = ui.copy(busyBot = null)
            }
        }
    }
    fun reply(message: PulseMessage?) { ui = ui.copy(reply = message) }
    fun copy(text: String) { activity.getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newPlainText("DSB", text)); toast("已复制。") }
    fun confirm(message: PulseMessage, accepted: Boolean) = perform {
        request("POST", "/api/confirm", JSONObject().put("id", message.id).put("accepted", accepted)); refresh()
    }
    fun ackDemo(message: PulseMessage) = perform { request("POST", "/api/ack", JSONObject().put("ids", JSONArray().put(message.id))); refresh(); toast("演练已结束。") }
    fun close() { setActiveInstance(null); draftJob?.cancel(); persistDrafts(); draftWriter.shutdown(); scope.cancel() }
}

internal fun JSONArray.objects(): List<JSONObject> = (0 until length()).map { getJSONObject(it) }
