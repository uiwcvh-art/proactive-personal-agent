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
 * [Module] PulseScreenHelpers
 * [Responsibility]
 * 視覺主題調色板、通用基礎組件與格式化工具領域模塊：封裝 Apple HIG + Telegram 主題配色 (PulsePalette, LocalPulsePalette, pulsePalette)、
 * 按壓縮放動效 (Modifier.iosPressScale)、首頁 Bot 卡片行 (NativeBotRow)、快捷入口磁貼 (WorkspaceQuickTile)、
 * 底部彈出菜單 (NativeMenuContent)、工具名稱人性化映射 (humanizeToolName)、Wiki 雙鏈轉換 (
ativeWikiMarkdown)，
 * 以及通用輸入框與按鈕組件 (PulseInputField, PulseRoundButton, PulseAction, SettingsCategoryRow, TelegramProfileActionTile)。
 *
 * [Public API]
 * - PulsePalette, LocalPulsePalette, pulsePalette(dark: Boolean), Modifier.iosPressScale(...)
 * - NativeBotRow(...), WorkspaceQuickTile(...), NativeMenuContent(...)
 * - humanizeToolName(...), 
ativeWikiMarkdown(...)
 * - PulseInputField(...), PulseRoundButton(...), PulseAction(...), SettingsCategoryRow(...), TelegramProfileActionTile(...)
 */

internal data class PulsePalette(
    val bg: Color,
    val chatBg: Color,
    val surface: Color,
    val elevated: Color,
    val pinnedRowBg: Color,
    val aiBubble: Color,
    val fill: Color,
    val line: Color,
    val ink: Color,
    val muted: Color,
    val faint: Color,
    val accent: Color,
    val green: Color,
    val amber: Color,
    val warn: Color,
    val userBubble: Color,
    val userInk: Color,
    val userMeta: Color
)

internal val LocalPulsePalette = staticCompositionLocalOf { pulsePalette(true) }

internal fun pulsePalette(dark: Boolean) = if (dark) PulsePalette(
    bg = Color(0xFF000000),
    chatBg = Color(0xFF0E1621),
    surface = Color(0xFF1C1C1E),
    elevated = Color(0xFF2C2C2E),
    pinnedRowBg = Color(0xFF151922),
    aiBubble = Color(0xFF182533),
    fill = Color(0xFF2C2C2E),
    line = Color(0xFF2C2C2E),
    ink = Color(0xFFFFFFFF),
    muted = Color(0xFF98989F),
    faint = Color(0xFF636366),
    accent = Color(0xFF0A84FF),
    green = Color(0xFF30D158),
    amber = Color(0xFFFF9F0A),
    warn = Color(0xFFFF453A),
    userBubble = Color(0xFF2B5278),
    userInk = Color(0xFFFFFFFF),
    userMeta = Color(0xFF78A3CC)
) else PulsePalette(
    bg = Color(0xFFF2F2F7),
    chatBg = Color(0xFFE4ECF2),
    surface = Color(0xFFFFFFFF),
    elevated = Color(0xFFFFFFFF),
    pinnedRowBg = Color(0xFFF7F8FA),
    aiBubble = Color(0xFFFFFFFF),
    fill = Color(0xFFEEEEF3),
    line = Color(0xFFD8D8DC),
    ink = Color(0xFF000000),
    muted = Color(0xFF6C6C70),
    faint = Color(0xFF8E8E93),
    accent = Color(0xFF007AFF),
    green = Color(0xFF34C759),
    amber = Color(0xFFFF9500),
    warn = Color(0xFFFF3B30),
    userBubble = Color(0xFF007AFF),
    userInk = Color(0xFFFFFFFF),
    userMeta = Color(0xFFD1E6FF)
)

/**
 * Apple HIG Spring Press-Scale Modifier:
 * Smoothly compresses interactive controls to `scaleDown` on touch-down and rebounds with iOS spring physics.
 */
internal fun Modifier.iosPressScale(
    scaleDown: Float = 0.96f,
    enabled: Boolean = true,
    onClick: (() -> Unit)? = null
): Modifier = composed {
    val interactionSource = remember { MutableInteractionSource() }
    val isPressed by interactionSource.collectIsPressedAsState()
    val scale by animateFloatAsState(
        targetValue = if (isPressed && enabled) scaleDown else 1f,
        animationSpec = spring(dampingRatio = 0.72f, stiffness = 560f),
        label = "ios_press_scale"
    )
    this.graphicsLayer {
        scaleX = scale
        scaleY = scale
    }.then(
        if (onClick != null) {
            Modifier.clickable(
                interactionSource = interactionSource,
                indication = null,
                enabled = enabled,
                onClick = onClick
            )
        } else Modifier
    )
}

@Composable
internal fun NativeMenuContent(menu: PulseMenu, closing: Boolean, close: () -> Unit, select: (PulseMenuItem) -> Unit) {
    val c = LocalPulsePalette.current
    val haptics = LocalHapticFeedback.current
    Column(
        Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal = 18.dp).padding(top = 8.dp, bottom = 28.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Box(Modifier.padding(bottom = 12.dp).width(36.dp).height(5.dp).clip(CircleShape).background(c.faint.copy(alpha = 0.38f)))
        Row(Modifier.fillMaxWidth().padding(horizontal = 4.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(menu.title, Modifier.weight(1f), color = c.ink, fontSize = 19.sp, fontWeight = FontWeight.SemiBold, letterSpacing = (-0.3).sp)
            PulseRoundButton("close", "关闭面板", close, background = true)
        }
        Column(
            Modifier.fillMaxWidth().padding(top = 8.dp).clip(RoundedCornerShape(16.dp)).background(c.fill.copy(alpha = 0.65f))
        ) {
            menu.items.forEachIndexed { idx, item ->
                if (idx > 0) {
                    HorizontalDivider(Modifier.padding(start = 56.dp), color = c.line.copy(alpha = 0.65f), thickness = 0.5.dp)
                }
                Row(
                    Modifier.fillMaxWidth()
                        .iosPressScale(0.98f, enabled = !closing) {
                            haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                            select(item)
                        }
                        .padding(vertical = 14.dp, horizontal = 14.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Box(
                        Modifier.size(32.dp).clip(RoundedCornerShape(8.dp)).background(c.accent.copy(alpha = 0.14f)),
                        contentAlignment = Alignment.Center
                    ) {
                        PulseIcon(item.icon, c.accent, Modifier.size(17.dp))
                    }
                    Column(Modifier.padding(start = 14.dp).weight(1f)) {
                        Text(item.title, fontSize = 16.sp, color = c.ink, fontWeight = FontWeight.Medium)
                        if (item.description.isNotEmpty()) Text(item.description, Modifier.padding(top = 2.dp), fontSize = 13.sp, color = c.muted)
                    }
                    PulseIcon("next", c.faint, Modifier.size(15.dp))
                }
            }
        }
    }
}

@Composable
internal fun WorkspaceQuickTile(
    icon: String,
    title: String,
    subtitle: String,
    tint: Color,
    modifier: Modifier = Modifier,
    onClick: () -> Unit
) {
    val c = LocalPulsePalette.current
    val haptics = LocalHapticFeedback.current
    Row(
        modifier
            .clip(RoundedCornerShape(12.dp))
            .background(c.surface)
            .iosPressScale(0.96f) {
                haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                onClick()
            }
            .padding(horizontal = 11.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Box(
            Modifier.size(34.dp).clip(RoundedCornerShape(10.dp)).background(tint.copy(alpha = 0.14f)),
            contentAlignment = Alignment.Center
        ) {
            PulseIcon(icon, tint, Modifier.size(18.dp))
        }
        Column(Modifier.padding(start = 10.dp).weight(1f)) {
            Text(title, color = c.ink, fontSize = 13.5.sp, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(subtitle, color = c.muted, fontSize = 11.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
    }
}

/**
 * True Telegram iOS Contact Row with Physical Finger-Tracking Swipe Reveal:
 * - Swipe Right (`offsetX > 0`): Smoothly tracks finger, reveals Telegram Blue `📌 置顶 / 取消置顶` action on the left,
 *   scales icon with spring pop + haptic tick at threshold (`72.dp`), and springs back on release!
 * - Swipe Left (`offsetX < 0`): Smoothly slides row left to reveal full-height Telegram colored action tiles:
 *   `👤 档案` (Blue), `🧹 压缩` (Orange), `⏹ 停止` (Red), snapping open to `-204.dp` or springing shut!
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
internal fun NativeBotRow(model: NativePulseModel, bot: PulseBot) {
    val s = model.ui
    val c = LocalPulsePalette.current
    val haptics = LocalHapticFeedback.current
    val density = LocalDensity.current
    val rowScope = rememberCoroutineScope()

    val latest = s.messages.lastOrNull { it.botId == bot.id }
    val unread = s.messages.count { it.botId == bot.id && it.id in s.unread }
    val isStreamingHere = s.streamBotId == bot.id
    val isWorkingHere = isStreamingHere || s.workingBot == bot.id || s.busyBot == bot.id
    val unsentDraft = model.draftOf(bot.id).trim()

    val drawerWidthPx = with(density) { 204.dp.toPx() }
    val pinThresholdPx = with(density) { 74.dp.toPx() }
    val maxRightPullPx = with(density) { 128.dp.toPx() }
    val offsetX = remember(bot.id) { Animatable(0f) }
    var pinThresholdCrossed by remember(bot.id) { mutableStateOf(false) }

    val (curTok, _, _) = model.contextStatsFor(bot.id)
    val (cachedTok, cachePct) = model.contextCacheFor(bot.id)

    val rowBgColor = if (bot.pinned) c.pinnedRowBg else c.surface

    Box(
        Modifier.fillMaxWidth()
            .height(IntrinsicSize.Min)
            .clipToBounds()
            .background(rowBgColor)
    ) {
        // 1. Background Left Reveal (Swipe Right -> Telegram Blue Pin / Unpin)
        if (offsetX.value > 0.5f) {
            val progress = (offsetX.value / pinThresholdPx).coerceIn(0f, 1.25f)
            val iconScale by animateFloatAsState(
                targetValue = if (offsetX.value >= pinThresholdPx) 1.16f else (0.65f + 0.35f * progress.coerceAtMost(1f)),
                animationSpec = spring(dampingRatio = 0.62f, stiffness = 520f),
                label = "pin_scale"
            )
            Box(
                Modifier.fillMaxSize()
                    .background(if (bot.pinned) Color(0xFF636366) else c.accent)
                    .padding(start = 20.dp),
                contentAlignment = Alignment.CenterStart
            ) {
                Row(
                    Modifier.graphicsLayer {
                        scaleX = iconScale
                        scaleY = iconScale
                    },
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(6.dp)
                ) {
                    PulseIcon("pin", Color.White, Modifier.size(20.dp))
                    Text(
                        if (bot.pinned) "取消置顶" else "置顶对话",
                        color = Color.White,
                        fontSize = 13.5.sp,
                        fontWeight = FontWeight.SemiBold
                    )
                }
            }
        }

        // 2. Background Right Reveal (Swipe Left -> Full-Height Telegram Colored Action Tiles)
        if (offsetX.value < -0.5f) {
            Row(
                Modifier.fillMaxHeight()
                    .width(204.dp)
                    .align(Alignment.CenterEnd),
                verticalAlignment = Alignment.CenterVertically
            ) {
                // Tile 1: Profile (Telegram Blue)
                Column(
                    Modifier.weight(1f).fillMaxHeight()
                        .background(c.accent)
                        .clickable {
                            haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                            rowScope.launch { offsetX.animateTo(0f, spring(dampingRatio = 0.82f, stiffness = 460f)) }
                            model.openBotEditor(bot.id)
                        },
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.Center
                ) {
                    PulseIcon("user", Color.White, Modifier.size(19.dp))
                    Text("档案", color = Color.White, fontSize = 11.5.sp, fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(top = 3.dp))
                }
                // Tile 2: Compact Context (Telegram Orange)
                Column(
                    Modifier.weight(1f).fillMaxHeight()
                        .background(c.amber)
                        .clickable {
                            haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                            rowScope.launch { offsetX.animateTo(0f, spring(dampingRatio = 0.82f, stiffness = 460f)) }
                            model.compactBotContext(bot.id)
                        },
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.Center
                ) {
                    PulseIcon("spark", Color.White, Modifier.size(19.dp))
                    Text("压缩", color = Color.White, fontSize = 11.5.sp, fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(top = 3.dp))
                }
                // Tile 3: Stop (Red) or More (Slate)
                Column(
                    Modifier.weight(1f).fillMaxHeight()
                        .background(if (isWorkingHere) c.warn else Color(0xFF636366))
                        .clickable {
                            haptics.performHapticFeedback(HapticFeedbackType.LongPress)
                            rowScope.launch { offsetX.animateTo(0f, spring(dampingRatio = 0.82f, stiffness = 460f)) }
                            if (isWorkingHere) model.stopBotById(bot.id) else model.botMenu(bot.id)
                        },
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.Center
                ) {
                    PulseIcon(if (isWorkingHere) "stop" else "more", Color.White, Modifier.size(19.dp))
                    Text(if (isWorkingHere) "停止" else "更多", color = Color.White, fontSize = 11.5.sp, fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(top = 3.dp))
                }
            }
        }

        // 3. Foreground Contact Row (Tracks Finger with Rubber-Band Physics)
        Row(
            Modifier.fillMaxWidth()
                .graphicsLayer { translationX = offsetX.value }
                .background(rowBgColor)
                .pointerInput(bot.id, bot.pinned) {
                    detectHorizontalDragGestures(
                        onDragStart = { pinThresholdCrossed = false },
                        onHorizontalDrag = { change, dragAmount ->
                            change.consume()
                            val cur = offsetX.value
                            val next = if (cur + dragAmount > 0f) {
                                // Rubber-band resistance when swiping right to Pin/Unpin
                                val raw = cur + dragAmount * 0.72f
                                raw.coerceAtMost(maxRightPullPx)
                            } else {
                                // Smooth slide left up to drawer width with light overscroll
                                val factor = if (cur < -drawerWidthPx) 0.25f else 0.92f
                                (cur + dragAmount * factor).coerceAtLeast(-drawerWidthPx * 1.18f)
                            }
                            if (!pinThresholdCrossed && next >= pinThresholdPx) {
                                pinThresholdCrossed = true
                                haptics.performHapticFeedback(HapticFeedbackType.LongPress)
                            } else if (pinThresholdCrossed && next < pinThresholdPx * 0.85f) {
                                pinThresholdCrossed = false
                            }
                            rowScope.launch { offsetX.snapTo(next) }
                        },
                        onDragEnd = {
                            val finalX = offsetX.value
                            rowScope.launch {
                                when {
                                    finalX >= pinThresholdPx -> {
                                        offsetX.animateTo(0f, spring(dampingRatio = 0.76f, stiffness = 420f))
                                        model.togglePinBot(bot.id)
                                    }
                                    finalX < -drawerWidthPx * 0.34f -> {
                                        haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                                        offsetX.animateTo(-drawerWidthPx, spring(dampingRatio = 0.80f, stiffness = 430f))
                                    }
                                    else -> {
                                        offsetX.animateTo(0f, spring(dampingRatio = 0.80f, stiffness = 460f))
                                    }
                                }
                            }
                        },
                        onDragCancel = {
                            rowScope.launch { offsetX.animateTo(0f, spring(dampingRatio = 0.82f, stiffness = 450f)) }
                        }
                    )
                }
                .combinedClickable(
                    onClick = {
                        if (abs(offsetX.value) > 8f) {
                            rowScope.launch { offsetX.animateTo(0f, spring(dampingRatio = 0.82f, stiffness = 460f)) }
                        } else {
                            model.open(bot.id)
                        }
                    },
                    onLongClick = {
                        haptics.performHapticFeedback(HapticFeedbackType.LongPress)
                        model.botMenu(bot.id)
                    },
                    onLongClickLabel = "对话选项"
                )
                .semantics { contentDescription = "打开${bot.name}的对话" }
                .padding(horizontal = 14.dp, vertical = 11.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            PulseAvatar(bot, s, diameter = 54.dp, showStatusDot = true, ringColor = rowBgColor)

            Column(Modifier.weight(1f).padding(start = 14.dp)) {
                // Top Line: Bot Name + Badges + Outgoing Checkmarks + Timestamp
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        bot.name,
                        color = c.ink,
                        fontWeight = FontWeight.SemiBold,
                        fontSize = 16.5.sp,
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
                    if (bot.curiosityEnabled) {
                        Text("✦", Modifier.padding(start = 4.dp), color = c.accent, fontSize = 11.sp)
                    }
                    if (cachedTok > 0) {
                        Text(
                            "缓存 $cachePct%",
                            Modifier.padding(start = 5.dp)
                                .clip(RoundedCornerShape(4.dp))
                                .background(c.green.copy(alpha = 0.12f))
                                .padding(horizontal = 4.dp, vertical = 1.dp),
                            color = c.green,
                            fontSize = 10.sp,
                            fontWeight = FontWeight.Medium,
                            style = TextStyle(fontFeatureSettings = "tnum")
                        )
                    }
                    Spacer(Modifier.weight(1f))
                    if (latest != null) {
                        if (latest.role == "user" && latest.kind != "bot_message") {
                            PulseIcon(
                                if (latest.optimistic) "check" else "check_double",
                                c.accent,
                                Modifier.padding(end = 3.dp).size(14.dp)
                            )
                        }
                        Text(
                            model.timeSeparator(latest.created),
                            fontSize = 12.5.sp,
                            color = if (unread > 0) c.accent else c.faint,
                            style = TextStyle(fontFeatureSettings = "tnum")
                        )
                    }
                }

                // Bottom Line: 2-Line Telegram Preview (Draft in Red / Sender Prefix in Ink / Sticker Tag in Blue) + Unread / Pin Badge
                Row(
                    Modifier.padding(top = 3.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    val annotatedPreview = buildAnnotatedString {
                        when {
                            isStreamingHere && s.streamTool.isNotBlank() -> {
                                withStyle(SpanStyle(color = c.accent, fontWeight = FontWeight.Medium)) {
                                    append("正在${humanizeToolName(s.streamTool, null)}…")
                                }
                            }
                            isStreamingHere && s.streamText.isNotBlank() -> {
                                withStyle(SpanStyle(color = c.accent, fontWeight = FontWeight.Medium)) {
                                    append("${s.streamSpeakerName.ifBlank { bot.name }}: ")
                                }
                                withStyle(SpanStyle(color = c.ink)) {
                                    append(s.streamText.replace(Regex("\\s+"), " "))
                                }
                            }
                            s.workingBot == bot.id -> {
                                withStyle(SpanStyle(color = c.accent, fontWeight = FontWeight.Medium)) {
                                    append("● ${s.workingText.ifBlank { "正在思考与执行…" }}")
                                }
                            }
                            unsentDraft.isNotEmpty() -> {
                                withStyle(SpanStyle(color = c.warn, fontWeight = FontWeight.SemiBold)) {
                                    append("草稿: ")
                                }
                                withStyle(SpanStyle(color = c.muted)) {
                                    append(unsentDraft.replace(Regex("\\s+"), " "))
                                }
                            }
                            latest != null -> {
                                when {
                                    latest.fromBotName.isNotBlank() && latest.fromBotId != bot.id -> {
                                        withStyle(SpanStyle(color = c.ink, fontWeight = FontWeight.Medium)) {
                                            append("${latest.fromBotName}: ")
                                        }
                                    }
                                    latest.role == "user" && latest.kind != "bot_message" -> {
                                        withStyle(SpanStyle(color = c.ink, fontWeight = FontWeight.Medium)) {
                                            append("我: ")
                                        }
                                    }
                                }
                                if (latest.sticker.isNotBlank() && latest.text.isBlank()) {
                                    withStyle(SpanStyle(color = c.accent)) {
                                        append(if (latest.sticker.contains("/stickers/")) "[表情贴纸]" else "[图片]")
                                    }
                                } else {
                                    val cleanPreview = latest.text
                                        .replace(Regex("^> [^\\n]+\\n\\n"), "↩ ")
                                        .replace(Regex("\n\n附件（工作区相对路径[\\s\\S]*"), " [图片/附件]")
                                        .replace(Regex("\\s+"), " ")
                                    withStyle(
                                        SpanStyle(
                                            color = when {
                                                latest.kind == "error" -> c.warn
                                                unread > 0 -> c.ink
                                                else -> c.muted
                                            }
                                        )
                                    ) {
                                        append(cleanPreview)
                                    }
                                }
                            }
                            else -> {
                                withStyle(SpanStyle(color = c.muted)) {
                                    append(bot.description)
                                }
                            }
                        }
                    }

                    Text(
                        annotatedPreview,
                        Modifier.weight(1f),
                        fontSize = 14.5.sp,
                        lineHeight = 19.sp,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis
                    )

                    if (unread > 0) {
                        Box(
                            Modifier.padding(start = 8.dp)
                                .defaultMinSize(minWidth = 21.dp, minHeight = 21.dp)
                                .clip(CircleShape)
                                .background(c.accent)
                                .padding(horizontal = 6.dp, vertical = 2.dp),
                            contentAlignment = Alignment.Center
                        ) {
                            Text(
                                unread.toString(),
                                color = Color.White,
                                fontSize = 11.5.sp,
                                fontWeight = FontWeight.Bold,
                                style = TextStyle(fontFeatureSettings = "tnum")
                            )
                        }
                    } else if (bot.pinned) {
                        PulseIcon(
                            "pin",
                            c.faint,
                            Modifier.padding(start = 8.dp).size(15.dp)
                        )
                    }
                }
            }
        }
    }
}

internal fun humanizeToolName(tool: String, args: org.json.JSONObject?): String {
    val op = args?.optString("op").orEmpty()
    return when (tool) {
        "phone_workspace" -> when (op) {
            "ui_tree" -> "读取虚拟屏界面节点"
            "shell", "bash", "cmd" -> "手机本地命令: ${args?.optString("command").orEmpty().take(28)}"
            "device_info", "phone_info" -> "查询手机当前状态"
            "pull_file" -> "从手机拉取文件: ${args?.optString("path", "latest").orEmpty().substringAfterLast('/')}"
            "push_file" -> "推送文件到手机: ${args?.optString("path").orEmpty().substringAfterLast('/')}"
            "list_files", "files" -> "查看手机目录: ${args?.optString("path", "/sdcard/Download").orEmpty()}"
            "send_screenshot" -> "截取画面发到聊天框"
            "notifications" -> "查看/过滤收敛通知栏"
            "tap_node" -> {
                val target = args?.optString("ref")?.takeIf { it.isNotBlank() }
                    ?: args?.optString("text")?.takeIf { it.isNotBlank() }
                    ?: args?.optString("view_id").orEmpty()
                "点击界面节点 ${target.ifBlank { "" }}".trim()
            }
            "type_node" -> "输入文字「${args?.optString("text").orEmpty().take(18)}」"
            "launch" -> "启动应用「${args?.optString("app")?.ifBlank { args.optString("package") }.orEmpty()}」"
            "shell" -> "执行系统指令"
            "swipe" -> "滑动浏览虚拟屏"
            "tap" -> "点击屏幕坐标 (${args?.optInt("x", 0)}, ${args?.optInt("y", 0)})"
            "key" -> "触发按键 ${args?.optString("key").orEmpty()}"
            "capture" -> "截取虚拟屏画面"
            "notifications", "get_notifications", "recent_notifications", "notifs", "notification" -> {
                val filterApp = args?.optString("app").orEmpty()
                if (filterApp.isNotBlank()) "读取「$filterApp」最近通知" else "读取手机最近通知"
            }
            "release", "auto_release" -> "释放虚拟屏工作区"
            else -> "操作虚拟屏 ${op.ifBlank { "" }}".trim()
        }
        "phone_ui_tree" -> "读取虚拟屏界面节点"
        "phone_tap_node" -> "点击节点 ${args?.optString("ref")?.ifBlank { args.optString("text") }.orEmpty()}"
        "phone_type_node" -> "输入文字「${args?.optString("text").orEmpty().take(18)}」"
        "phone_launch_app" -> "启动应用「${args?.optString("app").orEmpty()}」"
        "web_search" -> "搜索网页「${args?.optString("query").orEmpty().take(24)}」"
        "web_fetch" -> "读取网页 ${args?.optString("url").orEmpty().take(32)}"
        "read_file" -> "读取文件 ${args?.optString("path").orEmpty().substringAfterLast('/')}"
        "write_file" -> "写入文件 ${args?.optString("path").orEmpty().substringAfterLast('/')}"
        "edit_file" -> "修改文件 ${args?.optString("path").orEmpty().substringAfterLast('/')}"
        "memory_search" -> "检索记忆「${args?.optString("query").orEmpty().take(20)}」"
        "run_bash" -> "执行命令 ${args?.optString("command").orEmpty().take(28)}"
        "schedule_task" -> {
            val act = args?.optString("action").orEmpty()
            val atTime = args?.optString("at_time").orEmpty()
            when (act) {
                "list" -> "查阅日程与闹钟列表"
                "add_timer" -> "设定提醒 $atTime".trim()
                "update" -> "更新日程提醒"
                "remove" -> "移除旧提醒"
                "snooze" -> "开启免打扰模式"
                "wake" -> "恢复主动联系"
                else -> "管理日程与提醒"
            }
        }
        "send_message" -> "派发协作给 ${args?.optString("to_bot_id").orEmpty()}"
        "manage_bot" -> "配置团队助手"
        "list_models" -> "查询可用模型列表"
        "cloud_desktop" -> when (op) {
            "bash", "exec", "shell", "terminal", "run" -> {
                val cmd = args?.optString("command")?.takeIf { it.isNotBlank() } ?: args?.optString("goal").orEmpty()
                "在虚拟电脑执行「${cmd.take(22)}」"
            }
            "navigate" -> "在虚拟电脑打开网页 ${args?.optString("url").orEmpty().take(26)}"
            "launch_app", "app", "open_app" -> "在虚拟电脑打开应用 ${args?.optString("app").orEmpty()}"
            "batch" -> "在虚拟电脑连招执行 ${args?.optJSONArray("steps")?.length() ?: 0} 步"
            "click", "double_click", "right_click" -> "在虚拟电脑点击 (${args?.optInt("x", 0)}, ${args?.optInt("y", 0)})"
            "type" -> "在虚拟电脑输入「${args?.optString("text").orEmpty().take(18)}」"
            "key" -> "在虚拟电脑按键 ${args?.optString("key").orEmpty()}"
            "scroll" -> "在虚拟电脑滚动屏幕"
            "screenshot", "capture", "frame", "status" -> "读取云电脑屏幕画面"
            else -> "操作云电脑"
        }
        "notify_user" -> "发送通知「${args?.optString("title").orEmpty().take(20)}」"
        "emergency_alarm" -> "拉响紧急闹钟提醒「${args?.optString("title").orEmpty().take(18)}」"
        "conversation_search" -> "检索归档对话「${args?.optString("query").orEmpty().take(20)}」"
        "ctx_search" -> "按需检索长输出「${args?.optString("query").orEmpty().take(20)}」"
        "present_choices" -> "生成快捷选项胶囊"
        "memory_pin" -> "管理核心钉住记忆"
        "blackboard" -> "同步团队黑板与步骤账本"
        "watchdog_task" -> "管理零 Token 哨兵监控脚本"
        "custom_tool" -> "热插拔自造工具 ${args?.optString("name").orEmpty()}"
        "phone_macro" -> "执行手机动作宏 ${args?.optString("name").orEmpty()}"
        "gmail_connector" -> "操作 Gmail 邮件 (${args?.optString("action").orEmpty()})"
        "integration_hub" -> "调用第三方应用接口 (${args?.optString("service").orEmpty()}:${args?.optString("action").orEmpty()})"
        else -> "执行 $tool"
    }
}

@Composable
internal fun SettingsCategoryRow(
    icon: String,
    title: String,
    subtitle: String,
    value: String,
    onClick: () -> Unit
) {
    val c = LocalPulsePalette.current
    val haptics = LocalHapticFeedback.current
    Row(
        Modifier.fillMaxWidth()
            .iosPressScale(0.98f) {
                haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                onClick()
            }
            .padding(horizontal = 14.dp, vertical = 13.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Box(
            Modifier.size(32.dp).clip(RoundedCornerShape(8.dp)).background(c.accent.copy(alpha = 0.14f)),
            contentAlignment = Alignment.Center
        ) {
            PulseIcon(icon, c.accent, Modifier.size(17.dp))
        }
        Column(Modifier.padding(start = 12.dp).weight(1f)) {
            Text(title, color = c.ink, fontSize = 15.sp, fontWeight = FontWeight.Medium)
            if (subtitle.isNotBlank()) {
                Text(subtitle, color = c.muted, fontSize = 11.5.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
        if (value.isNotBlank()) {
            Text(
                value,
                color = c.muted,
                fontSize = 12.5.sp,
                modifier = Modifier.padding(horizontal = 6.dp),
                maxLines = 1,
                overflow = TextOverflow.Ellipsis
            )
        }
        PulseIcon("next", c.faint, Modifier.size(15.dp))
    }
}

@Composable
internal fun TelegramProfileActionTile(icon: String, label: String, modifier: Modifier = Modifier, onClick: () -> Unit) {
    val c = LocalPulsePalette.current
    val haptics = LocalHapticFeedback.current
    Column(
        modifier
            .clip(RoundedCornerShape(12.dp))
            .background(c.fill.copy(alpha = 0.75f))
            .iosPressScale(0.94f) {
                haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                onClick()
            }
            .padding(vertical = 10.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(4.dp)
    ) {
        PulseIcon(icon, c.accent, Modifier.size(19.dp))
        Text(label, color = c.accent, fontSize = 12.sp, fontWeight = FontWeight.Medium)
    }
}

@Composable
internal fun PulseInputField(
    label: String,
    value: String,
    placeholder: String,
    singleLine: Boolean = true,
    onChange: (String) -> Unit
) {
    val c = LocalPulsePalette.current
    Column(Modifier.fillMaxWidth()) {
        Text(
            label,
            color = c.muted,
            fontSize = 12.5.sp,
            fontWeight = FontWeight.Medium,
            modifier = Modifier.padding(bottom = 5.dp)
        )
        BasicTextField(
            value = value,
            onValueChange = onChange,
            singleLine = singleLine,
            textStyle = TextStyle(color = c.ink, fontSize = 15.5.sp, lineHeight = 21.sp),
            cursorBrush = SolidColor(c.accent),
            modifier = Modifier.fillMaxWidth()
                .clip(RoundedCornerShape(11.dp))
                .background(c.fill.copy(alpha = 0.78f))
                .padding(horizontal = 13.dp, vertical = 11.dp),
            decorationBox = { inner ->
                Box(Modifier.heightIn(min = if (singleLine) 21.dp else 88.dp)) {
                    if (value.isEmpty()) Text(placeholder, color = c.faint, fontSize = 15.5.sp)
                    inner()
                }
            }
        )
    }
}

@Composable
internal fun PulseRoundButton(icon: String, label: String, click: () -> Unit, background: Boolean = true) {
    val c = LocalPulsePalette.current
    val haptics = LocalHapticFeedback.current
    Box(
        Modifier.size(36.dp)
            .clip(CircleShape)
            .then(if (background) Modifier.background(c.fill.copy(alpha = 0.75f)) else Modifier)
            .iosPressScale(0.90f) {
                haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                click()
            }
            .semantics { contentDescription = label },
        contentAlignment = Alignment.Center
    ) {
        PulseIcon(icon, if (background) c.ink else c.accent, Modifier.size(18.5.dp))
    }
}

internal val wikiTokenRegex = Regex("""(?s)(```.*?```|`[^`\n]+`|\[\[([^\]\n]+)\]\])""")

internal fun nativeWikiMarkdown(text: String): String {
    if (!text.contains("[[")) return text
    return wikiTokenRegex.replace(text) { match ->
        val full = match.value
        if (full.startsWith("`")) {
            full
        } else {
            val inner = match.groupValues[2]
            val parts = inner.split('|', limit = 2)
            val path = parts[0]
            val label = parts.getOrElse(1) { path }.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
            "[$label](#vault:${android.net.Uri.encode(path)})"
        }
    }
}

@Composable
internal fun PulseAction(text: String, filled: Boolean = true, click: () -> Unit) {
    val c = LocalPulsePalette.current
    val haptics = LocalHapticFeedback.current
    Text(
        text,
        Modifier.fillMaxWidth()
            .clip(RoundedCornerShape(14.dp))
            .background(if (filled) c.accent else c.surface)
            .iosPressScale(0.97f) {
                haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                click()
            }
            .padding(horizontal = 16.dp, vertical = 14.dp),
        color = if (filled) Color.White else c.ink,
        fontSize = 15.sp,
        fontWeight = FontWeight.SemiBold,
        textAlign = TextAlign.Center
    )
}
