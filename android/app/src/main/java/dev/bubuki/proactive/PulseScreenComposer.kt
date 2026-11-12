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
 * [Module] PulseScreenComposer
 * [Responsibility]
 * 聊天室頂欄導航與底部斜杠指令輸入欄領域模塊：負責頂部狀態欄、Bot 快速切換膠囊欄、上下文 Token 進度條、
 * 子代理與 Todo 狀態橫幅 (NativeChat)，以及底部斜杠指令面板、附件預覽、消息編輯欄與輸入發送框 (NativeComposer)。
 *
 * [Public API]
 * - NativeChat(model: NativePulseModel, bot: PulseBot, modifier: Modifier, onComposerHeight: (Int) -> Unit)
 * - NativeComposer(model: NativePulseModel, bot: PulseBot, onHeight: (Int) -> Unit)
 */

@Composable
internal fun NativeChat(model: NativePulseModel, bot: PulseBot, modifier: Modifier, onComposerHeight: (Int) -> Unit) {
    val s = model.ui
    val c = LocalPulsePalette.current
    val focus = LocalFocusManager.current
    val haptics = LocalHapticFeedback.current
    val otherUnreadCount = remember(s.unread, s.messages, bot.id) {
        s.messages.count { it.botId != bot.id && it.id in s.unread }
    }

    Column(modifier.background(c.chatBg)) {
        // 1. Telegram iOS Navigation Bar (Swipe Left/Right to switch Bot, Swipe Down or Tap Avatar to open Contact Info)
        Surface(
            color = c.surface.copy(alpha = 0.96f),
            tonalElevation = 0.dp,
            modifier = Modifier.pointerInput(bot.id) {
                var dx = 0f
                var dy = 0f
                awaitEachGesture {
                    awaitFirstDown(requireUnconsumed = false)
                    dx = 0f
                    dy = 0f
                    do {
                        val event = awaitPointerEvent()
                        val change = event.changes.firstOrNull() ?: break
                        dx += change.position.x - change.previousPosition.x
                        dy += change.position.y - change.previousPosition.y
                    } while (event.changes.any { it.pressed })
                    if (abs(dx) > abs(dy) && abs(dx) > 70f) {
                        haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                        if (dx < 0) model.switchAdjacentBot(1) else model.switchAdjacentBot(-1)
                    } else if (dy > 60f && abs(dy) > abs(dx)) {
                        haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                        focus.clearFocus()
                        model.openBotEditor(bot.id)
                    }
                }
            }
        ) {
            Column {
                Row(
                    Modifier.fillMaxWidth().padding(horizontal = 6.dp, vertical = 6.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    // Left: Telegram iOS Back Button with Unread Counter Pill
                    Row(
                        Modifier.clip(RoundedCornerShape(12.dp))
                            .iosPressScale(0.95f) {
                                focus.clearFocus()
                                model.back()
                            }
                            .padding(horizontal = 6.dp, vertical = 7.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        PulseIcon("back", c.accent, Modifier.size(22.dp))
                        if (otherUnreadCount > 0) {
                            Box(
                                Modifier.padding(start = 2.dp)
                                    .defaultMinSize(minWidth = 19.dp, minHeight = 19.dp)
                                    .clip(CircleShape)
                                    .background(c.accent)
                                    .padding(horizontal = 5.dp, vertical = 1.dp),
                                contentAlignment = Alignment.Center
                            ) {
                                Text(
                                    otherUnreadCount.toString(),
                                    color = Color.White,
                                    fontSize = 11.sp,
                                    fontWeight = FontWeight.Bold,
                                    style = TextStyle(fontFeatureSettings = "tnum")
                                )
                            }
                        } else {
                            Text(
                                "对话",
                                color = c.accent,
                                fontSize = 16.5.sp,
                                fontWeight = FontWeight.Normal,
                                modifier = Modifier.padding(start = 1.dp)
                            )
                        }
                    }

                    // Center: Bot Avatar + Name + 3-Bot Switcher Dots + Live Status / Token Cache Subtitle
                    val effectiveModel = bot.model.ifBlank { s.defaultModel.ifBlank { "默认模型" } }
                    val isLiveHere = s.streamBotId == bot.id || s.workingBot == bot.id || s.busyBot == bot.id
                    Row(
                        Modifier.padding(start = 2.dp)
                            .weight(1f)
                            .clip(RoundedCornerShape(12.dp))
                            .iosPressScale(0.97f) {
                                focus.clearFocus()
                                model.openBotEditor(bot.id)
                            }
                            .padding(horizontal = 8.dp, vertical = 4.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        PulseAvatar(bot, s, diameter = 38.dp, showStatusDot = true, ringColor = c.surface)
                        Column(Modifier.padding(start = 10.dp).weight(1f)) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text(
                                    bot.name,
                                    color = c.ink,
                                    fontSize = 16.5.sp,
                                    fontWeight = FontWeight.SemiBold,
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis,
                                    letterSpacing = (-0.2).sp
                                )
                                if (s.primary == bot.id) {
                                    Text(
                                        "主控",
                                        Modifier.padding(start = 6.dp)
                                            .clip(RoundedCornerShape(4.dp))
                                            .background(c.accent.copy(alpha = 0.14f))
                                            .padding(horizontal = 5.dp, vertical = 1.dp),
                                        color = c.accent,
                                        fontSize = 10.sp,
                                        fontWeight = FontWeight.SemiBold
                                    )
                                }
                                // 3-Bot Switcher Indicator Dots (Swipe Left/Right hint)
                                if (s.bots.size > 1) {
                                    Row(
                                        Modifier.padding(start = 8.dp),
                                        horizontalArrangement = Arrangement.spacedBy(3.5.dp),
                                        verticalAlignment = Alignment.CenterVertically
                                    ) {
                                        s.bots.forEach { b ->
                                            val active = b.id == bot.id
                                            Box(
                                                Modifier.size(if (active) 5.5.dp else 4.dp)
                                                    .clip(CircleShape)
                                                    .background(if (active) c.accent else c.faint.copy(alpha = 0.45f))
                                            )
                                        }
                                    }
                                }
                            }
                            val (hdrCurTok, _, _) = model.contextStatsFor(bot.id)
                            val (hdrCachedTok, hdrCachePct) = model.contextCacheFor(bot.id)
                            val (hdrTtft, hdrTps, _) = model.contextPerfFor(bot.id)
                            val ttftStr = if (hdrTtft > 0) {
                                if (hdrTtft >= 1000) String.format(java.util.Locale.US, " · 首字%.1fs", hdrTtft / 1000.0)
                                else " · 首字${hdrTtft}ms"
                            } else ""
                            val tpsStr = if (hdrTps > 0.0) String.format(java.util.Locale.US, " · %.0ft/s", hdrTps) else ""
                            val ctxBadge = if (hdrCurTok > 0) {
                                (if (hdrCachedTok > 0) " · ${model.formatTokenCount(hdrCurTok)} ($hdrCachePct%)"
                                else " · ${model.formatTokenCount(hdrCurTok)}") + ttftStr + tpsStr
                            } else ""
                            Text(
                                if (isLiveHere) {
                                    (s.streamTool.takeIf { it.isNotBlank() }?.let { "正在${humanizeToolName(it, null)}…" }
                                        ?: "${s.streamSpeakerName.ifBlank { bot.name }} 正在输入…") + ctxBadge
                                } else {
                                    effectiveModel + ctxBadge
                                },
                                color = if (isLiveHere) c.accent else c.muted,
                                fontSize = 11.5.sp,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                                style = TextStyle(fontFeatureSettings = "tnum")
                            )
                        }
                    }

                    // Right: Quick Profile & More Options
                    PulseRoundButton("more", "选项", { focus.clearFocus(); model.botMenu(bot.id) }, background = false)
                }

                HorizontalDivider(color = c.line.copy(alpha = 0.65f), thickness = 0.5.dp)

                // 2. Telegram-Style Pinned Memory Bar (`📌 置顶记忆`)
                AnimatedVisibility(
                    visible = s.pinnedMemories.isNotEmpty(),
                    enter = expandVertically(spring(dampingRatio = 0.82f, stiffness = 400f)) + fadeIn(),
                    exit = shrinkVertically(spring(dampingRatio = 0.85f, stiffness = 450f)) + fadeOut()
                ) {
                    var pinsExpanded by remember { mutableStateOf(false) }
                    val latestPin = s.pinnedMemories.lastOrNull()?.optString("text").orEmpty()
                    Column(
                        Modifier.fillMaxWidth()
                            .background(c.surface.copy(alpha = 0.96f))
                            .clickable {
                                haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                                pinsExpanded = !pinsExpanded
                            }
                    ) {
                        Row(
                            Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 7.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Box(
                                Modifier.width(3.dp).height(28.dp)
                                    .clip(RoundedCornerShape(2.dp))
                                    .background(c.accent)
                            )
                            Column(Modifier.padding(start = 10.dp).weight(1f)) {
                                Text(
                                    "置顶记忆 #${s.pinnedMemories.size}",
                                    color = c.accent,
                                    fontSize = 11.5.sp,
                                    fontWeight = FontWeight.SemiBold,
                                    style = TextStyle(fontFeatureSettings = "tnum")
                                )
                                Text(
                                    latestPin,
                                    color = c.ink,
                                    fontSize = 13.sp,
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis
                                )
                            }
                            PulseIcon(
                                if (pinsExpanded) "chevron_up" else "chevron_down",
                                c.faint,
                                Modifier.padding(start = 8.dp).size(16.dp)
                            )
                        }
                        AnimatedVisibility(
                            visible = pinsExpanded,
                            enter = expandVertically(spring(dampingRatio = 0.80f, stiffness = 400f)) + fadeIn(),
                            exit = shrinkVertically(spring(dampingRatio = 0.85f, stiffness = 440f)) + fadeOut()
                        ) {
                            Column(
                                Modifier.fillMaxWidth().padding(start = 16.dp, end = 14.dp, bottom = 10.dp),
                                verticalArrangement = Arrangement.spacedBy(6.dp)
                            ) {
                                s.pinnedMemories.forEach { p ->
                                    val pid = p.optString("id")
                                    val ptext = p.optString("text")
                                    Row(
                                        Modifier.fillMaxWidth()
                                            .clip(RoundedCornerShape(10.dp))
                                            .background(c.fill.copy(alpha = 0.72f))
                                            .padding(horizontal = 10.dp, vertical = 7.dp),
                                        verticalAlignment = Alignment.CenterVertically
                                    ) {
                                        Text(
                                            ptext,
                                            color = c.ink,
                                            fontSize = 12.5.sp,
                                            lineHeight = 17.sp,
                                            modifier = Modifier.weight(1f)
                                        )
                                        Text(
                                            "移除",
                                            Modifier.padding(start = 8.dp)
                                                .clip(RoundedCornerShape(6.dp))
                                                .background(c.surface)
                                                .clickable { model.removeMemoryPin(pid) }
                                                .padding(horizontal = 8.dp, vertical = 3.dp),
                                            color = c.warn,
                                            fontSize = 11.sp,
                                            fontWeight = FontWeight.Medium
                                        )
                                    }
                                }
                            }
                        }
                        HorizontalDivider(color = c.line.copy(alpha = 0.55f), thickness = 0.5.dp)
                    }
                }
                val chatVdState by RootPhoneHarness.changes.collectAsState()
                if (chatVdState.displayCreated) {
                    Row(
                        Modifier.fillMaxWidth()
                            .background(c.green.copy(alpha = 0.12f))
                            .padding(horizontal = 14.dp, vertical = 6.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Row(
                            Modifier.weight(1f)
                                .clip(RoundedCornerShape(8.dp))
                                .clickable { model.launchFloatingVirtualDisplay() }
                                .padding(vertical = 2.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Box(Modifier.size(7.dp).clip(CircleShape).background(c.green))
                            Text(
                                "虚拟屏运行中 (#${chatVdState.displayId}) · 点此展开小窗",
                                Modifier.weight(1f).padding(start = 8.dp, end = 6.dp),
                                color = c.green,
                                fontSize = 12.sp,
                                fontWeight = FontWeight.SemiBold,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis
                            )
                        }
                        Box(
                            Modifier.clip(RoundedCornerShape(999.dp))
                                .background(c.warn.copy(alpha = 0.16f))
                                .clickable { model.destroyVirtualDisplay() }
                                .padding(horizontal = 10.dp, vertical = 3.5.dp),
                            contentAlignment = Alignment.Center
                        ) {
                            Text(
                                "销毁虚拟屏",
                                color = c.warn,
                                fontSize = 11.sp,
                                fontWeight = FontWeight.SemiBold
                            )
                        }
                    }
                    HorizontalDivider(color = c.line.copy(alpha = 0.55f), thickness = 0.5.dp)
                }
            }
        }

        // 3. Directional AnimatedContent for Bot Switching (Glides smoothly left/right when swiping between Bots!)
        AnimatedContent(
            targetState = bot,
            modifier = Modifier.weight(1f).fillMaxWidth(),
            transitionSpec = {
                val dir = s.lastSwitchDir
                (slideInHorizontally(spring(dampingRatio = 0.84f, stiffness = 400f)) { fullWidth -> dir * (fullWidth / 3) } +
                    fadeIn(tween(180))) togetherWith
                    (slideOutHorizontally(spring(dampingRatio = 0.86f, stiffness = 420f)) { fullWidth -> -dir * (fullWidth / 3) } +
                        fadeOut(tween(130)))
            },
            contentKey = { it.id },
            label = "bot_room_switch"
        ) { activeBot ->
            NativeChatRoomViewport(model, activeBot)
        }

        // 4. Telegram iOS Bottom Composer
        NativeComposer(model, bot, onComposerHeight)
    }
}

@Composable
internal fun NativeComposer(model: NativePulseModel, bot: PulseBot, onHeight: (Int) -> Unit) {
    val s = model.ui
    val c = LocalPulsePalette.current
    val focus = LocalFocusManager.current
    val haptics = LocalHapticFeedback.current
    val requester = remember { FocusRequester() }
    val executing = s.workingBot == bot.id || s.busyBot == bot.id || s.streamBotId == bot.id
    val hasDraft = !s.uploading && (s.draft.isNotBlank() || s.attachments.isNotEmpty())
    var showComposerTray by remember { mutableStateOf(false) }
    var showStickerDrawer by remember { mutableStateOf(false) }

    val enabledPack = remember(s.stickerPacks) {
        s.stickerPacks.firstOrNull { it.optBoolean("enabled", true) && (it.optJSONArray("stickers")?.length() ?: 0) > 0 }
    }

    Surface(
        Modifier.fillMaxWidth()
            .onSizeChanged { onHeight(it.height) }
            .pointerInput(bot.id) {
                var dx = 0f
                var dy = 0f
                awaitEachGesture {
                    awaitFirstDown(requireUnconsumed = false)
                    dx = 0f
                    dy = 0f
                    do {
                        val event = awaitPointerEvent()
                        val change = event.changes.firstOrNull() ?: break
                        dx += change.position.x - change.previousPosition.x
                        dy += change.position.y - change.previousPosition.y
                    } while (event.changes.any { it.pressed })
                    if (abs(dx) > abs(dy) && abs(dx) > 80f) {
                        haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                        if (dx < 0) model.switchAdjacentBot(1) else model.switchAdjacentBot(-1)
                    } else if (abs(dy) > abs(dx) && abs(dy) > 52f) {
                        haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                        if (dy < 0) {
                            showComposerTray = true
                        } else {
                            showComposerTray = false
                            showStickerDrawer = false
                            focus.clearFocus()
                        }
                    }
                }
            },
        color = c.surface.copy(alpha = 0.98f),
        tonalElevation = 0.dp
    ) {
        Column(Modifier.fillMaxWidth()) {
            HorizontalDivider(color = c.line.copy(alpha = 0.65f), thickness = 0.5.dp)

            Column(Modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 7.dp)) {
                // 1. Spring-Animated Pull-Up Tool Tray
                AnimatedVisibility(
                    visible = showComposerTray,
                    enter = expandVertically(spring(dampingRatio = 0.78f, stiffness = 400f)) + fadeIn(tween(150)),
                    exit = shrinkVertically(spring(dampingRatio = 0.85f, stiffness = 450f)) + fadeOut(tween(120))
                ) {
                    Row(
                        Modifier.fillMaxWidth()
                            .padding(bottom = 8.dp)
                            .clip(RoundedCornerShape(14.dp))
                            .background(c.fill.copy(alpha = 0.75f))
                            .padding(6.dp),
                        horizontalArrangement = Arrangement.spacedBy(6.dp)
                    ) {
                        listOf(
                            Triple("虚拟屏", c.ink) { showComposerTray = false; model.launchFloatingVirtualDisplay() },
                            Triple("云桌面", c.ink) { showComposerTray = false; model.openCloudDesktop() },
                            Triple("压缩上下文", c.accent) { showComposerTray = false; model.compactBotContext(bot.id) },
                            Triple("个人主页", c.ink) { showComposerTray = false; model.openBotEditor(bot.id) }
                        ).forEach { (label, col, act) ->
                            Text(
                                label,
                                Modifier.weight(1f)
                                    .clip(RoundedCornerShape(10.dp))
                                    .background(c.surface)
                                    .iosPressScale(0.95f) {
                                        haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                                        act()
                                    }
                                    .padding(vertical = 8.dp),
                                color = col,
                                fontSize = 12.sp,
                                fontWeight = FontWeight.Medium,
                                textAlign = TextAlign.Center
                            )
                        }
                    }
                }

                // 2. Spring-Animated Optional Sticker Plugin Drawer (Shirakami Fubuki Pack)
                AnimatedVisibility(
                    visible = showStickerDrawer && enabledPack != null,
                    enter = expandVertically(spring(dampingRatio = 0.78f, stiffness = 400f)) + fadeIn(tween(160)),
                    exit = shrinkVertically(spring(dampingRatio = 0.85f, stiffness = 450f)) + fadeOut(tween(120))
                ) {
                    val packId = enabledPack?.optString("id", "fubuki") ?: "fubuki"
                    val packName = enabledPack?.optString("name", "表情贴纸") ?: "表情贴纸"
                    val stickersArr = enabledPack?.optJSONArray("stickers") ?: JSONArray()
                    val stickers = remember(stickersArr) { (0 until stickersArr.length()).map { stickersArr.getJSONObject(it) } }
                    Column(
                        Modifier.fillMaxWidth()
                            .padding(bottom = 8.dp)
                            .clip(RoundedCornerShape(16.dp))
                            .background(c.fill.copy(alpha = 0.72f))
                            .padding(10.dp),
                        verticalArrangement = Arrangement.spacedBy(6.dp)
                    ) {
                        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                            Text("$packName (点按让 ${bot.name} 互动)", color = c.muted, fontSize = 12.sp, fontWeight = FontWeight.Medium, modifier = Modifier.weight(1f))
                            Text("收起", Modifier.clickable { showStickerDrawer = false }, color = c.accent, fontSize = 12.sp)
                        }
                        LazyRow(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                            items(stickers) { st ->
                                val fname = st.optString("file")
                                val slabel = st.optString("label", fname)
                                val url = st.optString("url", "/stickers/$packId/$fname")
                                Column(
                                    Modifier.clip(RoundedCornerShape(12.dp))
                                        .background(c.surface)
                                        .iosPressScale(0.93f) {
                                            showStickerDrawer = false
                                            model.sendQuickPrompt("[表情: $slabel]")
                                        }
                                        .padding(6.dp),
                                    horizontalAlignment = Alignment.CenterHorizontally
                                ) {
                                    PulseStickerThumb(model, url, 58.dp)
                                    Text(slabel, color = c.muted, fontSize = 10.sp, maxLines = 1, modifier = Modifier.padding(top = 2.dp))
                                }
                            }
                        }
                    }
                }

                // 4. Stop Pill when typing while Bot is busy
                AnimatedVisibility(visible = executing && hasDraft) {
                    Row(Modifier.fillMaxWidth().padding(bottom = 6.dp), horizontalArrangement = Arrangement.End) {
                        Row(
                            Modifier.clip(CircleShape)
                                .background(c.warn.copy(alpha = 0.14f))
                                .clickable { model.stop() }
                                .padding(horizontal = 12.dp, vertical = 4.5.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Box(Modifier.size(7.dp).clip(RoundedCornerShape(2.dp)).background(c.warn))
                            Text("停止当前生成", Modifier.padding(start = 6.dp), color = c.warn, fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
                        }
                    }
                }

                // 5. Attachment Bar
                AnimatedVisibility(
                    visible = s.attachments.isNotEmpty() || s.uploading,
                    enter = expandVertically(spring(dampingRatio = 0.80f, stiffness = 420f)) + fadeIn(),
                    exit = shrinkVertically(spring(dampingRatio = 0.85f, stiffness = 460f)) + fadeOut()
                ) {
                    Row(
                        Modifier.fillMaxWidth()
                            .padding(bottom = 6.dp)
                            .clip(RoundedCornerShape(12.dp))
                            .background(c.fill)
                            .padding(start = 12.dp, end = 4.dp, top = 2.dp, bottom = 2.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        PulseIcon("attach", c.accent, Modifier.size(16.dp))
                        Text(
                            if (s.uploading) "正在上传图片/附件…" else s.attachments.joinToString("、") { it.name },
                            Modifier.padding(start = 8.dp).weight(1f),
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                            fontSize = 13.sp,
                            color = c.ink,
                            fontWeight = FontWeight.Medium
                        )
                        if (!s.uploading) PulseRoundButton("close", "移除附件", model::clearAttachments, background = false)
                    }
                }

                // 6. Telegram-Style Edit Message Bar (Spring Animated)
                AnimatedVisibility(
                    visible = s.editingMessage != null,
                    enter = expandVertically(spring(dampingRatio = 0.78f, stiffness = 420f)) + fadeIn(),
                    exit = shrinkVertically(spring(dampingRatio = 0.85f, stiffness = 460f)) + fadeOut()
                ) {
                    val editing = s.editingMessage
                    if (editing != null) {
                        Row(
                            Modifier.fillMaxWidth()
                                .padding(bottom = 6.dp)
                                .clip(RoundedCornerShape(12.dp))
                                .background(c.fill.copy(alpha = 0.78f))
                                .padding(start = 10.dp, end = 4.dp, top = 3.dp, bottom = 3.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            PulseIcon("edit", c.accent, Modifier.size(17.dp))
                            Box(
                                Modifier.padding(start = 8.dp)
                                    .width(2.5.dp)
                                    .height(26.dp)
                                    .clip(RoundedCornerShape(2.dp))
                                    .background(c.accent)
                            )
                            Column(Modifier.padding(start = 8.dp).weight(1f)) {
                                Text("编辑消息", color = c.accent, fontSize = 11.5.sp, fontWeight = FontWeight.SemiBold)
                                Text(
                                    editing.text.replace("\n", " "),
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis,
                                    fontSize = 12.5.sp,
                                    color = c.ink
                                )
                            }
                            PulseRoundButton("close", "取消编辑", { model.startEditingMessage(null) }, background = false)
                        }
                    }
                }

                // 7. Telegram-Style Quote Reply Bar (Spring Animated)
                AnimatedVisibility(
                    visible = s.reply != null,
                    enter = expandVertically(spring(dampingRatio = 0.78f, stiffness = 420f)) + fadeIn(),
                    exit = shrinkVertically(spring(dampingRatio = 0.85f, stiffness = 460f)) + fadeOut()
                ) {
                    val quote = s.reply
                    if (quote != null) {
                        val quoteAuthor = if (quote.role == "user" && quote.kind != "bot_message") "我" else (quote.fromBotName.ifBlank { bot.name })
                        Row(
                            Modifier.fillMaxWidth()
                                .padding(bottom = 6.dp)
                                .clip(RoundedCornerShape(12.dp))
                                .background(c.fill.copy(alpha = 0.78f))
                                .padding(start = 10.dp, end = 4.dp, top = 3.dp, bottom = 3.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            PulseIcon("reply", c.accent, Modifier.size(17.dp))
                            Box(
                                Modifier.padding(start = 8.dp)
                                    .width(2.5.dp)
                                    .height(26.dp)
                                    .clip(RoundedCornerShape(2.dp))
                                    .background(c.accent)
                            )
                            Column(Modifier.padding(start = 8.dp).weight(1f)) {
                                Text(
                                    "回复 $quoteAuthor",
                                    color = c.accent,
                                    fontSize = 11.5.sp,
                                    fontWeight = FontWeight.SemiBold
                                )
                                Text(
                                    quote.text.replace("\n", " "),
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis,
                                    fontSize = 12.5.sp,
                                    color = c.muted
                                )
                            }
                            PulseRoundButton("close", "取消引用", { model.reply(null) }, background = false)
                        }
                    }
                }

                // 7.5. OpenCode / Grok Bot Style `/` Slash Command Autocomplete Popup
                val slashQuery = remember(s.draft) {
                    val trimmed = s.draft.trimStart()
                    if (trimmed.startsWith("/") && !trimmed.contains('\n') && !trimmed.contains(' ')) {
                        trimmed.lowercase()
                    } else null
                }
                val slashSuggestions = remember(slashQuery) {
                    if (slashQuery == null) emptyList()
                    else listOf(
                        Triple("/goal ", "深度目标闭环…", "开启长程死磕模式：自动拆解并持续执行直至目标 100% 完成"),
                        Triple("/compact", "压缩上下文", "归档较早对话至记忆库与 FTS5，释放 Token 窗口"),
                        Triple("/mcp", "MCP 与工具矩阵", "查看已挂载的 MCP 扩展工具、技能包与跨端工具"),
                        Triple("/memory", "记忆库概览", "查看 Zettelkasten 记忆库索引与个人印象档案"),
                        Triple("/models", "查看或切换模型", "列出可用大模型，或用 /model <ID> 切换模型"),
                        Triple("/sessions", "多端会话与工作区", "查看四端 Bot 房间及 Windows DSH 工作区会话"),
                        Triple("/undo", "撤销上一轮对话", "撤回当前房间最后一轮问答（可用 /redo 恢复）"),
                        Triple("/redo", "重做已撤销对话", "恢复最近一次通过 /undo 撤回的问答"),
                        Triple("/status", "上下文与缓存状态", "查看 Token 预算、Prefix KV 缓存命中率与在线状态"),
                        Triple("/doctor", "Harness 通道自检", "诊断多模态视觉、图片通道、记忆库与跨端桥接"),
                        Triple("/pins", "查看置顶记忆", "查看全部 📌 置顶记忆（或用 /pin <内容> 新增）"),
                        Triple("/pin ", "新增置顶记忆…", "填入 /pin 前缀以输入要永久钉住的规则"),
                        Triple("/clear", "归档并清空当前屏", "将当前房间历史归档至 FTS5 并开启新上下文"),
                        Triple("/share", "导出当前对话", "将当前房间对话导出为工作区 Markdown 文件"),
                        Triple("/help", "斜杠指令帮助", "显示所有支持的 Harness / 快捷指令大全")
                    ).filter { (cmd, title, _) ->
                        cmd.trim().startsWith(slashQuery) ||
                            (slashQuery.startsWith("/compack") && cmd == "/compact") ||
                            title.lowercase().contains(slashQuery.removePrefix("/"))
                    }
                }
                AnimatedVisibility(
                    visible = slashSuggestions.isNotEmpty(),
                    enter = expandVertically(spring(dampingRatio = 0.80f, stiffness = 420f)) + fadeIn(tween(140)),
                    exit = shrinkVertically(spring(dampingRatio = 0.85f, stiffness = 460f)) + fadeOut(tween(110))
                ) {
                    Column(
                        Modifier.fillMaxWidth()
                            .padding(bottom = 7.dp)
                            .heightIn(max = 220.dp)
                            .clip(RoundedCornerShape(16.dp))
                            .background(c.fill.copy(alpha = 0.96f))
                            .border(0.6.dp, c.accent.copy(alpha = 0.35f), RoundedCornerShape(16.dp))
                            .verticalScroll(rememberScrollState())
                            .padding(vertical = 4.dp, horizontal = 6.dp),
                        verticalArrangement = Arrangement.spacedBy(2.dp)
                    ) {
                        slashSuggestions.forEach { (cmd, title, desc) ->
                            Row(
                                Modifier.fillMaxWidth()
                                    .clip(RoundedCornerShape(10.dp))
                                    .clickable {
                                        haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                                        if (cmd.endsWith(" ")) {
                                            model.setDraft(cmd)
                                        } else {
                                            model.setDraft("")
                                            model.sendQuickPrompt(cmd)
                                        }
                                    }
                                    .padding(horizontal = 10.dp, vertical = 7.dp),
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Text(
                                    cmd.trim(),
                                    color = c.accent,
                                    fontSize = 13.5.sp,
                                    fontWeight = FontWeight.Bold,
                                    fontFamily = FontFamily.Monospace,
                                    modifier = Modifier.widthIn(min = 78.dp)
                                )
                                Text(
                                    title,
                                    color = c.ink,
                                    fontSize = 13.sp,
                                    fontWeight = FontWeight.SemiBold,
                                    modifier = Modifier.padding(start = 6.dp)
                                )
                                Spacer(Modifier.weight(1f))
                                Text(
                                    desc,
                                    color = c.muted,
                                    fontSize = 11.5.sp,
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis,
                                    modifier = Modifier.padding(start = 8.dp).widthIn(max = 170.dp)
                                )
                            }
                        }
                    }
                }

                // 8. Main Telegram iOS Composer Row: [📎 Attach] + [Input Capsule with Sticker Button] + [Morphing Send/Stop Circle]
                Row(
                    Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.Bottom,
                    horizontalArrangement = Arrangement.spacedBy(7.dp)
                ) {
                    // Left 1: Telegram Paperclip Attach Button (Long-press or tap "+" menu)
                    Box(
                        Modifier.padding(bottom = 2.dp)
                            .size(35.dp)
                            .clip(CircleShape)
                            .background(c.fill.copy(alpha = 0.82f))
                            .iosPressScale(0.92f) {
                                haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                                focus.clearFocus()
                                model.chooseAttachment()
                            }
                            .semantics { contentDescription = "发送图片或附件" },
                        contentAlignment = Alignment.Center
                    ) {
                        PulseIcon("attach", c.muted, Modifier.size(18.5.dp))
                    }

                    // Left 2: Workspace / Action Sheet Button
                    Box(
                        Modifier.padding(bottom = 2.dp)
                            .size(35.dp)
                            .clip(CircleShape)
                            .background(if (showComposerTray) c.accent.copy(alpha = 0.16f) else c.fill.copy(alpha = 0.82f))
                            .iosPressScale(0.92f) {
                                haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                                focus.clearFocus()
                                model.actionsMenu()
                            }
                            .semantics { contentDescription = "工作台与工具" },
                        contentAlignment = Alignment.Center
                    ) {
                        PulseIcon("plus", if (showComposerTray) c.accent else c.muted, Modifier.size(19.dp))
                    }

                    // Center: Telegram Rounded Input Capsule + Embedded Sticker Icon
                    Row(
                        Modifier.weight(1f)
                            .clip(RoundedCornerShape(20.dp))
                            .background(c.fill.copy(alpha = 0.88f))
                            .border(0.5.dp, c.line.copy(alpha = 0.55f), RoundedCornerShape(20.dp))
                            .padding(start = 13.dp, end = 6.dp, top = 5.dp, bottom = 5.dp),
                        verticalAlignment = Alignment.Bottom
                    ) {
                        BasicTextField(
                            s.draft,
                            { if (it.length <= 12000) model.setDraft(it) },
                            Modifier.weight(1f)
                                .heightIn(min = 28.dp, max = 128.dp)
                                .padding(vertical = 3.dp, horizontal = 2.dp)
                                .focusRequester(requester)
                                .semantics { contentDescription = "聊天消息" },
                            textStyle = TextStyle(color = c.ink, fontSize = 16.sp, lineHeight = 21.sp),
                            cursorBrush = SolidColor(c.accent),
                            maxLines = 6,
                            keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences),
                            decorationBox = { inner ->
                                Box {
                                    if (s.draft.isEmpty()) Text("消息…", color = c.faint, fontSize = 16.sp, maxLines = 1)
                                    inner()
                                }
                            }
                        )
                        if (enabledPack != null) {
                            Box(
                                Modifier.padding(bottom = 1.dp, end = 2.dp)
                                    .size(28.dp)
                                    .clip(CircleShape)
                                    .iosPressScale(0.90f) {
                                        haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                                        showStickerDrawer = !showStickerDrawer
                                    },
                                contentAlignment = Alignment.Center
                            ) {
                                PulseIcon(
                                    "sticker",
                                    if (showStickerDrawer) c.accent else c.muted,
                                    Modifier.size(19.dp)
                                )
                            }
                        }
                    }

                    // Right: Telegram Circular Send / Stop Button with Spring Scale & Color Animation
                    val showStopOnly = executing && !hasDraft
                    val buttonEnabled = (!s.uploading && hasDraft) || showStopOnly
                    val btnScale by animateFloatAsState(
                        targetValue = if (buttonEnabled) 1f else 0.88f,
                        animationSpec = spring(dampingRatio = 0.65f, stiffness = 500f),
                        label = "send_btn_scale"
                    )
                    val btnColor by animateColorAsState(
                        targetValue = when {
                            showStopOnly -> c.warn
                            buttonEnabled -> c.accent
                            else -> c.fill
                        },
                        animationSpec = tween(160),
                        label = "send_btn_color"
                    )
                    Box(
                        Modifier.padding(bottom = 2.dp)
                            .size(35.dp)
                            .graphicsLayer {
                                scaleX = btnScale
                                scaleY = btnScale
                            }
                            .clip(CircleShape)
                            .background(btnColor)
                            .iosPressScale(0.90f, enabled = buttonEnabled) {
                                haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                                if (showStopOnly) model.stop()
                                else if (s.editingMessage != null) model.editMessageAndResend(s.editingMessage, s.draft)
                                else model.send()
                            }
                            .semantics { contentDescription = if (showStopOnly) "停止执行" else "发送消息" },
                        contentAlignment = Alignment.Center
                    ) {
                        if (showStopOnly) {
                            Box(Modifier.size(11.dp).clip(RoundedCornerShape(2.5.dp)).background(Color.White))
                        } else {
                            PulseIcon(
                                "send",
                                if (buttonEnabled) Color.White else c.faint,
                                Modifier.size(17.dp)
                            )
                        }
                    }
                }
            }
        }
    }
}
