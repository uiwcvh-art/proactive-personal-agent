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
 * [Module] NativePulseScreen
 * [Responsibility]
 * 原生主界面容器與首頁工作台領域模塊：負責頂層頁面路由切換（首頁 Bot 列表、聊天室視口、二級面板、底部彈窗與畫中畫浮層）、
 * 首頁狀態總覽與搜索欄 (NativeHome)，以及聊天室滾動視口與手勢切頁容器 (NativeChatRoomViewport)。
 *
 * [Public API]
 * - NativePulseScreen(model: NativePulseModel)
 * - NativeHome(model: NativePulseModel, modifier: Modifier)
 * - NativeChatRoomViewport(model: NativePulseModel, bot: PulseBot)
 */

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

                            if (pFraction < 0.999f) {
                                val outerLayerMod = if (pFraction > 0.001f) {
                                    Modifier.fillMaxSize().graphicsLayer {
                                        translationX = -width * 0.24f * pFraction
                                    }
                                } else {
                                    Modifier.fillMaxSize()
                                }
                                Box(outerLayerMod) {
                                    if (chatFraction < 0.999f) {
                                        val homeLayerMod = if (chatFraction > 0.001f) {
                                            Modifier.fillMaxSize().graphicsLayer {
                                                translationX = -width * 0.26f * chatFraction
                                                alpha = (1f - 0.18f * chatFraction).coerceIn(0.75f, 1f)
                                            }
                                        } else {
                                            Modifier.fillMaxSize()
                                        }
                                        NativeHome(
                                            model,
                                            homeLayerMod.then(if (state.chatOpen || state.activePanel != null) Modifier.clearAndSetSemantics { } else Modifier)
                                        )
                                    }
                                    val bot = model.bot()
                                    if (bot != null && (chatFraction > 0.001f || state.chatOpen)) {
                                        val chatLayerMod = if (chatFraction < 0.999f) {
                                            Modifier.fillMaxSize().graphicsLayer {
                                                translationX = width * (1f - chatFraction)
                                                alpha = if (chatFraction <= 0.001f) 0f else 1f
                                            }
                                        } else {
                                            Modifier.fillMaxSize()
                                        }
                                        NativeChat(
                                            model,
                                            bot,
                                            chatLayerMod.then(if (!state.chatOpen || state.activePanel != null) Modifier.clearAndSetSemantics { } else Modifier)
                                        ) { composerHeight = it }
                                    }
                                    if (pFraction > 0.01f) {
                                        Box(
                                            Modifier.fillMaxSize()
                                                .background(Color.Black.copy(alpha = 0.28f * pFraction))
                                        )
                                    }
                                }
                            }

                            if (shownPanel != null && pFraction > 0.001f) {
                                val panelLayerMod = if (pFraction < 0.999f) {
                                    Modifier.fillMaxSize().graphicsLayer {
                                        translationX = width * (1f - pFraction)
                                        shadowElevation = 24f * pFraction
                                    }
                                } else {
                                    Modifier.fillMaxSize()
                                }
                                Box(panelLayerMod) {
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
internal fun NativeHome(model: NativePulseModel, modifier: Modifier) {
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

    val onlineDotAlpha = 1.0f

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
                            .padding(horizontal = 16.dp, vertical = 8.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Row(
                            Modifier.weight(1f)
                                .clip(RoundedCornerShape(8.dp))
                                .clickable { model.launchFloatingVirtualDisplay() }
                                .padding(vertical = 2.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Box(Modifier.size(7.5.dp).clip(CircleShape).background(c.green))
                            Text(
                                "手机虚拟屏运行中 (#${phoneState.displayId}) · 点此展开悬浮窗",
                                Modifier.weight(1f).padding(start = 9.dp, end = 6.dp),
                                color = c.green,
                                fontSize = 13.sp,
                                fontWeight = FontWeight.SemiBold,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis
                            )
                        }
                        Box(
                            Modifier.clip(RoundedCornerShape(999.dp))
                                .background(c.warn.copy(alpha = 0.16f))
                                .border(0.75.dp, c.warn.copy(alpha = 0.45f), RoundedCornerShape(999.dp))
                                .clickable { model.destroyVirtualDisplay() }
                                .padding(horizontal = 11.dp, vertical = 4.dp),
                            contentAlignment = Alignment.Center
                        ) {
                            Text(
                                "销毁虚拟屏",
                                color = c.warn,
                                fontSize = 11.5.sp,
                                fontWeight = FontWeight.SemiBold
                            )
                        }
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
internal fun NativeChatRoomViewport(model: NativePulseModel, bot: PulseBot) {
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
