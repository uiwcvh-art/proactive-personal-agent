package dev.bubuki.proactive

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.animateContentSize
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.CubicBezierEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.*
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.ui.layout.ContentScale
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.window.Popup
import androidx.compose.ui.window.PopupProperties
import androidx.compose.ui.semantics.*
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import dev.bubuki.proactive.markdown.MarkdownText
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.layout.onSizeChanged
import kotlinx.coroutines.launch
import org.json.JSONArray
import kotlin.math.abs

private data class PulsePalette(
    val bg: Color, val chatBg: Color, val surface: Color, val aiBubble: Color, val fill: Color, val line: Color,
    val ink: Color, val muted: Color, val faint: Color, val accent: Color,
    val green: Color, val warn: Color, val userBubble: Color, val userInk: Color
)
private val LocalPulsePalette = staticCompositionLocalOf { pulsePalette(false) }
private fun pulsePalette(dark: Boolean) = if (dark) PulsePalette(
    bg = Color(0xFF000000), chatBg = Color(0xFF000000), surface = Color(0xFF1C1C1E), aiBubble = Color(0xFF1C1C1E),
    fill = Color(0xFF2C2C2E), line = Color(0xFF2C2C2E),
    ink = Color(0xFFFFFFFF), muted = Color(0xFF98989F), faint = Color(0xFF636366), accent = Color(0xFF0A84FF),
    green = Color(0xFF30D158), warn = Color(0xFFFF453A), userBubble = Color(0xFF0A84FF), userInk = Color(0xFFFFFFFF)
) else PulsePalette(
    bg = Color(0xFFF2F2F7), chatBg = Color(0xFFFFFFFF), surface = Color(0xFFFFFFFF), aiBubble = Color(0xFFF2F2F7),
    fill = Color(0xFFEFEFF4), line = Color(0xFFE5E5EA),
    ink = Color(0xFF000000), muted = Color(0xFF6C6C70), faint = Color(0xFFAEAEB2), accent = Color(0xFF007AFF),
    green = Color(0xFF34C759), warn = Color(0xFFFF3B30), userBubble = Color(0xFF007AFF), userInk = Color(0xFFFFFFFF)
)

@OptIn(ExperimentalMaterial3Api::class, ExperimentalFoundationApi::class)
@Composable
internal fun NativePulseScreen(model: NativePulseModel) {
    val state = model.ui; val colors = remember(state.dark) { pulsePalette(state.dark) }
    val progress = remember { Animatable(if (state.chatOpen) 1f else 0f) }
    val focus = LocalFocusManager.current
    val modalScope = rememberCoroutineScope()
    var gestureEnd by remember { mutableStateOf<Float?>(null) }
    var composerHeight by remember { mutableIntStateOf(0) }
    val snackbar = remember { SnackbarHostState() }
    LaunchedEffect(state.toastId) { if (state.toast.isNotBlank()) snackbar.showSnackbar(state.toast) }
    LaunchedEffect(state.chatOpen, state.motion, model.backSettlement) {
        model.navigationRunning = true
        try {
            (model.backFrom ?: gestureEnd)?.let { progress.snapTo(it); gestureEnd = null; model.backFrom = null }
            val target = if (state.chatOpen) 1f else 0f
            if (!state.motion) progress.snapTo(target)
            else progress.animateTo(target, tween(if (state.chatOpen) 380 else 330, easing = CubicBezierEasing(.4f, 0f, .2f, 1f)))
        } finally { model.navigationRunning = false }
    }
    PulseTheme(dark = state.dark, reduceMotion = !state.motion) {
        CompositionLocalProvider(LocalPulsePalette provides colors) {
            Box(Modifier.fillMaxSize().background(colors.bg).windowInsetsPadding(WindowInsets.systemBars.union(WindowInsets.ime)), contentAlignment = Alignment.TopCenter) {
                BoxWithConstraints(Modifier.fillMaxHeight().widthIn(max = 700.dp).fillMaxWidth().clipToBounds().pointerInput(state.chatOpen, state.menu != null, state.activePanel != null) {
                    if (!state.chatOpen || state.menu != null || state.activePanel != null) return@pointerInput
                    awaitEachGesture {
                        val down = awaitFirstDown(requireUnconsumed = false)
                        if (down.position.x > 24.dp.toPx()) return@awaitEachGesture
                        var started = false; var dx = 0f; var rejected = false
                        do {
                            val event = awaitPointerEvent(); val change = event.changes.find { it.id == down.id } ?: break
                            dx = (change.position.x - down.position.x).coerceAtLeast(0f)
                            val dy = abs(change.position.y - down.position.y)
                            if (!started && (dx > viewConfiguration.touchSlop || dy > viewConfiguration.touchSlop)) {
                                if (dy >= dx) { rejected = true; break }; started = true
                            }
                            if (started) { change.consume(); model.predictiveBack = (dx / size.width).coerceIn(0f, 1f) }
                            if (!change.pressed) break
                        } while (true)
                        if (started && !rejected) {
                            gestureEnd = 1f - (model.predictiveBack ?: 0f); model.predictiveBack = null
                            if (dx > size.width * .28f) { focus.clearFocus(); model.back() } else model.backSettlement++
                        }
                    }
                }) {
                    val width = constraints.maxWidth.toFloat()
                    NativeHome(model, Modifier.fillMaxSize().graphicsLayer {
                        val fraction = model.predictiveBack?.let { 1f - it } ?: progress.value
                        translationX = -width * .24f * fraction
                    }.then(if (state.chatOpen || state.activePanel != null) Modifier.clearAndSetSemantics { } else Modifier))
                    val bot = model.bot()
                    if (bot != null) {
                        NativeChat(model, bot, Modifier.fillMaxSize().graphicsLayer {
                            val fraction = model.predictiveBack?.let { 1f - it } ?: progress.value
                            translationX = width * (1f - fraction); alpha = if (fraction == 0f) 0f else 1f
                        }.then(if (!state.chatOpen || state.activePanel != null) Modifier.clearAndSetSemantics { } else Modifier)) { composerHeight = it }
                    }
                    when (state.activePanel) {
                        "settings" -> NativeSettingsPanel(model, Modifier.fillMaxSize())
                        "bot_edit" -> NativeBotEditorPanel(model, Modifier.fillMaxSize())
                        "vault" -> NativeVaultPanel(model, Modifier.fillMaxSize())
                        "cloud_desktop" -> NativeCloudDesktopPanel(model, Modifier.fillMaxSize())
                        "schedules" -> NativeSchedulesPanel(model, Modifier.fillMaxSize())
                    }
                    InAppFloatingVirtualDisplay()
                }
                val toastBottom = if (state.chatOpen && state.activePanel == null) with(LocalDensity.current) { composerHeight.toDp() } + 12.dp else 20.dp
                SnackbarHost(snackbar, Modifier.align(Alignment.BottomCenter).padding(start = 20.dp, end = 20.dp, bottom = toastBottom))
            }
            state.menu?.let { menu ->
                if (!state.motion) {
                    Popup(alignment = Alignment.BottomCenter, onDismissRequest = model::dismissMenu, properties = PopupProperties(focusable = true, clippingEnabled = false)) {
                        Box(Modifier.fillMaxSize().background(Color.Black.copy(alpha = .32f)).clickable { model.dismissMenu() }, contentAlignment = Alignment.BottomCenter) {
                            Surface(Modifier.fillMaxWidth().heightIn(max = (LocalConfiguration.current.screenHeightDp * .85f).dp).windowInsetsPadding(WindowInsets.navigationBars),
                                shape = RoundedCornerShape(topStart = 28.dp, topEnd = 28.dp), color = colors.surface) {
                                NativeMenuContent(menu, false, model::dismissMenu) { item -> model.dismissMenu(); item.run() }
                            }
                        }
                    }
                } else {
                    val modalState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
                    var closing by remember(menu) { mutableStateOf(false) }
                    val closeThen: (() -> Unit) -> Unit = { next ->
                        if (!closing) {
                            closing = true
                            modalScope.launch {
                                if (state.motion) modalState.hide()
                                model.dismissMenu(); next()
                            }
                        }
                    }
                    ModalBottomSheet(onDismissRequest = model::dismissMenu, sheetState = modalState,
                        containerColor = colors.surface, contentColor = colors.ink, shape = RoundedCornerShape(topStart = 28.dp, topEnd = 28.dp),
                        tonalElevation = 0.dp) {
                        NativeMenuContent(menu, closing, { closeThen {} }) { item -> closeThen(item.run) }
                    }
                }
            }
        }
    }
}

@Composable
private fun NativeMenuContent(menu: PulseMenu, closing: Boolean, close: () -> Unit, select: (PulseMenuItem) -> Unit) {
    val c = LocalPulsePalette.current; val haptics = LocalHapticFeedback.current
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
                        .clickable(enabled = !closing) { haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove); select(item) }
                        .padding(vertical = 14.dp, horizontal = 14.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Box(
                        Modifier.size(32.dp).clip(RoundedCornerShape(8.dp)).background(c.accent.copy(alpha = 0.12f)),
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
private fun NativeHome(model: NativePulseModel, modifier: Modifier) {
    val s = model.ui; val c = LocalPulsePalette.current; val rows = model.visibleBots()
    val apiReady = s.hasApiKey && s.defaultModel.isNotBlank()
    val phoneState by RootPhoneHarness.changes.collectAsState()
    val focus = LocalFocusManager.current

    Column(modifier.background(c.bg)) {
        // 1. iOS Minimal Top Navigation Bar (Status Pill Left, 2 Action Circles Right)
        Row(
            Modifier.fillMaxWidth().padding(start = 20.dp, end = 16.dp, top = 12.dp, bottom = 2.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Row(
                Modifier.clip(CircleShape).background(c.surface).padding(horizontal = 10.dp, vertical = 5.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Box(Modifier.size(7.dp).clip(CircleShape).background(if (s.online) c.green else c.faint))
                Text(
                    if (s.online) "已连接 · ${s.latency}ms" else "离线",
                    Modifier.padding(start = 6.dp),
                    color = if (s.online) c.muted else c.faint,
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Medium
                )
            }
            Spacer(Modifier.weight(1f))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                PulseRoundButton("plus", "工作台", model::newMenu, background = true)
                PulseRoundButton("settings", "设置", { model.openSettings() }, background = true)
            }
        }

        // 2. Scrollable iOS Grouped Canvas (Large Title + Search Bar + Bento Strip + Inset Grouped List)
        LazyColumn(
            Modifier.fillMaxSize(),
            contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 4.dp, bottom = 28.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp)
        ) {
            item("large_title_and_search") {
                Column(Modifier.fillMaxWidth()) {
                    Text(
                        "对话",
                        Modifier.padding(start = 4.dp, bottom = 10.dp),
                        color = c.ink,
                        fontSize = 33.sp,
                        fontWeight = FontWeight.Bold,
                        letterSpacing = (-0.7).sp
                    )
                    // Always-present iOS Search Bar
                    Row(
                        Modifier.fillMaxWidth()
                            .clip(RoundedCornerShape(12.dp))
                            .background(c.surface)
                            .padding(horizontal = 12.dp, vertical = 10.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        PulseIcon("search", c.faint, Modifier.size(17.dp))
                        BasicTextField(
                            value = s.query,
                            onValueChange = { model.search(it.ifEmpty { null }) },
                            modifier = Modifier.weight(1f).padding(horizontal = 8.dp).semantics { contentDescription = "搜索助手或对话" },
                            textStyle = TextStyle(color = c.ink, fontSize = 16.sp),
                            singleLine = true,
                            cursorBrush = SolidColor(c.accent),
                            decorationBox = { inner ->
                                Box {
                                    if (s.query.isBlank()) Text("搜索助手或聊天记录…", color = c.faint, fontSize = 16.sp)
                                    inner()
                                }
                            }
                        )
                        if (s.query.isNotEmpty()) {
                            Text(
                                "清除",
                                Modifier.clickable { focus.clearFocus(); model.search(null) }.padding(horizontal = 4.dp),
                                color = c.accent,
                                fontSize = 14.sp
                            )
                        }
                    }
                }
            }

            // 3. Clean 3-Tile iOS Quick Bento Row (Zero borders, tactile surface cards)
            item("quick_bento") {
                val vdCreated = phoneState.displayCreated
                Row(
                    Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(10.dp)
                ) {
                    Row(
                        Modifier.weight(1f)
                            .clip(RoundedCornerShape(14.dp))
                            .background(if (vdCreated) c.green.copy(alpha = 0.14f) else c.surface)
                            .clickable { model.launchFloatingVirtualDisplay() }
                            .padding(horizontal = 12.dp, vertical = 11.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.Center
                    ) {
                        PulseIcon("screen", if (vdCreated) c.green else c.accent, Modifier.size(16.dp))
                        Text(
                            if (vdCreated) "虚拟屏 #${phoneState.displayId}" else "虚拟屏",
                            Modifier.padding(start = 6.dp),
                            color = if (vdCreated) c.green else c.ink,
                            fontSize = 13.5.sp,
                            fontWeight = FontWeight.Medium,
                            maxLines = 1
                        )
                    }
                    Row(
                        Modifier.weight(1f)
                            .clip(RoundedCornerShape(14.dp))
                            .background(c.surface)
                            .clickable { model.openSchedules() }
                            .padding(horizontal = 12.dp, vertical = 11.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.Center
                    ) {
                        PulseIcon("clock", c.accent, Modifier.size(16.dp))
                        Text("定时提醒", Modifier.padding(start = 6.dp), color = c.ink, fontSize = 13.5.sp, fontWeight = FontWeight.Medium, maxLines = 1)
                    }
                    Row(
                        Modifier.weight(1f)
                            .clip(RoundedCornerShape(14.dp))
                            .background(c.surface)
                            .clickable { model.openVault("INDEX.md") }
                            .padding(horizontal = 12.dp, vertical = 11.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.Center
                    ) {
                        PulseIcon("vault", c.accent, Modifier.size(16.dp))
                        Text("记忆库", Modifier.padding(start = 6.dp), color = c.ink, fontSize = 13.5.sp, fontWeight = FontWeight.Medium, maxLines = 1)
                    }
                }
            }

            if (!apiReady) {
                item("api_banner") {
                    Row(
                        Modifier.fillMaxWidth()
                            .clip(RoundedCornerShape(14.dp)).background(c.surface)
                            .clickable { model.openSettings() }.padding(horizontal = 16.dp, vertical = 13.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Box(Modifier.size(8.dp).clip(CircleShape).background(c.accent))
                        Text("配置 API Key 并同步可用模型", Modifier.weight(1f).padding(start = 10.dp), color = c.ink, fontSize = 15.sp, fontWeight = FontWeight.Medium)
                        PulseIcon("next", c.faint, Modifier.size(16.dp))
                    }
                }
            }

            // 4. iOS Inset Grouped Conversation Card (Clean unified card with 78dp inset hairlines)
            item("conversation_group") {
                Column(
                    Modifier.fillMaxWidth()
                        .clip(RoundedCornerShape(18.dp))
                        .background(c.surface)
                ) {
                    rows.forEachIndexed { idx, bot ->
                        if (idx > 0) {
                            HorizontalDivider(
                                Modifier.padding(start = 78.dp),
                                color = c.line.copy(alpha = 0.65f),
                                thickness = 0.5.dp
                            )
                        }
                        NativeBotRow(model, bot)
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun NativeBotRow(model: NativePulseModel, bot: PulseBot) {
    val s = model.ui; val c = LocalPulsePalette.current
    val latest = s.messages.lastOrNull { it.botId == bot.id }
    val unread = s.messages.count { it.botId == bot.id && it.id in s.unread }
    val isStreamingHere = s.streamBotId == bot.id
    val preview = when {
        isStreamingHere && s.streamTool.isNotBlank() -> "${s.streamSpeakerName.ifBlank { bot.name }} · 正在${humanizeToolName(s.streamTool, null)}"
        isStreamingHere && s.streamText.isNotBlank() -> "${s.streamSpeakerName.ifBlank { bot.name }}：${s.streamText.replace(Regex("\\s+"), " ")}"
        s.workingBot == bot.id -> s.workingText
        latest != null -> {
            val prefix = when {
                latest.fromBotName.isNotBlank() && latest.fromBotId != bot.id -> "${latest.fromBotName}："
                latest.role == "user" && latest.kind != "bot_message" -> "我："
                else -> ""
            }
            prefix + latest.text.replace(Regex("\n\n附件（工作区相对路径[\\s\\S]*"), " [图片/附件]").replace(Regex("\\s+"), " ")
        }
        else -> bot.description
    }
    Row(
        Modifier.fillMaxWidth()
            .combinedClickable(onClick = { model.open(bot.id) }, onLongClick = { model.botMenu(bot.id) }, onLongClickLabel = "对话选项")
            .semantics { contentDescription = "打开${bot.name}的对话" }
            .padding(vertical = 13.dp, horizontal = 14.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        PulseAvatar(bot, s, 50.dp)
        Column(Modifier.weight(1f).padding(start = 14.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(bot.name, color = c.ink, fontWeight = FontWeight.SemiBold, fontSize = 17.sp, maxLines = 1, overflow = TextOverflow.Ellipsis, letterSpacing = (-0.2).sp)
                if (s.primary == bot.id) {
                    Text(
                        "主控",
                        Modifier.padding(start = 6.dp).clip(RoundedCornerShape(5.dp)).background(c.accent.copy(alpha = 0.12f)).padding(horizontal = 5.dp, vertical = 1.dp),
                        color = c.accent, fontSize = 10.5.sp, fontWeight = FontWeight.Medium
                    )
                }
                if (bot.pinned) {
                    Text(
                        "置顶",
                        Modifier.padding(start = 5.dp).clip(RoundedCornerShape(5.dp)).background(c.fill).padding(horizontal = 5.dp, vertical = 1.dp),
                        color = c.muted, fontSize = 10.sp
                    )
                }
                Spacer(Modifier.weight(1f))
                if (latest != null) {
                    Text(
                        model.timeSeparator(latest.created),
                        fontSize = 13.sp,
                        color = if (unread > 0) c.accent else c.faint
                    )
                }
            }
            Row(Modifier.padding(top = 3.dp), verticalAlignment = Alignment.CenterVertically) {
                Text(
                    preview,
                    Modifier.weight(1f),
                    color = when {
                        latest?.kind == "error" -> c.warn
                        isStreamingHere || s.workingBot == bot.id -> c.accent
                        unread > 0 -> c.ink
                        else -> c.muted
                    },
                    fontSize = 15.sp,
                    lineHeight = 20.sp,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis
                )
                if (unread > 0) {
                    Box(
                        Modifier.padding(start = 8.dp)
                            .defaultMinSize(minWidth = 20.dp, minHeight = 20.dp)
                            .clip(CircleShape).background(c.accent).padding(horizontal = 6.dp, vertical = 2.dp),
                        contentAlignment = Alignment.Center
                    ) {
                        Text(unread.toString(), color = Color.White, fontSize = 11.sp, fontWeight = FontWeight.Bold)
                    }
                }
            }
        }
    }
}

@Composable
private fun NativeChat(model: NativePulseModel, bot: PulseBot, modifier: Modifier, onComposerHeight: (Int) -> Unit) {
    val s = model.ui; val c = LocalPulsePalette.current; val focus = LocalFocusManager.current
    val messages = remember(s.messages, bot.id) { s.messages.filter { it.botId == bot.id } }
    val list = rememberLazyListState()
    val chatScope = rememberCoroutineScope()
    var previousBot by remember { mutableStateOf("") }
    var lastMsgCount by remember { mutableIntStateOf(0) }
    val effectiveModel = bot.model.ifBlank { s.defaultModel.ifBlank { "默认模型" } }
    val lastMsg = messages.lastOrNull()
    val duplicateWithTail = lastMsg != null && lastMsg.role == "assistant" && s.streamText.isNotBlank() &&
        lastMsg.text.trim() == s.streamText.trim() && s.workingBot != bot.id && s.streamTool.isBlank()
    val isLiveHere = !duplicateWithTail && (s.streamBotId == bot.id || s.workingBot == bot.id || (s.busyBot == bot.id && lastMsg?.role == "user"))

    // Mika-style nearTail detection: true when the bottom anchor (or last message) is within 2 items of viewport end
    val nearTail by remember {
        derivedStateOf {
            val info = list.layoutInfo
            val total = info.totalItemsCount
            if (total <= 1) true
            else {
                val lastVisible = info.visibleItemsInfo.lastOrNull()?.index ?: 0
                lastVisible >= total - 3
            }
        }
    }

    // Smoothly anchor to the 1.dp bottom_anchor item so long AI messages NEVER jump to top (offset = 0)
    LaunchedEffect(messages.size, messages.lastOrNull()?.id, bot.id, isLiveHere) {
        val total = list.layoutInfo.totalItemsCount
        val userSentNew = messages.lastOrNull()?.role == "user" && messages.size != lastMsgCount
        if (previousBot != bot.id || userSentNew || nearTail) {
            val anchorIdx = (messages.size + (if (isLiveHere) 1 else 0)).coerceAtLeast(0)
            runCatching { list.scrollToItem(anchorIdx) }
        }
        previousBot = bot.id
        lastMsgCount = messages.size
    }
    LaunchedEffect(s.streamText.length / 28, s.activeTypewriterChars / 24, s.streamTrace.size, s.streamTool) {
        if ((isLiveHere || !s.activeTypewriterMsgId.isNullOrBlank()) && nearTail) {
            val anchorIdx = (messages.size + (if (isLiveHere) 1 else 0)).coerceAtLeast(0)
            runCatching { list.scrollToItem(anchorIdx) }
        }
    }

    Column(modifier.background(c.chatBg)) {
        // iOS Navigation Bar (Back chevron + "对话" on left, centered/clean Bot header, PiP & More on right)
        Surface(color = c.chatBg.copy(alpha = 0.96f), tonalElevation = 0.dp) {
            Column {
                Row(
                    Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 6.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Row(
                        Modifier.clip(RoundedCornerShape(12.dp))
                            .clickable { focus.clearFocus(); model.back() }
                            .padding(horizontal = 6.dp, vertical = 8.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        PulseIcon("back", c.accent, Modifier.size(21.dp))
                        Text("对话", color = c.accent, fontSize = 16.5.sp, fontWeight = FontWeight.Normal, modifier = Modifier.padding(start = 1.dp))
                    }
                    Row(
                        Modifier.padding(start = 4.dp).weight(1f).clip(RoundedCornerShape(12.dp))
                            .clickable { focus.clearFocus(); model.openBotEditor(bot.id) }
                            .padding(horizontal = 8.dp, vertical = 4.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        PulseAvatar(bot, s, 34.dp)
                        Column(Modifier.padding(start = 10.dp)) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text(bot.name, color = c.ink, fontSize = 16.5.sp, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis, letterSpacing = (-0.2).sp)
                                if (s.primary == bot.id) {
                                    Text(
                                        "主控",
                                        Modifier.padding(start = 6.dp).clip(RoundedCornerShape(5.dp)).background(c.accent.copy(alpha = 0.12f)).padding(horizontal = 5.dp, vertical = 1.dp),
                                        color = c.accent, fontSize = 10.sp, fontWeight = FontWeight.Medium
                                    )
                                }
                                if (isLiveHere) {
                                    Box(Modifier.padding(start = 6.dp).size(6.5.dp).clip(CircleShape).background(c.green))
                                }
                            }
                            Text(
                                if (isLiveHere) (s.streamTool.takeIf { it.isNotBlank() }?.let { "正在${humanizeToolName(it, null)}" } ?: "${s.streamSpeakerName.ifBlank { bot.name }} 正在回复…")
                                else effectiveModel,
                                color = if (isLiveHere) c.accent else c.muted, fontSize = 11.5.sp, maxLines = 1, overflow = TextOverflow.Ellipsis
                            )
                        }
                    }
                    PulseRoundButton("screen", "虚拟屏小窗", { focus.clearFocus(); model.launchFloatingVirtualDisplay() }, background = false)
                    PulseRoundButton("more", "选项", { focus.clearFocus(); model.botMenu(bot.id) }, background = false)
                }
                HorizontalDivider(color = c.line.copy(alpha = 0.6f), thickness = 0.5.dp)
            }
        }

        // Chat Viewport (Editorial borderless canvas like ChatGPT iOS / iMessage)
        Box(Modifier.weight(1f).fillMaxWidth()) {
            if (messages.isEmpty() && !isLiveHere) {
                Column(
                    Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 24.dp, vertical = 32.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.Center
                ) {
                    PulseAvatar(bot, s, 72.dp)
                    Text(bot.name, Modifier.padding(top = 16.dp), color = c.ink, fontWeight = FontWeight.Bold, fontSize = 22.sp, letterSpacing = (-0.4).sp)
                    Text(bot.description, Modifier.padding(top = 6.dp, bottom = 24.dp), color = c.muted, fontSize = 15.sp, textAlign = TextAlign.Center, lineHeight = 21.sp)
                    val starters = listOf(
                        "📱 打开学习通看一眼有没有未交作业" to "帮我打开学习通看一眼最近有没有未交的作业或通知",
                        "📅 看看我明天有哪些课和实验要准备" to "结合我的课表看看明天有哪些课和实验，帮我列一下准备事项",
                        "⏰ 定个明天早上 7:30 的早起提醒闹钟" to "帮我定一个明天早上 07:30 的闹钟，提醒我看早八课表",
                        "🌐 搜索一下今天最新的 AI / 大模型进展" to "帮我搜索一下今天最新的 AI 与大模型实用进展并总结重点"
                    )
                    Column(
                        Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(c.fill.copy(alpha = 0.65f))
                    ) {
                        starters.forEachIndexed { idx, (label, prompt) ->
                            if (idx > 0) {
                                HorizontalDivider(Modifier.padding(start = 16.dp), color = c.line.copy(alpha = 0.6f), thickness = 0.5.dp)
                            }
                            Row(
                                Modifier.fillMaxWidth()
                                    .clickable { model.sendQuickPrompt(prompt) }
                                    .padding(horizontal = 16.dp, vertical = 14.dp),
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Text(label, Modifier.weight(1f), color = c.ink, fontSize = 15.sp)
                                PulseIcon("next", c.faint, Modifier.size(15.dp))
                            }
                        }
                    }
                }
            } else {
                LazyColumn(
                    Modifier.fillMaxSize().semantics { contentDescription = "对话消息" },
                    state = list,
                    contentPadding = PaddingValues(horizontal = 16.dp, vertical = 14.dp),
                    verticalArrangement = Arrangement.spacedBy(18.dp)
                ) {
                    items(messages.size, key = { idx -> messages[idx].id }) { idx ->
                        val message = messages[idx]
                        val prevCreated = if (idx > 0) messages[idx - 1].created else 0.0
                        val showTimeHeader = idx == 0 || (message.created - prevCreated) >= 240.0
                        Column(Modifier.fillMaxWidth()) {
                            if (showTimeHeader && message.created > 0) {
                                Box(Modifier.fillMaxWidth().padding(top = 2.dp, bottom = 12.dp), contentAlignment = Alignment.Center) {
                                    Text(
                                        model.timeSeparator(message.created),
                                        color = c.faint,
                                        fontSize = 12.sp,
                                        fontWeight = FontWeight.Medium
                                    )
                                }
                            }
                            NativeMessage(model, bot, message)
                        }
                    }
                    if (isLiveHere) {
                        item("live_stream") {
                            NativeLiveStreamBubble(model, bot)
                        }
                    }
                    item("bottom_anchor") {
                        Spacer(Modifier.height(1.dp).fillMaxWidth())
                    }
                }

                // Floating iOS Capsule "回到底部" pill when user scrolls up
                if (!nearTail && messages.size > 3) {
                    Box(
                        Modifier.align(Alignment.BottomEnd).padding(end = 16.dp, bottom = 12.dp)
                            .clip(CircleShape).background(c.fill)
                            .clickable {
                                chatScope.launch {
                                    val anchorIdx = (messages.size + (if (isLiveHere) 1 else 0)).coerceAtLeast(0)
                                    runCatching { list.animateScrollToItem(anchorIdx) }
                                }
                            }
                            .padding(horizontal = 14.dp, vertical = 8.dp)
                    ) {
                        Text("↓ 最新消息", color = c.accent, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
                    }
                }
            }
        }
        NativeComposer(model, bot, onComposerHeight)
    }
}

@Composable
private fun NativeLiveStreamBubble(model: NativePulseModel, roomBot: PulseBot) {
    val s = model.ui; val c = LocalPulsePalette.current
    val speakerBot = s.streamSpeakerId?.let { model.bot(it) } ?: roomBot
    val speakerName = s.streamSpeakerName.ifBlank { speakerBot.name }
    val rawStream = s.streamText
    val isPlaceholder = rawStream.isBlank() || rawStream == "正在思考…" || rawStream == "正在思考与执行任务…" || rawStream == "正在整理回复…" || rawStream.startsWith("正在调用 ") || rawStream.startsWith("⏰ 正在执行")
    var stepsExpanded by remember { mutableStateOf(false) }

    var nowMs by remember { mutableLongStateOf(System.currentTimeMillis()) }
    LaunchedEffect(Unit) {
        while (true) {
            kotlinx.coroutines.delay(500L)
            nowMs = System.currentTimeMillis()
        }
    }
    val elapsedSec = if (s.streamStartedAt > 0L) ((nowMs - s.streamStartedAt) / 1000L).coerceAtLeast(0L) else 0L
    val elapsedText = "%d:%02d".format(elapsedSec / 60, elapsedSec % 60)

    val pulseTransition = rememberInfiniteTransition(label = "stream_pulse")
    val pulseAlpha by pulseTransition.animateFloat(
        initialValue = 0.32f,
        targetValue = 1.0f,
        animationSpec = infiniteRepeatable(tween(780), RepeatMode.Reverse),
        label = "pulse_alpha"
    )

    // Editorial borderless streaming block (ChatGPT iOS / Claude iOS style)
    Column(
        Modifier.fillMaxWidth().animateContentSize(tween(180)),
        verticalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        Row(
            Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically
        ) {
            PulseAvatar(speakerBot, s, 26.dp)
            Text(
                speakerName,
                Modifier.padding(start = 8.dp),
                fontSize = 14.sp,
                color = c.ink,
                fontWeight = FontWeight.SemiBold
            )
            Box(
                Modifier.padding(start = 8.dp).size(6.dp).clip(CircleShape)
                    .background(c.accent.copy(alpha = pulseAlpha))
            )
            Text(
                elapsedText,
                Modifier.padding(start = 5.dp),
                fontSize = 12.sp,
                color = c.muted,
                fontWeight = FontWeight.Medium
            )
        }

        val stepCount = s.streamTrace.size
        val activeStatusText = when {
            s.streamTool.isNotBlank() -> {
                val toolLabel = humanizeToolName(s.streamTool, null)
                if (stepCount > 0) "已完成 $stepCount 步 · 正在$toolLabel" else "正在$toolLabel…"
            }
            stepCount > 0 && isPlaceholder -> "已完成 $stepCount 步 · 正在整理回复…"
            stepCount > 0 -> "已完成 $stepCount 步操作"
            else -> rawStream.ifBlank { "正在思考…" }
        }

        if (stepCount > 0 || s.streamTool.isNotBlank() || isPlaceholder) {
            Column(
                Modifier.fillMaxWidth()
                    .clip(RoundedCornerShape(12.dp))
                    .background(c.fill.copy(alpha = 0.72f))
                    .clickable(enabled = stepCount > 0) { stepsExpanded = !stepsExpanded }
                    .padding(horizontal = 13.dp, vertical = 9.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp)
            ) {
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                    Box(
                        Modifier.size(6.5.dp).clip(CircleShape)
                            .background(if (s.streamTool.isNotBlank() || isPlaceholder) c.accent.copy(alpha = pulseAlpha) else c.green)
                    )
                    Text(
                        activeStatusText,
                        Modifier.padding(start = 8.dp).weight(1f),
                        color = c.ink.copy(alpha = 0.88f),
                        fontSize = 13.sp,
                        fontWeight = FontWeight.Medium,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis
                    )
                    if (stepCount > 0) {
                        Text(
                            if (stepsExpanded) "收起" else "${stepCount}步 ▾",
                            color = c.faint,
                            fontSize = 12.sp
                        )
                    }
                }

                if (stepsExpanded && s.streamTrace.isNotEmpty()) {
                    Column(
                        Modifier.padding(start = 4.dp, top = 2.dp),
                        verticalArrangement = Arrangement.spacedBy(5.dp)
                    ) {
                        s.streamTrace.takeLast(6).forEach { step ->
                            val tName = step.optString("tool")
                            val tArgs = step.optJSONObject("arguments")
                            val dur = step.optLong("duration_ms", 0L)
                            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                                Text("·", color = c.muted, fontSize = 13.sp)
                                Text(
                                    humanizeToolName(tName, tArgs),
                                    Modifier.padding(start = 6.dp).weight(1f),
                                    color = c.muted,
                                    fontSize = 12.5.sp,
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis
                                )
                                if (dur > 0) {
                                    Text("${dur}ms", color = c.faint, fontSize = 11.sp)
                                }
                            }
                        }
                    }
                }
            }
        }

        val cleanThought = s.streamThought
            .removePrefix("💭").removePrefix("⚡").trim()
        if (cleanThought.isNotBlank() && (isPlaceholder || s.streamTool.isNotBlank())) {
            Row(Modifier.fillMaxWidth().height(IntrinsicSize.Min)) {
                Box(
                    Modifier.width(2.5.dp).fillMaxHeight()
                        .clip(RoundedCornerShape(2.dp))
                        .background(c.accent.copy(alpha = 0.35f))
                )
                Text(
                    cleanThought,
                    Modifier.padding(start = 10.dp).weight(1f),
                    color = c.muted,
                    fontSize = 13.5.sp,
                    lineHeight = 19.sp,
                    maxLines = 3,
                    overflow = TextOverflow.Ellipsis
                )
            }
        }

        if (!isPlaceholder && rawStream.isNotBlank()) {
            val streamCursor = if (pulseAlpha > 0.62f) " ▍" else ""
            MarkdownText(
                nativeWikiMarkdown(rawStream + streamCursor),
                TextStyle(color = c.ink, fontSize = 16.5.sp, lineHeight = 24.sp),
                streaming = true,
                onOpenUri = model::openLink
            )
        }

        val usesCloudDesktop = s.streamTool == "cloud_desktop" ||
            s.streamTrace.any { it.optString("tool") == "cloud_desktop" } ||
            s.cloudDesktopLiveBotId == roomBot.id
        if (usesCloudDesktop) {
            LaunchedEffect(roomBot.id, usesCloudDesktop) {
                if (s.cloudDesktopFrame == null) {
                    model.refreshCloudDesktopFrame(forceFull = true)
                }
                while (true) {
                    kotlinx.coroutines.delay(750L)
                    model.refreshCloudDesktopFrame(forceFull = false)
                }
            }
            InlineCloudDesktopMonitor(model = model, isLive = true)
        }
    }
}

@Composable
private fun InlineCloudDesktopMonitor(model: NativePulseModel, isLive: Boolean) {
    val s = model.ui
    val c = LocalPulsePalette.current
    val focus = LocalFocusManager.current
    val win11Blue = Color(0xFF60CDFF)
    Column(
        Modifier.fillMaxWidth()
            .clip(RoundedCornerShape(14.dp))
            .background(Color(0xFF141824))
            .border(1.dp, if (isLive) win11Blue.copy(alpha = 0.65f) else Color(0xFF2C3652), RoundedCornerShape(14.dp))
    ) {
        // Windows 11 Acrylic Title Bar
        Row(
            Modifier.fillMaxWidth()
                .background(Color(0xFF1A2030))
                .padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Box(
                Modifier.size(7.dp).clip(CircleShape)
                    .background(if (isLive) win11Blue else c.green)
            )
            Text(
                if (isLive) "❖ Windows 11 虚拟电脑 · 实时操控中" else "❖ Windows 11 虚拟电脑 · 实时画面",
                Modifier.padding(start = 7.dp),
                color = Color(0xFFF3F6FC),
                fontSize = 12.sp,
                fontWeight = FontWeight.SemiBold
            )
            Text(
                s.cloudDesktopTitle.takeIf { it.isNotBlank() }?.let { " · $it" } ?: "",
                Modifier.weight(1f),
                color = Color(0xFF94A3B8),
                fontSize = 11.sp,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis
            )
            Text(
                "全屏操控 ↗",
                Modifier.clip(RoundedCornerShape(8.dp))
                    .background(win11Blue.copy(alpha = 0.18f))
                    .clickable { focus.clearFocus(); model.openCloudDesktop() }
                    .padding(horizontal = 9.dp, vertical = 4.dp),
                color = win11Blue,
                fontSize = 11.sp,
                fontWeight = FontWeight.SemiBold
            )
        }
        // Strict 16:10 (1280x800) interactive mini viewport
        Box(
            Modifier.fillMaxWidth().aspectRatio(1280f / 800f)
                .background(Color(0xFF0F1420)),
            contentAlignment = Alignment.Center
        ) {
            val frame = s.cloudDesktopFrame
            if (frame != null) {
                BoxWithConstraints(Modifier.fillMaxSize()) {
                    val boxW = maxWidth
                    val boxH = maxHeight
                    Image(
                        bitmap = frame,
                        contentDescription = "虚拟云电脑实时画面",
                        contentScale = ContentScale.FillBounds,
                        modifier = Modifier.fillMaxSize().pointerInput(Unit) {
                            detectTapGestures(
                                onTap = { offset ->
                                    if (size.width > 0 && size.height > 0) {
                                        focus.clearFocus()
                                        model.cloudDesktopClick(offset.x / size.width.toFloat(), offset.y / size.height.toFloat(), "click")
                                    }
                                },
                                onDoubleTap = { offset ->
                                    if (size.width > 0 && size.height > 0) {
                                        focus.clearFocus()
                                        model.cloudDesktopClick(offset.x / size.width.toFloat(), offset.y / size.height.toFloat(), "double_click")
                                    }
                                },
                                onLongPress = { offset ->
                                    if (size.width > 0 && size.height > 0) {
                                        focus.clearFocus()
                                        model.cloudDesktopClick(offset.x / size.width.toFloat(), offset.y / size.height.toFloat(), "right_click")
                                    }
                                }
                            )
                        }
                    )
                    s.cloudDesktopTapNorm?.let { (nx, ny) ->
                        Box(
                            Modifier.offset(x = boxW * nx - 12.dp, y = boxH * ny - 12.dp)
                                .size(24.dp)
                                .clip(CircleShape)
                                .background(win11Blue.copy(alpha = 0.28f))
                                .border(2.dp, win11Blue, CircleShape)
                        )
                    }
                }
            } else {
                Text(
                    "正在同步 Windows 11 虚拟电脑实时画面…",
                    color = Color(0xFF94A3B8),
                    fontSize = 12.sp
                )
            }
        }
        // Quick Win11 Mini Taskbar Strip
        Row(
            Modifier.fillMaxWidth()
                .background(Color(0xFF141824))
                .padding(horizontal = 10.dp, vertical = 6.dp),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            listOf(
                "lite_browser" to "⚡ 浏览器",
                "terminal" to "💻 终端",
                "files" to "📁 文件",
                "editor" to "📝 编辑器"
            ).forEach { (appKey, label) ->
                Text(
                    label,
                    Modifier.clip(RoundedCornerShape(6.dp))
                        .background(Color(0xFF1F273B))
                        .clickable { focus.clearFocus(); model.cloudDesktopLaunchApp(appKey) }
                        .padding(horizontal = 8.dp, vertical = 4.dp),
                    color = Color(0xFFD8E2F8),
                    fontSize = 11.sp
                )
            }
            Spacer(Modifier.weight(1f))
            Text(
                "点画面直接触控",
                color = Color(0xFF64748B),
                fontSize = 10.5.sp
            )
        }
    }
}

private fun humanizeToolName(tool: String, args: org.json.JSONObject?): String {
    val op = args?.optString("op").orEmpty()
    return when (tool) {
        "phone_workspace" -> when (op) {
            "ui_tree" -> "读取虚拟屏界面节点"
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
                "add_timer" -> "设定提醒 ${atTime}".trim()
                "update" -> "更新日程提醒"
                "remove" -> "移除旧提醒"
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
            "screenshot", "capture", "frame", "status" -> "读取虚拟电脑屏幕画面"
            else -> "操控 Windows 11 虚拟电脑"
        }
        "notify_user" -> "发送通知「${args?.optString("title").orEmpty().take(20)}」"
        else -> "执行 $tool"
    }
}

@Composable
private fun NativeComposer(model: NativePulseModel, bot: PulseBot, onHeight: (Int) -> Unit) {
    val s = model.ui; val c = LocalPulsePalette.current
    val focus = LocalFocusManager.current
    val requester = remember { FocusRequester() }
    val executing = s.workingBot == bot.id || s.busyBot == bot.id || s.streamBotId == bot.id
    val hasDraft = !s.uploading && (s.draft.isNotBlank() || s.attachments.isNotEmpty())
    val phoneState by RootPhoneHarness.changes.collectAsState()

    Surface(
        Modifier.fillMaxWidth().onSizeChanged { onHeight(it.height) },
        color = c.chatBg,
        tonalElevation = 0.dp
    ) {
        Column(Modifier.fillMaxWidth()) {
            HorizontalDivider(color = c.line.copy(alpha = 0.55f), thickness = 0.5.dp)
            Column(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp)) {
                if (phoneState.displayCreated) {
                    Row(
                        Modifier.fillMaxWidth().padding(bottom = 6.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Row(
                            Modifier.clip(CircleShape)
                                .background(c.green.copy(alpha = 0.14f))
                                .clickable { focus.clearFocus(); model.launchFloatingVirtualDisplay() }
                                .padding(horizontal = 12.dp, vertical = 5.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Box(Modifier.size(6.5.dp).clip(CircleShape).background(c.green))
                            Text("虚拟屏运行中 (#${phoneState.displayId}) · 点按展开", Modifier.padding(start = 6.dp), color = c.green, fontSize = 12.sp, fontWeight = FontWeight.Medium)
                        }
                    }
                }
                if (s.cloudDesktopPipVisible) {
                    Row(
                        Modifier.fillMaxWidth().padding(bottom = 6.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Row(
                            Modifier.clip(CircleShape)
                                .background(c.accent.copy(alpha = 0.14f))
                                .clickable { focus.clearFocus(); model.openCloudDesktop() }
                                .padding(horizontal = 12.dp, vertical = 5.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Box(Modifier.size(6.5.dp).clip(CircleShape).background(c.accent))
                            Text(
                                "❖ Windows 11 虚拟电脑运行中 · 点按全屏操控",
                                Modifier.padding(start = 6.dp),
                                color = c.accent,
                                fontSize = 12.sp,
                                fontWeight = FontWeight.Medium
                            )
                        }
                        Text(
                            "收起",
                            Modifier.clickable { model.dismissCloudDesktopPip() }.padding(horizontal = 8.dp, vertical = 4.dp),
                            color = c.faint,
                            fontSize = 11.5.sp
                        )
                    }
                }
                if (executing && hasDraft) {
                    Row(Modifier.fillMaxWidth().padding(bottom = 6.dp), horizontalArrangement = Arrangement.End) {
                        Row(
                            Modifier.clip(CircleShape).background(c.fill)
                                .clickable { model.stop() }.padding(horizontal = 12.dp, vertical = 5.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Box(Modifier.size(7.dp).clip(RoundedCornerShape(2.dp)).background(c.warn))
                            Text("停止当前生成", Modifier.padding(start = 6.dp), color = c.muted, fontSize = 12.sp, fontWeight = FontWeight.Medium)
                        }
                    }
                }
                if (s.attachments.isNotEmpty() || s.uploading) {
                    Row(
                        Modifier.fillMaxWidth().padding(bottom = 6.dp).clip(RoundedCornerShape(14.dp)).background(c.fill)
                            .padding(start = 14.dp, end = 4.dp, top = 2.dp, bottom = 2.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text(
                            if (s.uploading) "正在上传图片/附件…" else "🖼️ ${s.attachments.joinToString("、") { it.name }}",
                            Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis, fontSize = 13.5.sp, color = c.ink, fontWeight = FontWeight.Medium
                        )
                        if (!s.uploading) PulseRoundButton("close", "移除附件", model::clearAttachments, background = false)
                    }
                }
                s.reply?.let { quote ->
                    Row(
                        Modifier.fillMaxWidth().padding(bottom = 6.dp).clip(RoundedCornerShape(14.dp)).background(c.fill)
                            .padding(start = 14.dp, end = 4.dp, top = 2.dp, bottom = 2.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Box(Modifier.width(3.dp).height(16.dp).clip(RoundedCornerShape(2.dp)).background(c.accent))
                        Text(
                            quote.text.replace("\n", " "),
                            Modifier.padding(start = 8.dp).weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis, fontSize = 13.sp, color = c.muted
                        )
                        PulseRoundButton("close", "取消引用", { model.reply(null) }, background = false)
                    }
                }
                // iOS iMessage / ChatGPT iOS Composer Row: two circular action buttons on left + smooth capsule field on right
                Row(
                    Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.Bottom,
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    Box(
                        Modifier.padding(bottom = 2.dp).size(36.dp).clip(CircleShape).background(c.fill)
                            .clickable { focus.clearFocus(); model.actionsMenu() }
                            .semantics { contentDescription = "工作台与工具" },
                        contentAlignment = Alignment.Center
                    ) {
                        PulseIcon("plus", c.ink.copy(alpha = 0.8f), Modifier.size(19.dp))
                    }
                    Box(
                        Modifier.padding(bottom = 2.dp).size(36.dp).clip(CircleShape).background(c.fill)
                            .clickable { focus.clearFocus(); model.chooseAttachment() }
                            .semantics { contentDescription = "发送图片或附件" },
                        contentAlignment = Alignment.Center
                    ) {
                        PulseIcon("file", c.ink.copy(alpha = 0.8f), Modifier.size(18.dp))
                    }
                    Row(
                        Modifier.weight(1f)
                            .clip(RoundedCornerShape(22.dp))
                            .background(c.fill)
                            .padding(start = 14.dp, end = 5.dp, top = 5.dp, bottom = 5.dp),
                        verticalAlignment = Alignment.Bottom
                    ) {
                        BasicTextField(
                            s.draft,
                            { if (it.length <= 12000) model.setDraft(it) },
                            Modifier.weight(1f).heightIn(min = 30.dp, max = 130.dp).padding(vertical = 4.dp, horizontal = 2.dp).focusRequester(requester).semantics { contentDescription = "聊天消息" },
                            textStyle = TextStyle(color = c.ink, fontSize = 16.5.sp, lineHeight = 22.sp),
                            cursorBrush = SolidColor(c.accent),
                            maxLines = 6,
                            keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences),
                            decorationBox = { inner -> Box { if (s.draft.isEmpty()) Text("发消息…", color = c.faint, fontSize = 16.5.sp, maxLines = 1); inner() } }
                        )
                        val showStopOnly = executing && !hasDraft
                        val buttonEnabled = hasDraft || showStopOnly
                        Box(
                            Modifier.size(31.dp).clip(CircleShape)
                                .background(if (showStopOnly) c.warn else if (buttonEnabled) c.accent else c.faint.copy(alpha = 0.25f))
                                .clickable(enabled = buttonEnabled) { if (showStopOnly) model.stop() else model.send() }
                                .semantics { contentDescription = if (showStopOnly) "停止执行" else "发送消息" },
                            contentAlignment = Alignment.Center
                        ) {
                            if (showStopOnly) Box(Modifier.size(10.dp).clip(RoundedCornerShape(2.dp)).background(Color.White))
                            else PulseIcon("send", Color.White, Modifier.size(16.dp))
                        }
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun NativeMessage(model: NativePulseModel, bot: PulseBot, message: PulseMessage) {
    val s = model.ui; val c = LocalPulsePalette.current
    val isBotToBotIn = message.kind == "bot_message"
    val isBotToBotReply = message.kind == "bot_reply"
    val isError = message.kind == "error"
    val isNotification = message.kind == "notification"
    val user = message.role == "user" && !isBotToBotIn
    var showActions by rememberSaveable(message.id) { mutableStateOf(false) }

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

        Column(
            Modifier.fillMaxWidth().padding(start = 52.dp),
            horizontalAlignment = Alignment.End
        ) {
            Column(
                Modifier.clip(RoundedCornerShape(20.dp, 20.dp, 4.dp, 20.dp))
                    .background(c.userBubble)
                    .combinedClickable(
                        onClick = { showActions = !showActions },
                        onLongClick = { showActions = true; model.copy(cleanBody) }
                    )
                    .padding(horizontal = 15.dp, vertical = 10.dp)
            ) {
                if (quotePart.isNotBlank()) {
                    Row(
                        Modifier.padding(bottom = 6.dp).clip(RoundedCornerShape(8.dp))
                            .background(Color.White.copy(alpha = 0.14f))
                            .padding(horizontal = 9.dp, vertical = 5.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Box(Modifier.width(2.dp).height(13.dp).clip(RoundedCornerShape(1.dp)).background(Color.White.copy(alpha = 0.8f)))
                        Text(
                            quotePart,
                            Modifier.padding(start = 6.dp),
                            color = Color.White.copy(alpha = 0.9f),
                            fontSize = 13.sp,
                            maxLines = 2,
                            overflow = TextOverflow.Ellipsis
                        )
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
                                Text("🖼️ $fileName", color = Color.White, fontSize = 13.sp, fontWeight = FontWeight.Medium)
                            }
                        }
                    }
                }
                if (bodyPart.isNotBlank()) {
                    Text(bodyPart, color = c.userInk, fontSize = 16.5.sp, lineHeight = 23.sp)
                }
            }
            if (message.optimistic) {
                Text("正在发送…", Modifier.padding(top = 3.dp, end = 4.dp), color = c.faint, fontSize = 11.sp)
            } else if (showActions) {
                Row(
                    Modifier.padding(top = 5.dp, end = 4.dp),
                    horizontalArrangement = Arrangement.spacedBy(16.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text("复制", Modifier.clickable { model.copy(bodyPart); showActions = false }, color = c.muted, fontSize = 12.sp)
                    Text("引用", Modifier.clickable { model.reply(message); showActions = false }, color = c.muted, fontSize = 12.sp)
                    Text("重发", Modifier.clickable { model.retryMessage(message); showActions = false }, color = c.muted, fontSize = 12.sp)
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

    // Editorial borderless assistant response (ChatGPT iOS / Claude iOS layout — zero heavy box border!)
    Column(
        Modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        Row(
            Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically
        ) {
            PulseAvatar(speakerBot, s, 26.dp)
            Text(
                speakerBot.name,
                Modifier.padding(start = 8.dp),
                fontSize = 14.sp,
                color = if (isError) c.warn else if (isOtherBot) speakerColor else c.ink,
                fontWeight = FontWeight.SemiBold
            )
            if (isBotToBotIn) {
                Text(
                    "协作派发",
                    Modifier.padding(start = 6.dp).clip(RoundedCornerShape(5.dp)).background(c.accent.copy(alpha = 0.12f)).padding(horizontal = 5.dp, vertical = 1.dp),
                    fontSize = 10.sp, color = c.accent, fontWeight = FontWeight.Medium
                )
            } else if (isBotToBotReply || isOtherBot) {
                Text(
                    "协作汇报",
                    Modifier.padding(start = 6.dp).clip(RoundedCornerShape(5.dp)).background(speakerColor.copy(alpha = 0.14f)).padding(horizontal = 5.dp, vertical = 1.dp),
                    fontSize = 10.sp, color = speakerColor, fontWeight = FontWeight.Medium
                )
            }
            if (isNotification) {
                Text(
                    "提醒",
                    Modifier.padding(start = 6.dp).clip(RoundedCornerShape(5.dp)).background(c.green.copy(alpha = 0.12f)).padding(horizontal = 5.dp, vertical = 1.dp),
                    fontSize = 10.sp, color = c.green, fontWeight = FontWeight.Medium
                )
            } else if (message.kind == "curiosity") {
                Text(
                    "主动关注",
                    Modifier.padding(start = 6.dp).clip(RoundedCornerShape(5.dp)).background(c.accent.copy(alpha = 0.12f)).padding(horizontal = 5.dp, vertical = 1.dp),
                    fontSize = 10.sp, color = c.accent, fontWeight = FontWeight.Medium
                )
            }
            Spacer(Modifier.weight(1f))
            Text(model.time(message.created), color = c.faint, fontSize = 11.5.sp)
        }

        Column(
            Modifier.fillMaxWidth().animateContentSize(tween(160)),
            verticalArrangement = Arrangement.spacedBy(10.dp)
        ) {
            if (trace.isNotEmpty()) {
                NativeToolTraceGroup(model, message.id, trace)
            }
            val msgUsedCloudDesktop = remember(trace, message.kind) {
                message.kind.startsWith("computer_") || trace.any { it.optString("tool") == "cloud_desktop" }
            }
            val isLatestAssistantMsg = remember(s.messages, message.id) {
                s.messages.lastOrNull { it.botId == bot.id && it.role == "assistant" }?.id == message.id
            }
            if (msgUsedCloudDesktop && isLatestAssistantMsg && s.streamBotId != bot.id) {
                LaunchedEffect(message.id) {
                    if (s.cloudDesktopFrame == null) {
                        model.refreshCloudDesktopFrame(forceFull = true)
                    }
                }
                InlineCloudDesktopMonitor(model = model, isLive = false)
            }
            val isTypingThis = s.activeTypewriterMsgId == message.id && s.activeTypewriterChars < message.text.length
            val shownText = if (isTypingThis) message.text.take(s.activeTypewriterChars.coerceAtLeast(1)) else message.text
            SelectionContainer {
                MarkdownText(
                    nativeWikiMarkdown(shownText),
                    TextStyle(color = if (isError) c.warn else c.ink, fontSize = 16.5.sp, lineHeight = 24.sp),
                    streaming = isTypingThis,
                    onOpenUri = model::openLink
                )
            }
            if (isError) {
                PulseAction("让主助理自动修复模型配置并重试") { model.askPrimaryToHealError(message) }
            }
            if (message.kind == "result" && message.decision.isBlank()) {
                Row(Modifier.fillMaxWidth().padding(top = 4.dp), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text(
                        "固化为日常例行",
                        Modifier.weight(1f).clip(RoundedCornerShape(12.dp)).background(c.green.copy(alpha = 0.14f))
                            .clickable { model.confirm(message, true) }.padding(vertical = 10.dp),
                        color = c.green, fontSize = 13.sp, fontWeight = FontWeight.SemiBold, textAlign = TextAlign.Center
                    )
                    Text(
                        "仅此一次",
                        Modifier.weight(1f).clip(RoundedCornerShape(12.dp)).background(c.fill)
                            .clickable { model.confirm(message, false) }.padding(vertical = 10.dp),
                        color = c.muted, fontSize = 13.sp, textAlign = TextAlign.Center
                    )
                }
            }
        }

        // Whisper-quiet editorial footer actions
        Row(
            Modifier.padding(top = 2.dp),
            horizontalArrangement = Arrangement.spacedBy(16.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text("复制", Modifier.clickable { model.copy(message.text) }, color = c.faint, fontSize = 12.sp)
            Text("引用", Modifier.clickable { model.reply(message) }, color = c.faint, fontSize = 12.sp)
            Text("重新生成", Modifier.clickable { model.retryMessage(message) }, color = c.faint, fontSize = 12.sp)
            if (message.kind.startsWith("computer_")) {
                Text("查看云端桌面 ›", Modifier.clickable { model.openCloudDesktop() }, color = c.accent, fontSize = 12.sp, fontWeight = FontWeight.Medium)
            }
            if (message.kind in listOf("phone_result", "phone_question", "phone_stopped") || trace.any { it.optString("tool").startsWith("phone") }) {
                Text("虚拟屏小窗 ›", Modifier.clickable { model.launchFloatingVirtualDisplay() }, color = c.accent, fontSize = 12.sp, fontWeight = FontWeight.Medium)
            }
        }
    }
}

/**
 * Minimalist Collapsible Step Pill (ChatGPT iOS / Claude iOS style):
 * Borderless translucent fill pill, collapsed by default so completed messages stay clean.
 */
@Composable
private fun NativeToolTraceGroup(model: NativePulseModel, messageId: String, trace: List<org.json.JSONObject>) {
    val c = LocalPulsePalette.current
    var expanded by rememberSaveable(messageId) { mutableStateOf(false) }
    val totalMs = remember(trace) { trace.sumOf { it.optLong("duration_ms", 0L) } }
    val firstToolLabel = remember(trace) {
        trace.firstOrNull()?.let { humanizeToolName(it.optString("tool"), it.optJSONObject("arguments")) }.orEmpty()
    }

    Column(
        Modifier.fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .background(c.fill.copy(alpha = 0.72f))
            .clickable { expanded = !expanded }
            .animateContentSize(tween(160))
            .padding(horizontal = 13.dp, vertical = 8.dp)
    ) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(6.dp).clip(CircleShape).background(c.accent))
            Text(
                if (trace.size == 1) "已执行 · $firstToolLabel" else "已执行 ${trace.size} 步操作 · $firstToolLabel 等",
                Modifier.padding(start = 8.dp).weight(1f),
                color = c.muted,
                fontSize = 13.sp,
                fontWeight = FontWeight.Medium,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis
            )
            Text(
                if (expanded) "收起 ▴" else "${totalMs}ms ▾",
                Modifier.padding(start = 8.dp),
                color = c.faint,
                fontSize = 12.sp
            )
        }
        if (expanded) {
            Column(
                Modifier.padding(top = 8.dp, start = 2.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp)
            ) {
                trace.forEach { step ->
                    val toolName = step.optString("tool")
                    val argsObj = step.optJSONObject("arguments")
                    val dur = step.optLong("duration_ms", 0L)
                    val resObj = step.optJSONObject("result")
                    val hasErr = resObj?.has("error") == true
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                        Text("·", color = c.faint, fontSize = 13.sp)
                        Text(
                            humanizeToolName(toolName, argsObj),
                            Modifier.padding(start = 6.dp).weight(1f),
                            color = if (hasErr) c.warn else c.ink.copy(alpha = 0.85f),
                            fontSize = 12.5.sp,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis
                        )
                        if (dur > 0) {
                            Text("${dur}ms", Modifier.padding(start = 6.dp), color = c.faint, fontSize = 11.sp)
                        }
                    }
                }
            }
        }
    }
}

// --- 1. Native Settings Panel (One-Page API & /models Fetcher) ---
@Composable
private fun NativeSettingsPanel(model: NativePulseModel, modifier: Modifier) {
    val s = model.ui; val c = LocalPulsePalette.current; val focus = LocalFocusManager.current
    var modelFilter by remember { mutableStateOf("") }
    var showAdvancedNode by remember { mutableStateOf(false) }
    val filteredModels = remember(s.availableModels, modelFilter) {
        if (modelFilter.isBlank()) s.availableModels else s.availableModels.filter { it.contains(modelFilter, ignoreCase = true) }
    }
    Column(modifier.background(c.bg)) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
            PulseRoundButton("back", "返回", { focus.clearFocus(); model.closePanel() })
            Text("API 与模型设置", Modifier.padding(start = 14.dp).weight(1f), color = c.ink, fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
            Text(
                if (s.savingSettings) "同步中…" else "保存并同步",
                Modifier.clip(RoundedCornerShape(18.dp)).background(c.ink).clickable(enabled = !s.savingSettings) { focus.clearFocus(); model.saveSettings() }.padding(horizontal = 14.dp, vertical = 9.dp),
                color = c.bg, fontSize = 13.sp, fontWeight = FontWeight.Medium
            )
        }
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 22.dp, vertical = 12.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
            Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(c.surface).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text("1. 统一大模型接口 (OpenAI 兼容)", color = c.ink, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
                Text("填好后会自动同步到云端 workspace/settings.json，所有 Bot 均可读取并使用。", color = c.muted, fontSize = 12.sp)
                PulseInputField(
                    label = "API Base URL（不含 /chat/completions）",
                    value = s.apiBaseUrl,
                    placeholder = "https://api.openai.com/v1",
                    onChange = { model.updateSettingsField(apiBaseUrl = it) }
                )
                PulseInputField(
                    label = if (s.hasApiKey && s.apiKeyInput.isBlank()) "API Key（云端已保存，留空则保持不变）" else "API Key",
                    value = s.apiKeyInput,
                    placeholder = if (s.hasApiKey) "sk-...（已保存，如需更换请重新输入）" else "sk-...",
                    onChange = { model.updateSettingsField(apiKeyInput = it) }
                )
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp), verticalAlignment = Alignment.CenterVertically) {
                    Box(
                        Modifier.weight(1f).clip(RoundedCornerShape(14.dp)).background(c.accent)
                            .clickable(enabled = !s.fetchingModels) { focus.clearFocus(); model.fetchModels(false) }
                            .padding(vertical = 12.dp),
                        contentAlignment = Alignment.Center
                    ) {
                        Text(if (s.fetchingModels) "正在请求 /models…" else "↻ 一键获取可用模型 (/models)", color = Color.White, fontSize = 13.sp, fontWeight = FontWeight.Medium)
                    }
                }
                PulseInputField(
                    label = "全局默认模型（可点击下方列表选择或手动输入）",
                    value = s.defaultModel,
                    placeholder = "例如 gpt-4.1 / claude-3-7-sonnet / deepseek-chat",
                    onChange = { model.updateSettingsField(defaultModel = it) }
                )
                if (s.availableModels.isNotEmpty()) {
                    PulseInputField(
                        label = "筛选已拉取的 ${s.availableModels.size} 个模型",
                        value = modelFilter,
                        placeholder = "输入关键词过滤模型…",
                        onChange = { modelFilter = it }
                    )
                    LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth().padding(top = 4.dp)) {
                        items(filteredModels.take(60)) { m ->
                            val selected = m == s.defaultModel
                            Text(
                                m,
                                Modifier.clip(RoundedCornerShape(10.dp))
                                    .background(if (selected) c.ink else c.fill)
                                    .border(1.dp, if (selected) c.ink else c.line, RoundedCornerShape(10.dp))
                                    .clickable { model.updateSettingsField(defaultModel = m) }
                                    .padding(horizontal = 11.dp, vertical = 8.dp),
                                color = if (selected) c.bg else c.ink,
                                fontSize = 12.sp
                            )
                        }
                    }
                }
            }

            Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(c.surface).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text("2. 主动好奇心与通知", color = c.ink, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("启用云端主动好奇心心跳", color = c.ink, fontSize = 14.sp)
                        Text("闲置时由开启了好奇心的 Bot 主动关心或派发调研", color = c.muted, fontSize = 11.sp)
                    }
                    Switch(checked = s.curiosityEnabledGlobal, onCheckedChange = { model.updateSettingsField(curiosityEnabled = it) })
                }
                PulseInputField(
                    label = "好奇心间隔（分钟，5-1440）",
                    value = s.curiosityMinutes,
                    placeholder = "90",
                    onChange = { model.updateSettingsField(curiosityMinutes = it.filter { ch -> ch.isDigit() }) }
                )
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("手机后台常驻接收通知", color = c.ink, fontSize = 14.sp)
                        Text("保持加密 WebSocket 连接，仅在 Bot 调用 notify_user 时响铃", color = c.muted, fontSize = 11.sp)
                    }
                    Switch(checked = s.backgroundService, onCheckedChange = { model.updateSettingsField(backgroundService = it) })
                }
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    listOf("system" to "跟随系统", "light" to "浅色外观", "dark" to "深色外观").forEach { (key, label) ->
                        val active = s.appearance == key
                        Text(
                            label,
                            Modifier.weight(1f).clip(RoundedCornerShape(12.dp))
                                .background(if (active) c.ink else c.fill)
                                .clickable { model.updateSettingsField(appearance = key) }
                                .padding(vertical = 10.dp),
                            color = if (active) c.bg else c.ink, fontSize = 12.sp, textAlign = TextAlign.Center
                        )
                    }
                }
            }

            Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(c.surface).padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Row(Modifier.fillMaxWidth().clickable { showAdvancedNode = !showAdvancedNode }, verticalAlignment = Alignment.CenterVertically) {
                    Text("3. 云端服务器节点（已预配日本节点）", Modifier.weight(1f), color = c.ink, fontSize = 14.sp, fontWeight = FontWeight.Medium)
                    Text(if (showAdvancedNode) "收起" else "展开", color = c.muted, fontSize = 12.sp)
                }
                if (showAdvancedNode) {
                    PulseInputField(
                        label = "Server URL",
                        value = s.serverUrl,
                        placeholder = SecureTransport.DEFAULT_SERVER,
                        onChange = { model.updateSettingsField(serverUrl = it) }
                    )
                }
            }

            PulseAction(if (s.savingSettings) "正在同步到服务器…" else "保存并同步到云端") {
                focus.clearFocus(); model.saveSettings()
            }
            Spacer(Modifier.height(30.dp))
        }
    }
}

// --- 2. Native Bot Editor Panel ---
@Composable
private fun NativeBotEditorPanel(model: NativePulseModel, modifier: Modifier) {
    val s = model.ui; val c = LocalPulsePalette.current; val focus = LocalFocusManager.current
    val isNew = s.editingBotId == null
    Column(modifier.background(c.bg)) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
            PulseRoundButton("back", "返回", { focus.clearFocus(); model.closePanel() })
            Text(if (isNew) "创建新 Bot" else "编辑 Bot 设定与模型", Modifier.padding(start = 14.dp).weight(1f), color = c.ink, fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
            Text(
                if (s.savingBot) "保存中…" else "保存",
                Modifier.clip(RoundedCornerShape(18.dp)).background(c.ink).clickable(enabled = !s.savingBot) { focus.clearFocus(); model.saveBotEditor() }.padding(horizontal = 16.dp, vertical = 9.dp),
                color = c.bg, fontSize = 13.sp, fontWeight = FontWeight.Medium
            )
        }
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 22.dp, vertical = 12.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
            Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(c.surface).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                PulseInputField("Bot 名字", s.editBotName, "例如：代码审计员 / 资料搜集员") { model.updateBotEditField(name = it) }
                PulseInputField("一句话简介", s.editBotDesc, "显示在首页列表中") { model.updateBotEditField(desc = it) }
                PulseInputField(
                    "专属模型（留空则使用全局默认模型：${s.defaultModel.ifBlank { "未设置" }}）",
                    s.editBotModel,
                    "留空跟随全局默认，或填入/点选指定模型"
                ) { model.updateBotEditField(modelName = it) }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(
                        if (s.fetchingModels) "正在拉取 /models…" else "↻ 从 /models 获取模型列表",
                        Modifier.clip(RoundedCornerShape(10.dp)).background(c.fill).clickable(enabled = !s.fetchingModels) { model.fetchModels(true) }.padding(horizontal = 12.dp, vertical = 8.dp),
                        color = c.accent, fontSize = 12.sp
                    )
                    if (s.editBotModel.isNotBlank()) {
                        Text(
                            "清空（恢复跟随默认）",
                            Modifier.clip(RoundedCornerShape(10.dp)).background(c.fill).clickable { model.updateBotEditField(modelName = "") }.padding(horizontal = 12.dp, vertical = 8.dp),
                            color = c.muted, fontSize = 12.sp
                        )
                    }
                }
                if (s.availableModels.isNotEmpty()) {
                    LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                        items(s.availableModels.take(50)) { m ->
                            val selected = m == s.editBotModel
                            Text(
                                m,
                                Modifier.clip(RoundedCornerShape(10.dp))
                                    .background(if (selected) c.ink else c.fill)
                                    .clickable { model.updateBotEditField(modelName = m) }
                                    .padding(horizontal = 10.dp, vertical = 7.dp),
                                color = if (selected) c.bg else c.ink, fontSize = 12.sp
                            )
                        }
                    }
                }
                PulseInputField("角色设定与系统指令 (System Prompt)", s.editBotInstructions, "告诉这位 Bot 它的职责、说话风格与协作要求…", singleLine = false) {
                    model.updateBotEditField(instructions = it)
                }
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("开启此 Bot 的好奇心心跳", color = c.ink, fontSize = 14.sp)
                        Text("闲置时允许这位 Bot 主动向你发问或发起探索", color = c.muted, fontSize = 11.sp)
                    }
                    Switch(checked = s.editBotCuriosity, onCheckedChange = { model.updateBotEditField(curiosity = it) })
                }
            }
            PulseAction(if (s.savingBot) "保存中…" else if (isNew) "创建此 Bot" else "保存修改") {
                focus.clearFocus(); model.saveBotEditor()
            }
            if (!isNew && s.editingBotId != null) {
                if (s.primary != s.editingBotId) {
                    PulseAction("将此 Bot 设为主助理", filled = false) { model.setPrimaryBot(s.editingBotId) }
                }
                if (s.bots.size > 1 && s.primary != s.editingBotId) {
                    PulseAction("删除此 Bot", filled = false) { model.deleteBot(s.editingBotId) }
                }
            }
            Spacer(Modifier.height(30.dp))
        }
    }
}

// --- 3. Native Markdown Vault Browser & Editor Panel ---
@Composable
private fun NativeVaultPanel(model: NativePulseModel, modifier: Modifier) {
    val s = model.ui; val c = LocalPulsePalette.current; val focus = LocalFocusManager.current
    Column(modifier.background(c.bg)) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
            PulseRoundButton("back", "返回", { focus.clearFocus(); model.closePanel() })
            Column(Modifier.padding(start = 12.dp).weight(1f)) {
                Text("透明记忆库 (Obsidian Vault)", color = c.ink, fontSize = 17.sp, fontWeight = FontWeight.SemiBold)
                Text(s.selectedVaultPath, color = c.muted, fontSize = 11.sp)
            }
            Text(
                if (s.vaultSaving) "保存中…" else "保存修改",
                Modifier.clip(RoundedCornerShape(18.dp)).background(c.ink).clickable(enabled = !s.vaultSaving) { focus.clearFocus(); model.saveVaultFile() }.padding(horizontal = 14.dp, vertical = 9.dp),
                color = c.bg, fontSize = 13.sp, fontWeight = FontWeight.Medium
            )
        }
        LazyRow(contentPadding = PaddingValues(horizontal = 18.dp, vertical = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            items(s.vaultFiles) { path ->
                val selected = path == s.selectedVaultPath
                Text(
                    path,
                    Modifier.clip(RoundedCornerShape(12.dp))
                        .background(if (selected) c.ink else c.surface)
                        .border(1.dp, if (selected) c.ink else c.line, RoundedCornerShape(12.dp))
                        .clickable { model.selectVaultFile(path) }
                        .padding(horizontal = 12.dp, vertical = 8.dp),
                    color = if (selected) c.bg else c.ink, fontSize = 12.sp
                )
            }
        }
        Box(Modifier.weight(1f).fillMaxWidth().padding(horizontal = 18.dp, vertical = 10.dp).clip(RoundedCornerShape(18.dp)).background(c.surface).padding(16.dp)) {
            if (s.vaultLoading) {
                Text("正在读取 Markdown…", color = c.muted, fontSize = 14.sp)
            } else {
                BasicTextField(
                    value = s.vaultContent,
                    onValueChange = { model.updateVaultContent(it) },
                    modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()),
                    textStyle = TextStyle(color = c.ink, fontSize = 14.sp, lineHeight = 22.sp),
                    cursorBrush = SolidColor(c.accent)
                )
            }
        }
    }
}

@Composable
private fun PulseInputField(label: String, value: String, placeholder: String, singleLine: Boolean = true, onChange: (String) -> Unit) {
    val c = LocalPulsePalette.current
    Column(Modifier.fillMaxWidth()) {
        Text(label, color = c.muted, fontSize = 13.sp, fontWeight = FontWeight.Medium, modifier = Modifier.padding(bottom = 6.dp))
        BasicTextField(
            value = value,
            onValueChange = onChange,
            singleLine = singleLine,
            textStyle = TextStyle(color = c.ink, fontSize = 15.5.sp, lineHeight = 22.sp),
            cursorBrush = SolidColor(c.accent),
            modifier = Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(c.fill.copy(alpha = 0.75f)).padding(horizontal = 14.dp, vertical = 12.dp),
            decorationBox = { inner ->
                Box(Modifier.heightIn(min = if (singleLine) 22.dp else 88.dp)) {
                    if (value.isEmpty()) Text(placeholder, color = c.faint, fontSize = 15.5.sp)
                    inner()
                }
            }
        )
    }
}

@Composable
private fun PulseRoundButton(icon: String, label: String, click: () -> Unit, background: Boolean = true) {
    val c = LocalPulsePalette.current; val haptics = LocalHapticFeedback.current
    Box(
        Modifier.size(38.dp).clip(CircleShape)
            .then(if (background) Modifier.background(c.surface) else Modifier)
            .clickable { haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove); click() }
            .semantics { contentDescription = label },
        contentAlignment = Alignment.Center
    ) {
        PulseIcon(icon, if (background) c.ink else c.ink.copy(alpha = 0.85f), Modifier.size(19.dp))
    }
}

internal fun nativeWikiMarkdown(text: String): String = Regex("\\[\\[([^\\]\\n]+)\\]\\]").replace(text) { match ->
    val parts = match.groupValues[1].split('|', limit = 2); val path = parts[0]
    val label = parts.getOrElse(1) { path }.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
    "[$label](#vault:${android.net.Uri.encode(path)})"
}

@Composable
private fun PulseShortcut(icon: String, label: String, modifier: Modifier, click: () -> Unit) {
    val c = LocalPulsePalette.current
    Row(modifier.clip(RoundedCornerShape(16.dp)).background(c.surface).border(1.dp, c.line, RoundedCornerShape(16.dp)).clickable(onClick = click).padding(vertical = 13.dp, horizontal = 8.dp), horizontalArrangement = Arrangement.Center, verticalAlignment = Alignment.CenterVertically) {
        PulseIcon(icon, c.muted, Modifier.size(17.dp)); Text(label, Modifier.padding(start = 7.dp), color = c.ink, fontWeight = FontWeight.Medium, fontSize = 13.sp, maxLines = 1)
    }
}

@Composable
private fun PulseSuggestion(text: String, click: () -> Unit) {
    val c = LocalPulsePalette.current
    Text(text, Modifier.clip(RoundedCornerShape(15.dp)).border(1.dp, c.line, RoundedCornerShape(15.dp)).clickable(onClick = click).padding(horizontal = 13.dp, vertical = 12.dp), color = c.muted, fontSize = 12.sp)
}

@Composable
private fun NativeCloudDesktopPanel(model: NativePulseModel, modifier: Modifier) {
    val s = model.ui; val c = LocalPulsePalette.current; val focus = LocalFocusManager.current
    var urlInput by remember(s.cloudDesktopUrl) { mutableStateOf(s.cloudDesktopUrl) }
    var typeInput by remember { mutableStateOf("") }
    Column(modifier.background(c.bg)) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
            PulseRoundButton("back", "返回", { focus.clearFocus(); model.closePanel() })
            Column(Modifier.padding(start = 12.dp).weight(1f)) {
                Text("❖ Windows 11 极简云电脑 (1C1G Virtual Box)", color = c.ink, fontSize = 16.5.sp, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(s.cloudDesktopTitle.ifBlank { s.cloudDesktopUrl.ifBlank { "1280×800 · 独立隔离环境 ~/venv · 1-RTT 极速触控" } }, color = c.muted, fontSize = 11.5.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
            Text(
                if (s.cloudDesktopLoading) "同步中…" else "↻ 刷新",
                Modifier.clip(RoundedCornerShape(14.dp)).background(c.surface)
                    .clickable { model.openCloudDesktop() }.padding(horizontal = 12.dp, vertical = 7.dp),
                color = c.accent, fontSize = 12.5.sp, fontWeight = FontWeight.SemiBold
            )
        }
        // Windows 11 1C1G Ultra-Light App Launcher Dock (Chromium completely removed!)
        LazyRow(
            contentPadding = PaddingValues(horizontal = 16.dp, vertical = 4.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            modifier = Modifier.fillMaxWidth()
        ) {
            val dockApps = listOf(
                "lite_browser" to "⚡ Edge Lite (NetSurf)",
                "dillo" to "🚀 极速浏览器 (Dillo)",
                "terminal" to "💻 Windows Terminal (venv)",
                "files" to "📁 File Explorer (文件)",
                "editor" to "📝 Notepad (编辑器)"
            )
            items(dockApps) { (appKey, label) ->
                Text(
                    label,
                    Modifier.clip(RoundedCornerShape(10.dp)).background(c.surface)
                        .clickable { focus.clearFocus(); model.cloudDesktopLaunchApp(appKey, urlInput) }
                        .padding(horizontal = 11.dp, vertical = 7.dp),
                    color = c.ink, fontSize = 12.sp, fontWeight = FontWeight.Medium
                )
            }
        }
        Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
            BasicTextField(
                value = urlInput,
                onValueChange = { urlInput = it },
                singleLine = true,
                textStyle = TextStyle(color = c.ink, fontSize = 13.5.sp),
                cursorBrush = SolidColor(c.accent),
                modifier = Modifier.weight(1f).clip(RoundedCornerShape(12.dp)).background(c.surface).padding(horizontal = 12.dp, vertical = 10.dp),
                decorationBox = { inner -> Box { if (urlInput.isEmpty()) Text("输入网址 (例如 https://news.ycombinator.com)…", color = c.faint, fontSize = 13.5.sp); inner() } }
            )
            Text(
                "前往",
                Modifier.clip(RoundedCornerShape(12.dp)).background(c.accent).clickable { focus.clearFocus(); model.cloudDesktopNavigate(urlInput) }.padding(horizontal = 15.dp, vertical = 10.dp),
                color = Color.White, fontSize = 13.5.sp, fontWeight = FontWeight.SemiBold
            )
        }
        // Strict 1280x800 (16:10) aspect-ratio viewport so touch (x, y) maps with 100% pixel accuracy!
        Box(
            Modifier.fillMaxWidth().weight(1f).padding(horizontal = 12.dp, vertical = 6.dp),
            contentAlignment = Alignment.Center
        ) {
            Box(
                Modifier.fillMaxWidth().aspectRatio(1280f / 800f)
                    .clip(RoundedCornerShape(14.dp))
                    .background(Color(0xFF1C1C1E))
                    .border(0.5.dp, c.line, RoundedCornerShape(14.dp)),
                contentAlignment = Alignment.Center
            ) {
                val frame = s.cloudDesktopFrame
                if (frame != null) {
                    BoxWithConstraints(Modifier.fillMaxSize()) {
                        val boxW = maxWidth
                        val boxH = maxHeight
                        Image(
                            bitmap = frame,
                            contentDescription = "云端桌面画面",
                            contentScale = ContentScale.FillBounds,
                            modifier = Modifier.fillMaxSize().pointerInput(Unit) {
                                detectTapGestures(
                                    onTap = { offset ->
                                        if (size.width > 0 && size.height > 0) {
                                            focus.clearFocus()
                                            model.cloudDesktopClick(offset.x / size.width.toFloat(), offset.y / size.height.toFloat(), "click")
                                        }
                                    },
                                    onDoubleTap = { offset ->
                                        if (size.width > 0 && size.height > 0) {
                                            focus.clearFocus()
                                            model.cloudDesktopClick(offset.x / size.width.toFloat(), offset.y / size.height.toFloat(), "double_click")
                                        }
                                    },
                                    onLongPress = { offset ->
                                        if (size.width > 0 && size.height > 0) {
                                            focus.clearFocus()
                                            model.cloudDesktopClick(offset.x / size.width.toFloat(), offset.y / size.height.toFloat(), "right_click")
                                        }
                                    }
                                )
                            }
                        )
                        s.cloudDesktopTapNorm?.let { (nx, ny) ->
                            Box(
                                Modifier.offset(x = boxW * nx - 14.dp, y = boxH * ny - 14.dp)
                                    .size(28.dp)
                                    .clip(CircleShape)
                                    .background(c.accent.copy(alpha = 0.28f))
                                    .border(2.dp, c.accent, CircleShape)
                            )
                        }
                    }
                } else {
                    Text(if (s.cloudDesktopLoading) "正在连接 1C1G 极简 Linux 云桌面…" else "点按上方应用图标或输入网址开启画面", color = Color(0xFFEBEBF5).copy(alpha = 0.7f), fontSize = 13.sp)
                }
            }
        }
        Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                BasicTextField(
                    value = typeInput,
                    onValueChange = { typeInput = it },
                    singleLine = true,
                    textStyle = TextStyle(color = c.ink, fontSize = 13.5.sp),
                    cursorBrush = SolidColor(c.accent),
                    modifier = Modifier.weight(1f).clip(RoundedCornerShape(12.dp)).background(c.surface).padding(horizontal = 12.dp, vertical = 10.dp),
                    decorationBox = { inner -> Box { if (typeInput.isEmpty()) Text("向云桌面当前焦点窗口发送文字或命令…", color = c.faint, fontSize = 13.5.sp); inner() } }
                )
                Text(
                    "发送并回车",
                    Modifier.clip(RoundedCornerShape(12.dp)).background(c.accent).clickable { val t = typeInput; typeInput = ""; focus.clearFocus(); model.cloudDesktopType(t) }.padding(horizontal = 14.dp, vertical = 10.dp),
                    color = Color.White, fontSize = 13.sp, fontWeight = FontWeight.SemiBold
                )
            }
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Text(
                    "▲ 上滚",
                    Modifier.weight(1f).clip(RoundedCornerShape(10.dp)).background(c.surface)
                        .clickable { model.cloudDesktopScroll(0.5f, 0.5f, "up", 4) }.padding(vertical = 8.dp),
                    color = c.ink, fontSize = 12.sp, textAlign = TextAlign.Center, fontWeight = FontWeight.Medium
                )
                Text(
                    "▼ 下滚",
                    Modifier.weight(1f).clip(RoundedCornerShape(10.dp)).background(c.surface)
                        .clickable { model.cloudDesktopScroll(0.5f, 0.5f, "down", 4) }.padding(vertical = 8.dp),
                    color = c.ink, fontSize = 12.sp, textAlign = TextAlign.Center, fontWeight = FontWeight.Medium
                )
                listOf("Enter" to "回车", "Backspace" to "退格", "Tab" to "Tab", "Escape" to "Esc").forEach { (k, label) ->
                    Text(
                        label,
                        Modifier.weight(1f).clip(RoundedCornerShape(10.dp)).background(c.surface)
                            .clickable { model.cloudDesktopKey(k) }.padding(vertical = 8.dp),
                        color = c.ink, fontSize = 12.sp, textAlign = TextAlign.Center, fontWeight = FontWeight.Medium
                    )
                }
            }
        }
    }
}

@Composable
private fun NativeSchedulesPanel(model: NativePulseModel, modifier: Modifier) {
    val s = model.ui; val c = LocalPulsePalette.current; val focus = LocalFocusManager.current
    val data = s.schedulesData
    val routines = remember(data) { (data?.optJSONArray("routines") ?: JSONArray()).objects() }
    val deadlines = remember(data) { (data?.optJSONArray("deadlines") ?: JSONArray()).objects() }

    Column(modifier.background(c.bg)) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
            PulseRoundButton("back", "返回", { focus.clearFocus(); model.closePanel() })
            Column(Modifier.padding(start = 12.dp).weight(1f)) {
                Text("定时闹钟与例行自动化", color = c.ink, fontSize = 17.sp, fontWeight = FontWeight.SemiBold)
                Text("支持精确北京时间钟点提醒、课表巡检与死线追踪", color = c.muted, fontSize = 11.sp)
            }
            Text(
                if (s.schedulesLoading) "刷新中" else "刷新",
                Modifier.clip(RoundedCornerShape(14.dp)).background(c.surface).border(1.dp, c.line, RoundedCornerShape(14.dp))
                    .clickable { model.openSchedules() }.padding(horizontal = 12.dp, vertical = 7.dp),
                color = c.ink, fontSize = 12.sp
            )
        }
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 18.dp, vertical = 10.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp)
        ) {
            // Quick Create Card
            Column(
                Modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(c.surface)
                    .border(1.dp, c.line, RoundedCornerShape(18.dp)).padding(15.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                Text("✨ 一键跟主助理定闹钟或巡检", color = c.ink, fontSize = 14.5.sp, fontWeight = FontWeight.SemiBold)
                Text("直接在聊天里告诉主助理时间与目标，它会自动调用 schedule_task 创建准点闹钟或每日例行任务。", color = c.muted, fontSize = 12.sp)
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(
                        "🌅 每天 07:30 晨间课表提醒",
                        Modifier.weight(1f).clip(RoundedCornerShape(12.dp)).background(c.fill)
                            .clickable { model.closePanel(); model.open(s.primary); model.sendQuickPrompt("帮我设置一个每天早上 07:30 的定点例行提醒，结合我的课表提醒我当天的课程和实验安排") }
                            .padding(vertical = 10.dp, horizontal = 8.dp),
                        color = c.ink, fontSize = 11.5.sp, textAlign = TextAlign.Center
                    )
                    Text(
                        "📚 晚间 21:00 查学习通作业",
                        Modifier.weight(1f).clip(RoundedCornerShape(12.dp)).background(c.fill)
                            .clickable { model.closePanel(); model.open(s.primary); model.sendQuickPrompt("帮我设置一个每天晚上 21:00 的定点例行任务，检查我的待办死线和学习通作业") }
                            .padding(vertical = 10.dp, horizontal = 8.dp),
                        color = c.ink, fontSize = 11.5.sp, textAlign = TextAlign.Center
                    )
                }
            }

            // Routines List
            Column(
                Modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(c.surface)
                    .border(1.dp, c.line, RoundedCornerShape(18.dp)).padding(15.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                Text("常驻例行任务 (${routines.size})", color = c.ink, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
                if (routines.isEmpty()) {
                    Text("暂无常驻例行任务。可在聊天中随时让 Bot 添加，或在成果卡片点击「✓ 以后接着干」。", color = c.muted, fontSize = 12.5.sp)
                } else {
                    routines.forEach { r ->
                        val id = r.optString("id")
                        val goal = r.optString("goal")
                        val enabled = r.optBoolean("enabled", true)
                        val dailyTime = r.optString("daily_time")
                        val hours = r.optInt("interval_hours", 24)
                        val botName = model.bot(r.optString("bot_id", s.primary))?.name ?: r.optString("bot_id", "主控")
                        Column(
                            Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(c.fill)
                                .padding(12.dp),
                            verticalArrangement = Arrangement.spacedBy(6.dp)
                        ) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text(
                                    if (dailyTime.isNotBlank()) "⏰ 每天 $dailyTime" else "🔄 每 ${hours}h",
                                    color = if (enabled) c.accent else c.faint,
                                    fontSize = 12.sp,
                                    fontWeight = FontWeight.SemiBold
                                )
                                Text(" · $botName", color = c.muted, fontSize = 11.5.sp, modifier = Modifier.weight(1f))
                                Text(
                                    "立即执行",
                                    Modifier.padding(end = 8.dp).clip(RoundedCornerShape(8.dp)).background(c.surface)
                                        .clickable { model.runRoutineItem(id) }.padding(horizontal = 8.dp, vertical = 4.dp),
                                    color = c.accent, fontSize = 11.sp
                                )
                                Switch(checked = enabled, onCheckedChange = { model.toggleRoutineItem(id, it) })
                            }
                            Text(goal, color = c.ink, fontSize = 13.5.sp, lineHeight = 19.sp)
                        }
                    }
                }
            }

            // Deadlines List
            Column(
                Modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(c.surface)
                    .border(1.dp, c.line, RoundedCornerShape(18.dp)).padding(15.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                Text("已登记截止时间 / 死线 (${deadlines.size})", color = c.ink, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
                if (deadlines.isEmpty()) {
                    Text("暂无待办死线。", color = c.muted, fontSize = 12.5.sp)
                } else {
                    deadlines.forEach { d ->
                        val title = d.optString("title")
                        val dueAt = d.optDouble("due_at", 0.0)
                        val notified = d.optBoolean("notified", false)
                        Row(
                            Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(c.fill).padding(12.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Column(Modifier.weight(1f)) {
                                Text(title, color = c.ink, fontSize = 13.5.sp, fontWeight = FontWeight.Medium)
                                if (dueAt > 0) {
                                    Text("截止：${model.day(dueAt)} ${model.time(dueAt)}", color = c.muted, fontSize = 11.5.sp)
                                }
                            }
                            Text(
                                if (notified) "已提醒" else "待提醒",
                                color = if (notified) c.faint else c.green,
                                fontSize = 11.5.sp,
                                fontWeight = FontWeight.Medium
                            )
                        }
                    }
                }
            }
            Spacer(Modifier.height(24.dp))
        }
    }
}

@Composable
private fun PulseAction(text: String, filled: Boolean = true, click: () -> Unit) {
    val c = LocalPulsePalette.current
    Text(
        text,
        Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp))
            .background(if (filled) c.accent else c.surface)
            .clickable(onClick = click).padding(horizontal = 16.dp, vertical = 14.dp),
        color = if (filled) Color.White else c.ink,
        fontSize = 15.sp,
        fontWeight = FontWeight.SemiBold,
        textAlign = TextAlign.Center
    )
}
