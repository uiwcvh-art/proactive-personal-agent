package dev.bubuki.proactive

import android.content.Intent
import android.graphics.BitmapFactory
import android.net.Uri
import android.os.Bundle
import android.util.Base64
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

/**
 * [Module] PulseActivityMedia
 * [Responsibility]
 * 多媒體、雲端桌面流與系統權限交互領域服務：封裝文件附件選擇與上傳、系統分享 Intent 接收與解析、
 * 雲端桌面 (Cloud Desktop) 畫面拉取與遠程鍵鼠觸控注入、手機獨立虛擬屏懸浮小窗啟動與無障礙權限引導、
 * 多端節點身份切換 (`toggleNodeSource`)、全局/Bot 快捷菜單構建，以及貼紙表情包緩存與加載。
 *
 * [Public API]
 * - `NativePulseModel.chooseAttachment()`, `documentPicked(...)`, `handleIncomingShareIntent(...)`, `clearAttachments()`
 * - `NativePulseModel.openCloudDesktop()`, `refreshCloudDesktopFrame(...)`, `cloudDesktopNavigate(...)`, `cloudDesktopClick(...)`, `cloudDesktopScroll(...)`, `cloudDesktopLaunchApp(...)`, `cloudDesktopType(...)`, `cloudDesktopKey(...)`
 * - `NativePulseModel.launchFloatingVirtualDisplay()`, `openAccessibilitySettings()`, `connectRootVirtualDisplay()`, `requestPhonePermission(...)`
 * - `NativePulseModel.newMenu()`, `botMenu(...)`, `actionsMenu()`, `toggleNodeSource()`
 * - `NativePulseModel.loadStickerBitmap(...)`, `fetchStickerPacks()`, `toggleStickerPack(...)`
 */

internal fun NativePulseModel.dismissCloudDesktopPip() {
    ui = ui.copy(cloudDesktopPipVisible = false)
}

internal fun NativePulseModel.showCloudDesktopPip() {
    ui = ui.copy(cloudDesktopPipVisible = true)
    scope.launch { refreshCloudDesktopFrame(forceFull = true) }
}

internal fun NativePulseModel.openCloudDesktop() {
    dismissMenu()
    ui = ui.copy(activePanel = "cloud_desktop", cloudDesktopLoading = ui.cloudDesktopFrame == null)
    startCloudDesktopStreamLoop(forceFull = true)
}

internal fun NativePulseModel.startCloudDesktopStreamLoop(forceFull: Boolean = false) {
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

internal suspend fun NativePulseModel.applyCloudDesktopPacket(res: JSONObject) {
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

internal suspend fun NativePulseModel.refreshCloudDesktopFrame(forceFull: Boolean = false) {
    runCatching {
        val q = JSONObject().put("quality", 60)
        if (!forceFull && ui.cloudDesktopHash.isNotBlank()) {
            q.put("since", ui.cloudDesktopHash)
        }
        val res = request("GET", "/api/computer/frame", query = q)
        applyCloudDesktopPacket(res)
    }.onFailure { ui = ui.copy(cloudDesktopLoading = false) }
}

internal fun NativePulseModel.cloudDesktopNavigate(url: String) {
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

internal fun NativePulseModel.cloudDesktopClick(normX: Float, normY: Float, kind: String = "click") {
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

internal fun NativePulseModel.cloudDesktopScroll(normX: Float, normY: Float, direction: String, amount: Int = 4) {
    val x = (normX.coerceIn(0f, 1f) * 1279f).toInt()
    val y = (normY.coerceIn(0f, 1f) * 799f).toInt()
    perform {
        val res = request("POST", "/api/computer/input", JSONObject().put("kind", "scroll").put("x", x).put("y", y).put("direction", direction).put("amount", amount).put("include_frame", true))
        applyCloudDesktopPacket(res)
        if (!res.has("image")) refreshCloudDesktopFrame(forceFull = true)
    }
}

internal fun NativePulseModel.cloudDesktopLaunchApp(appKey: String, argText: String = "") {
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

internal fun NativePulseModel.cloudDesktopType(text: String) {
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

internal fun NativePulseModel.cloudDesktopKey(key: String) {
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

internal fun NativePulseModel.toggleNodeSource() {
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

internal fun NativePulseModel.newMenu() {
    val curSrc = SecureTransport.getNodeSource(activity)
    val nodeLabel = if (curSrc == "phone2") "安卓二 (平板/副机)" else "安卓一 (主力手机)"
    val vdState = RootPhoneHarness.changes.value
    val entries = mutableListOf(
        PulseMenuItem("创建新 Bot", "创建专属角色、指令与独立模型", "plus") { openBotEditor(null) },
        PulseMenuItem("切换多端节点身份 (当前：$nodeLabel)", "点击在「安卓一」与「安卓二」之间切换，各端对话独立不串台", "spark") { toggleNodeSource() },
        PulseMenuItem("手机虚拟屏 (桌面悬浮小窗)", "创建并弹出浮在系统桌面上的独立小窗", "screen") { launchFloatingVirtualDisplay() }
    )
    if (vdState.displayCreated) {
        entries += PulseMenuItem("销毁手机虚拟屏 (#${vdState.displayId})", "彻底释放后台虚拟屏并关闭悬浮小窗，不干扰主屏应用", "close") { destroyVirtualDisplay() }
    }
    entries += listOf(
        PulseMenuItem("云端桌面 (Cloud Desktop)", "查看云端浏览器实时画面与触控接管", "cloud") { openCloudDesktop() },
        PulseMenuItem("定时闹钟与例行自动化", "查看和管理每日定点提醒、课表闹钟与巡检", "clock") { openSchedules() },
        PulseMenuItem("透明记忆库 (Obsidian Vault)", "查看或修改 Markdown 记忆与画像", "vault") { openVault("INDEX.md") },
        PulseMenuItem("设置中心与检查更新", "日夜主题切换、API 模型配置与一键更新客户端", "settings") { openSettings() },
        PulseMenuItem("一键下载最新客户端更新", "直接从云端拉取并安装最新 APK (当前 v${BuildConfig.VERSION_NAME})", "spark") { downloadLatestApk() }
    )
    menu("DSB 工作台 · $nodeLabel", entries)
}

internal fun NativePulseModel.botMenu(id: String = ui.botId) {
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

internal fun NativePulseModel.actionsMenu() {
    val (curTok, threshTok, msgCnt) = contextStatsFor(ui.botId)
    val (cachedTok, cachePct) = contextCacheFor(ui.botId)
    val pct = ((curTok.toFloat() / threshTok.coerceAtLeast(1)) * 100).toInt()
    val vdState = RootPhoneHarness.changes.value
    val entries = mutableListOf(
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
        }
    )
    if (vdState.displayCreated) {
        entries += PulseMenuItem("销毁手机虚拟屏 (#${vdState.displayId})", "彻底关闭并释放虚拟屏与悬浮小窗", "close") { destroyVirtualDisplay() }
    }
    entries += listOf(
        PulseMenuItem("打开云端桌面", "查看云端浏览器实时画面或接管", "cloud") { secondary("computer") },
        PulseMenuItem("定时闹钟与例行自动化", "管理每日准点提醒与后台巡检", "clock") { openSchedules() },
        PulseMenuItem("触发好奇心主动搭话", "让主助理结合记忆投递实用情报或追问", "spark") { job("curiosity") },
        PulseMenuItem("打开透明记忆库", "查看或编辑 memory_vault/*.md", "vault") { openVault("INDEX.md") },
        PulseMenuItem("编辑当前 Bot 模型与设定", icon = "settings") { openBotEditor(ui.botId) },
        PulseMenuItem("设置中心与检查更新", "切换日间/夜间模式、API 配置或更新 APK", "settings") { openSettings() },
        PulseMenuItem("免打扰 1 小时", icon = "moon") { job("snooze") }
    )
    menu("工作台与工具", entries)
}

internal fun NativePulseModel.destroyVirtualDisplay() {
    dismissMenu()
    FloatingVirtualDisplay.hide()
    perform {
        try {
            withContext(Dispatchers.IO) {
                RootPhoneHarness.initialize(activity)
                RootPhoneHarness.command(
                    activity,
                    JSONObject()
                        .put("op", "release")
                        .put("destroy", true)
                        .put("user_close", true)
                        .put("manual", true)
                )
            }
            toast("已彻底销毁手机虚拟屏")
        } catch (e: Exception) {
            toast("销毁虚拟屏失败：${MainActivity.safeError(e)}")
        }
    }
}

internal fun NativePulseModel.launchFloatingVirtualDisplay() {
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

internal fun NativePulseModel.chooseAttachment() {
    if (!ensurePaired() || ui.uploading || pickerBot != null || ui.busyBot != null) return
    pickerBot = ui.botId
    pickerSource = "workspace"
    try { activity.pickDocument() } catch (e: Exception) { pickerBot = null; pickerSource = null; toast(MainActivity.safeError(e)) }
}

internal fun NativePulseModel.restorePicker(state: Bundle?) {
    pickerBot = state?.getString("upload_bot")
    pickerSource = state?.getString("upload_source")
}

internal fun NativePulseModel.savePicker(state: Bundle) {
    pickerBot?.let { state.putString("upload_bot", it); state.putString("upload_source", pickerSource) }
}

internal fun NativePulseModel.documentPicked(uri: Uri?) {
    val id = pickerBot
    val source = pickerSource
    pickerBot = null
    pickerSource = null
    if (uri == null || id == null || source == null) return
    ui = ui.copy(uploading = true)
    perform {
        try {
            val result = withContext(Dispatchers.IO) { CloudFiles.upload(activity, uri, source) }
            val file = PulseAttachment.from(result.put("source", source))
            synchronized(draftLock) { attachmentDrafts[id] = attachments(id) + file }
            localRevision++
            if (ui.botId == id) ui = ui.copy(attachments = attachments(id))
            withContext(Dispatchers.IO) { persistDrafts().get() }
            toast("已上传：${file.name}")
        } finally {
            ui = ui.copy(uploading = false)
        }
    }
}

@Suppress("DEPRECATION")
internal fun NativePulseModel.handleIncomingShareIntent(intent: Intent?) {
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

internal fun NativePulseModel.clearAttachments() {
    synchronized(draftLock) { attachmentDrafts[ui.botId] = emptyList() }
    localRevision++
    ui = ui.copy(attachments = emptyList())
    persistDrafts()
}

internal fun NativePulseModel.openLink(value: String) {
    if (value.startsWith("#vault:")) {
        if (!ensurePaired()) return
        val path = Uri.decode(value.removePrefix("#vault:")).let { if (it.endsWith(".md")) it else "$it.md" }
        openVault(path)
        return
    }
    val target = Uri.parse(value)
    if (target.scheme !in listOf("http", "https") || target.host.isNullOrBlank()) { toast("只支持 HTTP(S) 网页链接。"); return }
    try { activity.startActivity(Intent(Intent.ACTION_VIEW, target)) } catch (e: Exception) { toast("没有可用的浏览器。") }
}

internal fun NativePulseModel.sendPhone(text: String) {
    if (text.length > 6000) { toast("手机任务最多 6000 字，请分成几步交代。"); return }
    val selectedBot = ui.botId
    val originalDraft = ui.draft
    val context = ui.messages.filter { it.botId == selectedBot }.takeLast(12)
    ui = ui.copy(busyBot = selectedBot)
    perform {
        try {
            RootPhoneHarness.submit(activity, selectedBot, text, context)
            if (draft(selectedBot) == originalDraft) {
                synchronized(draftLock) { drafts[selectedBot] = "" }
                localRevision++
                if (ui.botId == selectedBot) ui = ui.copy(draft = "")
                withContext(Dispatchers.IO) { persistDrafts().get() }
            }
            applyPhoneState()
            toast("手机独立工作台已开始执行，可随时查看或停止。")
        } finally {
            ui = ui.copy(busyBot = null)
        }
    }
}

internal fun NativePulseModel.openAccessibilitySettings() {
    runCatching {
        activity.startActivity(Intent(android.provider.Settings.ACTION_ACCESSIBILITY_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        toast("请在无障碍列表中找到「DSB」并开启")
    }.onFailure {
        toast("无法打开系统无障碍设置，请手动前往系统设置 -> 无障碍")
    }
}

internal fun NativePulseModel.connectRootVirtualDisplay() {
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

internal fun NativePulseModel.requestPhonePermission(op: String) {
    perform {
        withContext(Dispatchers.IO) {
            RootPhoneHarness.command(activity, JSONObject().put("op", op))
            RootPhoneHarness.status(activity)
        }
    }
}

internal fun NativePulseModel.loadStickerBitmap(stickerPath: String, onLoaded: (ImageBitmap) -> Unit) {
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

internal fun NativePulseModel.fetchStickerPacks() {
    perform {
        runCatching {
            val res = request("GET", "/api/plugins/stickers")
            ui = ui.copy(stickerPacks = res.optJSONArray("packs")?.objects() ?: emptyList())
        }
    }
}

internal fun NativePulseModel.toggleStickerPack(packId: String, enabled: Boolean) {
    perform {
        val res = request("POST", "/api/plugins/stickers/toggle", JSONObject().put("pack_id", packId).put("enabled", enabled))
        ui = ui.copy(stickerPacks = res.optJSONArray("packs")?.objects() ?: ui.stickerPacks)
        toast(if (enabled) "已启用表情包插件" else "已关闭表情包插件")
    }
}
