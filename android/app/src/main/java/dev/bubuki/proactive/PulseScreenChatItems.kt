package dev.bubuki.proactive

import androidx.compose.animation.*
import androidx.compose.animation.core.*
import androidx.compose.foundation.*
import androidx.compose.foundation.gestures.*
import androidx.compose.foundation.interaction.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.*
import androidx.compose.foundation.shape.*
import androidx.compose.foundation.text.*
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.*
import androidx.compose.ui.draw.*
import androidx.compose.ui.focus.*
import androidx.compose.ui.graphics.*
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.*
import androidx.compose.ui.platform.*
import androidx.compose.ui.semantics.*
import androidx.compose.ui.text.*
import androidx.compose.ui.text.font.*
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.*
import androidx.compose.ui.unit.*
import androidx.compose.ui.window.*
import dev.bubuki.proactive.markdown.MarkdownText
import kotlinx.coroutines.launch
import org.json.JSONArray
import kotlin.math.abs
import kotlin.math.roundToInt

/**
 * [Module] PulseScreenChatItems
 * [Responsibility]
 * 消息氣泡與富媒體卡片渲染領域模塊：負責單條消息氣泡渲染、思考折疊塊、多代理委派卡片、性能指標膠囊 (NativeMessage)、
 * 工具調用軌跡折疊組 (NativeToolTraceGroup)、貼紙表情包縮略圖與大圖渲染 (PulseStickerThumb, PulseStickerImage)，
 * 以及快捷動作膠囊 (TelegramActionChip)。
 *
 * [Public API]
 * - NativeMessage(...)
 * - NativeToolTraceGroup(model: NativePulseModel, messageId: String, trace: List<org.json.JSONObject>)
 * - PulseStickerThumb(...), PulseStickerImage(...), TelegramActionChip(...)
 */

@OptIn(ExperimentalFoundationApi::class)
@Composable
internal fun NativeMessage(
    model: NativePulseModel,
    bot: PulseBot,
    message: PulseMessage,
    groupedWithPrev: Boolean = false,
    groupedWithNext: Boolean = false,
    isLatestMessage: Boolean = false
) {
    val s = model.ui
    val c = LocalPulsePalette.current
    val haptics = LocalHapticFeedback.current
    val density = LocalDensity.current
    val msgScope = rememberCoroutineScope()

    val isBotToBotIn = message.kind == "bot_message"
    val isBotToBotReply = message.kind == "bot_reply"
    val isError = message.kind == "error"
    val isNotification = message.kind == "notification"
    val user = message.role == "user" && !isBotToBotIn
    var showActions by rememberSaveable(message.id) { mutableStateOf(false) }

    // Telegram Interactive Finger-Tracking Left-Swipe to Quote Reply
    val swipeOffset = remember(message.id) { Animatable(0f) }
    val replyTriggerPx = with(density) { 48.dp.toPx() }
    val maxSwipeLeftPx = with(density) { 82.dp.toPx() }
    var replyThresholdCrossed by remember(message.id) { mutableStateOf(false) }

    if (message.kind == "compact_summary") {
        val meta = remember(message.decision) { runCatching { org.json.JSONObject(message.decision) }.getOrNull() }
        val beforeTok = meta?.optInt("before_tokens", 0) ?: 0
        val afterTok = meta?.optInt("after_tokens", 0) ?: 0
        val msgCnt = meta?.optInt("compacted_messages", 0) ?: 0
        val vaultPath = (meta?.optString("vault_path", "") ?: "").ifBlank { "INDEX.md" }
        val ctxId = meta?.optString("ctx_id", "").orEmpty()
        val modeLabel = (meta?.optString("mode", "上下文压缩归档") ?: "").ifBlank { "上下文压缩归档" }
        var summaryExpanded by rememberSaveable(message.id) { mutableStateOf(false) }

        Column(
            Modifier.fillMaxWidth()
                .clip(RoundedCornerShape(16.dp))
                .background(c.surface)
                .padding(horizontal = 14.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Box(
                    Modifier.clip(RoundedCornerShape(7.dp))
                        .background(c.accent.copy(alpha = 0.14f))
                        .padding(horizontal = 7.dp, vertical = 3.dp)
                ) {
                    Text(modeLabel, color = c.accent, fontSize = 11.5.sp, fontWeight = FontWeight.SemiBold)
                }
                Spacer(Modifier.weight(1f))
                Text(model.time(message.created), color = c.faint, fontSize = 11.sp, style = TextStyle(fontFeatureSettings = "tnum"))
            }
            Text(
                if (beforeTok > 0 || afterTok > 0) {
                    "上下文已浓缩：${model.formatTokenCount(beforeTok)} → ${model.formatTokenCount(afterTok)} Tokens（共浓缩 $msgCnt 条对话）"
                } else {
                    "上下文已浓缩并归档至记忆库"
                },
                color = c.ink,
                fontSize = 14.sp,
                fontWeight = FontWeight.SemiBold,
                style = TextStyle(fontFeatureSettings = "tnum")
            )
            Column(
                Modifier.fillMaxWidth()
                    .clip(RoundedCornerShape(10.dp))
                    .background(c.fill.copy(alpha = 0.72f))
                    .padding(horizontal = 11.dp, vertical = 8.dp),
                verticalArrangement = Arrangement.spacedBy(5.dp)
            ) {
                Text("压缩记忆存储去向：", color = c.muted, fontSize = 11.5.sp, fontWeight = FontWeight.SemiBold)
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        "1. 记忆库归档：memory_vault/$vaultPath",
                        Modifier.weight(1f),
                        color = c.ink.copy(alpha = 0.9f),
                        fontSize = 12.sp,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis
                    )
                    Text(
                        "查看归档记忆 ›",
                        Modifier.padding(start = 6.dp)
                            .clip(RoundedCornerShape(6.dp))
                            .background(c.accent.copy(alpha = 0.14f))
                            .clickable { model.openVault(vaultPath) }
                            .padding(horizontal = 7.dp, vertical = 3.dp),
                        color = c.accent,
                        fontSize = 11.sp,
                        fontWeight = FontWeight.SemiBold
                    )
                }
                Text(
                    "2. 时间线与检索库：memory_vault/timeline · SQLite FTS5" + (if (ctxId.isNotBlank()) " ($ctxId)" else ""),
                    color = c.muted,
                    fontSize = 11.5.sp
                )
            }
            Row(
                Modifier.fillMaxWidth()
                    .clickable {
                        haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                        summaryExpanded = !summaryExpanded
                    }
                    .padding(top = 2.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    if (summaryExpanded) "收起浓缩摘要正文 ▴" else "展开查看浓缩摘要正文 ▾",
                    color = c.accent,
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Medium
                )
            }
            AnimatedVisibility(
                visible = summaryExpanded,
                enter = expandVertically(spring(dampingRatio = 0.80f, stiffness = 400f)) + fadeIn(),
                exit = shrinkVertically(spring(dampingRatio = 0.85f, stiffness = 450f)) + fadeOut()
            ) {
                SelectionContainer {
                    MarkdownText(
                        nativeWikiMarkdown(message.text),
                        TextStyle(color = c.ink.copy(alpha = 0.9f), fontSize = 13.5.sp, lineHeight = 20.sp),
                        streaming = false,
                        onOpenUri = model::openLink
                    )
                }
            }
        }
        return
    }

    // Shared Finger-Tracking Left-Swipe Gesture Modifier
    val swipeGestureModifier = Modifier.pointerInput(message.id) {
        detectHorizontalDragGestures(
            onDragStart = { replyThresholdCrossed = false },
            onHorizontalDrag = { change, dragAmount ->
                if (dragAmount < 0f || swipeOffset.value < 0f) {
                    change.consume()
                    val cur = swipeOffset.value
                    val resistance = if (abs(cur) > replyTriggerPx) 0.34f else 0.78f
                    val next = (cur + dragAmount * resistance).coerceIn(-maxSwipeLeftPx, 0f)
                    if (!replyThresholdCrossed && abs(next) >= replyTriggerPx) {
                        replyThresholdCrossed = true
                        haptics.performHapticFeedback(HapticFeedbackType.LongPress)
                    } else if (replyThresholdCrossed && abs(next) < replyTriggerPx * 0.82f) {
                        replyThresholdCrossed = false
                    }
                    msgScope.launch { swipeOffset.snapTo(next) }
                }
            },
            onDragEnd = {
                val triggered = abs(swipeOffset.value) >= replyTriggerPx
                msgScope.launch {
                    swipeOffset.animateTo(0f, spring(dampingRatio = 0.70f, stiffness = 480f))
                }
                if (triggered) {
                    model.reply(message)
                }
            },
            onDragCancel = {
                msgScope.launch {
                    swipeOffset.animateTo(0f, spring(dampingRatio = 0.75f, stiffness = 460f))
                }
            }
        )
    }

    val replyIconFraction = (abs(swipeOffset.value) / replyTriggerPx).coerceIn(0f, 1.2f)
    val replyIconScale by animateFloatAsState(
        targetValue = if (abs(swipeOffset.value) >= replyTriggerPx) 1.15f else (0.5f + 0.5f * replyIconFraction.coerceAtMost(1f)),
        animationSpec = spring(dampingRatio = 0.60f, stiffness = 540f),
        label = "reply_icon_scale"
    )

    if (user) {
        val rawText = message.text
        val hasQuote = rawText.startsWith("> ") && rawText.contains("\n\n")
        val quotePart = if (hasQuote) rawText.substringBefore("\n\n").lines().joinToString(" ") { it.removePrefix("> ").trim() } else ""
        val rawBody = if (hasQuote) rawText.substringAfter("\n\n") else rawText
        val attachMarker = "\n\n附件（工作区相对路径"
        val hasAttachBlock = rawBody.contains(attachMarker)
        val cleanBody = if (hasAttachBlock) rawBody.substringBefore(attachMarker).trim() else rawBody
        val attachedFiles = if (hasAttachBlock) {
            rawBody.substringAfter(attachMarker).substringAfter("：\n", "")
                .lines().map { it.trim().substringAfterLast('/') }.filter { it.isNotBlank() }
        } else emptyList()
        val bodyPart = if (cleanBody == "请查看这些附件。" && attachedFiles.isNotEmpty()) "" else cleanBody

        val userBubbleShape = RoundedCornerShape(
            topStart = 18.dp,
            topEnd = if (groupedWithPrev) 6.dp else 18.dp,
            bottomEnd = if (groupedWithNext) 6.dp else 4.dp,
            bottomStart = 18.dp
        )

        Box(
            Modifier.fillMaxWidth().then(swipeGestureModifier),
            contentAlignment = Alignment.CenterEnd
        ) {
            // Emerging Reply Arrow Icon on Right during Left-Swipe
            if (swipeOffset.value < -2f) {
                Box(
                    Modifier.padding(end = 8.dp)
                        .size(30.dp)
                        .graphicsLayer {
                            alpha = replyIconFraction.coerceIn(0f, 1f)
                            scaleX = replyIconScale
                            scaleY = replyIconScale
                        }
                        .clip(CircleShape)
                        .background(c.surface.copy(alpha = 0.90f)),
                    contentAlignment = Alignment.Center
                ) {
                    PulseIcon("reply", c.accent, Modifier.size(16.dp))
                }
            }

            Column(
                Modifier.fillMaxWidth()
                    .padding(start = 54.dp)
                    .graphicsLayer { translationX = swipeOffset.value },
                horizontalAlignment = Alignment.End
            ) {
                Column(
                    Modifier.clip(userBubbleShape)
                        .background(c.userBubble)
                        .combinedClickable(
                            onClick = {
                                haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                                showActions = !showActions
                            },
                            onDoubleClick = {
                                haptics.performHapticFeedback(HapticFeedbackType.LongPress)
                                model.reply(message)
                            },
                            onLongClick = {
                                haptics.performHapticFeedback(HapticFeedbackType.LongPress)
                                showActions = true
                            }
                        )
                        .padding(horizontal = 13.dp, vertical = 8.dp)
                ) {
                    if (quotePart.isNotBlank()) {
                        Row(
                            Modifier.padding(bottom = 5.dp)
                                .clip(RoundedCornerShape(8.dp))
                                .background(Color.White.copy(alpha = 0.15f))
                                .padding(horizontal = 8.dp, vertical = 5.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Box(
                                Modifier.width(2.5.dp).height(24.dp)
                                    .clip(RoundedCornerShape(1.5.dp))
                                    .background(Color.White.copy(alpha = 0.9f))
                            )
                            Column(Modifier.padding(start = 7.dp)) {
                                Text("引用消息", color = Color.White, fontSize = 11.sp, fontWeight = FontWeight.SemiBold)
                                Text(
                                    quotePart,
                                    color = Color.White.copy(alpha = 0.88f),
                                    fontSize = 12.sp,
                                    maxLines = 2,
                                    overflow = TextOverflow.Ellipsis
                                )
                            }
                        }
                    }
                    if (attachedFiles.isNotEmpty()) {
                        Column(
                            Modifier.padding(bottom = if (bodyPart.isNotBlank()) 6.dp else 0.dp),
                            verticalArrangement = Arrangement.spacedBy(4.dp)
                        ) {
                            attachedFiles.forEach { fileName ->
                                Row(
                                    Modifier.clip(RoundedCornerShape(8.dp))
                                        .background(Color.White.copy(alpha = 0.16f))
                                        .padding(horizontal = 10.dp, vertical = 5.dp),
                                    verticalAlignment = Alignment.CenterVertically
                                ) {
                                    PulseIcon("attach", Color.White, Modifier.size(14.dp))
                                    Text(fileName, Modifier.padding(start = 5.dp), color = Color.White, fontSize = 13.sp, fontWeight = FontWeight.Medium)
                                }
                            }
                        }
                    }
                    if (bodyPart.isNotBlank()) {
                        Text(bodyPart, color = c.userInk, fontSize = 16.sp, lineHeight = 21.5.sp)
                    }
                    Row(
                        Modifier.align(Alignment.End).padding(top = 2.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(3.dp)
                    ) {
                        Text(
                            if (message.optimistic) "发送中" else model.time(message.created),
                            color = c.userMeta,
                            fontSize = 10.5.sp,
                            style = TextStyle(fontFeatureSettings = "tnum")
                        )
                        PulseIcon(
                            if (message.optimistic) "check" else "check_double",
                            c.userMeta,
                            Modifier.size(13.dp)
                        )
                    }
                }

                // Spring-Animated Telegram Action Pill Menu
                AnimatedVisibility(
                    visible = showActions,
                    enter = expandVertically(spring(dampingRatio = 0.75f, stiffness = 440f)) +
                        scaleIn(spring(dampingRatio = 0.75f, stiffness = 440f), initialScale = 0.88f) +
                        fadeIn(tween(140)),
                    exit = shrinkVertically(spring(dampingRatio = 0.85f, stiffness = 480f)) +
                        scaleOut(targetScale = 0.90f) +
                        fadeOut(tween(110))
                ) {
                    Row(
                        Modifier.padding(top = 5.dp)
                            .clip(RoundedCornerShape(12.dp))
                            .background(c.surface)
                            .border(0.5.dp, c.line.copy(alpha = 0.7f), RoundedCornerShape(12.dp))
                            .padding(horizontal = 12.dp, vertical = 7.dp),
                        horizontalArrangement = Arrangement.spacedBy(14.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        TelegramActionChip("copy", "复制", c.ink) { model.copy(bodyPart.ifBlank { cleanBody.ifBlank { rawText } }); showActions = false }
                        TelegramActionChip("reply", "引用", c.ink) { model.reply(message); showActions = false }
                        TelegramActionChip("edit", "编辑", c.accent) { model.startEditingMessage(message); showActions = false }
                        TelegramActionChip("spark", "重发", c.muted) { model.retryMessage(message); showActions = false }
                    }
                }
            }
        }
        return
    }

    val speakerBot = message.fromBotId.takeIf { it.isNotBlank() }?.let { model.bot(it) } ?: bot
    val isOtherBot = speakerBot.id != bot.id || isBotToBotIn || isBotToBotReply
    val speakerColor = remember(speakerBot.color) {
        runCatching { Color(android.graphics.Color.parseColor(speakerBot.color)) }.getOrDefault(c.accent)
    }
    val trace = remember(message.trace) { runCatching { JSONArray(message.trace).objects() }.getOrElse { emptyList() } }
    var thoughtExpanded by rememberSaveable(message.id) { mutableStateOf(false) }

    val aiBubbleShape = RoundedCornerShape(
        topStart = if (groupedWithPrev) 6.dp else 18.dp,
        topEnd = 18.dp,
        bottomEnd = 18.dp,
        bottomStart = if (groupedWithNext) 6.dp else 4.dp
    )

    Box(
        Modifier.fillMaxWidth().then(swipeGestureModifier),
        contentAlignment = Alignment.CenterStart
    ) {
        // Emerging Reply Arrow Icon on Right during Left-Swipe
        if (swipeOffset.value < -2f) {
            Box(
                Modifier.align(Alignment.CenterEnd)
                    .padding(end = 10.dp)
                    .size(30.dp)
                    .graphicsLayer {
                        alpha = replyIconFraction.coerceIn(0f, 1f)
                        scaleX = replyIconScale
                        scaleY = replyIconScale
                    }
                    .clip(CircleShape)
                    .background(c.surface.copy(alpha = 0.90f)),
                contentAlignment = Alignment.Center
            ) {
                PulseIcon("reply", c.accent, Modifier.size(16.dp))
            }
        }

        Column(
            Modifier.fillMaxWidth()
                .padding(end = 42.dp)
                .graphicsLayer { translationX = swipeOffset.value },
            verticalArrangement = Arrangement.spacedBy(5.dp)
        ) {
            // 1. Standalone Single-Line Tool Call Capsule
            if (trace.isNotEmpty()) {
                NativeToolTraceGroup(model, message.id, trace)
            }

            // 2. Collapsible [💭 查看思考过程 ▸] Pill with Spring Animation
            val cleanThought = message.thought.trim().takeIf { it.isNotEmpty() && !it.startsWith("正在生成 ") && !it.startsWith("正在准备调用 ") }.orEmpty()
            if (cleanThought.isNotBlank()) {
                Column(
                    Modifier.clip(RoundedCornerShape(12.dp))
                        .background(c.surface.copy(alpha = 0.92f))
                        .clickable {
                            haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                            thoughtExpanded = !thoughtExpanded
                        }
                        .padding(horizontal = 11.dp, vertical = 6.dp)
                ) {
                    Text(
                        when {
                            thoughtExpanded -> "收起思考与阶段过程 ▴"
                            trace.isNotEmpty() -> "查看思考与阶段过程 (${message.apiCalls.coerceAtLeast(1)}轮) ▸"
                            else -> "查看思考过程 ▸"
                        },
                        color = c.accent,
                        fontSize = 12.sp,
                        fontWeight = FontWeight.SemiBold
                    )
                    AnimatedVisibility(
                        visible = thoughtExpanded,
                        enter = expandVertically(spring(dampingRatio = 0.80f, stiffness = 400f)) + fadeIn(),
                        exit = shrinkVertically(spring(dampingRatio = 0.85f, stiffness = 450f)) + fadeOut()
                    ) {
                        Row(Modifier.padding(top = 6.dp)) {
                            Box(
                                Modifier.width(2.5.dp)
                                    .heightIn(min = 18.dp)
                                    .clip(RoundedCornerShape(2.dp))
                                    .background(c.accent.copy(alpha = 0.55f))
                            )
                            Text(
                                cleanThought,
                                Modifier.padding(start = 8.dp),
                                color = c.muted,
                                fontSize = 13.sp,
                                lineHeight = 18.5.sp
                            )
                        }
                    }
                }
            }

            // 3. Sticker / Returned Image rendered cleanly
            if (message.sticker.isNotBlank()) {
                PulseStickerImage(model, message.sticker)
            }

            // 4. Clean Telegram Incoming Bubble (Purple Collaboration Header & Border for Bot-to-Bot messages on Left)
            val isCollabBubble = isBotToBotIn || isBotToBotReply
            val collabPurple = Color(0xFFB568F7)
            if (message.text.isNotBlank() || isError) {
                Column(
                    Modifier.clip(aiBubbleShape)
                        .background(if (isCollabBubble) c.aiBubble.copy(alpha = 0.96f) else c.aiBubble)
                        .then(
                            if (isCollabBubble) {
                                Modifier.border(1.dp, collabPurple.copy(alpha = 0.42f), aiBubbleShape)
                            } else {
                                Modifier
                            }
                        )
                        .combinedClickable(
                            onClick = {
                                haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                                showActions = !showActions
                            },
                            onDoubleClick = {
                                haptics.performHapticFeedback(HapticFeedbackType.LongPress)
                                model.reply(message)
                            },
                            onLongClick = {
                                haptics.performHapticFeedback(HapticFeedbackType.LongPress)
                                showActions = true
                            }
                        )
                        .padding(horizontal = 13.dp, vertical = 9.dp),
                    verticalArrangement = Arrangement.spacedBy(5.dp)
                ) {
                    if (isCollabBubble || ((isOtherBot || isNotification || message.kind == "curiosity") && !groupedWithPrev)) {
                        val headerTitle = when {
                            isBotToBotIn && message.direction == "outbound" ->
                                "🟣 委派给 ${message.toBotName.ifBlank { speakerBot.name }} · 协作派单"
                            isBotToBotIn ->
                                "🟣 来自 ${message.fromBotName.ifBlank { speakerBot.name }} · 协作委托"
                            isBotToBotReply && message.direction == "outbound" ->
                                "🟣 回复 ${message.toBotName.ifBlank { "主助理" }} · 协作结论"
                            isBotToBotReply ->
                                "🟣 来自 ${message.fromBotName.ifBlank { speakerBot.name }} · 协作回信"
                            else ->
                                message.fromBotName.ifBlank { speakerBot.name }
                        }
                        if (isCollabBubble) {
                            Row(
                                Modifier.clip(RoundedCornerShape(8.dp))
                                    .background(collabPurple.copy(alpha = 0.16f))
                                    .border(0.6.dp, collabPurple.copy(alpha = 0.38f), RoundedCornerShape(8.dp))
                                    .padding(horizontal = 8.dp, vertical = 3.5.dp),
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Text(
                                    headerTitle,
                                    fontSize = 12.sp,
                                    color = collabPurple,
                                    fontWeight = FontWeight.SemiBold
                                )
                            }
                        } else {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text(
                                    headerTitle,
                                    fontSize = 13.sp,
                                    color = if (isOtherBot) speakerColor else c.accent,
                                    fontWeight = FontWeight.SemiBold
                                )
                                if (message.kind == "curiosity") {
                                    Text(" · 主动关心", fontSize = 11.sp, color = c.accent)
                                }
                            }
                        }
                    }
                    val isTypingThis = s.activeTypewriterMsgId == message.id && s.activeTypewriterChars < message.text.length
                    val shownText = if (isTypingThis) {
                        val rawLimit = s.activeTypewriterChars.coerceIn(1, message.text.length)
                        val safeLimit = if (rawLimit < message.text.length && message.text[rawLimit - 1].isHighSurrogate()) rawLimit + 1 else rawLimit
                        message.text.substring(0, safeLimit.coerceAtMost(message.text.length))
                    } else message.text
                    MarkdownText(
                        remember(shownText) { nativeWikiMarkdown(shownText) },
                        TextStyle(color = if (isError) c.warn else c.ink, fontSize = 16.sp, lineHeight = 22.5.sp),
                        streaming = isTypingThis,
                        onOpenUri = model::openLink
                    )
                    Row(
                        Modifier.align(Alignment.End),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(6.dp)
                    ) {
                        val perfParts = buildList {
                            if (message.cacheHitPct > 0) add("缓存${message.cacheHitPct}%")
                            if (message.ttftMs > 0) {
                                add(
                                    if (message.ttftMs >= 1000) String.format(java.util.Locale.US, "首字%.1fs", message.ttftMs / 1000.0)
                                    else "首字${message.ttftMs}ms"
                                )
                            }
                            if (message.tps > 0.0) add(String.format(java.util.Locale.US, "%.0ft/s", message.tps))
                            if (message.apiCalls > 1) add("${message.apiCalls}轮")
                        }
                        if (perfParts.isNotEmpty()) {
                            Text(
                                perfParts.joinToString(" · "),
                                color = c.faint,
                                fontSize = 10.sp,
                                style = TextStyle(fontFeatureSettings = "tnum")
                            )
                        }
                        if (trace.any {
                                it.optString("tool").startsWith("phone") &&
                                    it.optJSONObject("arguments")?.optString("op") !in listOf("notifications", "get_notifications", "recent_notifications", "notifs", "notification")
                            }
                        ) {
                            Text(
                                "虚拟屏 ›",
                                Modifier.clickable { model.launchFloatingVirtualDisplay() },
                                color = c.accent,
                                fontSize = 11.sp,
                                fontWeight = FontWeight.Medium
                            )
                        }
                        Text(
                            model.time(message.created),
                            color = c.faint,
                            fontSize = 10.5.sp,
                            style = TextStyle(fontFeatureSettings = "tnum")
                        )
                    }
                }
            }

            if (isError) {
                PulseAction("让主助理自动修复模型配置并重试") { model.askPrimaryToHealError(message) }
            }

            // 5. Telegram Inline Keyboard Buttons
            if (isLatestMessage && message.id !in s.consumedChoiceIds && message.choices.isNotEmpty()) {
                Column(Modifier.fillMaxWidth().padding(top = 2.dp), verticalArrangement = Arrangement.spacedBy(5.dp)) {
                    message.choices.forEach { choice ->
                        Box(
                            Modifier.fillMaxWidth()
                                .clip(RoundedCornerShape(11.dp))
                                .background(c.surface.copy(alpha = 0.90f))
                                .iosPressScale(0.97f) {
                                    haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                                    model.consumeChoiceAndSend(message.id, choice.value)
                                }
                                .padding(horizontal = 14.dp, vertical = 9.dp),
                            contentAlignment = Alignment.Center
                        ) {
                            Text(
                                "✦ ${choice.label}",
                                color = c.accent,
                                fontSize = 13.5.sp,
                                fontWeight = FontWeight.SemiBold,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis
                            )
                        }
                    }
                }
            }

            // 6. Spring-Animated Floating Action Pill Menu
            AnimatedVisibility(
                visible = showActions,
                enter = expandVertically(spring(dampingRatio = 0.75f, stiffness = 440f)) +
                    scaleIn(spring(dampingRatio = 0.75f, stiffness = 440f), initialScale = 0.88f) +
                    fadeIn(tween(140)),
                exit = shrinkVertically(spring(dampingRatio = 0.85f, stiffness = 480f)) +
                    scaleOut(targetScale = 0.90f) +
                    fadeOut(tween(110))
            ) {
                Row(
                    Modifier.clip(RoundedCornerShape(12.dp))
                        .background(c.surface)
                        .border(0.5.dp, c.line.copy(alpha = 0.7f), RoundedCornerShape(12.dp))
                        .padding(horizontal = 12.dp, vertical = 7.dp),
                    horizontalArrangement = Arrangement.spacedBy(14.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    TelegramActionChip("copy", "复制", c.ink) { model.copy(message.text); showActions = false }
                    TelegramActionChip("reply", "引用", c.ink) { model.reply(message); showActions = false }
                    TelegramActionChip("spark", "重生成", c.ink) { model.rerollMessage(message); showActions = false }
                    TelegramActionChip("pin", "钉住记忆", c.accent) {
                        val t = message.text.trim()
                        val clipped = if (t.length <= 280) t else {
                            val cut = if (t[279].isHighSurrogate()) 279 else 280
                            t.substring(0, cut) + "…"
                        }
                        model.addMemoryPin(clipped)
                        showActions = false
                    }
                }
            }
        }
    }
}

@Composable
internal fun TelegramActionChip(icon: String, label: String, color: Color, onClick: () -> Unit) {
    val haptics = LocalHapticFeedback.current
    Row(
        Modifier.iosPressScale(0.93f) {
            haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
            onClick()
        },
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(4.dp)
    ) {
        PulseIcon(icon, color, Modifier.size(13.5.dp))
        Text(label, color = color, fontSize = 12.sp, fontWeight = FontWeight.Medium)
    }
}

@Composable
internal fun PulseStickerThumb(model: NativePulseModel, stickerPath: String, size: Dp = 56.dp) {
    var bmp by remember(stickerPath) { mutableStateOf<ImageBitmap?>(null) }
    LaunchedEffect(stickerPath) {
        model.loadStickerBitmap(stickerPath) { loaded -> bmp = loaded }
    }
    Box(Modifier.size(size), contentAlignment = Alignment.Center) {
        bmp?.let { image ->
            Image(
                bitmap = image,
                contentDescription = "贴纸预览",
                modifier = Modifier.fillMaxSize().clip(RoundedCornerShape(8.dp)),
                contentScale = ContentScale.Fit
            )
        }
    }
}

@Composable
internal fun PulseStickerImage(model: NativePulseModel, stickerPath: String) {
    val c = LocalPulsePalette.current
    var bmp by remember(stickerPath) { mutableStateOf<ImageBitmap?>(null) }
    var showLightbox by remember { mutableStateOf(false) }
    val isSticker = stickerPath.contains("/stickers/")
    LaunchedEffect(stickerPath) {
        model.loadStickerBitmap(stickerPath) { loaded -> bmp = loaded }
    }
    val popScale by animateFloatAsState(
        targetValue = if (bmp != null) 1f else 0.82f,
        animationSpec = spring(dampingRatio = 0.68f, stiffness = 420f),
        label = "sticker_pop"
    )
    bmp?.let { image ->
        if (isSticker) {
            Image(
                bitmap = image,
                contentDescription = "表情贴纸",
                modifier = Modifier
                    .size(136.dp)
                    .graphicsLayer {
                        scaleX = popScale
                        scaleY = popScale
                    }
                    .clip(RoundedCornerShape(14.dp)),
                contentScale = ContentScale.Fit
            )
        } else {
            Image(
                bitmap = image,
                contentDescription = "AI返回图片",
                modifier = Modifier
                    .widthIn(min = 120.dp, max = 248.dp)
                    .heightIn(min = 80.dp, max = 268.dp)
                    .graphicsLayer {
                        scaleX = popScale
                        scaleY = popScale
                    }
                    .clip(RoundedCornerShape(16.dp))
                    .border(0.5.dp, c.line.copy(alpha = 0.6f), RoundedCornerShape(16.dp))
                    .clickable { showLightbox = true },
                contentScale = ContentScale.Fit
            )
            if (showLightbox) {
                androidx.compose.ui.window.Dialog(
                    onDismissRequest = { showLightbox = false },
                    properties = androidx.compose.ui.window.DialogProperties(usePlatformDefaultWidth = false)
                ) {
                    Box(
                        Modifier.fillMaxSize()
                            .background(Color.Black.copy(alpha = 0.90f))
                            .clickable { showLightbox = false },
                        contentAlignment = Alignment.Center
                    ) {
                        Image(
                            bitmap = image,
                            contentDescription = "全屏大图预览",
                            modifier = Modifier.fillMaxWidth(0.95f).fillMaxHeight(0.84f),
                            contentScale = ContentScale.Fit
                        )
                        Row(
                            Modifier.align(Alignment.BottomCenter).padding(bottom = 32.dp),
                            horizontalArrangement = Arrangement.spacedBy(12.dp)
                        ) {
                            Box(
                                Modifier.clip(RoundedCornerShape(20.dp))
                                    .background(Color.White.copy(alpha = 0.18f))
                                    .clickable {
                                        val full = if (stickerPath.startsWith("http")) stickerPath else model.ui.serverUrl.trimEnd('/') + "/" + stickerPath.trimStart('/')
                                        showLightbox = false
                                        model.openLink(full)
                                    }
                                    .padding(horizontal = 16.dp, vertical = 9.dp)
                            ) {
                                Text("在浏览器打开", color = Color.White, fontSize = 13.sp, fontWeight = FontWeight.Medium)
                            }
                            Box(
                                Modifier.clip(RoundedCornerShape(20.dp))
                                    .background(Color.White.copy(alpha = 0.25f))
                                    .clickable { showLightbox = false }
                                    .padding(horizontal = 18.dp, vertical = 9.dp)
                            ) {
                                Text("关闭", color = Color.White, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
                            }
                        }
                    }
                }
            }
        }
    }
}

/**
 * Single-Line Tool Call Capsule (Telegram Bot Action Pill):
 * Strictly 1 line by default, spring-animated expansion on tap.
 */
@Composable
internal fun NativeToolTraceGroup(model: NativePulseModel, messageId: String, trace: List<org.json.JSONObject>) {
    val c = LocalPulsePalette.current
    val haptics = LocalHapticFeedback.current
    var expanded by rememberSaveable(messageId) { mutableStateOf(false) }
    val totalMs = remember(trace) { trace.sumOf { it.optLong("duration_ms", 0L) } }
    val firstToolLabel = remember(trace) {
        trace.firstOrNull()?.let { humanizeToolName(it.optString("tool"), it.optJSONObject("arguments")) }.orEmpty()
    }

    Column(
        Modifier.fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .background(c.surface.copy(alpha = 0.90f))
            .clickable {
                haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                expanded = !expanded
            }
            .padding(horizontal = 12.dp, vertical = 7.dp)
    ) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(6.dp).clip(CircleShape).background(c.accent))
            Text(
                if (trace.size == 1) firstToolLabel else "已执行 ${trace.size} 步 · $firstToolLabel",
                Modifier.padding(start = 8.dp).weight(1f),
                color = c.muted,
                fontSize = 12.5.sp,
                fontWeight = FontWeight.Medium,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis
            )
            Text(
                if (expanded) "收起 ▴" else "${totalMs}ms ▾",
                Modifier.padding(start = 8.dp),
                color = c.faint,
                fontSize = 11.5.sp,
                style = TextStyle(fontFeatureSettings = "tnum")
            )
        }
        AnimatedVisibility(
            visible = expanded,
            enter = expandVertically(spring(dampingRatio = 0.80f, stiffness = 420f)) + fadeIn(),
            exit = shrinkVertically(spring(dampingRatio = 0.85f, stiffness = 460f)) + fadeOut()
        ) {
            Column(
                Modifier.padding(top = 7.dp, start = 2.dp),
                verticalArrangement = Arrangement.spacedBy(5.dp)
            ) {
                trace.forEach { step ->
                    val toolName = step.optString("tool")
                    val argsObj = step.optJSONObject("arguments")
                    val dur = step.optLong("duration_ms", 0L)
                    val resObj = step.optJSONObject("result")
                    val hasErr = resObj?.has("error") == true
                    val stepNote = step.optString("step_note").trim()
                    if (stepNote.isNotBlank()) {
                        Text(
                            "💬 $stepNote",
                            Modifier.padding(bottom = 2.dp),
                            color = c.muted,
                            fontSize = 11.5.sp,
                            lineHeight = 16.sp,
                            maxLines = 3,
                            overflow = TextOverflow.Ellipsis
                        )
                    }
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                        Text(if (hasErr) "✕" else "✓", color = if (hasErr) c.warn else c.green, fontSize = 11.sp, fontWeight = FontWeight.Bold)
                        Text(
                            humanizeToolName(toolName, argsObj),
                            Modifier.padding(start = 6.dp).weight(1f),
                            color = if (hasErr) c.warn else c.ink.copy(alpha = 0.86f),
                            fontSize = 12.sp,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis
                        )
                        if (toolName == "cloud_desktop") {
                            Text(
                                "查看电脑 ›",
                                Modifier.padding(start = 6.dp)
                                    .clip(RoundedCornerShape(6.dp))
                                    .background(c.accent.copy(alpha = 0.14f))
                                    .clickable { model.openCloudDesktop() }
                                    .padding(horizontal = 7.dp, vertical = 2.dp),
                                color = c.accent,
                                fontSize = 11.sp,
                                fontWeight = FontWeight.Medium
                            )
                        }
                        if (dur > 0) {
                            Text(
                                "${dur}ms",
                                Modifier.padding(start = 6.dp),
                                color = c.faint,
                                fontSize = 11.sp,
                                style = TextStyle(fontFeatureSettings = "tnum")
                            )
                        }
                    }
                }
            }
        }
    }
}
