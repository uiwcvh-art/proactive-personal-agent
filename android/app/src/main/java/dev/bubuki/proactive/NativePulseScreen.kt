package dev.bubuki.proactive

import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.animateContentSize
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.CubicBezierEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.animation.scaleOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.animation.slideInHorizontally
import androidx.compose.animation.slideOutHorizontally
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.*
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectVerticalDragGestures
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.ui.graphics.ImageBitmap
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
import androidx.compose.ui.composed
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
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import dev.bubuki.proactive.markdown.MarkdownText
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.layout.onSizeChanged
import kotlinx.coroutines.launch
import org.json.JSONArray
import kotlin.math.abs
import kotlin.math.roundToInt

/**
 * Apple HIG + Telegram iOS Color System (Strictly aligned with `.agents/skills/apple-hig-design/SKILL.md`):
 * - Dark Mode: True OLED Black (`#000000`) grouped canvas, Telegram Night chat canvas (`#0E1621`),
 *   elevated surface (`#1C1C1E`), tertiary fill (`#2C2C2E`), hairline (`#2C2C2E`), iOS System Blue (`#0A84FF`),
 *   Telegram Night outgoing bubble (`#2B5278`), incoming bubble (`#182533`).
 * - Light Mode: iOS Grouped (`#F2F2F7`), Telegram Classic chat canvas (`#E4ECF2`),
 *   pure white surface (`#FFFFFF`), fill (`#EEEEF3`), hairline (`#D8D8DC`), iOS System Blue (`#007AFF`).
 */
private data class PulsePalette(
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

private val LocalPulsePalette = staticCompositionLocalOf { pulsePalette(true) }

private fun pulsePalette(dark: Boolean) = if (dark) PulsePalette(
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
private fun Modifier.iosPressScale(
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

@OptIn(ExperimentalMaterial3Api::class, ExperimentalFoundationApi::class)
@Composable
internal fun NativePulseScreen(model: NativePulseModel) {
    val state = model.ui
    val colors = remember(state.dark) { pulsePalette(state.dark) }
    val progress = remember { Animatable(if (state.chatOpen) 1f else 0f) }
    val panelProgress = remember { Animatable(if (state.activePanel != null) 1f else 0f) }
    var retainedPanel by remember { mutableStateOf(state.activePanel) }
    if (state.activePanel != null) {
        retainedPanel = state.activePanel
    }
    var panelDragBack by remember { mutableStateOf<Float?>(null) }
    val focus = LocalFocusManager.current
    val haptics = LocalHapticFeedback.current
    val modalScope = rememberCoroutineScope()
    var gestureEnd by remember { mutableStateOf<Float?>(null) }
    var composerHeight by remember { mutableIntStateOf(0) }
    val snackbar = remember { SnackbarHostState() }

    LaunchedEffect(state.toastId) {
        if (state.toast.isNotBlank()) snackbar.showSnackbar(state.toast)
    }

    // 1st-level -> Chat iOS Spring Push/Pop transition
    LaunchedEffect(state.chatOpen, state.motion, model.backSettlement) {
        model.navigationRunning = true
        try {
            (model.backFrom ?: gestureEnd)?.let {
                progress.snapTo(it)
                gestureEnd = null
                model.backFrom = null
            }
            val target = if (state.chatOpen) 1f else 0f
            if (!state.motion) progress.snapTo(target)
            else progress.animateTo(
                target,
                spring(dampingRatio = 0.88f, stiffness = 390f)
            )
        } finally {
            model.navigationRunning = false
        }
    }

    // 2nd-level Panel (`bot_edit`, `settings`, `vault`, `cloud_desktop`, `schedules`) iOS Spring Push/Pop transition
    LaunchedEffect(state.activePanel, state.motion) {
        val target = if (state.activePanel != null) 1f else 0f
        if (!state.motion) {
            panelProgress.snapTo(target)
        } else {
            panelProgress.animateTo(
                target,
                spring(dampingRatio = 0.86f, stiffness = 380f)
            )
        }
        if (state.activePanel == null) {
            retainedPanel = null
        }
    }

    PulseTheme(dark = state.dark, reduceMotion = !state.motion) {
        val sysHaptics = LocalHapticFeedback.current
        val effectiveHaptics = if (state.haptics) sysHaptics else QuietHaptics
        val mdColors = if (state.dark) DarkColors else LightColors
        CompositionLocalProvider(
            LocalPulsePalette provides colors,
            LocalPulseColors provides mdColors,
            LocalHapticFeedback provides effectiveHaptics
        ) {
            Box(
                Modifier.fillMaxSize()
                    .background(colors.bg)
                    .windowInsetsPadding(WindowInsets.systemBars.union(WindowInsets.ime)),
                contentAlignment = Alignment.TopCenter
            ) {
                BoxWithConstraints(
                    Modifier.fillMaxSize()
                        .clipToBounds()
                ) {
                    val isTabletDualPane = maxWidth >= 600.dp
                    val shownPanel = state.activePanel ?: retainedPanel

                    if (isTabletDualPane) {
                        Row(Modifier.fillMaxSize().background(colors.bg)) {
                            NativeHome(
                                model,
                                Modifier.width(330.dp).fillMaxHeight()
                            )
                            Box(Modifier.width(0.5.dp).fillMaxHeight().background(colors.line))
                            Box(Modifier.weight(1f).fillMaxHeight().background(colors.bg)) {
                                val activeBot = model.bot() ?: state.bots.firstOrNull()
                                if (activeBot != null) {
                                    NativeChat(
                                        model,
                                        activeBot,
                                        Modifier.fillMaxSize()
                                    ) { composerHeight = it }
                                }
                                if (shownPanel != null) {
                                    Box(Modifier.fillMaxSize().background(colors.bg)) {
                                        when (shownPanel) {
                                            "settings" -> NativeSettingsPanel(model, Modifier.fillMaxSize())
                                            "bot_edit" -> NativeBotEditorPanel(model, Modifier.fillMaxSize())
                                            "vault" -> NativeVaultPanel(model, Modifier.fillMaxSize())
                                            "cloud_desktop" -> NativeCloudDesktopPanel(model, Modifier.fillMaxSize())
                                            "schedules" -> NativeSchedulesPanel(model, Modifier.fillMaxSize())
                                        }
                                    }
                                }
                            }
                        }
                        InAppFloatingVirtualDisplay()
                    } else {
                        BoxWithConstraints(
                            Modifier.fillMaxHeight()
                                .widthIn(max = 700.dp)
                                .fillMaxWidth()
                                .align(Alignment.TopCenter)
                                .clipToBounds()
                                .pointerInput(state.chatOpen, state.menu != null, state.activePanel != null) {
                                    if ((!state.chatOpen && state.activePanel == null) || state.menu != null) return@pointerInput
                                    awaitEachGesture {
                                        val down = awaitFirstDown(requireUnconsumed = false)
                                        if (down.position.x > 28.dp.toPx()) return@awaitEachGesture
                                        var started = false
                                        var dx = 0f
                                        var rejected = false
                                        do {
                                            val event = awaitPointerEvent()
                                            val change = event.changes.find { it.id == down.id } ?: break
                                            dx = (change.position.x - down.position.x).coerceAtLeast(0f)
                                            val dy = abs(change.position.y - down.position.y)
                                            if (!started && (dx > viewConfiguration.touchSlop || dy > viewConfiguration.touchSlop)) {
                                                if (dy >= dx) {
                                                    rejected = true
                                                    break
                                                }
                                                started = true
                                                haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                                            }
                                            if (started) {
                                                change.consume()
                                                val frac = (dx / size.width).coerceIn(0f, 1f)
                                                if (state.activePanel != null) {
                                                    panelDragBack = frac
                                                } else {
                                                    model.predictiveBack = frac
                                                }
                                            }
                                            if (!change.pressed) break
                                        } while (true)
                                        if (started && !rejected) {
                                            if (state.activePanel != null) {
                                                val dragged = panelDragBack ?: 0f
                                                panelDragBack = null
                                                if (dx > size.width * 0.26f) {
                                                    focus.clearFocus()
                                                    modalScope.launch { panelProgress.snapTo(1f - dragged) }
                                                    model.closePanel()
                                                } else {
                                                    modalScope.launch {
                                                        panelProgress.snapTo(1f - dragged)
                                                        panelProgress.animateTo(1f, spring(dampingRatio = 0.85f, stiffness = 420f))
                                                    }
                                                }
                                            } else {
                                                gestureEnd = 1f - (model.predictiveBack ?: 0f)
                                                model.predictiveBack = null
                                                if (dx > size.width * 0.26f) {
                                                    focus.clearFocus()
                                                    model.back()
                                                } else {
                                                    model.backSettlement++
                                                }
                                            }
                                        }
                                    }
                                }
                        ) {
                            val width = constraints.maxWidth.toFloat()
                            val chatFraction = model.predictiveBack?.let { 1f - it } ?: progress.value
                            val pFraction = panelDragBack?.let { 1f - it } ?: panelProgress.value

                            Box(
                                Modifier.fillMaxSize().graphicsLayer {
                                    translationX = -width * 0.24f * pFraction
                                }
                            ) {
                                NativeHome(
                                    model,
                                    Modifier.fillMaxSize()
                                        .graphicsLayer {
                                            translationX = -width * 0.26f * chatFraction
                                            alpha = (1f - 0.18f * chatFraction).coerceIn(0.75f, 1f)
                                        }
                                        .then(if (state.chatOpen || state.activePanel != null) Modifier.clearAndSetSemantics { } else Modifier)
                                )
                                val bot = model.bot()
                                if (bot != null && (chatFraction > 0.001f || state.chatOpen)) {
                                    NativeChat(
                                        model,
                                        bot,
                                        Modifier.fillMaxSize()
                                            .graphicsLayer {
                                                translationX = width * (1f - chatFraction)
                                                alpha = if (chatFraction <= 0.001f) 0f else 1f
                                            }
                                            .then(if (!state.chatOpen || state.activePanel != null) Modifier.clearAndSetSemantics { } else Modifier)
                                    ) { composerHeight = it }
                                }
                                if (pFraction > 0.01f) {
                                    Box(
                                        Modifier.fillMaxSize()
                                            .background(Color.Black.copy(alpha = 0.28f * pFraction))
                                    )
                                }
                            }

                            if (shownPanel != null && pFraction > 0.001f) {
                                Box(
                                    Modifier.fillMaxSize()
                                        .graphicsLayer {
                                            translationX = width * (1f - pFraction)
                                            shadowElevation = 24f * pFraction
                                        }
                                ) {
                                    when (shownPanel) {
                                        "settings" -> NativeSettingsPanel(model, Modifier.fillMaxSize())
                                        "bot_edit" -> NativeBotEditorPanel(model, Modifier.fillMaxSize())
                                        "vault" -> NativeVaultPanel(model, Modifier.fillMaxSize())
                                        "cloud_desktop" -> NativeCloudDesktopPanel(model, Modifier.fillMaxSize())
                                        "schedules" -> NativeSchedulesPanel(model, Modifier.fillMaxSize())
                                    }
                                }
                            }

                            InAppFloatingVirtualDisplay()
                        }
                    }
                }
                val toastBottom = if (state.chatOpen && state.activePanel == null) with(LocalDensity.current) { composerHeight.toDp() } + 12.dp else 20.dp
                SnackbarHost(snackbar, Modifier.align(Alignment.BottomCenter).padding(start = 20.dp, end = 20.dp, bottom = toastBottom))
            }
            state.menu?.let { menu ->
                if (!state.motion) {
                    Popup(alignment = Alignment.BottomCenter, onDismissRequest = model::dismissMenu, properties = PopupProperties(focusable = true, clippingEnabled = false)) {
                        Box(Modifier.fillMaxSize().background(Color.Black.copy(alpha = .32f)).clickable { model.dismissMenu() }, contentAlignment = Alignment.BottomCenter) {
                            Surface(
                                Modifier.fillMaxWidth().heightIn(max = (LocalConfiguration.current.screenHeightDp * .85f).dp).windowInsetsPadding(WindowInsets.navigationBars),
                                shape = RoundedCornerShape(topStart = 28.dp, topEnd = 28.dp), color = colors.surface
                            ) {
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
                                model.dismissMenu()
                                next()
                            }
                        }
                    }
                    ModalBottomSheet(
                        onDismissRequest = model::dismissMenu,
                        sheetState = modalState,
                        containerColor = colors.surface,
                        contentColor = colors.ink,
                        shape = RoundedCornerShape(topStart = 28.dp, topEnd = 28.dp),
                        tonalElevation = 0.dp
                    ) {
                        NativeMenuContent(menu, closing, { closeThen {} }) { item -> closeThen(item.run) }
                    }
                }
            }
        }
    }
}

@Composable
private fun NativeMenuContent(menu: PulseMenu, closing: Boolean, close: () -> Unit, select: (PulseMenuItem) -> Unit) {
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
private fun NativeHome(model: NativePulseModel, modifier: Modifier) {
    val s = model.ui
    val c = LocalPulsePalette.current
    val allRows = model.visibleBots()
    val apiReady = s.hasApiKey && s.defaultModel.isNotBlank()
    val phoneState by RootPhoneHarness.changes.collectAsState()
    val focus = LocalFocusManager.current
    val haptics = LocalHapticFeedback.current
    var showQuickDrawer by rememberSaveable { mutableStateOf(false) }
    var activeFilter by rememberSaveable { mutableStateOf("all") } // "all" | "unread" | "pinned" | "working"

    val rows = remember(allRows, activeFilter, s.unread, s.workingBot, s.streamBotId) {
        when (activeFilter) {
            "unread" -> allRows.filter { b -> s.messages.any { it.botId == b.id && it.id in s.unread } }
            "pinned" -> allRows.filter { it.pinned || it.id == s.primary }
            "working" -> allRows.filter { it.id == s.workingBot || it.id == s.streamBotId || it.id == s.busyBot }
            else -> allRows
        }
    }

    val pulseInfinite = rememberInfiniteTransition(label = "home_online_pulse")
    val onlineDotAlpha by pulseInfinite.animateFloat(
        initialValue = 0.55f,
        targetValue = 1.0f,
        animationSpec = infiniteRepeatable(tween(1100), RepeatMode.Reverse),
        label = "online_dot"
    )

    Column(
        modifier
            .background(c.bg)
            .pointerInput(Unit) {
                var accY = 0f
                detectVerticalDragGestures(
                    onVerticalDrag = { _, dragAmount -> accY += dragAmount },
                    onDragEnd = {
                        if (accY > 64f && !showQuickDrawer) {
                            haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                            showQuickDrawer = true
                        } else if (accY < -54f && showQuickDrawer) {
                            haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                            showQuickDrawer = false
                        }
                        accY = 0f
                    }
                )
            }
    ) {
        // 1. Telegram iOS Navigation Header Bar
        Surface(
            color = c.surface.copy(alpha = 0.96f),
            tonalElevation = 0.dp
        ) {
            Column(Modifier.fillMaxWidth()) {
                Row(
                    Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    // Left: Connection Status & Pull-down Drawer Trigger Pill
                    Row(
                        Modifier.clip(CircleShape)
                            .background(c.fill.copy(alpha = 0.78f))
                            .iosPressScale(0.95f) {
                                haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                                showQuickDrawer = !showQuickDrawer
                            }
                            .padding(horizontal = 11.dp, vertical = 6.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Box(
                            Modifier.size(7.5.dp)
                                .clip(CircleShape)
                                .background(if (s.online) c.green.copy(alpha = onlineDotAlpha) else c.warn)
                        )
                        Text(
                            if (s.online) "工作台 · ${s.latency}ms" else "离线 · 工作台",
                            Modifier.padding(start = 6.dp),
                            color = c.ink,
                            fontSize = 12.5.sp,
                            fontWeight = FontWeight.Medium,
                            style = TextStyle(fontFeatureSettings = "tnum")
                        )
                        PulseIcon(
                            if (showQuickDrawer) "chevron_up" else "chevron_down",
                            c.muted,
                            Modifier.padding(start = 4.dp).size(13.dp)
                        )
                    }

                    Spacer(Modifier.weight(1f))

                    // Center: Telegram iOS Title ("DSB")
                    Text(
                        if (s.workingBot != null || s.streamBotId != null) "执行中…" else "DSB",
                        color = c.ink,
                        fontSize = 17.5.sp,
                        fontWeight = FontWeight.SemiBold,
                        letterSpacing = (-0.3).sp
                    )

                    Spacer(Modifier.weight(1f))

                    // Right: Quick Day/Night Toggle + Create Bot + Settings
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
                        PulseRoundButton(
                            icon = "moon",
                            label = if (s.dark) "切换日间模式" else "切换夜间模式",
                            click = { model.toggleAppearanceQuick() },
                            background = true
                        )
                        PulseRoundButton("plus", "新建助理", { model.openBotEditor(null) }, background = true)
                        PulseRoundButton("settings", "设置中心", { model.openSettings() }, background = true)
                    }
                }

                // 2. Always-Present Telegram iOS Search Bar + Filter Capsules
                Column(Modifier.fillMaxWidth().padding(start = 14.dp, end = 14.dp, bottom = 10.dp)) {
                    Row(
                        Modifier.fillMaxWidth()
                            .clip(RoundedCornerShape(11.dp))
                            .background(c.fill.copy(alpha = 0.85f))
                            .padding(horizontal = 12.dp, vertical = 8.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        PulseIcon("search", c.faint, Modifier.size(16.dp))
                        BasicTextField(
                            value = s.query,
                            onValueChange = { model.search(it.ifEmpty { null }) },
                            modifier = Modifier.weight(1f).padding(horizontal = 8.dp).semantics { contentDescription = "搜索助手或对话" },
                            textStyle = TextStyle(color = c.ink, fontSize = 15.5.sp),
                            singleLine = true,
                            cursorBrush = SolidColor(c.accent),
                            decorationBox = { inner ->
                                Box {
                                    if (s.query.isBlank()) Text("搜索助手、消息或记忆…", color = c.faint, fontSize = 15.5.sp)
                                    inner()
                                }
                            }
                        )
                        if (s.query.isNotEmpty()) {
                            Box(
                                Modifier.size(18.dp).clip(CircleShape).background(c.faint.copy(alpha = 0.35f))
                                    .clickable { focus.clearFocus(); model.search(null) },
                                contentAlignment = Alignment.Center
                            ) {
                                PulseIcon("close", c.surface, Modifier.size(11.dp))
                            }
                        }
                    }

                    // Telegram Folder / Filter Tabs (全部 · 未读 · 置顶 · 运行中 · 记忆库)
                    LazyRow(
                        Modifier.fillMaxWidth().padding(top = 8.dp),
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        val unreadTotal = s.unread.size
                        val filterItems = listOf(
                            "all" to "全部 (${allRows.size})",
                            "unread" to if (unreadTotal > 0) "未读 ($unreadTotal)" else "未读",
                            "pinned" to "置顶",
                            "working" to "运行中"
                        )
                        items(filterItems) { (key, label) ->
                            val selected = activeFilter == key
                            val bgCol by animateColorAsState(
                                if (selected) c.accent else c.fill.copy(alpha = 0.7f),
                                animationSpec = spring(stiffness = 500f),
                                label = "filter_bg"
                            )
                            Text(
                                label,
                                Modifier.clip(CircleShape)
                                    .background(bgCol)
                                    .iosPressScale(0.95f) {
                                        haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                                        activeFilter = key
                                    }
                                    .padding(horizontal = 12.dp, vertical = 5.dp),
                                color = if (selected) Color.White else c.muted,
                                fontSize = 12.5.sp,
                                fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Medium,
                                style = TextStyle(fontFeatureSettings = "tnum")
                            )
                        }
                    }

                    // 3. Spring-Animated Pull-Down Telegram Mini-App Workspace Drawer
                    AnimatedVisibility(
                        visible = showQuickDrawer,
                        enter = expandVertically(spring(dampingRatio = 0.78f, stiffness = 380f)) + fadeIn(tween(180)),
                        exit = shrinkVertically(spring(dampingRatio = 0.85f, stiffness = 440f)) + fadeOut(tween(140))
                    ) {
                        Column(
                            Modifier.fillMaxWidth()
                                .padding(top = 10.dp)
                                .clip(RoundedCornerShape(16.dp))
                                .background(c.fill.copy(alpha = 0.65f))
                                .padding(10.dp),
                            verticalArrangement = Arrangement.spacedBy(8.dp)
                        ) {
                            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                WorkspaceQuickTile(
                                    icon = "screen",
                                    title = "手机虚拟屏",
                                    subtitle = if (phoneState.displayCreated) "运行中 #${phoneState.displayId}" else "独立悬浮小窗",
                                    tint = c.green,
                                    modifier = Modifier.weight(1f)
                                ) { showQuickDrawer = false; model.launchFloatingVirtualDisplay() }

                                WorkspaceQuickTile(
                                    icon = "cloud",
                                    title = "云端电脑桌面",
                                    subtitle = "浏览器与终端",
                                    tint = c.accent,
                                    modifier = Modifier.weight(1f)
                                ) { showQuickDrawer = false; model.openCloudDesktop() }
                            }
                            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                WorkspaceQuickTile(
                                    icon = "clock",
                                    title = "定时与哨兵",
                                    subtitle = "闹钟与0-Token巡检",
                                    tint = c.amber,
                                    modifier = Modifier.weight(1f)
                                ) { showQuickDrawer = false; model.openSchedules() }

                                WorkspaceQuickTile(
                                    icon = "vault",
                                    title = "透明记忆库",
                                    subtitle = "Obsidian Markdown",
                                    tint = c.accent,
                                    modifier = Modifier.weight(1f)
                                ) { showQuickDrawer = false; model.openVault("INDEX.md") }
                            }
                        }
                    }
                }
                HorizontalDivider(color = c.line.copy(alpha = 0.7f), thickness = 0.5.dp)
            }
        }

        // 4. Full-Bleed Telegram iOS Conversation List (Edge-to-edge rows + 78dp inset hairlines + real-time swipe physics)
        LazyColumn(
            Modifier.fillMaxSize().background(c.surface),
            contentPadding = PaddingValues(bottom = 32.dp)
        ) {
            if (phoneState.displayCreated) {
                item("active_vd_banner") {
                    Row(
                        Modifier.fillMaxWidth()
                            .background(c.green.copy(alpha = 0.14f))
                            .clickable { model.launchFloatingVirtualDisplay() }
                            .padding(horizontal = 16.dp, vertical = 9.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Box(Modifier.size(7.5.dp).clip(CircleShape).background(c.green))
                        Text(
                            "手机虚拟屏正在后台运行 (#${phoneState.displayId}) · 点按展开悬浮窗",
                            Modifier.weight(1f).padding(start = 9.dp),
                            color = c.green,
                            fontSize = 13.sp,
                            fontWeight = FontWeight.SemiBold
                        )
                        PulseIcon("next", c.green, Modifier.size(14.dp))
                    }
                    HorizontalDivider(color = c.line.copy(alpha = 0.6f), thickness = 0.5.dp)
                }
            }

            if (!apiReady) {
                item("api_banner") {
                    Row(
                        Modifier.fillMaxWidth()
                            .background(c.accent.copy(alpha = 0.12f))
                            .clickable { model.openSettings() }
                            .padding(horizontal = 16.dp, vertical = 11.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Box(Modifier.size(8.dp).clip(CircleShape).background(c.accent))
                        Text(
                            "点击配置 API Key 并同步可用模型 (/models)",
                            Modifier.weight(1f).padding(start = 10.dp),
                            color = c.accent,
                            fontSize = 13.5.sp,
                            fontWeight = FontWeight.SemiBold
                        )
                        PulseIcon("next", c.accent, Modifier.size(15.dp))
                    }
                    HorizontalDivider(color = c.line.copy(alpha = 0.6f), thickness = 0.5.dp)
                }
            }

            if (rows.isEmpty()) {
                item("empty_rows") {
                    Column(
                        Modifier.fillMaxWidth().padding(vertical = 56.dp, horizontal = 24.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        Text("暂无匹配的对话", color = c.ink, fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
                        Text("可切换上方筛选标签或清空搜索关键词", color = c.muted, fontSize = 13.5.sp)
                    }
                }
            } else {
                items(rows.size, key = { idx -> rows[idx].id }) { idx ->
                    val bot = rows[idx]
                    if (idx > 0) {
                        HorizontalDivider(
                            Modifier.padding(start = 82.dp),
                            color = c.line.copy(alpha = 0.65f),
                            thickness = 0.5.dp
                        )
                    }
                    NativeBotRow(model, bot)
                }
                item("bottom_hairline") {
                    HorizontalDivider(color = c.line.copy(alpha = 0.55f), thickness = 0.5.dp)
                    Row(
                        Modifier.fillMaxWidth()
                            .clickable { showQuickDrawer = !showQuickDrawer }
                            .padding(vertical = 14.dp),
                        horizontalArrangement = Arrangement.Center,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text(
                            "左滑对话打开操作抽屉 · 右滑对话置顶/取消置顶 · 下拉展开工作台",
                            color = c.faint,
                            fontSize = 11.5.sp
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun WorkspaceQuickTile(
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
private fun NativeBotRow(model: NativePulseModel, bot: PulseBot) {
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

@Composable
private fun NativeChat(model: NativePulseModel, bot: PulseBot, modifier: Modifier, onComposerHeight: (Int) -> Unit) {
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
private fun NativeChatRoomViewport(model: NativePulseModel, bot: PulseBot) {
    val s = model.ui
    val c = LocalPulsePalette.current
    val messages = remember(s.messages, bot.id) { s.messages.filter { it.botId == bot.id } }
    val list = rememberLazyListState()
    val chatScope = rememberCoroutineScope()
    var initialScrollDoneForBot by remember(bot.id) { mutableStateOf(false) }
    var lastMsgCount by remember(bot.id) { mutableIntStateOf(0) }

    val lastMsg = messages.lastOrNull()
    val duplicateWithTail = lastMsg != null && lastMsg.role == "assistant" && s.streamText.isNotBlank() &&
        lastMsg.text.trim() == s.streamText.trim() && s.workingBot != bot.id && s.streamBotId != bot.id && s.streamTool.isBlank() && s.streamTrace.isEmpty()
    val isLiveHere = !duplicateWithTail && (s.streamBotId == bot.id || s.workingBot == bot.id || s.busyBot == bot.id || (s.streamBotId == bot.id && s.streamTrace.isNotEmpty()))

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

    LaunchedEffect(messages.size, messages.lastOrNull()?.id, bot.id, isLiveHere, s.chatOpen) {
        if (!s.chatOpen) return@LaunchedEffect
        val anchorIdx = (messages.size + (if (isLiveHere) 1 else 0)).coerceAtLeast(0)
        val userSentNew = messages.lastOrNull()?.role == "user" && messages.size != lastMsgCount
        if (!initialScrollDoneForBot) {
            if (messages.isNotEmpty() || isLiveHere) {
                runCatching { list.scrollToItem(anchorIdx) }
                kotlinx.coroutines.yield()
                runCatching { list.scrollToItem(anchorIdx) }
                initialScrollDoneForBot = true
            }
        } else if (userSentNew || nearTail) {
            runCatching { list.scrollToItem(anchorIdx) }
        }
        lastMsgCount = messages.size
    }

    LaunchedEffect(s.streamText.length / 80, s.activeTypewriterChars / 48, s.streamTrace.size, s.streamTool) {
        if ((isLiveHere || !s.activeTypewriterMsgId.isNullOrBlank()) && nearTail) {
            val anchorIdx = (messages.size + (if (isLiveHere) 1 else 0)).coerceAtLeast(0)
            runCatching { list.scrollToItem(anchorIdx) }
        }
    }

    Box(Modifier.fillMaxSize()) {
        if (messages.isEmpty() && !isLiveHere) {
            // Telegram Bot Welcome Card
            Column(
                Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 24.dp, vertical = 28.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center
            ) {
                Column(
                    Modifier.fillMaxWidth()
                        .clip(RoundedCornerShape(22.dp))
                        .background(c.surface)
                        .padding(horizontal = 20.dp, vertical = 24.dp),
                    horizontalAlignment = Alignment.CenterHorizontally
                ) {
                    PulseAvatar(bot, s, diameter = 72.dp, showStatusDot = true, ringColor = c.surface)
                    Text(
                        bot.name,
                        Modifier.padding(top = 14.dp),
                        color = c.ink,
                        fontWeight = FontWeight.Bold,
                        fontSize = 21.sp,
                        letterSpacing = (-0.4).sp
                    )
                    Text(
                        bot.description,
                        Modifier.padding(top = 6.dp, bottom = 20.dp),
                        color = c.muted,
                        fontSize = 14.5.sp,
                        textAlign = TextAlign.Center,
                        lineHeight = 20.sp
                    )
                    val starters = listOf(
                        "打开学习通看一眼有没有未交作业" to "帮我打开学习通看一眼最近有没有未交的作业或通知",
                        "看看我明天有哪些课和实验要准备" to "结合我的课表看看明天有哪些课和实验，帮我列一下准备事项",
                        "定个明天早上 7:30 的早起提醒闹钟" to "帮我定一个明天早上 07:30 的闹钟，提醒我看早八课表",
                        "搜索一下今天最新的 AI / 大模型进展" to "帮我搜索一下今天最新的 AI 与大模型实用进展并总结重点"
                    )
                    Column(
                        Modifier.fillMaxWidth()
                            .clip(RoundedCornerShape(14.dp))
                            .background(c.fill.copy(alpha = 0.65f))
                    ) {
                        starters.forEachIndexed { idx, (label, prompt) ->
                            if (idx > 0) {
                                HorizontalDivider(Modifier.padding(start = 14.dp), color = c.line.copy(alpha = 0.6f), thickness = 0.5.dp)
                            }
                            Row(
                                Modifier.fillMaxWidth()
                                    .iosPressScale(0.98f) { model.sendQuickPrompt(prompt) }
                                    .padding(horizontal = 14.dp, vertical = 13.dp),
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Text(label, Modifier.weight(1f), color = c.ink, fontSize = 14.5.sp)
                                PulseIcon("next", c.faint, Modifier.size(14.dp))
                            }
                        }
                    }
                }
            }
        } else {
            LazyColumn(
                Modifier.fillMaxSize().semantics { contentDescription = "对话消息" },
                state = list,
                contentPadding = PaddingValues(horizontal = 12.dp, vertical = 12.dp)
            ) {
                items(messages.size, key = { idx -> messages[idx].id }) { idx ->
                    val message = messages[idx]
                    val prev = if (idx > 0) messages[idx - 1] else null
                    val next = if (idx < messages.lastIndex) messages[idx + 1] else null

                    val prevCreated = prev?.created ?: 0.0
                    val showTimeHeader = idx == 0 || (message.created - prevCreated) >= 300.0

                    val sameSenderAsPrev = prev != null &&
                        prev.role == message.role &&
                        prev.fromBotId == message.fromBotId &&
                        prev.kind == message.kind &&
                        (message.created - prev.created) < 180.0 &&
                        !showTimeHeader

                    val sameSenderAsNext = next != null &&
                        next.role == message.role &&
                        next.fromBotId == message.fromBotId &&
                        next.kind == message.kind &&
                        (next.created - message.created) < 180.0

                    val topGap = when {
                        idx == 0 -> 2.dp
                        sameSenderAsPrev -> 3.dp
                        else -> 11.dp
                    }

                    Column(Modifier.fillMaxWidth().padding(top = topGap)) {
                        // Telegram Floating Pill Date / Time Header
                        if (showTimeHeader && message.created > 0) {
                            Box(
                                Modifier.fillMaxWidth().padding(top = 4.dp, bottom = 10.dp),
                                contentAlignment = Alignment.Center
                            ) {
                                Box(
                                    Modifier.clip(CircleShape)
                                        .background(c.surface.copy(alpha = 0.82f))
                                        .padding(horizontal = 11.dp, vertical = 3.5.dp)
                                ) {
                                    Text(
                                        model.timeSeparator(message.created),
                                        color = c.muted,
                                        fontSize = 11.5.sp,
                                        fontWeight = FontWeight.Medium,
                                        style = TextStyle(fontFeatureSettings = "tnum")
                                    )
                                }
                            }
                        }
                        NativeMessage(
                            model = model,
                            bot = bot,
                            message = message,
                            groupedWithPrev = sameSenderAsPrev,
                            groupedWithNext = sameSenderAsNext,
                            isLatestMessage = (idx == messages.lastIndex && !isLiveHere)
                        )
                    }
                }
                if (isLiveHere) {
                    item("live_stream") {
                        Box(Modifier.padding(top = 10.dp)) {
                            NativeLiveStreamBubble(model, bot)
                        }
                    }
                }
                item("bottom_anchor") {
                    Spacer(Modifier.height(2.dp).fillMaxWidth())
                }
            }

            // Telegram Circular Scroll-to-Bottom Button (Animated Scale + Fade)
            AnimatedVisibility(
                visible = !nearTail && messages.size > 3,
                modifier = Modifier.align(Alignment.BottomEnd).padding(end = 14.dp, bottom = 14.dp),
                enter = scaleIn(spring(dampingRatio = 0.72f, stiffness = 460f)) + fadeIn(tween(150)),
                exit = scaleOut(spring(dampingRatio = 0.85f, stiffness = 500f)) + fadeOut(tween(120))
            ) {
                Box(
                    Modifier.size(40.dp)
                        .clip(CircleShape)
                        .background(c.surface.copy(alpha = 0.95f))
                        .border(0.5.dp, c.line.copy(alpha = 0.7f), CircleShape)
                        .iosPressScale(0.92f) {
                            chatScope.launch {
                                val anchorIdx = (messages.size + (if (isLiveHere) 1 else 0)).coerceAtLeast(0)
                                runCatching { list.animateScrollToItem(anchorIdx) }
                            }
                        },
                    contentAlignment = Alignment.Center
                ) {
                    PulseIcon("chevron_down", c.accent, Modifier.size(20.dp))
                }
            }
        }
    }
}

@Composable
private fun NativeLiveStreamBubble(model: NativePulseModel, roomBot: PulseBot) {
    val s = model.ui
    val c = LocalPulsePalette.current
    val haptics = LocalHapticFeedback.current
    val rawStream = s.streamText
    val isPlaceholder = rawStream.isBlank() || rawStream == "正在思考…" || rawStream == "正在思考与执行任务…" || rawStream == "正在整理回复…" || rawStream == "正在重新生成…" || rawStream.startsWith("正在调用 ") || rawStream.startsWith("正在并行执行 ") || rawStream.startsWith("⏳ ") || rawStream.startsWith("⏰ 正在执行")
    var stepsExpanded by remember { mutableStateOf(false) }
    var liveThoughtExpanded by remember { mutableStateOf(false) }

    var nowMs by remember { mutableLongStateOf(System.currentTimeMillis()) }
    LaunchedEffect(Unit) {
        while (true) {
            kotlinx.coroutines.delay(1000L)
            nowMs = System.currentTimeMillis()
        }
    }
    val elapsedSec = if (s.streamStartedAt > 0L) ((nowMs - s.streamStartedAt) / 1000L).coerceAtLeast(0L) else 0L
    val elapsedText = "%d:%02d".format(elapsedSec / 60, elapsedSec % 60)

    val pulseTransition = rememberInfiniteTransition(label = "stream_pulse")
    val pulseAlpha by pulseTransition.animateFloat(
        initialValue = 0.32f,
        targetValue = 1.0f,
        animationSpec = infiniteRepeatable(tween(760), RepeatMode.Reverse),
        label = "pulse_alpha"
    )

    val stepCount = s.streamTrace.size
    val lastStep = s.streamTrace.lastOrNull()
    val singleLineToolText = when {
        rawStream.startsWith("⏳ ") -> rawStream
        rawStream.startsWith("正在并行执行 ") -> rawStream
        s.streamTool.isNotBlank() && !s.streamTool.startsWith("已执行") -> humanizeToolName(s.streamTool, lastStep?.optJSONObject("arguments"))
        lastStep != null -> humanizeToolName(lastStep.optString("tool"), lastStep.optJSONObject("arguments"))
        else -> "正在思考…"
    }
    val cleanThought = s.streamThought.removePrefix("💭").removePrefix("⚡").trim()

    Column(
        Modifier.fillMaxWidth().padding(end = 44.dp),
        verticalArrangement = Arrangement.spacedBy(5.dp)
    ) {
        // 1. Strictly Single-Line Tool Call Capsule (No bottom clutter!)
        if (stepCount > 0 || s.streamTool.isNotBlank() || (isPlaceholder && cleanThought.isBlank())) {
            Column(
                Modifier.clip(RoundedCornerShape(14.dp))
                    .background(c.surface.copy(alpha = 0.94f))
                    .clickable(enabled = stepCount > 0) {
                        haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                        stepsExpanded = !stepsExpanded
                    }
                    .padding(horizontal = 12.dp, vertical = 7.5.dp)
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.size(6.5.dp).clip(CircleShape).background(c.accent.copy(alpha = 0.88f)))
                    Text(
                        singleLineToolText,
                        Modifier.padding(start = 8.dp).weight(1f, fill = false),
                        color = c.ink,
                        fontSize = 13.sp,
                        fontWeight = FontWeight.Medium,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis
                    )
                    Text(
                        if (stepCount > 0) " · ${stepCount}步 ($elapsedText) " + (if (stepsExpanded) "▴" else "▾") else " · $elapsedText",
                        color = c.muted,
                        fontSize = 11.5.sp,
                        style = TextStyle(fontFeatureSettings = "tnum")
                    )
                }
                AnimatedVisibility(
                    visible = stepsExpanded && s.streamTrace.isNotEmpty(),
                    enter = expandVertically(spring(dampingRatio = 0.80f, stiffness = 420f)) + fadeIn(),
                    exit = shrinkVertically(spring(dampingRatio = 0.85f, stiffness = 460f)) + fadeOut()
                ) {
                    Column(Modifier.padding(top = 6.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        s.streamTrace.takeLast(10).forEach { step ->
                            val tName = step.optString("tool")
                            val tArgs = step.optJSONObject("arguments")
                            val dur = step.optLong("duration_ms", 0L)
                            val isRunning = step.optString("status") == "running"
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text(
                                    if (isRunning) "▸" else "✓",
                                    color = if (isRunning) c.accent else c.green,
                                    fontSize = 11.sp,
                                    fontWeight = FontWeight.Bold
                                )
                                Text(
                                    humanizeToolName(tName, tArgs),
                                    Modifier.padding(start = 6.dp).weight(1f),
                                    color = c.muted,
                                    fontSize = 12.sp,
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis
                                )
                                if (dur > 0) {
                                    Text("${dur}ms", color = c.faint, fontSize = 11.sp, style = TextStyle(fontFeatureSettings = "tnum"))
                                }
                            }
                        }
                    }
                }
            }
        }

        if (s.streamSubAgents.isNotEmpty()) {
            Row(
                Modifier.clip(RoundedCornerShape(12.dp))
                    .background(c.surface.copy(alpha = 0.88f))
                    .padding(horizontal = 10.dp, vertical = 5.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text("协同：", color = c.accent, fontSize = 11.5.sp, fontWeight = FontWeight.SemiBold)
                s.streamSubAgents.take(3).forEach { sa ->
                    val bName = sa.optString("name", sa.optString("bot_id", "助手"))
                    val bSt = sa.optString("status", "running")
                    Text(
                        if (bSt == "done") "$bName ✓" else "$bName…",
                        color = if (bSt == "done") c.green else c.muted,
                        fontSize = 11.5.sp
                    )
                }
            }
        }

        if (s.streamTodos.isNotEmpty()) {
            Column(
                Modifier.clip(RoundedCornerShape(12.dp))
                    .background(c.surface.copy(alpha = 0.88f))
                    .padding(horizontal = 10.dp, vertical = 6.dp),
                verticalArrangement = Arrangement.spacedBy(3.dp)
            ) {
                s.streamTodos.take(5).forEach { td ->
                    val st = td.optString("status", "pending")
                    val title = td.optString("title", td.optString("step", ""))
                    if (title.isNotBlank()) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(
                                when (st) {
                                    "done", "completed" -> "✓ "
                                    "in_progress", "running" -> "▸ "
                                    else -> "· "
                                },
                                color = if (st in listOf("done", "completed")) c.green else c.accent,
                                fontSize = 11.5.sp,
                                fontWeight = FontWeight.Bold
                            )
                            Text(title, color = c.muted, fontSize = 11.5.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        }
                    }
                }
            }
        }

        // 2. Collapsible [💭 查看思考过程 ▸] Pill with Spring Expansion
        if (cleanThought.isNotBlank()) {
            Column(
                Modifier.clip(RoundedCornerShape(13.dp))
                    .background(c.surface.copy(alpha = 0.90f))
                    .clickable {
                        haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                        liveThoughtExpanded = !liveThoughtExpanded
                    }
                    .padding(horizontal = 11.dp, vertical = 6.5.dp)
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        if (liveThoughtExpanded) "收起思考过程 ▴" else "查看思考过程 ▸",
                        color = c.accent,
                        fontSize = 12.sp,
                        fontWeight = FontWeight.SemiBold
                    )
                }
                AnimatedVisibility(
                    visible = liveThoughtExpanded,
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

        // 3. Telegram Incoming Chat Bubble ONLY when real reply text is streaming
        if (!isPlaceholder && rawStream.isNotBlank()) {
            val streamCursor = ""
            Column(
                Modifier.clip(RoundedCornerShape(18.dp, 18.dp, 18.dp, 4.dp))
                    .background(c.aiBubble)
                    .padding(horizontal = 13.dp, vertical = 9.dp)
            ) {
                MarkdownText(
                    remember(rawStream) { nativeWikiMarkdown(rawStream) },
                    TextStyle(color = c.ink, fontSize = 16.sp, lineHeight = 22.sp),
                    streaming = true,
                    onOpenUri = model::openLink
                )
            }
        }

        if (s.streamChoices.isNotEmpty()) {
            Column(Modifier.fillMaxWidth().padding(top = 3.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                s.streamChoices.forEach { choice ->
                    Box(
                        Modifier.fillMaxWidth()
                            .clip(RoundedCornerShape(11.dp))
                            .background(c.surface.copy(alpha = 0.92f))
                            .iosPressScale(0.97f) { model.consumeChoiceAndSend("live_stream", choice.value) }
                            .padding(horizontal = 13.dp, vertical = 9.dp),
                        contentAlignment = Alignment.Center
                    ) {
                        Text("✦ ${choice.label}", color = c.accent, fontSize = 13.5.sp, fontWeight = FontWeight.SemiBold)
                    }
                }
            }
        }
    }
}

private fun humanizeToolName(tool: String, args: org.json.JSONObject?): String {
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
private fun NativeComposer(model: NativePulseModel, bot: PulseBot, onHeight: (Int) -> Unit) {
    val s = model.ui
    val c = LocalPulsePalette.current
    val focus = LocalFocusManager.current
    val haptics = LocalHapticFeedback.current
    val requester = remember { FocusRequester() }
    val executing = s.workingBot == bot.id || s.busyBot == bot.id || s.streamBotId == bot.id
    val hasDraft = !s.uploading && (s.draft.isNotBlank() || s.attachments.isNotEmpty())
    val phoneState by RootPhoneHarness.changes.collectAsState()
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

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun NativeMessage(
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
                        if (thoughtExpanded) "收起思考过程 ▴" else "查看思考过程 ▸",
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

            // 4. Clean Telegram Incoming Bubble (No heavy border, dynamic grouping tail, inline bottom-right timestamp)
            if (message.text.isNotBlank() || isError) {
                Column(
                    Modifier.clip(aiBubbleShape)
                        .background(c.aiBubble)
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
                    verticalArrangement = Arrangement.spacedBy(3.dp)
                ) {
                    if ((isOtherBot || isNotification || message.kind == "curiosity") && !groupedWithPrev) {
                        val headerTitle = when {
                            isBotToBotIn && message.direction == "outbound" ->
                                "➔ 发给 ${message.toBotName.ifBlank { speakerBot.name }}"
                            isBotToBotIn ->
                                "📩 来自 ${message.fromBotName.ifBlank { speakerBot.name }}"
                            isBotToBotReply ->
                                message.fromBotName.ifBlank { speakerBot.name }
                            else ->
                                message.fromBotName.ifBlank { speakerBot.name }
                        }
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(
                                headerTitle,
                                fontSize = 13.sp,
                                color = if (isOtherBot) speakerColor else c.accent,
                                fontWeight = FontWeight.SemiBold
                            )
                            if (isBotToBotReply) {
                                Text(" · 协作汇报", fontSize = 11.sp, color = speakerColor)
                            } else if (message.kind == "curiosity") {
                                Text(" · 主动关心", fontSize = 11.sp, color = c.accent)
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
private fun TelegramActionChip(icon: String, label: String, color: Color, onClick: () -> Unit) {
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
private fun PulseStickerThumb(model: NativePulseModel, stickerPath: String, size: Dp = 56.dp) {
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
private fun PulseStickerImage(model: NativePulseModel, stickerPath: String) {
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
private fun NativeToolTraceGroup(model: NativePulseModel, messageId: String, trace: List<org.json.JSONObject>) {
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

// --- 1. Native Settings Panel (Clean 2-Level Sub-Interface, Zero Emojis) ---
@Composable
private fun NativeSettingsPanel(model: NativePulseModel, modifier: Modifier) {
    val s = model.ui
    val c = LocalPulsePalette.current
    val focus = LocalFocusManager.current
    val phoneState by RootPhoneHarness.changes.collectAsState()
    var subPage by rememberSaveable { mutableStateOf<String?>(null) }
    androidx.activity.compose.BackHandler(enabled = subPage != null) {
        focus.clearFocus()
        subPage = null
    }
    var modelFilter by remember { mutableStateOf("") }
    val filteredModels = remember(s.availableModels, modelFilter) {
        if (modelFilter.isBlank()) s.availableModels else s.availableModels.filter { it.contains(modelFilter, ignoreCase = true) }
    }

    val pageTitle = when (subPage) {
        "api" -> "模型接口与节点"
        "phone" -> "手机虚拟屏与权限"
        "rhythm" -> "主动节奏与后台通知"
        "appearance" -> "外观与交互"
        "update" -> "版本与软件更新"
        else -> "设置"
    }

    Column(modifier.background(c.bg)) {
        Surface(color = c.surface.copy(alpha = 0.96f), tonalElevation = 0.dp) {
            Column {
                Row(
                    Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 10.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Row(
                        Modifier.clip(RoundedCornerShape(10.dp))
                            .iosPressScale(0.95f) {
                                focus.clearFocus()
                                if (subPage != null) subPage = null else model.closePanel()
                            }
                            .padding(horizontal = 6.dp, vertical = 6.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        PulseIcon("back", c.accent, Modifier.size(21.dp))
                        Text(if (subPage != null) "设置" else "返回", color = c.accent, fontSize = 16.sp)
                    }
                    Text(
                        pageTitle,
                        Modifier.weight(1f),
                        color = c.ink,
                        fontSize = 17.5.sp,
                        fontWeight = FontWeight.SemiBold,
                        textAlign = TextAlign.Center
                    )
                    Text(
                        if (s.savingSettings) "同步中…" else "保存",
                        Modifier.clip(RoundedCornerShape(14.dp))
                            .background(c.accent)
                            .iosPressScale(0.95f, enabled = !s.savingSettings) { focus.clearFocus(); model.saveSettings() }
                            .padding(horizontal = 14.dp, vertical = 7.dp),
                        color = Color.White,
                        fontSize = 13.5.sp,
                        fontWeight = FontWeight.SemiBold
                    )
                }
                HorizontalDivider(color = c.line.copy(alpha = 0.65f), thickness = 0.5.dp)
            }
        }

        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 14.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp)
        ) {
            when (subPage) {
                null -> {
                    // Level 1: Minimal Grouped Category Rows
                    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Text("连接与模型", color = c.muted, fontSize = 12.sp, fontWeight = FontWeight.Medium, modifier = Modifier.padding(start = 6.dp))
                        Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(c.surface)) {
                            SettingsCategoryRow(
                                icon = "cloud",
                                title = "模型接口与节点",
                                subtitle = "API 地址、密钥、默认模型与云端节点",
                                value = s.defaultModel.ifBlank { if (s.hasApiKey) "已配置" else "待配置" }
                            ) { subPage = "api" }
                        }
                    }

                    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Text("工作区与运行", color = c.muted, fontSize = 12.sp, fontWeight = FontWeight.Medium, modifier = Modifier.padding(start = 6.dp))
                        Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(c.surface)) {
                            SettingsCategoryRow(
                                icon = "screen",
                                title = "手机虚拟屏与权限",
                                subtitle = "Root 后台独立虚拟屏与悬浮窗控制",
                                value = if (phoneState.root) "已就绪" else "待授权"
                            ) { subPage = "phone" }
                            HorizontalDivider(Modifier.padding(start = 54.dp), color = c.line.copy(alpha = 0.55f), thickness = 0.5.dp)
                            SettingsCategoryRow(
                                icon = "clock",
                                title = "主动节奏与后台通知",
                                subtitle = "好奇心间隔、自动压缩阈值与常驻连接",
                                value = if (s.curiosityEnabledGlobal) "${s.curiosityMinutes}m" else "已关闭"
                            ) { subPage = "rhythm" }
                        }
                    }

                    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Text("偏好与系统", color = c.muted, fontSize = 12.sp, fontWeight = FontWeight.Medium, modifier = Modifier.padding(start = 6.dp))
                        Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(c.surface)) {
                            SettingsCategoryRow(
                                icon = "moon",
                                title = "外观与交互",
                                subtitle = "深浅色主题、过渡动画与触感反馈",
                                value = if (s.appearance == "light") "浅色" else "深色"
                            ) { subPage = "appearance" }
                            HorizontalDivider(Modifier.padding(start = 54.dp), color = c.line.copy(alpha = 0.55f), thickness = 0.5.dp)
                            SettingsCategoryRow(
                                icon = "spark",
                                title = "版本与软件更新",
                                subtitle = "当前 v${BuildConfig.VERSION_NAME} (Build ${BuildConfig.VERSION_CODE})",
                                value = if (s.updateAvailable) "有更新" else "最新"
                            ) { subPage = "update" }
                        }
                    }

                    PulseAction(if (s.savingSettings) "正在同步到服务器…" else "保存并同步到云端") {
                        focus.clearFocus()
                        model.saveSettings()
                    }
                }

                "api" -> {
                    Column(
                        Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(c.surface).padding(16.dp),
                        verticalArrangement = Arrangement.spacedBy(12.dp)
                    ) {
                        Text("统一大模型接口 (OpenAI 兼容)", color = c.ink, fontSize = 15.5.sp, fontWeight = FontWeight.SemiBold)
                        Text("保存后自动同步至云端，各助手可单独指定专属模型。", color = c.muted, fontSize = 12.sp)
                        PulseInputField(
                            label = "API Base URL（不含 /chat/completions）",
                            value = s.apiBaseUrl,
                            placeholder = "https://api.openai.com/v1",
                            onChange = { model.updateSettingsField(apiBaseUrl = it) }
                        )
                        PulseInputField(
                            label = if (s.hasApiKey && s.apiKeyInput.isBlank()) "API Key（云端已保存，留空保持不变）" else "API Key",
                            value = s.apiKeyInput,
                            placeholder = if (s.hasApiKey) "sk-...（已保存，如需更换请重新输入）" else "sk-...",
                            onChange = { model.updateSettingsField(apiKeyInput = it) }
                        )
                        Box(
                            Modifier.fillMaxWidth()
                                .clip(RoundedCornerShape(12.dp))
                                .background(c.accent)
                                .iosPressScale(0.97f, enabled = !s.fetchingModels) { focus.clearFocus(); model.fetchModels(false) }
                                .padding(vertical = 11.dp),
                            contentAlignment = Alignment.Center
                        ) {
                            Text(
                                if (s.fetchingModels) "正在请求 /models…" else "获取可用模型列表 (/models)",
                                color = Color.White,
                                fontSize = 13.5.sp,
                                fontWeight = FontWeight.SemiBold
                            )
                        }
                        PulseInputField(
                            label = "全局默认模型",
                            value = s.defaultModel,
                            placeholder = "填入或从下方选择模型名称",
                            onChange = { model.updateSettingsField(defaultModel = it) }
                        )
                        if (s.availableModels.isNotEmpty()) {
                            PulseInputField(
                                label = "筛选已拉取的 ${s.availableModels.size} 个模型",
                                value = modelFilter,
                                placeholder = "输入关键词过滤模型…",
                                onChange = { modelFilter = it }
                            )
                            LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth().padding(top = 2.dp)) {
                                items(filteredModels.take(60)) { m ->
                                    val selected = m == s.defaultModel
                                    Text(
                                        m,
                                        Modifier.clip(RoundedCornerShape(9.dp))
                                            .background(if (selected) c.accent else c.fill)
                                            .iosPressScale(0.95f) { model.updateSettingsField(defaultModel = m) }
                                            .padding(horizontal = 11.dp, vertical = 7.dp),
                                        color = if (selected) Color.White else c.ink,
                                        fontSize = 12.sp,
                                        fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal
                                    )
                                }
                            }
                        }
                    }

                    Column(
                        Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(c.surface).padding(16.dp),
                        verticalArrangement = Arrangement.spacedBy(12.dp)
                    ) {
                        Text("统一备用模型 (主模型超时/报错时自动切换)", color = c.ink, fontSize = 15.5.sp, fontWeight = FontWeight.SemiBold)
                        Text("当主模型或专属模型返回 500/502/429/超时时，云端自动无感切换至此备用模型继续回答。", color = c.muted, fontSize = 12.sp)
                        PulseInputField(
                            label = "统一备用模型名称",
                            value = s.fallbackModel,
                            placeholder = "例如 commandcode/deepseek/deepseek-v4.1-flash",
                            onChange = { model.updateSettingsField(fallbackModel = it) }
                        )
                        if (s.availableModels.isNotEmpty()) {
                            Text("点选设为统一备用模型：", color = c.muted, fontSize = 11.5.sp)
                            LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                                items(filteredModels.take(60)) { m ->
                                    val selected = m == s.fallbackModel
                                    Text(
                                        m,
                                        Modifier.clip(RoundedCornerShape(9.dp))
                                            .background(if (selected) c.green else c.fill)
                                            .iosPressScale(0.95f) { model.updateSettingsField(fallbackModel = m) }
                                            .padding(horizontal = 11.dp, vertical = 7.dp),
                                        color = if (selected) Color.White else c.ink,
                                        fontSize = 12.sp,
                                        fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal
                                    )
                                }
                            }
                        }
                        PulseInputField(
                            label = "备用 API Base URL（留空则共用上方主接口）",
                            value = s.fallbackBaseUrl,
                            placeholder = "留空则共用主接口 Base URL",
                            onChange = { model.updateSettingsField(fallbackBaseUrl = it) }
                        )
                        PulseInputField(
                            label = if (s.hasFallbackApiKey && s.fallbackApiKeyInput.isBlank()) "备用 API Key（云端已保存，留空保持不变）" else "备用 API Key（留空则共用主 Key）",
                            value = s.fallbackApiKeyInput,
                            placeholder = "sk-...（留空则共用主 API Key）",
                            onChange = { model.updateSettingsField(fallbackApiKeyInput = it) }
                        )
                    }

                    Column(
                        Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(c.surface).padding(16.dp),
                        verticalArrangement = Arrangement.spacedBy(10.dp)
                    ) {
                        Text("云端服务器节点", color = c.ink, fontSize = 15.5.sp, fontWeight = FontWeight.SemiBold)
                        PulseInputField(
                            label = "Server URL",
                            value = s.serverUrl,
                            placeholder = SecureTransport.DEFAULT_SERVER,
                            onChange = { model.updateSettingsField(serverUrl = it) }
                        )
                    }
                }

                "phone" -> {
                    Column(
                        Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(c.surface).padding(16.dp),
                        verticalArrangement = Arrangement.spacedBy(12.dp)
                    ) {
                        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text("手机独立虚拟屏与权限状态", color = c.ink, fontSize = 15.5.sp, fontWeight = FontWeight.SemiBold)
                                Text(
                                    if (phoneState.root) "Root 后台独立虚拟屏已就绪 (uid 0)"
                                    else "需授权 Root 与悬浮窗以启用后台独立虚拟屏",
                                    color = if (phoneState.root) c.green else c.muted,
                                    fontSize = 12.5.sp,
                                    fontWeight = FontWeight.Medium
                                )
                            }
                            Text(
                                if (phoneState.root) "已连接" else "待连接",
                                color = if (phoneState.root) c.green else c.amber,
                                fontSize = 12.sp,
                                fontWeight = FontWeight.SemiBold
                            )
                        }
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Box(
                                Modifier.weight(1f)
                                    .clip(RoundedCornerShape(10.dp))
                                    .background(if (phoneState.root) c.fill else c.accent)
                                    .iosPressScale(0.96f) { model.connectRootVirtualDisplay() }
                                    .padding(vertical = 10.dp),
                                contentAlignment = Alignment.Center
                            ) {
                                Text(
                                    if (phoneState.root) "Root 已授权" else "连接并授权 Root",
                                    color = if (phoneState.root) c.green else Color.White,
                                    fontSize = 12.5.sp,
                                    fontWeight = FontWeight.SemiBold
                                )
                            }
                            Box(
                                Modifier.weight(1f)
                                    .clip(RoundedCornerShape(10.dp))
                                    .background(c.fill)
                                    .iosPressScale(0.96f) { model.launchFloatingVirtualDisplay() }
                                    .padding(vertical = 10.dp),
                                contentAlignment = Alignment.Center
                            ) {
                                Text(
                                    if (phoneState.displayCreated) "展开虚拟屏小窗" else "启动虚拟屏工作区",
                                    color = c.ink,
                                    fontSize = 12.5.sp,
                                    fontWeight = FontWeight.Medium
                                )
                            }
                        }
                    }
                }

                "rhythm" -> {
                    Column(
                        Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(c.surface).padding(16.dp),
                        verticalArrangement = Arrangement.spacedBy(12.dp)
                    ) {
                        Text("主动好奇心与常驻通知", color = c.ink, fontSize = 15.5.sp, fontWeight = FontWeight.SemiBold)
                        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text("启用云端主动好奇心心跳", color = c.ink, fontSize = 14.5.sp)
                                Text("闲置时由开启了好奇心的助手主动关心或派发调研", color = c.muted, fontSize = 11.5.sp)
                            }
                            Switch(checked = s.curiosityEnabledGlobal, onCheckedChange = { model.updateSettingsField(curiosityEnabled = it) })
                        }
                        PulseInputField(
                            label = "好奇心间隔（分钟，5-1440）",
                            value = s.curiosityMinutes,
                            placeholder = "90",
                            onChange = { model.updateSettingsField(curiosityMinutes = it.filter { ch -> ch.isDigit() }) }
                        )
                        PulseInputField(
                            label = "自动压缩上下文阈值（Tokens）",
                            value = s.autoCompactTokens,
                            placeholder = "120000",
                            onChange = { model.updateSettingsField(autoCompactTokens = it.filter { ch -> ch.isDigit() }) }
                        )
                        HorizontalDivider(color = c.line.copy(alpha = 0.55f), thickness = 0.5.dp)
                        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text("手机后台常驻接收通知", color = c.ink, fontSize = 14.5.sp)
                                Text("保持加密 WebSocket 实时连接，支持后台虚拟屏操作与提醒", color = c.muted, fontSize = 11.5.sp)
                            }
                            Switch(checked = s.backgroundService, onCheckedChange = { model.updateSettingsField(backgroundService = it) })
                        }
                    }
                }

                "appearance" -> {
                    Column(
                        Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(c.surface).padding(16.dp),
                        verticalArrangement = Arrangement.spacedBy(12.dp)
                    ) {
                        Text("外观与物理交互", color = c.ink, fontSize = 15.5.sp, fontWeight = FontWeight.SemiBold)
                        Row(
                            Modifier.fillMaxWidth().clip(RoundedCornerShape(11.dp)).background(c.fill).padding(3.dp),
                            horizontalArrangement = Arrangement.spacedBy(4.dp)
                        ) {
                            listOf("dark" to "深色模式", "light" to "浅色模式").forEach { (key, label) ->
                                val active = (key == "dark" && s.appearance != "light") || (key == "light" && s.appearance == "light")
                                Text(
                                    label,
                                    Modifier.weight(1f)
                                        .clip(RoundedCornerShape(9.dp))
                                        .background(if (active) c.surface else Color.Transparent)
                                        .iosPressScale(0.96f) { model.updateSettingsField(appearance = key) }
                                        .padding(vertical = 9.dp),
                                    color = if (active) c.ink else c.muted,
                                    fontSize = 13.sp,
                                    fontWeight = if (active) FontWeight.SemiBold else FontWeight.Normal,
                                    textAlign = TextAlign.Center
                                )
                            }
                        }
                        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text("阻尼弹簧过渡动效", color = c.ink, fontSize = 14.5.sp)
                                Text("开启页面视差推入、气泡跟手与步骤弹簧展开", color = c.muted, fontSize = 11.5.sp)
                            }
                            Switch(checked = s.motion, onCheckedChange = { model.updateSettingsField(motion = it) })
                        }
                        HorizontalDivider(color = c.line.copy(alpha = 0.55f), thickness = 0.5.dp)
                        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text("触感震动反馈", color = c.ink, fontSize = 14.5.sp)
                                Text("左滑引用、右滑置顶、切换状态时提供物理震动", color = c.muted, fontSize = 11.5.sp)
                            }
                            Switch(checked = s.haptics, onCheckedChange = { model.updateSettingsField(haptics = it) })
                        }
                    }
                }

                "update" -> {
                    Column(
                        Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(c.surface).padding(16.dp),
                        verticalArrangement = Arrangement.spacedBy(11.dp)
                    ) {
                        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text("版本与软件更新", color = c.ink, fontSize = 15.5.sp, fontWeight = FontWeight.SemiBold)
                                Text(
                                    buildString {
                                        append("当前版本 v${BuildConfig.VERSION_NAME} (Build ${BuildConfig.VERSION_CODE})")
                                        if (s.latestReleaseVersion.isNotBlank()) append(" · 云端最新 v${s.latestReleaseVersion}")
                                    },
                                    color = if (s.updateAvailable) c.green else c.muted,
                                    fontSize = 12.sp,
                                    style = TextStyle(fontFeatureSettings = "tnum")
                                )
                            }
                            Text(
                                if (s.checkingUpdate) "检查中…" else "检查更新",
                                Modifier.clip(RoundedCornerShape(10.dp))
                                    .background(c.fill)
                                    .iosPressScale(0.95f, enabled = !s.checkingUpdate) { model.checkForUpdates(false) }
                                    .padding(horizontal = 12.dp, vertical = 7.dp),
                                color = c.ink,
                                fontSize = 12.sp,
                                fontWeight = FontWeight.Medium
                            )
                        }
                        if (s.latestReleaseNotes.isNotBlank()) {
                            Text(s.latestReleaseNotes, color = c.muted, fontSize = 12.sp, lineHeight = 17.sp)
                        }
                        Box(
                            Modifier.fillMaxWidth()
                                .clip(RoundedCornerShape(12.dp))
                                .background(if (s.updateAvailable) c.green else c.accent)
                                .iosPressScale(0.97f, enabled = !s.downloadingApk) { model.downloadLatestApk() }
                                .padding(vertical = 11.dp),
                            contentAlignment = Alignment.Center
                        ) {
                            Text(
                                when {
                                    s.downloadingApk -> "正在应用内下载更新… ${s.downloadProgress}% (下载完自动安装)"
                                    s.updateAvailable -> "发现新版本 v${s.latestReleaseVersion} · 立即下载安装"
                                    else -> "重新下载并覆盖安装最新客户端 APK"
                                },
                                color = Color.White,
                                fontSize = 13.5.sp,
                                fontWeight = FontWeight.SemiBold
                            )
                        }
                    }
                }
            }
            Spacer(Modifier.height(28.dp))
        }
    }
}

@Composable
private fun SettingsCategoryRow(
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

// --- 2. Telegram Contact Info Style Bot Profile & Context/Cache Secondary Page ---
@Composable
private fun NativeBotEditorPanel(model: NativePulseModel, modifier: Modifier) {
    val s = model.ui
    val c = LocalPulsePalette.current
    val focus = LocalFocusManager.current
    val isNew = s.editingBotId == null
    val targetBotId = s.editingBotId ?: s.botId
    val targetBot = model.bot(targetBotId)
    val (curCtxTok, threshCtxTok, ctxMsgCnt) = model.contextStatsFor(targetBotId)
    val (cachedCtxTok, cacheHitPct) = model.contextCacheFor(targetBotId)
    val (perfTtft, perfTps, perfCompTok) = model.contextPerfFor(targetBotId)
    val rawCtxRatio = (curCtxTok.toFloat() / threshCtxTok.coerceAtLeast(1)).coerceIn(0f, 1f)
    val animatedCtxRatio by animateFloatAsState(
        targetValue = rawCtxRatio.coerceAtLeast(0.03f),
        animationSpec = spring(dampingRatio = 0.80f, stiffness = 320f),
        label = "ctx_bar"
    )
    val ctxPct = (rawCtxRatio * 100).toInt()
    val rawCacheRatio = if (curCtxTok > 0) (cachedCtxTok.toFloat() / curCtxTok.toFloat()).coerceIn(0f, 1f) else 0f
    val animatedCacheRatio by animateFloatAsState(
        targetValue = if (cachedCtxTok > 0) rawCacheRatio.coerceAtLeast(0.04f) else 0f,
        animationSpec = spring(dampingRatio = 0.80f, stiffness = 320f),
        label = "cache_bar"
    )

    Column(modifier.background(c.bg)) {
        // Telegram Contact Info Top Bar
        Surface(color = c.surface.copy(alpha = 0.96f), tonalElevation = 0.dp) {
            Column {
                Row(
                    Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 10.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Row(
                        Modifier.clip(RoundedCornerShape(10.dp))
                            .iosPressScale(0.95f) { focus.clearFocus(); model.closePanel() }
                            .padding(horizontal = 6.dp, vertical = 6.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        PulseIcon("back", c.accent, Modifier.size(21.dp))
                        Text("返回", color = c.accent, fontSize = 16.sp)
                    }
                    Text(
                        if (isNew) "新建助手" else "个人主页",
                        Modifier.weight(1f),
                        color = c.ink,
                        fontSize = 17.5.sp,
                        fontWeight = FontWeight.SemiBold,
                        textAlign = TextAlign.Center
                    )
                    Text(
                        if (s.savingBot) "保存中…" else "保存",
                        Modifier.clip(RoundedCornerShape(14.dp))
                            .background(c.accent)
                            .iosPressScale(0.95f, enabled = !s.savingBot) { focus.clearFocus(); model.saveBotEditor() }
                            .padding(horizontal = 15.dp, vertical = 7.dp),
                        color = Color.White,
                        fontSize = 13.5.sp,
                        fontWeight = FontWeight.SemiBold
                    )
                }
                HorizontalDivider(color = c.line.copy(alpha = 0.65f), thickness = 0.5.dp)
            }
        }

        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 14.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp)
        ) {
            if (!isNew && targetBot != null) {
                // 0. Telegram Contact Info Centered Hero Card + 3 Action Tiles
                Column(
                    Modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(c.surface).padding(vertical = 20.dp, horizontal = 16.dp),
                    horizontalAlignment = Alignment.CenterHorizontally
                ) {
                    PulseAvatar(targetBot, s, diameter = 78.dp, showStatusDot = true, ringColor = c.surface)
                    Text(
                        targetBot.name,
                        Modifier.padding(top = 12.dp),
                        color = c.ink,
                        fontSize = 22.sp,
                        fontWeight = FontWeight.Bold,
                        letterSpacing = (-0.4).sp
                    )
                    Text(
                        targetBot.model.ifBlank { s.defaultModel.ifBlank { "跟随全局默认模型" } } +
                            (if (s.primary == targetBot.id) " · 主控助手" else ""),
                        Modifier.padding(top = 3.dp),
                        color = c.accent,
                        fontSize = 13.sp,
                        fontWeight = FontWeight.Medium
                    )
                    Text(
                        targetBot.description,
                        Modifier.padding(top = 6.dp, bottom = 16.dp),
                        color = c.muted,
                        fontSize = 13.5.sp,
                        textAlign = TextAlign.Center,
                        lineHeight = 19.sp
                    )
                    // Telegram 3-Tile Quick Action Row
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        TelegramProfileActionTile("chat", "发消息", Modifier.weight(1f)) {
                            model.closePanel()
                            model.open(targetBot.id)
                        }
                        TelegramProfileActionTile("spark", "压缩上下文", Modifier.weight(1f)) {
                            model.compactBotContext(targetBot.id)
                        }
                        TelegramProfileActionTile(
                            "pin",
                            if (targetBot.pinned) "取消置顶" else "置顶对话",
                            Modifier.weight(1f)
                        ) {
                            model.togglePinBot(targetBot.id)
                        }
                    }
                }

                // 1. Per-Bot Context & Prefix Cache Hit Card (Animated Spring Progress Bars)
                Column(
                    Modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(c.surface).padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp)
                ) {
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            "专属上下文与前缀缓存",
                            Modifier.weight(1f),
                            color = c.ink,
                            fontSize = 15.5.sp,
                            fontWeight = FontWeight.SemiBold
                        )
                        Text(
                            "$ctxMsgCnt 条消息",
                            color = c.muted,
                            fontSize = 12.sp,
                            style = TextStyle(fontFeatureSettings = "tnum")
                        )
                    }
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            "上下文占用：${model.formatTokenCount(curCtxTok)} / ${model.formatTokenCount(threshCtxTok)}",
                            Modifier.weight(1f),
                            color = if (rawCtxRatio >= 0.80f) c.warn else c.ink,
                            fontSize = 13.sp,
                            fontWeight = FontWeight.Medium,
                            style = TextStyle(fontFeatureSettings = "tnum")
                        )
                        Text(
                            "$ctxPct%",
                            color = if (rawCtxRatio >= 0.80f) c.warn else c.accent,
                            fontSize = 13.sp,
                            fontWeight = FontWeight.Bold,
                            style = TextStyle(fontFeatureSettings = "tnum")
                        )
                    }
                    Box(Modifier.fillMaxWidth().height(8.dp).clip(RoundedCornerShape(4.dp)).background(c.fill)) {
                        Box(
                            Modifier.fillMaxWidth(animatedCtxRatio).fillMaxHeight()
                                .clip(RoundedCornerShape(4.dp))
                                .background(
                                    when {
                                        rawCtxRatio >= 0.85f -> c.warn
                                        rawCtxRatio >= 0.65f -> c.amber
                                        else -> c.accent
                                    }
                                )
                        )
                    }
                    Row(Modifier.fillMaxWidth().padding(top = 2.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            "前缀缓存命中：${model.formatTokenCount(cachedCtxTok)} Tokens",
                            Modifier.weight(1f),
                            color = if (cachedCtxTok > 0) c.green else c.muted,
                            fontSize = 13.sp,
                            fontWeight = FontWeight.Medium,
                            style = TextStyle(fontFeatureSettings = "tnum")
                        )
                        Text(
                            "命中率 $cacheHitPct%",
                            color = if (cachedCtxTok > 0) c.green else c.faint,
                            fontSize = 13.sp,
                            fontWeight = FontWeight.Bold,
                            style = TextStyle(fontFeatureSettings = "tnum")
                        )
                    }
                    Box(Modifier.fillMaxWidth().height(8.dp).clip(RoundedCornerShape(4.dp)).background(c.fill)) {
                        if (cachedCtxTok > 0) {
                            Box(
                                Modifier.fillMaxWidth(animatedCacheRatio).fillMaxHeight()
                                    .clip(RoundedCornerShape(4.dp))
                                    .background(c.green)
                            )
                        }
                    }
                    Row(
                        Modifier.fillMaxWidth().padding(top = 4.dp),
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        Column(
                            Modifier.weight(1f).clip(RoundedCornerShape(10.dp)).background(c.fill.copy(alpha = 0.72f)).padding(horizontal = 10.dp, vertical = 7.dp)
                        ) {
                            Text("首字延迟 (TTFT)", color = c.muted, fontSize = 11.sp)
                            Text(
                                if (perfTtft > 0) "${perfTtft} ms" else "待测",
                                color = if (perfTtft in 1..1500) c.green else c.ink,
                                fontSize = 13.5.sp,
                                fontWeight = FontWeight.Bold,
                                style = TextStyle(fontFeatureSettings = "tnum")
                            )
                        }
                        Column(
                            Modifier.weight(1f).clip(RoundedCornerShape(10.dp)).background(c.fill.copy(alpha = 0.72f)).padding(horizontal = 10.dp, vertical = 7.dp)
                        ) {
                            Text("生成速度 (TPS)", color = c.muted, fontSize = 11.sp)
                            Text(
                                if (perfTps > 0.0) String.format(java.util.Locale.US, "%.1f tok/s", perfTps) else "待测",
                                color = if (perfTps >= 20.0) c.accent else c.ink,
                                fontSize = 13.5.sp,
                                fontWeight = FontWeight.Bold,
                                style = TextStyle(fontFeatureSettings = "tnum")
                            )
                        }
                        Column(
                            Modifier.weight(1f).clip(RoundedCornerShape(10.dp)).background(c.fill.copy(alpha = 0.72f)).padding(horizontal = 10.dp, vertical = 7.dp)
                        ) {
                            Text("最近输出", color = c.muted, fontSize = 11.sp)
                            Text(
                                if (perfCompTok > 0) "${model.formatTokenCount(perfCompTok)} tok" else "—",
                                color = c.ink,
                                fontSize = 13.5.sp,
                                fontWeight = FontWeight.Bold,
                                style = TextStyle(fontFeatureSettings = "tnum")
                            )
                        }
                    }
                    Text("自动压缩阈值：", color = c.muted, fontSize = 12.sp, modifier = Modifier.padding(top = 2.dp))
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        listOf("32000" to "32K", "64000" to "64K", "100000" to "100K", "120000" to "120K", "200000" to "200K").forEach { (tokVal, label) ->
                            val selected = s.autoCompactTokens.trim() == tokVal
                            Text(
                                label,
                                Modifier.weight(1f)
                                    .clip(RoundedCornerShape(9.dp))
                                    .background(if (selected) c.accent else c.fill)
                                    .iosPressScale(0.95f) {
                                        model.updateSettingsField(autoCompactTokens = tokVal)
                                        model.saveSettings()
                                    }
                                    .padding(vertical = 8.dp),
                                color = if (selected) Color.White else c.ink,
                                fontSize = 12.sp,
                                fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
                                textAlign = TextAlign.Center,
                                style = TextStyle(fontFeatureSettings = "tnum")
                            )
                        }
                    }
                    Box(
                        Modifier.fillMaxWidth()
                            .padding(top = 2.dp)
                            .clip(RoundedCornerShape(12.dp))
                            .background(c.accent.copy(alpha = 0.14f))
                            .iosPressScale(0.97f, enabled = !s.compactingContext) {
                                focus.clearFocus()
                                model.compactBotContext(targetBotId)
                            }
                            .padding(vertical = 11.dp),
                        contentAlignment = Alignment.Center
                    ) {
                        Text(
                            if (s.compactingContext) "正在浓缩归档对话…" else "立即压缩 ${targetBot.name} 的上下文",
                            color = c.accent,
                            fontSize = 13.5.sp,
                            fontWeight = FontWeight.SemiBold
                        )
                    }
                }

                // 2. Pluggable Sticker Pack Card with Live Thumbnail Strip
                if (s.stickerPacks.isNotEmpty()) {
                    Column(
                        Modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(c.surface).padding(16.dp),
                        verticalArrangement = Arrangement.spacedBy(10.dp)
                    ) {
                        Text("可选扩展与表情包插件", color = c.ink, fontSize = 15.5.sp, fontWeight = FontWeight.SemiBold)
                        s.stickerPacks.forEach { pack ->
                            val pid = pack.optString("id", "fubuki")
                            val pname = pack.optString("name", pid)
                            val penabled = pack.optBoolean("enabled", true)
                            val pcnt = pack.optInt("count", 0)
                            val stickersArr = pack.optJSONArray("stickers") ?: JSONArray()
                            val stickers = remember(stickersArr) { (0 until stickersArr.length()).map { stickersArr.getJSONObject(it) } }
                            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                                Column(Modifier.weight(1f)) {
                                    Text(pname, color = c.ink, fontSize = 14.5.sp, fontWeight = FontWeight.Medium)
                                    Text("共 $pcnt 枚贴纸 · 开启后聊天可收发表情包", color = c.muted, fontSize = 11.5.sp)
                                }
                                Switch(checked = penabled, onCheckedChange = { model.toggleStickerPack(pid, it) })
                            }
                            if (penabled && stickers.isNotEmpty()) {
                                LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(top = 4.dp)) {
                                    items(stickers.take(12)) { st ->
                                        val url = st.optString("url", "/stickers/$pid/${st.optString("file")}")
                                        Box(
                                            Modifier.clip(RoundedCornerShape(10.dp))
                                                .background(c.fill.copy(alpha = 0.65f))
                                                .padding(5.dp)
                                        ) {
                                            PulseStickerThumb(model, url, 48.dp)
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // 3. Bot Configuration & System Prompt Section (Minimal, ZERO Preset Templates)
            Column(
                Modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(c.surface).padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                Text("全端共用人设（手机 / 平板 / 电脑同步）", color = c.ink, fontSize = 15.5.sp, fontWeight = FontWeight.SemiBold)
                Text("头像造型、名称、简介与系统指令在所有客户端保持一致", color = c.muted, fontSize = 11.5.sp)
                Text("头像造型", color = c.muted, fontSize = 12.5.sp, fontWeight = FontWeight.Medium)
                LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                    val shapes = listOf(
                        "flower" to "花朵",
                        "circle" to "圆形",
                        "square" to "圆角方",
                        "star" to "星芒",
                        "drop" to "水滴",
                        "diamond" to "菱形",
                        "pill" to "胶囊"
                    )
                    items(shapes) { (shapeKey, shapeLabel) ->
                        val selected = s.editBotShape == shapeKey
                        Text(
                            shapeLabel,
                            Modifier.clip(RoundedCornerShape(9.dp))
                                .background(if (selected) c.accent else c.fill)
                                .iosPressScale(0.95f) { model.updateBotEditField(shape = shapeKey) }
                                .padding(horizontal = 11.dp, vertical = 7.dp),
                            color = if (selected) Color.White else c.ink,
                            fontSize = 12.sp,
                            fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal
                        )
                    }
                }
                PulseInputField("名称（全端共用）", s.editBotName, "输入助手名称") { model.updateBotEditField(name = it) }
                PulseInputField("简介（全端共用）", s.editBotDesc, "显示在对话列表中") { model.updateBotEditField(desc = it) }
                PulseInputField(
                    "系统指令 / 角色设定（全端共用 System Prompt）",
                    s.editBotInstructions,
                    "告诉这位 Bot 它的职责、说话风格与协作要求…",
                    singleLine = false
                ) {
                    model.updateBotEditField(instructions = it)
                }
                HorizontalDivider(color = c.line.copy(alpha = 0.55f), thickness = 0.5.dp)
                Text("当前客户端独立设置（仅本设备生效）", color = c.ink, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
                Text("不同设备（如手机打开、平板关闭）可各自独立开关好奇心、通知与专属模型", color = c.muted, fontSize = 11.5.sp)
                PulseInputField(
                    "本端专属模型（留空则使用全局默认：${s.defaultModel.ifBlank { "未设置" }}）",
                    s.editBotModel,
                    "留空跟随全局默认，或填入/点选指定模型"
                ) { model.updateBotEditField(modelName = it) }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(
                        if (s.fetchingModels) "正在拉取 /models…" else "刷新 /models 列表",
                        Modifier.clip(RoundedCornerShape(10.dp))
                            .background(c.fill)
                            .iosPressScale(0.95f, enabled = !s.fetchingModels) { model.fetchModels(true) }
                            .padding(horizontal = 12.dp, vertical = 8.dp),
                        color = c.accent,
                        fontSize = 12.sp,
                        fontWeight = FontWeight.Medium
                    )
                    if (s.editBotModel.isNotBlank()) {
                        Text(
                            "清空（恢复跟随默认）",
                            Modifier.clip(RoundedCornerShape(10.dp))
                                .background(c.fill)
                                .iosPressScale(0.95f) { model.updateBotEditField(modelName = "") }
                                .padding(horizontal = 12.dp, vertical = 8.dp),
                            color = c.muted,
                            fontSize = 12.sp
                        )
                    }
                }
                if (s.availableModels.isNotEmpty()) {
                    LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                        items(s.availableModels.take(50)) { m ->
                            val selected = m == s.editBotModel
                            Text(
                                m,
                                Modifier.clip(RoundedCornerShape(9.dp))
                                    .background(if (selected) c.accent else c.fill)
                                    .iosPressScale(0.95f) { model.updateBotEditField(modelName = m) }
                                    .padding(horizontal = 10.dp, vertical = 7.dp),
                                color = if (selected) Color.White else c.ink,
                                fontSize = 12.sp,
                                fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal
                            )
                        }
                    }
                }
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("在本设备上开启好奇心心跳", color = c.ink, fontSize = 14.5.sp)
                        Text("仅控制当前客户端（如手机开、平板关互不影响）", color = c.muted, fontSize = 11.5.sp)
                    }
                    Switch(checked = s.editBotCuriosity, onCheckedChange = { model.updateBotEditField(curiosity = it) })
                }
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("在本设备上允许主动通知", color = c.ink, fontSize = 14.5.sp)
                        Text("仅控制当前客户端是否弹出这位 Bot 的主动提醒", color = c.muted, fontSize = 11.5.sp)
                    }
                    Switch(checked = s.editBotNotify, onCheckedChange = { model.updateBotEditField(notify = it) })
                }
            }

            PulseAction(if (s.savingBot) "保存中…" else if (isNew) "创建此 Bot" else "保存修改") {
                focus.clearFocus()
                model.saveBotEditor()
            }
            if (!isNew && s.editingBotId != null) {
                if (s.primary != s.editingBotId) {
                    PulseAction("将此 Bot 设为主助理", filled = false) { model.setPrimaryBot(s.editingBotId) }
                }
                if (s.bots.size > 1 && s.primary != s.editingBotId) {
                    var confirmDelete by remember { mutableStateOf(false) }
                    if (confirmDelete) {
                        PulseAction("确认删除「${s.editBotName.ifBlank { s.editingBotId }}」（不可恢复）", filled = false) {
                            confirmDelete = false
                            model.deleteBot(s.editingBotId)
                        }
                    } else {
                        PulseAction("删除此 Bot", filled = false) { confirmDelete = true }
                    }
                }
            }
            Spacer(Modifier.height(28.dp))
        }
    }
}

@Composable
private fun TelegramProfileActionTile(icon: String, label: String, modifier: Modifier = Modifier, onClick: () -> Unit) {
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

// --- 3. Native Markdown Vault Browser & Editor Panel ---
@Composable
private fun NativeVaultPanel(model: NativePulseModel, modifier: Modifier) {
    val s = model.ui
    val c = LocalPulsePalette.current
    val focus = LocalFocusManager.current
    Column(modifier.background(c.bg)) {
        Surface(color = c.surface.copy(alpha = 0.96f), tonalElevation = 0.dp) {
            Column {
                Row(
                    Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 10.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Row(
                        Modifier.clip(RoundedCornerShape(10.dp))
                            .iosPressScale(0.95f) { focus.clearFocus(); model.closePanel() }
                            .padding(horizontal = 6.dp, vertical = 6.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        PulseIcon("back", c.accent, Modifier.size(21.dp))
                        Text("返回", color = c.accent, fontSize = 16.sp)
                    }
                    Column(Modifier.weight(1f), horizontalAlignment = Alignment.CenterHorizontally) {
                        Text("透明记忆库", color = c.ink, fontSize = 16.5.sp, fontWeight = FontWeight.SemiBold)
                        Text(s.selectedVaultPath, color = c.muted, fontSize = 11.sp)
                    }
                    Text(
                        if (s.vaultSaving) "保存中…" else "保存",
                        Modifier.clip(RoundedCornerShape(14.dp))
                            .background(c.accent)
                            .iosPressScale(0.95f, enabled = !s.vaultSaving) { focus.clearFocus(); model.saveVaultFile() }
                            .padding(horizontal = 14.dp, vertical = 7.dp),
                        color = Color.White,
                        fontSize = 13.5.sp,
                        fontWeight = FontWeight.SemiBold
                    )
                }
                HorizontalDivider(color = c.line.copy(alpha = 0.65f), thickness = 0.5.dp)
            }
        }
        LazyRow(
            contentPadding = PaddingValues(horizontal = 16.dp, vertical = 10.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            items(s.vaultFiles) { path ->
                val selected = path == s.selectedVaultPath
                Text(
                    path,
                    Modifier.clip(RoundedCornerShape(10.dp))
                        .background(if (selected) c.accent else c.surface)
                        .iosPressScale(0.95f) { model.selectVaultFile(path) }
                        .padding(horizontal = 12.dp, vertical = 7.dp),
                    color = if (selected) Color.White else c.ink,
                    fontSize = 12.5.sp,
                    fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal
                )
            }
        }
        Box(
            Modifier.weight(1f)
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 8.dp)
                .clip(RoundedCornerShape(18.dp))
                .background(c.surface)
                .padding(16.dp)
        ) {
            if (s.vaultLoading) {
                Text("正在读取 Markdown…", color = c.muted, fontSize = 14.sp)
            } else {
                val vaultScroll = remember(s.selectedVaultPath) { androidx.compose.foundation.ScrollState(0) }
                BasicTextField(
                    value = s.vaultContent,
                    onValueChange = { model.updateVaultContent(it) },
                    modifier = Modifier.fillMaxSize().verticalScroll(vaultScroll),
                    textStyle = TextStyle(color = c.ink, fontSize = 14.5.sp, lineHeight = 22.sp),
                    cursorBrush = SolidColor(c.accent)
                )
            }
        }
    }
}

@Composable
private fun PulseInputField(
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
private fun PulseRoundButton(icon: String, label: String, click: () -> Unit, background: Boolean = true) {
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

private val wikiTokenRegex = Regex("""(?s)(```.*?```|`[^`\n]+`|\[\[([^\]\n]+)\]\])""")

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
private fun NativeCloudDesktopPanel(model: NativePulseModel, modifier: Modifier) {
    val s = model.ui
    val focus = LocalFocusManager.current
    val botName = remember(s.cloudDesktopLiveBotId, s.botId, s.bots) {
        model.bot(s.cloudDesktopLiveBotId ?: s.botId)?.name ?: "助理"
    }
    var takingControl by remember { mutableStateOf(false) }
    var urlInput by remember(s.cloudDesktopUrl) { mutableStateOf(s.cloudDesktopUrl) }
    var typeInput by remember { mutableStateOf("") }

    Column(
        modifier.background(Color(0xFF0E1621)),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(
                "↻",
                Modifier.clip(CircleShape)
                    .iosPressScale(0.92f) { focus.clearFocus(); model.openCloudDesktop() }
                    .padding(horizontal = 10.dp, vertical = 6.dp),
                color = Color(0xFF94A3B8),
                fontSize = 16.sp
            )
            Spacer(Modifier.weight(1f))
            Row(
                Modifier.clip(RoundedCornerShape(10.dp))
                    .background(Color(0xFF182533))
                    .border(0.5.dp, Color(0xFF2B5278), RoundedCornerShape(10.dp))
                    .padding(horizontal = 14.dp, vertical = 7.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                PulseIcon("cloud", Color(0xFF94A3B8), Modifier.size(15.dp))
                Text(
                    "$botName 的云端电脑",
                    Modifier.padding(start = 7.dp),
                    color = Color(0xFFF1F5F9),
                    fontSize = 13.5.sp,
                    fontWeight = FontWeight.Medium,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis
                )
            }
            Spacer(Modifier.weight(1f))
            Text(
                "✕",
                Modifier.clip(CircleShape)
                    .iosPressScale(0.92f) { focus.clearFocus(); model.closePanel() }
                    .padding(horizontal = 10.dp, vertical = 6.dp),
                color = Color(0xFF94A3B8),
                fontSize = 16.sp
            )
        }

        Box(
            Modifier.fillMaxWidth().weight(1f).padding(horizontal = 14.dp, vertical = 6.dp),
            contentAlignment = Alignment.Center
        ) {
            Column(
                Modifier.fillMaxWidth()
                    .clip(RoundedCornerShape(24.dp))
                    .background(Color(0xFFAEC0D4))
                    .border(0.75.dp, Color.White.copy(alpha = 0.28f), RoundedCornerShape(24.dp))
                    .padding(start = 14.dp, end = 14.dp, top = 16.dp, bottom = 12.dp),
                horizontalAlignment = Alignment.CenterHorizontally
            ) {
                Box(
                    Modifier.fillMaxWidth().aspectRatio(1280f / 800f)
                        .clip(RoundedCornerShape(12.dp))
                        .background(Color(0xFFF8FAFC))
                        .border(0.5.dp, Color(0xFF64748B).copy(alpha = 0.35f), RoundedCornerShape(12.dp)),
                    contentAlignment = Alignment.Center
                ) {
                    val frame = s.cloudDesktopFrame
                    if (frame != null) {
                        BoxWithConstraints(Modifier.fillMaxSize()) {
                            val boxW = maxWidth
                            val boxH = maxHeight
                            Image(
                                bitmap = frame,
                                contentDescription = "云端电脑画面",
                                contentScale = ContentScale.FillBounds,
                                modifier = Modifier.fillMaxSize().pointerInput(takingControl) {
                                    if (!takingControl) return@pointerInput
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
                                    Modifier.offset(x = boxW * nx - 7.dp, y = boxH * ny - 7.dp)
                                        .size(14.dp)
                                        .clip(CircleShape)
                                        .background(Color(0xFF0A84FF).copy(alpha = 0.55f))
                                        .border(1.5.dp, Color.White, CircleShape)
                                )
                            }
                        }
                    } else {
                        Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(6.dp)) {
                            Text(
                                if (s.cloudDesktopLoading) "正在同步云电脑画面…" else "点击下方图标开启浏览器或终端",
                                color = Color(0xFF475569),
                                fontSize = 14.sp,
                                fontWeight = FontWeight.Medium
                            )
                            Text(
                                "1 vCPU · 1 GB RAM · 10 GB SSD",
                                color = Color(0xFF94A3B8),
                                fontSize = 11.5.sp
                            )
                        }
                    }
                }

                Row(
                    Modifier.padding(top = 10.dp)
                        .clip(RoundedCornerShape(16.dp))
                        .background(Color(0xFF8B9EB7).copy(alpha = 0.72f))
                        .border(0.5.dp, Color.White.copy(alpha = 0.42f), RoundedCornerShape(16.dp))
                        .padding(horizontal = 10.dp, vertical = 6.dp),
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    listOf(
                        Triple("lite_browser", "网页", "浏览器"),
                        Triple("terminal", ">_", "终端"),
                        Triple("files", "文件", "文件"),
                        Triple("editor", "编辑", "编辑")
                    ).forEach { (appKey, iconTxt, label) ->
                        Box(
                            Modifier.size(38.dp)
                                .clip(RoundedCornerShape(10.dp))
                                .background(
                                    if (appKey == "terminal") Color(0xFF334155)
                                    else Color.White.copy(alpha = 0.90f)
                                )
                                .iosPressScale(0.92f, enabled = takingControl) { if (takingControl) { focus.clearFocus(); model.cloudDesktopLaunchApp(appKey, urlInput) } }
                                .semantics { contentDescription = label },
                            contentAlignment = Alignment.Center
                        ) {
                            Text(
                                iconTxt,
                                color = if (appKey == "terminal") Color.White else Color(0xFF1E293B),
                                fontSize = 12.sp,
                                fontWeight = FontWeight.SemiBold
                            )
                        }
                    }
                }
            }
        }

        AnimatedVisibility(
            visible = takingControl,
            enter = expandVertically(spring(dampingRatio = 0.80f, stiffness = 400f)) + fadeIn(),
            exit = shrinkVertically(spring(dampingRatio = 0.85f, stiffness = 450f)) + fadeOut()
        ) {
            Column(
                Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 6.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                    BasicTextField(
                        value = urlInput,
                        onValueChange = { urlInput = it },
                        singleLine = true,
                        textStyle = TextStyle(color = Color.White, fontSize = 13.5.sp),
                        cursorBrush = SolidColor(Color(0xFF0A84FF)),
                        modifier = Modifier.weight(1f).clip(RoundedCornerShape(12.dp)).background(Color(0xFF182533)).padding(horizontal = 12.dp, vertical = 10.dp),
                        decorationBox = { inner -> Box { if (urlInput.isEmpty()) Text("输入网址前往…", color = Color(0xFF64748B), fontSize = 13.5.sp); inner() } }
                    )
                    Text(
                        "前往",
                        Modifier.clip(RoundedCornerShape(12.dp)).background(Color(0xFF0A84FF))
                            .iosPressScale(0.95f) { focus.clearFocus(); model.cloudDesktopNavigate(urlInput) }
                            .padding(horizontal = 14.dp, vertical = 10.dp),
                        color = Color.White, fontSize = 13.sp, fontWeight = FontWeight.SemiBold
                    )
                }
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                    BasicTextField(
                        value = typeInput,
                        onValueChange = { typeInput = it },
                        singleLine = true,
                        textStyle = TextStyle(color = Color.White, fontSize = 13.5.sp),
                        cursorBrush = SolidColor(Color(0xFF0A84FF)),
                        modifier = Modifier.weight(1f).clip(RoundedCornerShape(12.dp)).background(Color(0xFF182533)).padding(horizontal = 12.dp, vertical = 10.dp),
                        decorationBox = { inner -> Box { if (typeInput.isEmpty()) Text("输入文字或命令发送至当前窗口…", color = Color(0xFF64748B), fontSize = 13.5.sp); inner() } }
                    )
                    Text(
                        "发送",
                        Modifier.clip(RoundedCornerShape(12.dp)).background(Color(0xFF0A84FF))
                            .iosPressScale(0.95f) { val t = typeInput; typeInput = ""; focus.clearFocus(); model.cloudDesktopType(t) }
                            .padding(horizontal = 14.dp, vertical = 10.dp),
                        color = Color.White, fontSize = 13.sp, fontWeight = FontWeight.SemiBold
                    )
                }
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    listOf(
                        "▲ 上滚" to { model.cloudDesktopScroll(0.5f, 0.5f, "up", 4) },
                        "▼ 下滚" to { model.cloudDesktopScroll(0.5f, 0.5f, "down", 4) },
                        "回车" to { model.cloudDesktopKey("Enter") },
                        "退格" to { model.cloudDesktopKey("Backspace") },
                        "Esc" to { model.cloudDesktopKey("Escape") }
                    ).forEach { (label, act) ->
                        Text(
                            label,
                            Modifier.weight(1f).clip(RoundedCornerShape(10.dp)).background(Color(0xFF182533))
                                .iosPressScale(0.95f) { act() }.padding(vertical = 8.dp),
                            color = Color(0xFFE2E8F0), fontSize = 12.sp, textAlign = TextAlign.Center
                        )
                    }
                }
            }
        }

        Row(
            Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 14.dp),
            horizontalArrangement = Arrangement.Center,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(
                if (takingControl) "您当前拥有控制权（可直接点击画面或输入）" else "$botName 拥有控制权",
                color = Color(0xFF94A3B8),
                fontSize = 13.5.sp,
                fontWeight = FontWeight.Medium
            )
            Text(
                if (takingControl) "交还控制权" else "获取控制权",
                Modifier.padding(start = 12.dp)
                    .clip(CircleShape)
                    .background(if (takingControl) Color(0xFF334155) else Color(0xFFE2E8F0))
                    .iosPressScale(0.95f) { focus.clearFocus(); takingControl = !takingControl }
                    .padding(horizontal = 16.dp, vertical = 8.dp),
                color = if (takingControl) Color.White else Color(0xFF0F172A),
                fontSize = 13.sp,
                fontWeight = FontWeight.SemiBold
            )
        }
    }
}

@Composable
private fun NativeSchedulesPanel(model: NativePulseModel, modifier: Modifier) {
    val s = model.ui
    val c = LocalPulsePalette.current
    val focus = LocalFocusManager.current
    val data = s.schedulesData
    val routines = remember(data) { (data?.optJSONArray("routines") ?: JSONArray()).objects() }
    val deadlines = remember(data) { (data?.optJSONArray("deadlines") ?: JSONArray()).objects() }

    Column(modifier.background(c.bg)) {
        Surface(color = c.surface.copy(alpha = 0.96f), tonalElevation = 0.dp) {
            Column {
                Row(
                    Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 10.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Row(
                        Modifier.clip(RoundedCornerShape(10.dp))
                            .iosPressScale(0.95f) { focus.clearFocus(); model.closePanel() }
                            .padding(horizontal = 6.dp, vertical = 6.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        PulseIcon("back", c.accent, Modifier.size(21.dp))
                        Text("返回", color = c.accent, fontSize = 16.sp)
                    }
                    Text(
                        "定时与例行巡检",
                        Modifier.weight(1f),
                        color = c.ink,
                        fontSize = 17.sp,
                        fontWeight = FontWeight.SemiBold,
                        textAlign = TextAlign.Center
                    )
                    Text(
                        if (s.schedulesLoading) "刷新中" else "刷新",
                        Modifier.clip(RoundedCornerShape(12.dp))
                            .background(c.fill)
                            .iosPressScale(0.95f) { model.openSchedules() }
                            .padding(horizontal = 12.dp, vertical = 6.dp),
                        color = c.accent,
                        fontSize = 13.sp,
                        fontWeight = FontWeight.Medium
                    )
                }
                HorizontalDivider(color = c.line.copy(alpha = 0.65f), thickness = 0.5.dp)
            }
        }

        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp)
        ) {
            Column(
                Modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(c.surface).padding(15.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                Text("快速设定闹钟或巡检", color = c.ink, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
                Text("直接在聊天里告诉主助理时间与目标，它会自动调用 schedule_task 创建准点闹钟或每日例行任务。", color = c.muted, fontSize = 12.sp)
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(
                        "每天 07:30 晨间课表提醒",
                        Modifier.weight(1f)
                            .clip(RoundedCornerShape(12.dp))
                            .background(c.fill)
                            .iosPressScale(0.96f) {
                                model.closePanel()
                                model.open(s.primary)
                                model.sendQuickPrompt("帮我设置一个每天早上 07:30 的定点例行提醒，结合我的课表提醒我当天的课程和实验安排")
                            }
                            .padding(vertical = 10.dp, horizontal = 8.dp),
                        color = c.ink,
                        fontSize = 12.sp,
                        fontWeight = FontWeight.Medium,
                        textAlign = TextAlign.Center
                    )
                    Text(
                        "晚间 21:00 查学习通作业",
                        Modifier.weight(1f)
                            .clip(RoundedCornerShape(12.dp))
                            .background(c.fill)
                            .iosPressScale(0.96f) {
                                model.closePanel()
                                model.open(s.primary)
                                model.sendQuickPrompt("帮我设置一个每天晚上 21:00 的定点例行任务，检查我的待办死线和学习通作业")
                            }
                            .padding(vertical = 10.dp, horizontal = 8.dp),
                        color = c.ink,
                        fontSize = 12.sp,
                        fontWeight = FontWeight.Medium,
                        textAlign = TextAlign.Center
                    )
                }
            }

            Column(
                Modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(c.surface).padding(15.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                Text("常驻例行任务 (${routines.size})", color = c.ink, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
                if (routines.isEmpty()) {
                    Text("暂无常驻例行任务。可在聊天中随时让 Bot 添加。", color = c.muted, fontSize = 12.5.sp)
                } else {
                    routines.forEach { r ->
                        val id = r.optString("id")
                        val goal = r.optString("goal")
                        val enabled = r.optBoolean("enabled", true)
                        val dailyTime = r.optString("daily_time")
                        val hours = r.optInt("interval_hours", 24)
                        val botName = model.bot(r.optString("bot_id", s.primary))?.name ?: r.optString("bot_id", "主控")
                        Column(
                            Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(c.fill).padding(12.dp),
                            verticalArrangement = Arrangement.spacedBy(6.dp)
                        ) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text(
                                    if (dailyTime.isNotBlank()) "每天 $dailyTime" else "每 ${hours}h",
                                    color = if (enabled) c.accent else c.faint,
                                    fontSize = 12.sp,
                                    fontWeight = FontWeight.SemiBold,
                                    style = TextStyle(fontFeatureSettings = "tnum")
                                )
                                Text(" · $botName", color = c.muted, fontSize = 11.5.sp, modifier = Modifier.weight(1f))
                                Text(
                                    "立即执行",
                                    Modifier.padding(end = 8.dp)
                                        .clip(RoundedCornerShape(8.dp))
                                        .background(c.surface)
                                        .iosPressScale(0.95f) { model.runRoutineItem(id) }
                                        .padding(horizontal = 8.dp, vertical = 4.dp),
                                    color = c.accent,
                                    fontSize = 11.sp,
                                    fontWeight = FontWeight.SemiBold
                                )
                                Switch(checked = enabled, onCheckedChange = { model.toggleRoutineItem(id, it) })
                            }
                            Text(goal, color = c.ink, fontSize = 13.5.sp, lineHeight = 19.sp)
                        }
                    }
                }
            }

            Column(
                Modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(c.surface).padding(15.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                Text("零 Token 哨兵监控 (${s.watchdogs.size})", color = c.ink, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
                if (s.watchdogs.isEmpty()) {
                    Text("暂无活跃哨兵脚本。可让 Bot 调用 watchdog_task 编写低开销网页/接口巡检脚本，仅在检测到目标变化时唤醒大模型。", color = c.muted, fontSize = 12.5.sp)
                } else {
                    s.watchdogs.forEach { w ->
                        val wid = w.optString("id")
                        val wname = w.optString("name", wid)
                        val enabled = w.optBoolean("enabled", true)
                        val intervalSec = w.optInt("interval_sec", 60)
                        val lastStatus = w.optString("last_status", "待运行")
                        val runs = w.optInt("runs", 0)
                        Column(
                            Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(c.fill).padding(12.dp),
                            verticalArrangement = Arrangement.spacedBy(5.dp)
                        ) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text(
                                    if (enabled) "● 运行中 · 每 ${intervalSec}s" else "○ 已停止",
                                    color = if (enabled) c.green else c.faint,
                                    fontSize = 12.sp,
                                    fontWeight = FontWeight.SemiBold,
                                    modifier = Modifier.weight(1f),
                                    style = TextStyle(fontFeatureSettings = "tnum")
                                )
                                Text("已巡检 ${runs} 次", color = c.muted, fontSize = 11.5.sp, modifier = Modifier.padding(end = 8.dp), style = TextStyle(fontFeatureSettings = "tnum"))
                                if (enabled) {
                                    Text(
                                        "停止",
                                        Modifier.clip(RoundedCornerShape(8.dp))
                                            .background(c.surface)
                                            .iosPressScale(0.95f) { model.stopWatchdog(wid) }
                                            .padding(horizontal = 9.dp, vertical = 4.dp),
                                        color = c.warn,
                                        fontSize = 11.5.sp,
                                        fontWeight = FontWeight.Medium
                                    )
                                }
                            }
                            Text(wname, color = c.ink, fontSize = 13.5.sp, fontWeight = FontWeight.Medium)
                            if (lastStatus.isNotBlank()) {
                                Text("最近状态：$lastStatus", color = c.muted, fontSize = 11.5.sp, maxLines = 2, overflow = TextOverflow.Ellipsis)
                            }
                        }
                    }
                }
            }

            Column(
                Modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(c.surface).padding(15.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                Text("静态前缀钉住记忆 (${s.pinnedMemories.size})", color = c.ink, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
                if (s.pinnedMemories.isEmpty()) {
                    Text("暂无钉住记忆。可在任意消息菜单点按「钉住记忆」，将其锁定在缓存静态前缀中永不遗忘。", color = c.muted, fontSize = 12.5.sp)
                } else {
                    s.pinnedMemories.forEach { p ->
                        val pid = p.optString("id")
                        val ptext = p.optString("text")
                        Row(
                            Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(c.fill).padding(12.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Text(ptext, color = c.ink, fontSize = 13.sp, lineHeight = 18.sp, modifier = Modifier.weight(1f))
                            Text(
                                "移除",
                                Modifier.padding(start = 8.dp)
                                    .clip(RoundedCornerShape(8.dp))
                                    .background(c.surface)
                                    .iosPressScale(0.95f) { model.removeMemoryPin(pid) }
                                    .padding(horizontal = 9.dp, vertical = 4.dp),
                                color = c.warn,
                                fontSize = 11.5.sp,
                                fontWeight = FontWeight.Medium
                            )
                        }
                    }
                }
            }

            Column(
                Modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(c.surface).padding(15.dp),
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
                                    Text("截止：${model.day(dueAt)} ${model.time(dueAt)}", color = c.muted, fontSize = 11.5.sp, style = TextStyle(fontFeatureSettings = "tnum"))
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
