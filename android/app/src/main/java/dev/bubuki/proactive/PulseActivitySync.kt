package dev.bubuki.proactive

import android.content.Intent
import android.graphics.BitmapFactory
import android.os.SystemClock
import android.util.Base64
import androidx.compose.ui.graphics.asImageBitmap
import kotlinx.coroutines.*
import kotlinx.coroutines.sync.withLock
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.abs

/**
 * [Module] PulseActivitySync
 * [Responsibility] Network synchronization, WebSocket realtime event state machine (`onRealtimeEvent`),
 *                  incremental `/api/messages` & `/api/status` delta syncing (`refresh`, `refreshStreamOnly`),
 *                  monotonic tool-trace merging, optimistic message deduplication, local roster/message caching,
 *                  and bot avatar loading.
 * [Public API]
 *   - NativePulseModel.hasInFlightWork()
 *   - NativePulseModel.resume() / pause() / refresh(force) / refreshStreamOnly()
 *   - NativePulseModel.onRealtimeEvent(event)
 *   - NativePulseModel.request(method, path, body, query)
 *   - NativePulseModel.combinedMessages() / mergeRemoteMessages(incoming, fullReplace)
 *   - NativePulseModel.applyPhoneState() / cacheRoster(bots, primary, serverScope)
 */

internal fun NativePulseModel.hasInFlightWork(): Boolean {
    return inFlightSends > 0 || ui.busyBot != null || ui.workingBot != null || ui.streamBotId != null ||
        (sendGuardBotId != null && SystemClock.uptimeMillis() < sendGuardUntilUptime)
}

internal fun NativePulseModel.deduplicateAssistantList(rawMerged: List<PulseMessage>): ArrayList<PulseMessage> {
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

internal fun NativePulseModel.combinedMessages(): List<PulseMessage> {
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

internal fun NativePulseModel.applyPhoneState() {
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

internal fun NativePulseModel.scopeOf(settings: JSONObject): String {
    val value = settings.optString("serverUrl", SecureTransport.DEFAULT_SERVER).trim().trimEnd('/') + "\u0000" + settings.optString("publicKey") + "\u0000" + settings.optString("deviceToken", SecureTransport.defaultToken()).ifBlank { SecureTransport.defaultToken() } + "\u0000" + SecureTransport.getNodeSource(activity)
    return java.security.MessageDigest.getInstance("SHA-256").digest(value.toByteArray()).joinToString("") { "%02x".format(it.toInt() and 255) }
}

internal fun NativePulseModel.homeCache(settings: JSONObject): Triple<List<PulseBot>, String, List<PulseMessage>> {
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

internal fun NativePulseModel.cacheRoster(bots: List<PulseBot>, primary: String, serverScope: String = scopeOf(prefs)) {
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

internal fun NativePulseModel.readAttachments(settings: JSONObject) {
    val stored = settings.optJSONObject("attachmentDrafts") ?: JSONObject()
    synchronized(draftLock) { stored.keys().forEach { id -> attachmentDrafts[id] = (stored.optJSONArray(id) ?: JSONArray()).objects().map(PulseAttachment::from) } }
}

internal fun NativePulseModel.attachments(id: String) = synchronized(draftLock) { attachmentDrafts[id] ?: emptyList() }

internal fun NativePulseModel.draftMode(id: String): String = attachments(id).firstOrNull()?.let { if (it.source == "computer") "computer" else "auto" } ?: "auto"

internal suspend fun NativePulseModel.request(method: String, path: String, body: JSONObject? = null, query: JSONObject? = null) =
    withContext(Dispatchers.IO) { SecureTransport(activity).request(method, path, body, query) }

internal fun NativePulseModel.resume() {
    active = true
    NativePulseModel.setActiveInstance(this)
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

internal fun NativePulseModel.isStreamPlaceholder(t: String): Boolean {
    return t.isBlank() || t == "正在思考…" || t == "正在思考与执行任务…" || t == "正在整理回复…" || t == "正在重新生成…" || t.startsWith("正在调用 ") || t.startsWith("正在并行执行 ") || t.startsWith("⏳ ") || t.startsWith("⏰ 正在执行") || t.startsWith("📦 ")
}

internal fun NativePulseModel.markTurnCompleted(turnId: String) {
    if (turnId.isBlank()) return
    completedTurnIds.add(turnId)
    while (completedTurnIds.size > 240) {
        val first = completedTurnIds.firstOrNull() ?: break
        completedTurnIds.remove(first)
    }
}

internal fun NativePulseModel.mergeMonotonicTrace(
    prev: List<JSONObject>,
    incoming: List<JSONObject>,
    sameBot: Boolean
): List<JSONObject> {
    if (!sameBot || prev.isEmpty()) return incoming
    if (incoming.isEmpty()) return prev
    return incoming.mapIndexed { idx, item ->
        val prevItem = prev.getOrNull(idx)
        val prevDone = prevItem?.optString("status") == "done"
        val incDone = item.optString("status") == "done"
        if (prevItem != null && prevDone && !incDone && prevItem.optString("tool") == item.optString("tool")) {
            prevItem
        } else {
            item
        }
    }
}

internal fun NativePulseModel.mergeRemoteMessages(incoming: List<PulseMessage>, fullReplace: Boolean = false) {
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

internal fun NativePulseModel.applyPacedStreamText(sBot: String?, resolvedText: String): String {
    if (sBot == null) {
        streamPacerJob?.cancel()
        targetStreamFullText = ""
        return ""
    }
    if (isStreamPlaceholder(resolvedText)) {
        streamPacerJob?.cancel()
        return resolvedText
    }
    targetStreamFullText = resolvedText
    streamPacerJob?.cancel()
    return resolvedText
}

internal fun NativePulseModel.resolveStreamText(targetBot: String?, turnId: String, rawStreamText: String): String {
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

internal fun NativePulseModel.onRealtimeEvent(event: JSONObject) {
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
            "config_updated" -> {
                val remoteThresh = event.optInt("auto_compact_tokens", 0)
                val (ctxTokensMap, ctxMsgsMap, ctxCachedMap) = parseContextByBot(event.optJSONObject("context_by_bot"))
                ui = ui.copy(
                    autoCompactTokens = if (remoteThresh >= 8000) remoteThresh.toString() else ui.autoCompactTokens,
                    contextTokensByBot = if (ctxTokensMap.isNotEmpty()) ctxTokensMap else ui.contextTokensByBot,
                    contextCachedByBot = if (ctxCachedMap.isNotEmpty()) ctxCachedMap else ui.contextCachedByBot,
                    contextMsgsByBot = if (ctxMsgsMap.isNotEmpty()) ctxMsgsMap else ui.contextMsgsByBot
                )
            }
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
                    val clearedTurn = event.optString("turn_id", "")
                    val clearedSeq = event.optLong("seq", 0L)
                    if (clearedSeq > lastStreamSeq) lastStreamSeq = clearedSeq
                    if (clearedTurn.isNotBlank()) markTurnCompleted(clearedTurn)
                    if (clearedBot.isBlank() || clearedBot == ui.botId || clearedBot == ui.streamBotId || clearedBot == ui.workingBot || clearedBot == ui.busyBot) {
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
                if (event.isNull("execution")) {
                    val clearedBot = event.optString("bot_id", "")
                    val clearedSeq = event.optLong("seq", 0L)
                    if (clearedSeq > lastStreamSeq) lastStreamSeq = clearedSeq
                    if (clearedBot.isBlank() || clearedBot == ui.botId || clearedBot == ui.streamBotId || clearedBot == ui.workingBot || clearedBot == ui.busyBot) {
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
                val exec = event.optJSONObject("execution") ?: return@launch
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

internal fun NativePulseModel.lastServerMessageId(): String? =
    remoteMessages.lastOrNull { !it.id.startsWith("stopped-") && !it.id.startsWith("local-") && !it.optimistic }?.id

internal suspend fun NativePulseModel.refresh(force: Boolean = false) {
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
                autoCompactTokens = if (remoteCompactThresh >= 8000 && ui.activePanel !in listOf("settings", "bot_edit")) remoteCompactThresh.toString() else ui.autoCompactTokens,
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

internal suspend fun NativePulseModel.refreshStreamOnly() {
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
                status.optJSONArray("recent_tail")?.objects()?.mapNotNull { runCatching { PulseMessage.from(it) }.getOrNull() }?.let {
                    mergeRemoteMessages(it, fullReplace = false)
                }
            }
            val lastDone = status.optJSONObject("last_completed_stream")
            val lastDoneTurn = lastDone?.optString("turn_id").orEmpty()
            if (lastDoneTurn.isNotBlank()) markTurnCompleted(lastDoneTurn)

            val statusSeq = status.optLong("seq", 0L)
            if (statusSeq in 1 until lastStreamSeq) {
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
                autoCompactTokens = if (remoteCompactThresh >= 8000 && ui.activePanel !in listOf("settings", "bot_edit")) remoteCompactThresh.toString() else ui.autoCompactTokens,
                cloudDesktopLiveBotId = if (usesCloudDesktop) (effectiveSBot ?: effectiveWorking) else ui.cloudDesktopLiveBotId
            )
            if (ui.activePanel == "cloud_desktop") refreshCloudDesktopFrame()
        }
    }
}

internal fun NativePulseModel.loadAvatars() {
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

internal fun NativePulseModel.acknowledge() {
    if (!active || !ui.chatOpen || !ui.online) return
    val ids = ui.messages.filter { it.botId == ui.botId && it.id in ui.unread && it.kind != "escalation_demo" }.map { it.id }
    if (ids.isEmpty()) return
    perform { request("POST", "/api/ack", JSONObject().put("ids", JSONArray(ids))); ui = ui.copy(unread = ui.unread - ids.toSet()) }
}

internal fun NativePulseModel.pause() {
    active = false
    cloudStreamJob?.cancel()
    streamPacerJob?.cancel()
}
