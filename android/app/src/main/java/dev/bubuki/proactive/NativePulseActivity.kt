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
import kotlin.math.abs
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
                        // When WebSocket is connected, all stream/message/execution events are pushed in 0ms;
                        // only run a lightweight delta check every 45s while the Activity is actively resumed!
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
    // Native panels: null | "settings" | "bot_edit" | "vault"
    val activePanel: String? = null,
    // Settings panel state
    val serverUrl: String = SecureTransport.DEFAULT_SERVER,
    val apiBaseUrl: String = "https://api.openai.com/v1",
    val apiKeyInput: String = "",
    val hasApiKey: Boolean = false,
    val fallbackBaseUrl: String = "http://149.104.25.8:3000/v1",
    val fallbackApiKeyInput: String = "",
    val fallbackModel: String = "commandcode/deepseek/deepseek-v4.1-flash",
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
    // Bot Editor state
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
    val streamChoices: List<PulseChoice> = emptyList(),
    val consumedChoiceIds: Set<String> = emptySet(),
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
    val schedulesLoading: Boolean = false,
    // Multi-agent sub_agents, todos, pinned memories, watchdogs, and message editing
    val streamSubAgents: List<JSONObject> = emptyList(),
    val streamTodos: List<JSONObject> = emptyList(),
    val pinnedMemories: List<JSONObject> = emptyList(),
    val watchdogs: List<JSONObject> = emptyList(),
    val stickerPacks: List<JSONObject> = emptyList(),
    val editingMessage: PulseMessage? = null,
    val lastSwitchDir: Int = 1,
    // Welcome / Onboarding Wizard & DSH Peer state
    val onboardingStep: Int = 1,
    val dshPeers: List<JSONObject> = emptyList(),
    val testingConnection: Boolean = false,
    val connectionTestOk: Boolean? = null,
    val connectionTestMsg: String = ""
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
    private var lastMessagesEpoch: Long = 0L
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
        @JvmStatic fun isForeground(): Boolean = activeInstance?.active == true
        @JvmStatic fun syncOnReconnect() {
            activeInstance?.let { inst ->
                inst.scope.launch { runCatching { inst.refreshStreamOnly() } }
            }
        }
    }

    internal fun hasInFlightWork(): Boolean {
        return inFlightSends > 0 || ui.busyBot != null || ui.workingBot != null || ui.streamBotId != null ||
            (sendGuardBotId != null && SystemClock.uptimeMillis() < sendGuardUntilUptime)
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

    /**
     * Strictly preserves server chronological order for remoteMessages, deduplicates identical assistant
     * replies (e.g. when stopping/pausing a chat or phone workspace turn), and appends in-flight optimisticList
     * items at the tail so clock skew between phone and VPS NEVER causes AI messages to jump above user messages.
     */
    private fun deduplicateAssistantList(rawMerged: List<PulseMessage>): ArrayList<PulseMessage> {
        val out = ArrayList<PulseMessage>(rawMerged.size)
        for (msg in rawMerged) {
            val prev = out.lastOrNull()
            if (prev != null &&
                prev.botId == msg.botId &&
                prev.role == msg.role &&
                prev.role == "assistant" &&
                prev.text.trim().isNotEmpty() &&
                prev.text.trim() == msg.text.trim() &&
                abs(prev.created - msg.created) < 30.0
            ) {
                val keepMsg = when {
                    prev.id.startsWith("stopped-") && !msg.id.startsWith("stopped-") -> msg
                    !prev.id.startsWith("stopped-") && msg.id.startsWith("stopped-") -> prev
                    msg.trace.length > prev.trace.length || msg.choices.size > prev.choices.size -> msg
                    else -> prev
                }
                out[out.lastIndex] = keepMsg.copy(
                    trace = if (msg.trace.length > prev.trace.length) msg.trace else prev.trace,
                    choices = if (msg.choices.isNotEmpty()) msg.choices else prev.choices,
                    thought = if (msg.thought.isNotBlank()) msg.thought else prev.thought,
                    sticker = if (msg.sticker.isNotBlank()) msg.sticker else prev.sticker
                )
            } else {
                out.add(msg)
            }
        }
        return out
    }

    private fun combinedMessages(): List<PulseMessage> {
        val phoneMsgs = RootPhoneHarness.changes.value.messages.filter { it.role != "user" && it.kind != "phone_task" }
        val rawMerged = if (phoneMsgs.isEmpty()) remoteMessages else (remoteMessages + phoneMsgs).distinctBy { it.id }.sortedBy { it.created }
        val baseList = deduplicateAssistantList(rawMerged)
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
        val activeWorking = if (phone.active) {
            phone.botId
        } else {
            remoteWorkingBot ?: ui.streamBotId ?: (if (sendGuardBotId != null && SystemClock.uptimeMillis() < sendGuardUntilUptime) sendGuardBotId else null)
        }
        ui = ui.copy(
            messages = combinedMessages(),
            workingBot = activeWorking,
            workingText = if (phone.active) when (phone.phase) {
                "waiting_user" -> "手机工作区需要你接管"
                "stopping" -> "正在停止手机执行…"
                else -> "正在操作独立手机工作区…"
            } else remoteWorkingText
        )
    }

    private fun scopeOf(settings: JSONObject): String {
        val value = settings.optString("serverUrl", SecureTransport.DEFAULT_SERVER).trim().trimEnd('/') + "\u0000" + settings.optString("publicKey") + "\u0000" + settings.optString("deviceToken", SecureTransport.defaultToken()).ifBlank { SecureTransport.defaultToken() } + "\u0000" + SecureTransport.getNodeSource(activity)
        return java.security.MessageDigest.getInstance("SHA-256").digest(value.toByteArray()).joinToString("") { "%02x".format(it.toInt() and 255) }
    }
    private fun homeCache(settings: JSONObject): Triple<List<PulseBot>, String, List<PulseMessage>> {
        val cache = settings.optJSONObject("nativeHomeCache")
        if (cache?.optString("scope") == scopeOf(settings)) runCatching {
            val myNodeSource = SecureTransport.getNodeSource(activity)
            val bots = cache.getJSONArray("bots").objects().map { PulseBot.from(it, myNodeSource) }
            val primary = cache.getString("primary")
            val msgs = (cache.optJSONArray("messages") ?: JSONArray()).objects().map(PulseMessage::from)
            return Triple(bots, primary, msgs)
        }
        return Triple(
            listOf(
                PulseBot(
                    "companion",
                    "白kawa",
                    "聊聊近况，把挂心的事理清楚。",
                    "#B568F7",
                    "flower",
                    "small",
                    false,
                    false,
                    0.0,
                    "",
                    instructions = "",
                    curiosityEnabled = true
                ),
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
                    .put("choices", JSONArray(m.choices.map { it.json() }))
                    .put("from_bot_id", m.fromBotId).put("from_bot_name", m.fromBotName)
                    .put("thought", m.thought).put("sticker", m.sticker)
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
            val savedApp = settings.optString("appearance", "dark").let { if (it == "light") "light" else "dark" }
            val dark = savedApp != "light"
            val config = settings.optJSONObject("config") ?: JSONObject()
            val smallM = config.optJSONObject("small_brain")?.optString("model") ?: ""
            val bigM = config.optJSONObject("big_brain")?.optString("model") ?: ""
            val defM = config.optString("default_model").ifBlank { bigM.ifBlank { smallM } }
            ui = ui.copy(
                paired = settings.optString("deviceToken", SecureTransport.defaultToken()).ifBlank { SecureTransport.defaultToken() }.isNotBlank(), dark = dark, appearance = savedApp,
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
                if (ui.activePanel == "cloud_desktop" && cloudStreamJob?.isActive != true) {
                    startCloudDesktopStreamLoop(forceFull = false)
                }
            }
            refresh()
        }
    }

    private fun isStreamPlaceholder(t: String): Boolean {
        return t.isBlank() || t == "正在思考…" || t == "正在思考与执行任务…" || t == "正在整理回复…" || t == "正在重新生成…" || t.startsWith("正在调用 ") || t.startsWith("正在并行执行 ") || t.startsWith("⏳ ") || t.startsWith("⏰ 正在执行") || t.startsWith("📦 ")
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
     * Monotonically merges streaming tool trace arrays within the same turn so a tool call item
     * NEVER flickers or disappears between `tc_delta`, `execution`, and `stream` updates.
     */
    private fun mergeMonotonicTrace(
        prev: List<JSONObject>,
        incoming: List<JSONObject>,
        sameBot: Boolean
    ): List<JSONObject> {
        if (!sameBot || prev.isEmpty()) return incoming
        if (incoming.isEmpty()) return prev
        if (incoming.size > prev.size) return incoming
        if (incoming.size < prev.size) {
            val result = prev.toMutableList()
            incoming.forEachIndexed { idx, item ->
                val prevItem = result[idx]
                val prevDone = prevItem.optString("status") == "done"
                val incDone = item.optString("status") == "done"
                if (incDone || !prevDone) {
                    result[idx] = item
                }
            }
            return result
        }
        return incoming.mapIndexed { idx, item ->
            val prevItem = prev[idx]
            val prevDone = prevItem.optString("status") == "done"
            val incDone = item.optString("status") == "done"
            if (prevDone && !incDone && prevItem.optString("tool") == item.optString("tool")) {
                prevItem
            } else {
                item
            }
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
        val rawMerged = if (fullReplace) {
            incoming
        } else {
            val byId = LinkedHashMap<String, PulseMessage>(remoteMessages.size + incoming.size)
            remoteMessages.forEach { local ->
                val replacedByServerStop = local.id.startsWith("stopped-") && incoming.any { inc ->
                    !inc.id.startsWith("stopped-") && inc.botId == local.botId && inc.role == "assistant" &&
                        (inc.text.trim() == local.text.trim() || abs(inc.created - local.created) < 20.0)
                }
                if (!replacedByServerStop) {
                    byId[local.id] = local
                }
            }
            incoming.forEach { byId[it.id] = it }
            byId.values.toList()
        }
        val merged = deduplicateAssistantList(rawMerged)
        remoteMessages = merged
        optimisticList.removeAll { opt ->
            merged.takeLast(18).any { it.role == "user" && it.botId == opt.botId && it.text.trim() == opt.text.trim() }
        }
        // Clear sendGuard when a brand-new final assistant message arrives for the active bot
        if (!fullReplace && oldIds.isNotEmpty()) {
            val newestAssistant = incoming.lastOrNull { it.role == "assistant" && !it.isSubTurn && it.id !in oldIds }
            if (newestAssistant != null && newestAssistant.id == merged.lastOrNull { it.botId == newestAssistant.botId }?.id) {
                if (sendGuardBotId == newestAssistant.botId) {
                    sendGuardBotId = null
                    sendGuardUntilUptime = 0L
                }
            }
        }
    }

    private fun startMessageTypewriter(msgId: String, totalLength: Int, startChars: Int) {
        streamPacerJob?.cancel()
        targetStreamFullText = ""
        typewriterJob?.cancel()
        ui = ui.copy(activeTypewriterMsgId = null, activeTypewriterChars = Int.MAX_VALUE)
    }

    private fun applyPacedStreamText(sBot: String?, resolvedText: String): String {
        if (sBot == null) {
            streamPacerJob?.cancel()
            targetStreamFullText = ""
            return ""
        }
        if (isStreamPlaceholder(resolvedText)) {
            streamPacerJob?.cancel()
            return resolvedText
        }
        targetStreamFullText = resolvedText; streamPacerJob?.cancel(); if (true) return resolvedText
        if (!ui.motion) {
            streamPacerJob?.cancel()
            return resolvedText
        }
        val curShown = ui.streamText.takeIf { !isStreamPlaceholder(it) && ui.streamBotId == sBot }.orEmpty()
        val initialShown = when {
            curShown.isEmpty() && resolvedText.length > 6 -> resolvedText.take(3)
            curShown.isNotEmpty() && resolvedText.startsWith(curShown) && (resolvedText.length - curShown.length) > 6 ->
                resolvedText.take((curShown.length + 2).coerceAtMost(resolvedText.length))
            else -> {
                streamPacerJob?.cancel()
                return resolvedText
            }
        }
        if (streamPacerJob?.isActive != true) {
            streamPacerJob = scope.launch {
                while (true) {
                    delay(18L)
                    val target = targetStreamFullText
                    val current = ui.streamText.takeIf { !isStreamPlaceholder(it) }.orEmpty()
                    if (target.isBlank() || ui.streamBotId == null || current.length >= target.length || !target.startsWith(current)) {
                        if (target.isNotBlank() && ui.streamBotId != null && current != target) {
                            ui = ui.copy(streamText = target)
                        }
                        break
                    }
                    val backlog = target.length - current.length
                    val step = (backlog / 6).coerceIn(2, 16)
                    val nextLen = (current.length + step).coerceAtMost(target.length)
                    ui = ui.copy(streamText = target.take(nextLen))
                    if (nextLen >= target.length) break
                }
            }
        }
        return initialShown
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
            val mySource = SecureTransport.getNodeSource(activity)
            val evSource = event.optString("source").ifBlank {
                event.optJSONObject("message")?.optString("source")
                    ?: event.optJSONObject("stream")?.optString("source")
                    ?: event.optJSONObject("execution")?.optString("source")
                    ?: ""
            }.trim()
            if (evSource.isNotEmpty() && evSource != mySource && type in listOf("message", "stream", "execution", "stopped")) {
                return@launch
            }
            when (type) {
                "bots_changed" -> { val botsArr = event.optJSONArray("bots"); val primaryId = event.optString("primary_bot_id", ui.primary).ifBlank { "companion" }; if (botsArr != null && botsArr.length() > 0) { val nextBots = botsArr.objects().mapNotNull { runCatching { PulseBot.from(it, mySource) }.getOrNull() }; if (nextBots.isNotEmpty()) { revision++; val deletedId = event.optString("deleted_bot_id", ""); val needCloseChat = (deletedId.isNotBlank() && ui.botId == deletedId) || nextBots.none { it.id == ui.botId }; ui = ui.copy(bots = nextBots, primary = primaryId, botId = if (needCloseChat) primaryId else ui.botId, chatOpen = if (needCloseChat) false else ui.chatOpen); cacheRoster(nextBots, primaryId); loadAvatars(); return@launch } }; launch { refresh(force = true) } }
                "messages_truncated" -> {
                    val newEpoch = event.optLong("messages_epoch", 0L)
                    if (newEpoch > 0L) lastMessagesEpoch = newEpoch
                    val removedIds = event.optJSONArray("removed_message_ids")?.let { arr ->
                        (0 until arr.length()).mapNotNull { arr.optString(it).takeIf { s -> s.isNotBlank() } }.toHashSet()
                    }.orEmpty()
                    val editedMsg = event.optJSONObject("edited_message")?.let { runCatching { PulseMessage.from(it) }.getOrNull() }
                    remoteMessages = remoteMessages.mapNotNull { m ->
                        if (m.id in removedIds) null
                        else if (editedMsg != null && m.id == editedMsg.id) editedMsg
                        else m
                    }
                    ui = ui.copy(messages = combinedMessages())
                }
                "cloud_desktop_frame" -> {
                    val frameObj = event.optJSONObject("frame") ?: return@launch
                    val botId = event.optString("bot_id").takeIf { it.isNotBlank() }
                    val op = event.optString("op", "")
                    ui = ui.copy(
                        cloudDesktopLiveBotId = botId ?: ui.cloudDesktopLiveBotId,
                        cloudDesktopLiveOp = op.ifBlank { ui.cloudDesktopLiveOp },
                        cloudDesktopUrl = frameObj.optString("url").takeIf { it.isNotBlank() } ?: ui.cloudDesktopUrl,
                        cloudDesktopTitle = frameObj.optString("title").takeIf { it.isNotBlank() } ?: ui.cloudDesktopTitle
                    )
                    // Only pull or decode heavy frame bitmaps when the user actually has the Cloud Computer panel open!
                    if (ui.activePanel == "cloud_desktop") {
                        if (frameObj.has("image")) applyCloudDesktopPacket(frameObj)
                        else launch { refreshCloudDesktopFrame(forceFull = false) }
                    }
                }
                "stopped" -> {
                    val stoppedBot = event.optString("bot_id").takeIf { it.isNotBlank() }
                    val stoppedTurn = event.optString("turn_id", "")
                    if (stoppedTurn.isNotBlank()) markTurnCompleted(stoppedTurn)
                    if (stoppedBot == null || ui.streamBotId == stoppedBot || ui.workingBot == stoppedBot) {
                        streamPacerJob?.cancel()
                        targetStreamFullText = ""
                        remoteWorkingBot = null
                        sendGuardBotId = null
                        sendGuardUntilUptime = 0L
                        ui = ui.copy(
                            busyBot = null,
                            workingBot = null,
                            streamBotId = null,
                            streamText = "",
                            streamThought = "",
                            streamTool = "",
                            streamTrace = emptyList(),
                            streamChoices = emptyList(),
                            streamSubAgents = emptyList(),
                            streamTodos = emptyList(),
                            streamStartedAt = 0L
                        )
                    }
                }
                "stream" -> {
                    if (event.isNull("stream")) {
                        val clearedBot = event.optString("bot_id", "")
                        if (clearedBot.isBlank() || ui.chatOpen.not() || clearedBot == ui.botId || clearedBot == ui.streamBotId || clearedBot == ui.workingBot) {
                            streamPacerJob?.cancel()
                            targetStreamFullText = ""
                            remoteWorkingBot = null
                            sendGuardBotId = null
                            sendGuardUntilUptime = 0L
                            ui = ui.copy(
                                busyBot = null,
                                workingBot = null,
                                streamBotId = null,
                                streamText = "",
                                streamThought = "",
                                streamTool = "",
                                streamTrace = emptyList(),
                                streamChoices = emptyList(),
                                streamSubAgents = emptyList(),
                                streamTodos = emptyList(),
                                streamStartedAt = 0L
                            )
                        }
                        return@launch
                    }
                    val stream = event.optJSONObject("stream") ?: return@launch
                    val seq = stream.optLong("seq", 0L)
                    val turnId = stream.optString("turn_id", "")
                    if (turnId.isNotBlank() && turnId in completedTurnIds) return@launch
                    if (seq in 1 until lastStreamSeq) return@launch
                    if (seq > lastStreamSeq) lastStreamSeq = seq
                    val sBot = stream.optString("bot_id").takeIf { it.isNotBlank() } ?: return@launch
                    val incomingTrace = stream.optJSONArray("trace")?.objects().orEmpty()
                    val trace = mergeMonotonicTrace(ui.streamTrace, incomingTrace, ui.streamBotId == sBot || ui.workingBot == sBot)
                    val rawTool = if (stream.isNull("tool")) "" else stream.optString("tool", "").takeIf { it != "null" }.orEmpty()
                    val toolName = rawTool.ifBlank {
                        if (ui.streamBotId == sBot && ui.streamTool.isNotBlank()) ui.streamTool
                        else if (trace.isNotEmpty()) "已执行 ${trace.size} 步"
                        else ""
                    }
                    val usesCloudDesktop = toolName == "cloud_desktop" || trace.any { it.optString("tool") == "cloud_desktop" }
                    if (usesCloudDesktop) {
                        ui = ui.copy(cloudDesktopLiveBotId = sBot)
                        if (ui.activePanel == "cloud_desktop") {
                            launch { refreshCloudDesktopFrame(forceFull = false) }
                        }
                    }
                    // If user is currently viewing a specific Bot's chat, ignore unrelated background Bot stream events
                    if (ui.chatOpen && sBot != ui.botId && ui.streamBotId == ui.botId) return@launch
                    val delta = stream.optString("delta", "")
                    val rawText = stream.optString("text", "").ifEmpty {
                        if (delta.isNotEmpty() && !isStreamPlaceholder(ui.streamText)) ui.streamText + delta else delta
                    }
                    val thought = stream.optString("thought", "").ifBlank { ui.streamThought }
                    val subAgents = stream.optJSONArray("sub_agents")?.objects() ?: ui.streamSubAgents
                    val todos = stream.optJSONArray("todos")?.objects() ?: ui.streamTodos
                    val choices = PulseChoice.parseArray(stream.optJSONArray("choices")).ifEmpty {
                        if (ui.streamBotId == sBot) ui.streamChoices else emptyList()
                    }
                    val preserved = resolveStreamText(sBot, turnId, rawText)
                    val paced = applyPacedStreamText(sBot, preserved)
                    val startedMs = ui.streamStartedAt.takeIf { it > 0L } ?: System.currentTimeMillis()
                    sendGuardBotId = sBot
                    sendGuardUntilUptime = SystemClock.uptimeMillis() + 3_000L
                    remoteWorkingBot = sBot
                    val liveCtx = stream.optJSONObject("context_stats")
                    val liveCurTok = liveCtx?.optInt("current_tokens", 0) ?: 0
                    val liveCachedTok = liveCtx?.optInt("cached_tokens", 0) ?: 0
                    val liveTtft = liveCtx?.optInt("ttft_ms", 0) ?: 0
                    val liveTps = liveCtx?.optDouble("tps", 0.0)?.let { if (it.isNaN()) 0.0 else it } ?: 0.0
                    val liveCompTok = liveCtx?.optInt("completion_tokens", 0) ?: 0
                    val nextCtxTokens = if (liveCurTok > 0) (ui.contextTokensByBot + (sBot to liveCurTok)) else ui.contextTokensByBot
                    val nextCtxCached = if (liveCurTok > 0) (ui.contextCachedByBot + (sBot to liveCachedTok)) else ui.contextCachedByBot
                    val nextTtft = if (liveTtft > 0) (ui.contextTtftByBot + (sBot to liveTtft)) else ui.contextTtftByBot
                    val nextTps = if (liveTps > 0.0) (ui.contextTpsByBot + (sBot to liveTps)) else ui.contextTpsByBot
                    val nextComp = if (liveCompTok > 0) (ui.contextCompTokensByBot + (sBot to liveCompTok)) else ui.contextCompTokensByBot
                    ui = ui.copy(
                        online = true,
                        workingBot = sBot,
                        streamBotId = sBot,
                        contextTokensByBot = nextCtxTokens,
                        contextCachedByBot = nextCtxCached,
                        contextTtftByBot = nextTtft,
                        contextTpsByBot = nextTps,
                        contextCompTokensByBot = nextComp,
                        streamSpeakerId = stream.optString("speaker_id").takeIf { it.isNotBlank() },
                        streamSpeakerName = stream.optString("speaker_name", ""),
                        streamText = paced,
                        streamThought = thought,
                        streamTool = toolName,
                        streamTrace = trace,
                        streamChoices = choices,
                        streamSubAgents = subAgents,
                        streamTodos = todos,
                        streamStartedAt = startedMs
                    )
                }
                "execution" -> {
                    val exec = event.optJSONObject("execution")
                    val working = exec?.optString("bot_id")?.takeIf { it.isNotBlank() }
                    val incomingTrace = exec?.optJSONArray("trace")?.objects().orEmpty()
                    val sameBot = working == null || ui.workingBot == null || ui.workingBot == working || ui.streamBotId == working
                    val trace = mergeMonotonicTrace(ui.streamTrace, incomingTrace, sameBot)
                    val rawTool = if (exec == null || exec.isNull("tool")) "" else exec.optString("tool").takeIf { it != "null" }.orEmpty()
                    val tool = rawTool.ifBlank {
                        if (working != null && ui.streamTool.isNotBlank()) ui.streamTool
                        else if (trace.isNotEmpty()) "已执行 ${trace.size} 步"
                        else ""
                    }
                    val usesCloudDesktop = tool == "cloud_desktop" || trace.any { it.optString("tool") == "cloud_desktop" }
                    if (usesCloudDesktop) {
                        ui = ui.copy(cloudDesktopLiveBotId = working ?: ui.botId)
                        if (ui.activePanel == "cloud_desktop") {
                            launch { refreshCloudDesktopFrame(forceFull = false) }
                        }
                    }
                    if (ui.chatOpen && working != null && working != ui.botId && ui.workingBot == ui.botId) return@launch
                    val activeBot = working ?: ui.workingBot ?: ui.streamBotId ?: sendGuardBotId
                    if (activeBot != null) {
                        sendGuardBotId = activeBot
                        sendGuardUntilUptime = SystemClock.uptimeMillis() + 3_000L
                    }
                    remoteWorkingBot = activeBot
                    remoteWorkingText = if (tool.isNotBlank()) "正在调用 $tool…" else "正在思考…"
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
                    // Final assistant replies (`chat`, `bot_reply`, `error`, `stopped`) terminate the turn immediately!
                    val isFinalAssistantMsg = msg.role == "assistant" && !msg.isSubTurn && msg.kind != "bot_message"
                    if (turnId.isNotBlank() && isFinalAssistantMsg) {
                        markTurnCompleted(turnId)
                    }
                    mergeRemoteMessages(listOf(msg), fullReplace = false)
                    val sameOrMatchingTurn = turnId.isBlank() || currentTurnId.isBlank() || turnId == currentTurnId || currentTurnId.startsWith("pending-") || msg.kind == "stopped" || msg.kind == "bot_reply"
                    val finishedStreamHere = isFinalAssistantMsg && (ui.streamBotId == msg.botId || ui.workingBot == msg.botId || sendGuardBotId == msg.botId || ui.busyBot == msg.botId) &&
                        sameOrMatchingTurn
                    val updatedTokensByBot = if (msg.role == "assistant" && msg.promptTokens > 0) {
                        ui.contextTokensByBot + (msg.botId to msg.promptTokens)
                    } else ui.contextTokensByBot
                    val updatedCachedByBot = if (msg.role == "assistant" && msg.promptTokens > 0) {
                        ui.contextCachedByBot + (msg.botId to msg.cachedTokens)
                    } else ui.contextCachedByBot
                    val updatedTtftByBot = if (msg.role == "assistant" && msg.ttftMs > 0) {
                        ui.contextTtftByBot + (msg.botId to msg.ttftMs)
                    } else ui.contextTtftByBot
                    val updatedTpsByBot = if (msg.role == "assistant" && msg.tps > 0f) {
                        ui.contextTpsByBot + (msg.botId to msg.tps)
                    } else ui.contextTpsByBot
                    val updatedCompByBot = if (msg.role == "assistant" && msg.completionTokens > 0) {
                        ui.contextCompTokensByBot + (msg.botId to msg.completionTokens)
                    } else ui.contextCompTokensByBot
                    if (finishedStreamHere) {
                        remoteWorkingBot = null
                        sendGuardBotId = null
                        sendGuardUntilUptime = 0L
                        streamPacerJob?.cancel()
                        targetStreamFullText = ""
                        ui = ui.copy(
                            messages = combinedMessages(),
                            busyBot = null,
                            workingBot = null,
                            streamBotId = null,
                            streamText = "",
                            streamThought = "",
                            streamTool = "",
                            streamTrace = emptyList(),
                            streamChoices = emptyList(),
                            streamSubAgents = emptyList(),
                            streamTodos = emptyList(),
                            streamStartedAt = 0L,
                            contextTokensByBot = updatedTokensByBot,
                            contextCachedByBot = updatedCachedByBot,
                            contextTtftByBot = updatedTtftByBot,
                            contextTpsByBot = updatedTpsByBot,
                            contextCompTokensByBot = updatedCompByBot
                        )
                    } else {
                        ui = ui.copy(
                            messages = combinedMessages(),
                            contextTokensByBot = updatedTokensByBot,
                            contextCachedByBot = updatedCachedByBot,
                            contextTtftByBot = updatedTtftByBot,
                            contextTpsByBot = updatedTpsByBot,
                            contextCompTokensByBot = updatedCompByBot
                        )
                    }
                    if (!active) {
                        cacheRoster(ui.bots, ui.primary)
                    }
                    acknowledge()
                }
            }
        }
    }

    private fun lastServerMessageId(): String? =
        remoteMessages.lastOrNull { !it.id.startsWith("stopped-") && !it.id.startsWith("local-") && !it.optimistic }?.id

    suspend fun refresh(force: Boolean = false) {
        if (!ui.paired || (!force && refreshLock.isLocked)) return
        refreshLock.withLock {
            val before = revision; val started = SystemClock.uptimeMillis(); val serverScope = scopeOf(prefs)
            try {
                val afterId = if (force) null else lastServerMessageId()
                val msgQuery = JSONObject().apply {
                    if (!afterId.isNullOrBlank()) put("after_id", afterId)
                    if (lastMessagesEpoch > 0L) put("messages_epoch", lastMessagesEpoch)
                }.takeIf { it.length() > 0 }
                val statusQuery = JSONObject().apply {
                    if (!afterId.isNullOrBlank()) put("after_id", afterId)
                    if (lastMessagesEpoch > 0L) put("messages_epoch", lastMessagesEpoch)
                }.takeIf { it.length() > 0 }
                val fetched = coroutineScope {
                    val b = async { request("GET", "/api/bots") }
                    val h = async { request("GET", "/api/messages", null, msgQuery) }
                    val s = async { request("GET", "/api/status", null, statusQuery) }
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
                val incomingEpoch = history.optLong("messages_epoch", status.optLong("messages_epoch", 0L))
                val epochChanged = (incomingEpoch > 0L && lastMessagesEpoch > 0L && incomingEpoch != lastMessagesEpoch) ||
                    history.optBoolean("epoch_reset", false) || status.optBoolean("epoch_reset", false)
                if (incomingEpoch > 0L) lastMessagesEpoch = incomingEpoch
                val isDelta = history.optBoolean("delta", false) && !epochChanged
                val messages = history.getJSONArray("messages").objects().map(PulseMessage::from)
                mergeRemoteMessages(messages, fullReplace = !isDelta)
                if (!epochChanged) {
                    status.optJSONArray("recent_tail")?.objects()?.mapNotNull { runCatching { PulseMessage.from(it) }.getOrNull() }?.let {
                        mergeRemoteMessages(it, fullReplace = false)
                    }
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
                val hasLiveExec = execution?.optString("bot_id")?.isNotBlank() == true
                val streamAlreadyDone = !hasLiveExec && streamTurnId.isNotBlank() && streamTurnId in completedTurnIds

                val computer = status.optJSONObject("computer")
                val rawWorking = if (streamAlreadyDone) null else execution?.optString("bot_id")?.takeIf { it.isNotBlank() }
                val rawSBot = if (streamAlreadyDone) null else stream?.optString("bot_id")?.takeIf { it.isNotBlank() }

                // Bridge gap if stream just finished on server but final_message_id hasn't landed in remoteMessages yet
                val finalMsgId = lastDone?.optString("final_message_id").orEmpty()
                val holdDoneStream = rawSBot == null && rawWorking == null && finalMsgId.isNotBlank() && remoteMessages.none { it.id == finalMsgId } &&
                    (!ui.chatOpen || lastDone?.optString("bot_id") == ui.botId)
                val lastRoomMsg = sendGuardBotId?.let { gBot -> remoteMessages.lastOrNull { it.botId == gBot } }
                if (rawSBot == null && rawWorking == null && lastRoomMsg != null && lastRoomMsg.role == "assistant" && !lastRoomMsg.isSubTurn && lastRoomMsg.kind != "bot_message") {
                    sendGuardBotId = null
                    sendGuardUntilUptime = 0L
                }
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
                val execArr = execution?.optJSONArray("trace")?.objects().orEmpty()
                val streamArr = stream?.optJSONArray("trace")?.objects().orEmpty()
                val rawExecTrace = if (streamArr.size >= execArr.size && streamArr.isNotEmpty()) streamArr else execArr
                val sameActiveBot = (effectiveSBot != null || effectiveWorking != null) && (ui.streamBotId == (effectiveSBot ?: effectiveWorking) || ui.workingBot == (effectiveSBot ?: effectiveWorking))
                val execTrace = if (isStaleStatus) ui.streamTrace else if (effectiveSBot == null && effectiveWorking == null) emptyList() else mergeMonotonicTrace(ui.streamTrace, rawExecTrace, sameActiveBot)
                val streamChoices = if (isStaleStatus) ui.streamChoices else if (effectiveSBot == null && effectiveWorking == null) emptyList() else PulseChoice.parseArray(stream?.optJSONArray("choices")).ifEmpty { if (sameActiveBot) ui.streamChoices else emptyList() }
                val sTool = if (stream == null || stream.isNull("tool")) "" else stream.optString("tool").takeIf { it != "null" }.orEmpty()
                val eTool = if (execution == null || execution.isNull("tool")) "" else execution.optString("tool").takeIf { it != "null" }.orEmpty()
                val curTool = if (isStaleStatus) ui.streamTool else (sTool.ifBlank { eTool }.ifBlank { if ((effectiveSBot != null || effectiveWorking != null) && execTrace.isNotEmpty()) ui.streamTool.ifBlank { "已执行 ${execTrace.size} 步" } else "" })
                val usesCloudDesktop = curTool == "cloud_desktop" || execTrace.any { it.optString("tool") == "cloud_desktop" }
                val startedSec = execution?.optDouble("started_at", 0.0) ?: 0.0
                val startedMs = if (startedSec > 0) (startedSec * 1000).toLong() else if (effectiveSBot != null || effectiveWorking != null) (ui.streamStartedAt.takeIf { it > 0L } ?: System.currentTimeMillis()) else 0L
                val routinesCount = (status.optInt("routine_count", -1)).let { if (it >= 0) it else ui.routineCount }
                val (ctxTokensMap, ctxMsgsMap, ctxCachedMap) = parseContextByBot(status.optJSONObject("context_by_bot"))
                val remoteCompactThresh = status.optInt("auto_compact_tokens", remoteCfg?.optInt("auto_compact_tokens", 0) ?: 0)
                val myNodeSource = SecureTransport.getNodeSource(activity)
                ui = ui.copy(
                    bots = if (before == revision) roster.getJSONArray("bots").objects().map { PulseBot.from(it, myNodeSource) } else ui.bots,
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
                    streamChoices = streamChoices,
                    streamStartedAt = startedMs,
                    cloudDesktopTitle = computer?.optString("title")?.takeIf { it.isNotBlank() } ?: ui.cloudDesktopTitle,
                    cloudDesktopUrl = computer?.optString("url")?.takeIf { it.isNotBlank() } ?: ui.cloudDesktopUrl,
                    cloudDesktopLiveBotId = if (usesCloudDesktop) (effectiveSBot ?: effectiveWorking) else ui.cloudDesktopLiveBotId,
                    routineCount = routinesCount,
                    contextTokensByBot = if (ctxTokensMap.isNotEmpty()) ctxTokensMap else ui.contextTokensByBot,
                    contextCachedByBot = if (ctxCachedMap.isNotEmpty()) ctxCachedMap else ui.contextCachedByBot,
                    contextMsgsByBot = if (ctxMsgsMap.isNotEmpty()) ctxMsgsMap else ui.contextMsgsByBot,
                    autoCompactTokens = if (remoteCompactThresh >= 8000 && ui.activePanel != "settings") remoteCompactThresh.toString() else ui.autoCompactTokens,
                    snoozed = status.optDouble("snooze_until", 0.0) * 1000 > System.currentTimeMillis(),
                    smallReady = status.optBoolean("small_ready"), bigReady = status.optBoolean("big_ready"),
                    defaultModel = defM, hasApiKey = hasKey
                )
                if (ui.activePanel == "cloud_desktop") refreshCloudDesktopFrame()
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
                val afterId = lastServerMessageId()
                val statusQuery = JSONObject().apply {
                    if (!afterId.isNullOrBlank()) put("after_id", afterId)
                    if (lastMessagesEpoch > 0L) put("messages_epoch", lastMessagesEpoch)
                }.takeIf { it.length() > 0 }
                val status = request("GET", "/api/status", null, statusQuery)
                val rtt = SystemClock.uptimeMillis() - started
                val incomingEpoch = status.optLong("messages_epoch", 0L)
                val epochReset = status.optBoolean("epoch_reset", false) ||
                    (incomingEpoch > 0L && lastMessagesEpoch > 0L && incomingEpoch != lastMessagesEpoch)
                if (incomingEpoch > 0L) lastMessagesEpoch = incomingEpoch
                if (epochReset && status.has("messages")) {
                    val fullMsgs = status.optJSONArray("messages")?.objects()?.mapNotNull { runCatching { PulseMessage.from(it) }.getOrNull() }.orEmpty()
                    mergeRemoteMessages(fullMsgs, fullReplace = true)
                } else {
                    // 1. Always merge recent_tail immediately so completed messages appear in 0ms without an extra RTT!
                    status.optJSONArray("recent_tail")?.objects()?.mapNotNull { runCatching { PulseMessage.from(it) }.getOrNull() }?.let {
                        mergeRemoteMessages(it, fullReplace = false)
                    }
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
                val hasLiveExec = execution?.optString("bot_id")?.isNotBlank() == true
                val streamAlreadyDone = !hasLiveExec && streamTurnId.isNotBlank() && streamTurnId in completedTurnIds

                val working = if (streamAlreadyDone) null else execution?.optString("bot_id")?.takeIf { it.isNotBlank() }
                val sBot = if (streamAlreadyDone) null else stream?.optString("bot_id")?.takeIf { it.isNotBlank() }

                val finalMsgId = lastDone?.optString("final_message_id").orEmpty()
                val holdDoneStream = sBot == null && working == null && finalMsgId.isNotBlank() && remoteMessages.none { it.id == finalMsgId } &&
                    (!ui.chatOpen || lastDone?.optString("bot_id") == ui.botId)
                val lastRoomMsg = sendGuardBotId?.let { gBot -> remoteMessages.lastOrNull { it.botId == gBot } }
                if (sBot == null && working == null && lastRoomMsg != null && lastRoomMsg.role == "assistant" && !lastRoomMsg.isSubTurn && lastRoomMsg.kind != "bot_message") {
                    sendGuardBotId = null
                    sendGuardUntilUptime = 0L
                }
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
                val execArr = execution?.optJSONArray("trace")?.objects().orEmpty()
                val streamArr = stream?.optJSONArray("trace")?.objects().orEmpty()
                val rawExecTrace = if (streamArr.size >= execArr.size && streamArr.isNotEmpty()) streamArr else execArr
                val sameActiveBot = (effectiveSBot != null || effectiveWorking != null) && (ui.streamBotId == (effectiveSBot ?: effectiveWorking) || ui.workingBot == (effectiveSBot ?: effectiveWorking))
                val execTrace = if (effectiveSBot == null && effectiveWorking == null) emptyList() else mergeMonotonicTrace(ui.streamTrace, rawExecTrace, sameActiveBot)
                val streamChoices = if (effectiveSBot == null && effectiveWorking == null) emptyList() else PulseChoice.parseArray(stream?.optJSONArray("choices")).ifEmpty { if (sameActiveBot) ui.streamChoices else emptyList() }
                val subAgents = stream?.optJSONArray("sub_agents")?.objects() ?: if (effectiveSBot != null || effectiveWorking != null) ui.streamSubAgents else emptyList()
                val todos = stream?.optJSONArray("todos")?.objects() ?: if (effectiveSBot != null || effectiveWorking != null) ui.streamTodos else emptyList()
                val sTool = if (stream == null || stream.isNull("tool")) "" else stream.optString("tool").takeIf { it != "null" }.orEmpty()
                val eTool = if (execution == null || execution.isNull("tool")) "" else execution.optString("tool").takeIf { it != "null" }.orEmpty()
                val curTool = sTool.ifBlank { eTool }.ifBlank { if ((effectiveSBot != null || effectiveWorking != null) && execTrace.isNotEmpty()) ui.streamTool.ifBlank { "已执行 ${execTrace.size} 步" } else "" }
                val usesCloudDesktop = curTool == "cloud_desktop" || execTrace.any { it.optString("tool") == "cloud_desktop" }
                val startedSec = execution?.optDouble("started_at", 0.0) ?: 0.0
                val startedMs = if (startedSec > 0) (startedSec * 1000).toLong() else if (effectiveSBot != null || effectiveWorking != null) (ui.streamStartedAt.takeIf { it > 0L } ?: System.currentTimeMillis()) else 0L
                val (ctxTokensMap, ctxMsgsMap, ctxCachedMap) = parseContextByBot(status.optJSONObject("context_by_bot"))
                val remoteCompactThresh = status.optInt("auto_compact_tokens", 0)
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
                    streamChoices = streamChoices,
                    streamSubAgents = subAgents,
                    streamTodos = todos,
                    streamStartedAt = startedMs,
                    contextTokensByBot = if (ctxTokensMap.isNotEmpty()) ctxTokensMap else ui.contextTokensByBot,
                    contextCachedByBot = if (ctxCachedMap.isNotEmpty()) ctxCachedMap else ui.contextCachedByBot,
                    contextMsgsByBot = if (ctxMsgsMap.isNotEmpty()) ctxMsgsMap else ui.contextMsgsByBot,
                    autoCompactTokens = if (remoteCompactThresh >= 8000 && ui.activePanel != "settings") remoteCompactThresh.toString() else ui.autoCompactTokens,
                    cloudDesktopLiveBotId = if (usesCloudDesktop) (effectiveSBot ?: effectiveWorking) else ui.cloudDesktopLiveBotId
                )
                if (ui.activePanel == "cloud_desktop") refreshCloudDesktopFrame()
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
    fun closePanel() {
        cloudStreamJob?.cancel()
        ui = ui.copy(activePanel = null)
    }

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
        val storedFbUrl = localCfg.optString("fallback_base_url", "http://149.104.25.8:3000/v1")
        val storedFbKey = localCfg.optString("fallback_api_key", "")
        val storedFbModel = localCfg.optString("fallback_model", "commandcode/deepseek/deepseek-v4.1-flash")
        val savedApp = saved.optString("appearance", "dark").let { if (it == "light") "light" else "dark" }
        val savedCompact = localCfg.optInt("auto_compact_tokens", 0).takeIf { it >= 8000 }?.toString() ?: ui.autoCompactTokens
        ui = ui.copy(
            activePanel = "settings",
            serverUrl = saved.optString("serverUrl", SecureTransport.DEFAULT_SERVER),
            apiBaseUrl = storedUrl,
            apiKeyInput = storedKey,
            defaultModel = storedModel.ifBlank { ui.defaultModel },
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
            val rFbUrl = remote.optString("fallback_base_url", "")
            val rFbModel = remote.optString("fallback_model", "")
            val rCompact = remote.optInt("auto_compact_tokens", 120000).coerceIn(8000, 1000000)
            ui = ui.copy(
                apiBaseUrl = if (ui.apiBaseUrl == "https://api.openai.com/v1" && rUrl.isNotBlank()) rUrl else ui.apiBaseUrl,
                defaultModel = ui.defaultModel.ifBlank { rModel },
                hasApiKey = remote.optBoolean("has_api_key", storedKey.isNotBlank()),
                fallbackBaseUrl = rFbUrl.ifBlank { ui.fallbackBaseUrl },
                fallbackModel = rFbModel.ifBlank { ui.fallbackModel },
                hasFallbackApiKey = remote.optBoolean("has_fallback_api_key", storedFbKey.isNotBlank()),
                curiosityEnabledGlobal = remote.optBoolean("enabled", true),
                curiosityMinutes = remote.optInt("curiosity_interval_minutes", 90).toString(),
                autoCompactTokens = rCompact.toString()
            )
        }
    }

    fun checkForUpdates(silent: Boolean = false) {
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

    fun downloadLatestApk() {
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
                // 1. Try silent Root install if Root is available, else fallback to standard Android package installer
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

    fun updateSettingsField(
        serverUrl: String = ui.serverUrl,
        apiBaseUrl: String = ui.apiBaseUrl,
        apiKeyInput: String = ui.apiKeyInput,
        defaultModel: String = ui.defaultModel,
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
                    .put("api_base_url", cleanBase)
                    .put("fallback_base_url", cleanFbBase)
                    .put("fallback_model", cleanFbModel)
                    .put("enabled", ui.curiosityEnabledGlobal)
                    .put("curiosity_interval_minutes", minutes)
                    .put("auto_compact_tokens", compactTokens)
                if (cleanKey.isNotBlank()) updatePayload.put("api_key", cleanKey)
                if (cleanModel.isNotBlank()) updatePayload.put("default_model", cleanModel)
                if (cleanFbKey.isNotBlank()) updatePayload.put("fallback_api_key", cleanFbKey)
                request("POST", "/api/config", updatePayload)
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
                    activePanel = null
                )
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
            editBotInstructions = existing?.instructions ?: "",
            editBotColor = existing?.color ?: "#B568F7",
            editBotShape = existing?.shape ?: "flower",
            editBotCuriosity = existing?.curiosityEnabled ?: false,
            editBotNotify = existing?.notify ?: true
        )
        loadMemoryPins()
        fetchStickerPacks()
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
        startCloudDesktopStreamLoop(forceFull = true)
    }

    private fun startCloudDesktopStreamLoop(forceFull: Boolean = false) {
        cloudStreamJob?.cancel()
        cloudStreamJob = scope.launch {
            refreshCloudDesktopFrame(forceFull = forceFull)
            while (isActive && active && ui.activePanel == "cloud_desktop") {
                val isBusy = ui.workingBot != null || ui.streamBotId != null || ui.busyBot != null
                delay(if (isBusy) 1000L else 2200L)
                if (active && ui.activePanel == "cloud_desktop" && !ui.cloudDesktopLoading) {
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
    private fun draft(id: String) = synchronized(draftLock) { drafts[id] ?: "" }
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
    private fun menu(title: String, items: List<PulseMenuItem>) { ui = ui.copy(menu = PulseMenu(title, items)) }
    fun toggleNodeSource() {
        dismissMenu()
        val cur = SecureTransport.getNodeSource(activity)
        val next = if (cur == "phone2") "phone" else "phone2"
        val label = if (next == "phone2") "安卓二 (phone2)" else "安卓一 (phone)"
        perform {
            withContext(Dispatchers.IO) {
                val s = SecurePrefs.read(activity)
                s.put("nodeSource", next)
                SecurePrefs.save(activity, s)
                prefs = s
                SecureTransport.invalidateCache()
            }
            remoteMessages = emptyList()
            optimisticList.clear()
            ui = ui.copy(messages = emptyList(), streamBotId = null, workingBot = null, streamText = "", streamThought = "")
            runCatching {
                activity.stopService(Intent(activity, AgentForegroundService::class.java))
                if (ui.backgroundService) {
                    activity.startForegroundService(Intent(activity, AgentForegroundService::class.java))
                }
            }
            toast("已切换当前端标识为：$label（多端对话完全独立隔离）")
            refresh(force = true)
        }
    }
    fun newMenu() {
        val curSrc = SecureTransport.getNodeSource(activity)
        val nodeLabel = if (curSrc == "phone2") "安卓二 (平板/副机)" else "安卓一 (主力手机)"
        menu("DSB 工作台 · $nodeLabel", listOf(
            PulseMenuItem("创建新 Bot", "创建专属角色、指令与独立模型", "plus") { openBotEditor(null) },
            PulseMenuItem("切换多端节点身份 (当前：$nodeLabel)", "点击在「安卓一」与「安卓二」之间切换，各端对话独立不串台", "spark") { toggleNodeSource() },
            PulseMenuItem("手机虚拟屏 (桌面悬浮小窗)", "创建并弹出浮在系统桌面上的独立小窗", "screen") { launchFloatingVirtualDisplay() },
            PulseMenuItem("云端桌面 (Cloud Desktop)", "查看云端浏览器实时画面与触控接管", "cloud") { openCloudDesktop() },
            PulseMenuItem("定时闹钟与例行自动化", "查看和管理每日定点提醒、课表闹钟与巡检", "clock") { openSchedules() },
            PulseMenuItem("透明记忆库 (Obsidian Vault)", "查看或修改 Markdown 记忆与画像", "vault") { openVault("INDEX.md") },
            PulseMenuItem("设置中心与检查更新", "日夜主题切换、API 模型配置与一键更新客户端", "settings") { openSettings() },
            PulseMenuItem("一键下载最新客户端更新", "直接从云端拉取并安装最新 APK (当前 v${BuildConfig.VERSION_NAME})", "spark") { downloadLatestApk() }))
    }
    fun botMenu(id: String = ui.botId) {
        val b = bot(id) ?: return
        val (curTok, threshTok, msgCnt) = contextStatsFor(id)
        val (cachedTok, cachePct) = contextCacheFor(id)
        val pct = ((curTok.toFloat() / threshTok.coerceAtLeast(1)) * 100).toInt()
        val entries = mutableListOf<PulseMenuItem>()
        if (!ui.chatOpen || ui.botId != id) entries += PulseMenuItem("打开对话", icon = "chat") { open(id) }
        entries += listOf(
            PulseMenuItem(
                "立即压缩上下文 (${formatTokenCount(curTok)} / ${formatTokenCount(threshTok)} · $pct%)",
                "缓存命中 ${formatTokenCount(cachedTok)} ($cachePct%) · 已积攒 $msgCnt 条消息，点击浓缩归档",
                "spark"
            ) { compactBotContext(id) },
            PulseMenuItem("自定义上下文自动压缩阈值", "当前达到 ${formatTokenCount(threshTok)} Tokens 时自动压缩", "settings") { openSettings() },
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

    internal fun parseContextByBot(obj: JSONObject?): Triple<Map<String, Int>, Map<String, Int>, Map<String, Int>> {
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

    /** Returns Triple(ttftMs, tps, lastCompletionTokens) for the given bot. */
    fun contextPerfFor(botId: String = ui.botId): Triple<Int, Double, Int> {
        val lastBotMsg = ui.messages.lastOrNull { it.botId == botId && it.role == "assistant" && (it.ttftMs > 0 || it.tps > 0.0) }
        val ttft = (ui.contextTtftByBot[botId] ?: lastBotMsg?.ttftMs ?: 0).coerceAtLeast(0)
        val tps = (ui.contextTpsByBot[botId] ?: lastBotMsg?.tps ?: 0.0).coerceAtLeast(0.0)
        val comp = (ui.contextCompTokensByBot[botId] ?: lastBotMsg?.completionTokens ?: 0).coerceAtLeast(0)
        return Triple(ttft, tps, comp)
    }

    internal fun estimateLocalTextTokens(text: String): Int {
        if (text.isEmpty()) return 0
        var cjk = 0
        for (ch in text) {
            if (ch in '\u4e00'..'\u9fff') cjk++
        }
        val other = (text.length - cjk).coerceAtLeast(0)
        return cjk + (other / 3)
    }

    /** Returns Triple(currentTokens, thresholdTokens, messageCount) for the given bot. */
    fun contextStatsFor(botId: String = ui.botId): Triple<Int, Int, Int> {
        val thresh = (ui.autoCompactTokens.toIntOrNull() ?: 120000).coerceIn(8000, 1000000)
        val localMsgs = ui.messages.filter { it.botId == botId }
        val localEst = 14800 + localMsgs.sumOf { estimateLocalTextTokens(it.text) + 12 }
        val serverTok = ui.contextTokensByBot[botId] ?: 0
        val curTok = if (serverTok > 0) serverTok else localEst
        val msgCnt = ui.contextMsgsByBot[botId] ?: localMsgs.size
        return Triple(curTok, thresh, msgCnt)
    }

    /** Returns Pair(cachedTokens, cacheHitPercent 0..100) for the given bot. */
    fun contextCacheFor(botId: String = ui.botId): Pair<Int, Int> {
        val (curTok, _, _) = contextStatsFor(botId)
        val cachedTok = (ui.contextCachedByBot[botId] ?: 0).coerceIn(0, curTok.coerceAtLeast(0))
        val pct = if (curTok > 0 && cachedTok > 0) ((cachedTok.toDouble() / curTok.toDouble()) * 100.0).toInt().coerceIn(0, 100) else 0
        return cachedTok to pct
    }

    fun formatTokenCount(tokens: Int): String {
        return when {
            tokens >= 10_000 -> String.format(Locale.US, "%.1fK", tokens / 1000.0).removeSuffix(".0K") + (if (String.format(Locale.US, "%.1fK", tokens / 1000.0).endsWith(".0K")) "K" else "")
            tokens >= 1_000 -> String.format(Locale.US, "%.1fK", tokens / 1000.0)
            else -> tokens.toString()
        }
    }

    fun compactBotContext(botId: String = ui.botId) {
        if (ui.compactingContext) return
        dismissMenu()
        val thresh = (ui.autoCompactTokens.toIntOrNull() ?: 120000).coerceIn(8000, 1000000)
        ui = ui.copy(compactingContext = true)
        toast("正在浓缩并归档上下文…")
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
                val resultObj = res.optJSONObject("result")
                if (resultObj != null) {
                    val cnt = resultObj.optInt("compacted_messages", 0)
                    val beforeT = resultObj.optInt("before_tokens", 0)
                    val afterT = resultObj.optInt("after_tokens", 0)
                    // Full refresh to replace compacted messages with the new compact_summary bubble
                    val msgResp = request("GET", "/api/messages", null, JSONObject().put("limit", 120))
                    val freshMsgs = msgResp.getJSONArray("messages").objects().map(PulseMessage::from)
                    mergeRemoteMessages(freshMsgs, fullReplace = true)
                    ui = ui.copy(messages = combinedMessages())
                    toast("已压缩 $cnt 条早期对话：${formatTokenCount(beforeT)} → ${formatTokenCount(afterT)} Tokens")
                } else {
                    toast("当前对话条数较少，暂无需压缩")
                }
            } finally {
                ui = ui.copy(compactingContext = false)
            }
        }
    }
    fun actionsMenu() {
        val (curTok, threshTok, msgCnt) = contextStatsFor(ui.botId)
        val (cachedTok, cachePct) = contextCacheFor(ui.botId)
        val pct = ((curTok.toFloat() / threshTok.coerceAtLeast(1)) * 100).toInt()
        menu("工作台与工具", listOf(
            PulseMenuItem(
                "压缩上下文 (${formatTokenCount(curTok)} / ${formatTokenCount(threshTok)} · $pct%)",
                "缓存命中 ${formatTokenCount(cachedTok)} ($cachePct%) · 已积攒 $msgCnt 条对话，点击浓缩归档",
                "spark"
            ) { compactBotContext(ui.botId) },
            PulseMenuItem(
                "自定义上下文压缩阈值",
                "当前满 ${formatTokenCount(threshTok)} 自动压缩，点此修改设置",
                "settings"
            ) { openSettings() },
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
            PulseMenuItem("设置中心与检查更新", "切换日间/夜间模式、API 配置或更新 APK", "settings") { openSettings() },
            PulseMenuItem("免打扰 1 小时", icon = "moon") { job("snooze") }
        ))
    }

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
            val myNodeSource = SecureTransport.getNodeSource(activity)
            val updated = PulseBot.from(request("PATCH", "/api/bots/$id/placement", JSONObject().put(key, value).put("source", myNodeSource)), myNodeSource)
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
    fun pause() {
        active = false
        cloudStreamJob?.cancel()
        streamPacerJob?.cancel()
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

    @Suppress("DEPRECATION")
    fun handleIncomingShareIntent(intent: Intent?) {
        if (intent == null) return
        val act = intent.action ?: return
        if (act != Intent.ACTION_SEND && act != Intent.ACTION_SEND_MULTIPLE && act != Intent.ACTION_PROCESS_TEXT) return
        intent.action = Intent.ACTION_MAIN
        val targetBot = ui.primary.ifBlank { "companion" }
        open(targetBot)
        val sharedTexts = mutableListOf<String>()
        if (act == Intent.ACTION_PROCESS_TEXT) {
            intent.getCharSequenceExtra(Intent.EXTRA_PROCESS_TEXT)?.toString()?.trim()?.takeIf { it.isNotEmpty() }?.let { sharedTexts.add(it) }
        }
        intent.getStringExtra(Intent.EXTRA_SUBJECT)?.trim()?.takeIf { it.isNotEmpty() }?.let { sharedTexts.add(it) }
        intent.getCharSequenceExtra(Intent.EXTRA_TEXT)?.toString()?.trim()?.takeIf { it.isNotEmpty() }?.let { sharedTexts.add(it) }
        if (sharedTexts.isNotEmpty()) {
            val joined = sharedTexts.distinct().joinToString("\n")
            val cur = draft(targetBot).trim()
            setDraft(if (cur.isEmpty()) joined else "$cur\n$joined")
        }
        val uris = mutableListOf<Uri>()
        if (act == Intent.ACTION_SEND) {
            (intent.getParcelableExtra<Uri>(Intent.EXTRA_STREAM))?.let { uris.add(it) }
        } else if (act == Intent.ACTION_SEND_MULTIPLE) {
            intent.getParcelableArrayListExtra<Uri>(Intent.EXTRA_STREAM)?.let { uris.addAll(it) }
        }
        if (uris.isEmpty() && intent.clipData != null) {
            val cd = intent.clipData!!
            for (i in 0 until cd.itemCount) {
                cd.getItemAt(i)?.uri?.let { uris.add(it) }
            }
        }
        val distinctUris = uris.distinct().take(6)
        if (distinctUris.isNotEmpty()) {
            ui = ui.copy(uploading = true)
            perform {
                try {
                    val uploaded = mutableListOf<PulseAttachment>()
                    for (u in distinctUris) {
                        runCatching {
                            val res = withContext(Dispatchers.IO) { CloudFiles.upload(activity, u, "workspace") }
                            uploaded.add(PulseAttachment.from(res.put("source", "workspace")))
                        }
                    }
                    if (uploaded.isNotEmpty()) {
                        synchronized(draftLock) { attachmentDrafts[targetBot] = attachments(targetBot) + uploaded }
                        localRevision++
                        if (ui.botId == targetBot) ui = ui.copy(attachments = attachments(targetBot))
                        withContext(Dispatchers.IO) { persistDrafts().get() }
                        toast("已接收分享文件 (${uploaded.size})，可直接发送")
                    }
                } finally {
                    ui = ui.copy(uploading = false)
                }
            }
        } else if (sharedTexts.isNotEmpty()) {
            toast("已接收分享内容，可直接发送")
        }
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
    fun stop(targetBot: String = ui.botId) {
        val phone = RootPhoneHarness.changes.value
        val hasPhoneToolInTurn = (phone.active && phone.botId == targetBot) ||
            (ui.streamBotId == targetBot && (ui.streamTool == "phone_workspace" ||
                ui.streamTrace.any { it.optString("tool") == "phone_workspace" }))
        if (hasPhoneToolInTurn) {
            if (phone.active) {
                RootPhoneHarness.stop(activity)
            }
            FloatingVirtualDisplay.hide()
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
        // Optimistically update the edited user message IN-PLACE and truncate subsequent messages of the same bot
        val targetIdx = remoteMessages.indexOfFirst { it.id == message.id }
        if (targetIdx >= 0) {
            val updatedList = ArrayList<PulseMessage>(remoteMessages.size)
            for (i in remoteMessages.indices) {
                val cur = remoteMessages[i]
                if (i == targetIdx) {
                    updatedList.add(cur.copy(text = clean))
                } else if (i > targetIdx && cur.botId == id) {
                    // Truncated subsequent message in same bot conversation
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
        // Optimistically keep the user message in-place and truncate only assistant replies after it
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

    fun loadMemoryPins() {
        perform {
            val res = request("GET", "/api/memories/pins")
            ui = ui.copy(pinnedMemories = res.optJSONArray("pins")?.objects() ?: emptyList())
        }
    }

    fun addMemoryPin(text: String) {
        val clean = text.trim()
        if (clean.isEmpty()) return
        perform {
            val res = request("POST", "/api/memories/pins", JSONObject().put("action", "add").put("text", clean))
            ui = ui.copy(pinnedMemories = res.optJSONArray("pins")?.objects() ?: ui.pinnedMemories)
            toast("已钉住记忆")
        }
    }

    fun removeMemoryPin(pinId: String) {
        perform {
            val res = request("POST", "/api/memories/pins", JSONObject().put("action", "remove").put("pin_id", pinId))
            ui = ui.copy(pinnedMemories = res.optJSONArray("pins")?.objects() ?: emptyList())
            toast("已取消钉住")
        }
    }

    fun loadWatchdogs() {
        perform {
            val res = request("GET", "/api/watchdogs")
            ui = ui.copy(watchdogs = res.optJSONArray("watchdogs")?.objects() ?: emptyList())
        }
    }

    fun stopWatchdog(watchdogId: String) {
        perform {
            request("POST", "/api/watchdogs/$watchdogId/stop")
            loadWatchdogs()
            toast("已停止哨兵监控：$watchdogId")
        }
    }
    fun reply(message: PulseMessage?) { ui = ui.copy(reply = message) }
    fun copy(text: String) { activity.getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newPlainText("DSB", text)); toast("已复制。") }

    fun openAccessibilitySettings() {
        runCatching {
            activity.startActivity(Intent(android.provider.Settings.ACTION_ACCESSIBILITY_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            toast("请在无障碍列表中找到「DSB」并开启")
        }.onFailure {
            toast("无法打开系统无障碍设置，请手动前往系统设置 -> 无障碍")
        }
    }

    fun connectRootVirtualDisplay() {
        perform {
            withContext(Dispatchers.IO) {
                RootPhoneHarness.initialize(activity)
                RootPhoneHarness.command(activity, JSONObject().put("op", "connect").put("manual", true))
                RootPhoneHarness.status(activity)
            }
            val st = RootPhoneHarness.changes.value
            toast(if (st.root) "Root 后台虚拟屏权限已就绪 (uid 0)" else "请在 Root 管理器（KernelSU / Magisk）中授予 DSB 超级用户权限")
        }
    }

    fun requestPhonePermission(op: String) {
        perform {
            withContext(Dispatchers.IO) {
                RootPhoneHarness.command(activity, JSONObject().put("op", op))
                RootPhoneHarness.status(activity)
            }
        }
    }
    fun confirm(message: PulseMessage, accepted: Boolean) = perform {
        request("POST", "/api/confirm", JSONObject().put("id", message.id).put("accepted", accepted)); refresh()
    }
    fun ackDemo(message: PulseMessage) = perform { request("POST", "/api/ack", JSONObject().put("ids", JSONArray().put(message.id))); refresh(); toast("演练已结束。") }
    private val stickerCache = android.util.LruCache<String, ImageBitmap>(48)

    fun loadStickerBitmap(stickerPath: String, onLoaded: (ImageBitmap) -> Unit) {
        val clean = stickerPath.trim()
        if (clean.isEmpty()) return
        synchronized(stickerCache) { stickerCache.get(clean) }?.let { onLoaded(it); return }
        scope.launch(Dispatchers.IO) {
            runCatching {
                val base = ui.serverUrl.trimEnd('/')
                val rel = if (clean.startsWith("/")) clean else "/$clean"
                val fullUrl = if (clean.startsWith("http")) clean else "$base$rel"
                val conn = (java.net.URL(fullUrl).openConnection() as java.net.HttpURLConnection).apply {
                    connectTimeout = 8000
                    readTimeout = 8000
                }
                try {
                    conn.inputStream.use { ins ->
                        val bmp = BitmapFactory.decodeStream(ins)?.asImageBitmap()
                        if (bmp != null) {
                            synchronized(stickerCache) { stickerCache.put(clean, bmp) }
                            withContext(Dispatchers.Main) { onLoaded(bmp) }
                        }
                    }
                } finally {
                    conn.disconnect()
                }
            }
        }
    }

    fun fetchStickerPacks() {
        perform {
            runCatching {
                val res = request("GET", "/api/plugins/stickers")
                ui = ui.copy(stickerPacks = res.optJSONArray("packs")?.objects() ?: emptyList())
            }
        }
    }

    fun toggleStickerPack(packId: String, enabled: Boolean) {
        perform {
            val res = request("POST", "/api/plugins/stickers/toggle", JSONObject().put("pack_id", packId).put("enabled", enabled))
            ui = ui.copy(stickerPacks = res.optJSONArray("packs")?.objects() ?: ui.stickerPacks)
            toast(if (enabled) "已启用表情包插件" else "已关闭表情包插件")
        }
    }

    fun switchAdjacentBot(delta: Int) {
        val list = visibleBots().ifEmpty { ui.bots.filter { !it.hidden } }
        if (list.size <= 1) return
        val curIdx = list.indexOfFirst { it.id == ui.botId }.coerceAtLeast(0)
        val nextIdx = (curIdx + delta + list.size) % list.size
        val nextBot = list[nextIdx]
        ui = ui.copy(lastSwitchDir = if (delta >= 0) 1 else -1)
        open(nextBot.id)
    }

    fun togglePinBot(targetBotId: String) {
        val target = bot(targetBotId) ?: return
        placement(target.id, "pinned", !target.pinned)
    }

    fun toggleAppearanceQuick() {
        val next = if (ui.appearance == "light") "dark" else "light"
        updateSettingsField(appearance = next)
    }

    fun stopBotById(targetBotId: String) {
        stop(targetBotId)
    }

    fun close() { if (activeInstance === this) activeInstance = null; draftJob?.cancel(); persistDrafts(); draftWriter.shutdown(); scope.cancel() }
}

internal fun JSONArray.objects(): List<JSONObject> = (0 until length()).map { getJSONObject(it) }
