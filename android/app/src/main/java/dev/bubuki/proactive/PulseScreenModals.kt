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
 * [Module] PulseScreenModals
 * [Responsibility]
 * 模態設置與 Bot 檔案編輯器領域模塊：負責全局設置面板、首次配對引導向導、API 與備用模型配置、OTA 檢查更新 (NativeSettingsPanel)，
 * 以及 Telegram 風格 Bot 詳情頁、人設指令編輯、上下文緩存統計與記憶釘住管理 (NativeBotEditorPanel)。
 *
 * [Public API]
 * - NativeSettingsPanel(model: NativePulseModel, modifier: Modifier)
 * - NativeBotEditorPanel(model: NativePulseModel, modifier: Modifier)
 */

// --- 1. Native Settings Panel (Clean 2-Level Sub-Interface, Zero Emojis) ---
@Composable
internal fun NativeSettingsPanel(model: NativePulseModel, modifier: Modifier) {
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
                        textAlign = TextAlign.Center)
                    Text(
                        if (s.savingSettings) "同步中…" else "保存",
                        Modifier.clip(RoundedCornerShape(14.dp))
                            .background(c.accent)
                            .iosPressScale(0.95f, enabled = !s.savingSettings) { focus.clearFocus(); model.saveSettings() }
                            .padding(horizontal = 14.dp, vertical = 7.dp),
                        color = Color.White,
                        fontSize = 13.5.sp,
                        fontWeight = FontWeight.SemiBold)
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
                            onChange = { model.updateSettingsField(apiBaseUrl = it) })
                        PulseInputField(
                            label = if (s.hasApiKey && s.apiKeyInput.isBlank()) "API Key（云端已保存，留空保持不变）" else "API Key",
                            value = s.apiKeyInput,
                            placeholder = if (s.hasApiKey) "sk-...（已保存，如需更换请重新输入）" else "sk-...",
                            onChange = { model.updateSettingsField(apiKeyInput = it) })
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
                                fontWeight = FontWeight.SemiBold)
                        }
                        PulseInputField(
                            label = "全局默认模型",
                            value = s.defaultModel,
                            placeholder = "填入或从下方选择模型名称",
                            onChange = { model.updateSettingsField(defaultModel = it) })
                        if (s.availableModels.isNotEmpty()) {
                            PulseInputField(
                                label = "筛选已拉取的 ${s.availableModels.size} 个模型",
                                value = modelFilter,
                                placeholder = "输入关键词过滤模型…",
                                onChange = { modelFilter = it })
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
                                        fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal)
                                }
                            }
                        }
                    }
                    Column(
                        Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(c.surface).padding(16.dp),
                        verticalArrangement = Arrangement.spacedBy(12.dp)
                    ) {
                        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text("启用备用模型自动切换", color = c.ink, fontSize = 15.5.sp, fontWeight = FontWeight.SemiBold)
                                Text(
                                    if (s.fallbackEnabled) "当主模型返回 500/502/429/超时时，自动切换至备用模型"
                                    else "已关闭（仅使用主模型，不自动切换备用接口）",
                                    color = c.muted,
                                    fontSize = 12.sp
                                )
                            }
                            Switch(
                                checked = s.fallbackEnabled,
                                onCheckedChange = { model.updateSettingsField(fallbackEnabled = it) }
                            )
                        }
                        if (s.fallbackEnabled) {
                            PulseInputField(
                                label = "统一备用模型名称",
                                value = s.fallbackModel,
                                placeholder = "填入备用模型名称",
                                onChange = { model.updateSettingsField(fallbackModel = it) })
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
                                            fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal)
                                    }
                                }
                            }
                            PulseInputField(
                                label = "备用 API Base URL（留空则共用上方主接口）",
                                value = s.fallbackBaseUrl,
                                placeholder = "留空则共用主接口 Base URL",
                                onChange = { model.updateSettingsField(fallbackBaseUrl = it) })
                            PulseInputField(
                                label = if (s.hasFallbackApiKey && s.fallbackApiKeyInput.isBlank()) "备用 API Key（云端已保存，留空保持不变）" else "备用 API Key（留空则共用主 Key）",
                                value = s.fallbackApiKeyInput,
                                placeholder = "sk-...（留空则共用主 API Key）",
                                onChange = { model.updateSettingsField(fallbackApiKeyInput = it) })
                        }
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
                            onChange = { model.updateSettingsField(serverUrl = it) })
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
                                    fontWeight = FontWeight.Medium)
                            }
                            Text(
                                if (phoneState.root) "已连接" else "待连接",
                                color = if (phoneState.root) c.green else c.amber,
                                fontSize = 12.sp,
                                fontWeight = FontWeight.SemiBold)
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
                                    fontWeight = FontWeight.SemiBold)
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
                                    fontWeight = FontWeight.Medium)
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
                            onChange = { model.updateSettingsField(curiosityMinutes = it.filter { ch -> ch.isDigit() }) })
                        val curSettingCompact = (s.autoCompactTokens.toIntOrNull() ?: 120000).coerceIn(8000, 1000000)
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                            Text("上下文窗口上限 / 自动压缩阈值", color = c.ink, fontSize = 14.sp, fontWeight = FontWeight.Medium)
                            Text(
                                "${model.formatTokenCount(curSettingCompact)} Tokens",
                                color = c.accent,
                                fontSize = 13.5.sp,
                                fontWeight = FontWeight.Bold,
                                style = TextStyle(fontFeatureSettings = "tnum")
                            )
                        }
                        Slider(
                            value = curSettingCompact.coerceIn(16000, 200000).toFloat(),
                            onValueChange = { v ->
                                val stepped = ((v / 4000f).roundToInt() * 4000).coerceIn(16000, 200000)
                                model.updateSettingsField(autoCompactTokens = stepped.toString())
                            },
                            onValueChangeFinished = {
                                val finalTok = (s.autoCompactTokens.toIntOrNull() ?: 120000).coerceIn(8000, 1000000)
                                model.applyContextWindowTokens(finalTok)
                            },
                            valueRange = 16000f..200000f,
                            colors = SliderDefaults.colors(
                                thumbColor = c.accent,
                                activeTrackColor = c.accent,
                                inactiveTrackColor = c.fill
                            )
                        )
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(5.dp)) {
                            listOf(16000 to "16K", 32000 to "32K", 64000 to "64K", 100000 to "100K", 120000 to "120K", 200000 to "200K").forEach { (tokInt, label) ->
                                val selected = curSettingCompact == tokInt
                                Text(
                                    label,
                                    Modifier.weight(1f)
                                        .clip(RoundedCornerShape(8.dp))
                                        .background(if (selected) c.accent else c.fill)
                                        .iosPressScale(0.95f) { model.applyContextWindowTokens(tokInt) }
                                        .padding(vertical = 7.dp),
                                    color = if (selected) Color.White else c.ink,
                                    fontSize = 11.5.sp,
                                    fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
                                    textAlign = TextAlign.Center,
                                    style = TextStyle(fontFeatureSettings = "tnum")
                                )
                            }
                        }
                        PulseInputField(
                            label = "自定义上下文窗口（Tokens，8000-1000000）",
                            value = s.autoCompactTokens,
                            placeholder = "120000",
                            onChange = { model.updateSettingsField(autoCompactTokens = it.filter { ch -> ch.isDigit() }) })
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
                                    textAlign = TextAlign.Center)
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
                                    style = TextStyle(fontFeatureSettings = "tnum"))
                            }
                            Text(
                                if (s.checkingUpdate) "检查中…" else "检查更新",
                                Modifier.clip(RoundedCornerShape(10.dp))
                                    .background(c.fill)
                                    .iosPressScale(0.95f, enabled = !s.checkingUpdate) { model.checkForUpdates(false) }
                                    .padding(horizontal = 12.dp, vertical = 7.dp),
                                color = c.ink,
                                fontSize = 12.sp,
                                fontWeight = FontWeight.Medium)
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
                                fontWeight = FontWeight.SemiBold)
                        }
                    }
                }
            }
            Spacer(Modifier.height(28.dp))
        }
    }
}

// --- 2. Telegram Contact Info Style Bot Profile & Context/Cache Secondary Page ---
@Composable
internal fun NativeBotEditorPanel(model: NativePulseModel, modifier: Modifier) {
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
        label = "ctx_bar")
    val ctxPct = (rawCtxRatio * 100).toInt()
    val rawCacheRatio = if (curCtxTok > 0) (cachedCtxTok.toFloat() / curCtxTok.toFloat()).coerceIn(0f, 1f) else 0f
    val animatedCacheRatio by animateFloatAsState(
        targetValue = if (cachedCtxTok > 0) rawCacheRatio.coerceAtLeast(0.04f) else 0f,
        animationSpec = spring(dampingRatio = 0.80f, stiffness = 320f),
        label = "cache_bar")
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
                        textAlign = TextAlign.Center)
                    Text(
                        if (s.savingBot) "保存中…" else "保存",
                        Modifier.clip(RoundedCornerShape(14.dp))
                            .background(c.accent)
                            .iosPressScale(0.95f, enabled = !s.savingBot) { focus.clearFocus(); model.saveBotEditor() }
                            .padding(horizontal = 15.dp, vertical = 7.dp),
                        color = Color.White,
                        fontSize = 13.5.sp,
                        fontWeight = FontWeight.SemiBold)
                }
                HorizontalDivider(color = c.line.copy(alpha = 0.65f), thickness = 0.5.dp)
            }
        }
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 14.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp)
        ) {
            if (!isNew && targetBot != null) {
                val previewBot = remember(targetBot, s.editBotName, s.editBotShape, s.editBotColor) {
                    targetBot.copy(
                        name = s.editBotName.ifBlank { targetBot.name },
                        shape = s.editBotShape.ifBlank { targetBot.shape },
                        color = s.editBotColor.ifBlank { targetBot.color })
                }
                // 0. Telegram Contact Info Centered Hero Card + 3 Action Tiles
                Column(
                    Modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(c.surface).padding(vertical = 20.dp, horizontal = 16.dp),
                    horizontalAlignment = Alignment.CenterHorizontally
                ) {
                    PulseAvatar(previewBot, s, diameter = 78.dp, showStatusDot = true, ringColor = c.surface)
                    Text(
                        previewBot.name,
                        Modifier.padding(top = 12.dp),
                        color = c.ink,
                        fontSize = 22.sp,
                        fontWeight = FontWeight.Bold,
                        letterSpacing = (-0.4).sp)
                    Text(
                        targetBot.model.ifBlank { s.defaultModel.ifBlank { "跟随全局默认模型" } } +
                            (if (s.primary == targetBot.id) " · 主控助手" else ""),
                        Modifier.padding(top = 3.dp),
                        color = c.accent,
                        fontSize = 13.sp,
                        fontWeight = FontWeight.Medium)
                    Text(
                        targetBot.description,
                        Modifier.padding(top = 6.dp, bottom = 16.dp),
                        color = c.muted,
                        fontSize = 13.5.sp,
                        textAlign = TextAlign.Center,
                        lineHeight = 19.sp)
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
                            fontWeight = FontWeight.SemiBold)
                        Text(
                            "$ctxMsgCnt 条消息",
                            color = c.muted,
                            fontSize = 12.sp,
                            style = TextStyle(fontFeatureSettings = "tnum"))
                    }
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            "上下文占用：${model.formatTokenCount(curCtxTok)} / ${model.formatTokenCount(threshCtxTok)}",
                            Modifier.weight(1f),
                            color = if (rawCtxRatio >= 0.80f) c.warn else c.ink,
                            fontSize = 13.sp,
                            fontWeight = FontWeight.Medium,
                            style = TextStyle(fontFeatureSettings = "tnum"))
                        Text(
                            "$ctxPct%",
                            color = if (rawCtxRatio >= 0.80f) c.warn else c.accent,
                            fontSize = 13.sp,
                            fontWeight = FontWeight.Bold,
                            style = TextStyle(fontFeatureSettings = "tnum"))
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
                                    })
                        )
                    }
                    Row(Modifier.fillMaxWidth().padding(top = 2.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            "前缀缓存命中：${model.formatTokenCount(cachedCtxTok)} Tokens",
                            Modifier.weight(1f),
                            color = if (cachedCtxTok > 0) c.green else c.muted,
                            fontSize = 13.sp,
                            fontWeight = FontWeight.Medium,
                            style = TextStyle(fontFeatureSettings = "tnum"))
                        Text(
                            "命中率 $cacheHitPct%",
                            color = if (cachedCtxTok > 0) c.green else c.faint,
                            fontSize = 13.sp,
                            fontWeight = FontWeight.Bold,
                            style = TextStyle(fontFeatureSettings = "tnum"))
                    }
                    Box(Modifier.fillMaxWidth().height(8.dp).clip(RoundedCornerShape(4.dp)).background(c.fill)) {
                        if (cachedCtxTok > 0) {
                            Box(
                                Modifier.fillMaxWidth(animatedCacheRatio).fillMaxHeight()
                                    .clip(RoundedCornerShape(4.dp))
                                    .background(c.green))
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
                                style = TextStyle(fontFeatureSettings = "tnum"))
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
                                style = TextStyle(fontFeatureSettings = "tnum"))
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
                                style = TextStyle(fontFeatureSettings = "tnum"))
                        }
                    }
                    Row(
                        Modifier.fillMaxWidth().padding(top = 2.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text("上下文窗口上限（滑块或点按实时生效）：", color = c.muted, fontSize = 12.sp)
                        Text(
                            "${model.formatTokenCount(threshCtxTok)} Tokens",
                            color = c.accent,
                            fontSize = 12.5.sp,
                            fontWeight = FontWeight.Bold,
                            style = TextStyle(fontFeatureSettings = "tnum")
                        )
                    }
                    Slider(
                        value = threshCtxTok.coerceIn(16000, 200000).toFloat(),
                        onValueChange = { v ->
                            val stepped = ((v / 4000f).roundToInt() * 4000).coerceIn(16000, 200000)
                            model.updateSettingsField(autoCompactTokens = stepped.toString())
                        },
                        onValueChangeFinished = {
                            val finalTok = (s.autoCompactTokens.toIntOrNull() ?: 120000).coerceIn(8000, 1000000)
                            model.applyContextWindowTokens(finalTok, targetBotId)
                        },
                        valueRange = 16000f..200000f,
                        colors = SliderDefaults.colors(
                            thumbColor = c.accent,
                            activeTrackColor = c.accent,
                            inactiveTrackColor = c.fill
                        )
                    )
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(5.dp)) {
                        listOf(16000 to "16K", 32000 to "32K", 64000 to "64K", 100000 to "100K", 120000 to "120K", 200000 to "200K").forEach { (tokInt, label) ->
                            val selected = threshCtxTok == tokInt
                            Text(
                                label,
                                Modifier.weight(1f)
                                    .clip(RoundedCornerShape(9.dp))
                                    .background(if (selected) c.accent else c.fill)
                                    .iosPressScale(0.95f) {
                                        model.applyContextWindowTokens(tokInt, targetBotId)
                                    }
                                    .padding(vertical = 8.dp),
                                color = if (selected) Color.White else c.ink,
                                fontSize = 11.5.sp,
                                fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
                                textAlign = TextAlign.Center,
                                style = TextStyle(fontFeatureSettings = "tnum"))
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
                            fontWeight = FontWeight.SemiBold)
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
                Text("头像造型、主题色、名称、简介与系统指令在所有客户端保持一致", color = c.muted, fontSize = 11.5.sp)
                Text("主题色（色彩特征）", color = c.muted, fontSize = 12.5.sp, fontWeight = FontWeight.Medium)
                BotColorSwatchRow(
                    selectedColorHex = s.editBotColor,
                    onSelectColor = { model.updateBotEditField(color = it) })
                Text("几何特征（头像造型）", color = c.muted, fontSize = 12.5.sp, fontWeight = FontWeight.Medium)
                BotShapeSelectorGrid(
                    selectedShape = s.editBotShape,
                    previewColorHex = s.editBotColor,
                    onSelectShape = { model.updateBotEditField(shape = it) })
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
                        fontWeight = FontWeight.Medium)
                    if (s.editBotModel.isNotBlank()) {
                        Text(
                            "清空（恢复跟随默认）",
                            Modifier.clip(RoundedCornerShape(10.dp))
                                .background(c.fill)
                                .iosPressScale(0.95f) { model.updateBotEditField(modelName = "") }
                                .padding(horizontal = 12.dp, vertical = 8.dp),
                            color = c.muted,
                            fontSize = 12.sp)
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
                                fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal)
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
