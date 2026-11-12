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
 * [Module] PulseScreenPanels
 * [Responsibility]
 * 二級業務面板與實時流卡片領域模塊：負責透明記憶庫瀏覽與 Markdown 編輯面板 (NativeVaultPanel)、
 * 雲端桌面實時畫面與遠程觸控面板 (NativeCloudDesktopPanel)、定時任務/倒計時/哨兵面板 (NativeSchedulesPanel)，
 * 以及聊天室實時流式輸出與工具執行狀態氣泡 (NativeLiveStreamBubble)。
 *
 * [Public API]
 * - NativeVaultPanel(model: NativePulseModel, modifier: Modifier)
 * - NativeCloudDesktopPanel(model: NativePulseModel, modifier: Modifier)
 * - NativeSchedulesPanel(model: NativePulseModel, modifier: Modifier)
 * - NativeLiveStreamBubble(model: NativePulseModel, roomBot: PulseBot)
 */

@Composable
internal fun NativeLiveStreamBubble(model: NativePulseModel, roomBot: PulseBot) {
    val s = model.ui
    val c = LocalPulsePalette.current
    val haptics = LocalHapticFeedback.current
    val rawStream = s.streamText
    val isPlaceholder = rawStream.isBlank() || rawStream == "正在思考…" || rawStream == "正在思考与执行任务…" || rawStream == "正在整理回复…" || rawStream == "正在重新生成…" || rawStream.startsWith("正在调用 ") || rawStream.startsWith("正在并行执行 ") || rawStream.startsWith("⏳ ") || rawStream.startsWith("⏰ 正在执行")
    var stepsExpanded by remember { mutableStateOf(true) }
    var liveThoughtExpanded by remember { mutableStateOf(true) }
    val liveThoughtScroll = rememberScrollState()

    var nowMs by remember { mutableLongStateOf(System.currentTimeMillis()) }
    LaunchedEffect(s.streamStartedAt) {
        if (s.streamStartedAt <= 0L) return@LaunchedEffect
        while (true) {
            kotlinx.coroutines.delay(1000L)
            nowMs = System.currentTimeMillis()
        }
    }
    val elapsedSec = if (s.streamStartedAt > 0L) ((nowMs - s.streamStartedAt) / 1000L).coerceAtLeast(0L) else 0L
    val elapsedText = "%d:%02d".format(elapsedSec / 60, elapsedSec % 60)

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

        // 2. Auto-Expanded Live Process & Thinking Card (collapses automatically once turn completes into NativeMessage)
        if (cleanThought.isNotBlank()) {
            LaunchedEffect(cleanThought.length / 20) {
                if (liveThoughtExpanded) {
                    runCatching { liveThoughtScroll.scrollTo(liveThoughtScroll.maxValue) }
                }
            }
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
                        if (liveThoughtExpanded) "💭 实时思考与阶段进展 ▴" else "💭 查看实时思考与阶段进展 ▸",
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
                    Row(
                        Modifier.padding(top = 6.dp)
                            .heightIn(max = 210.dp)
                            .verticalScroll(liveThoughtScroll)
                    ) {
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

// --- 3. Native Markdown Vault Browser & Editor Panel ---
@Composable
internal fun NativeVaultPanel(model: NativePulseModel, modifier: Modifier) {
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
internal fun NativeCloudDesktopPanel(model: NativePulseModel, modifier: Modifier) {
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
internal fun NativeSchedulesPanel(model: NativePulseModel, modifier: Modifier) {
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
