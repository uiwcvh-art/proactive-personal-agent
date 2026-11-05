package dev.bubuki.proactive

import android.graphics.BitmapFactory
import android.util.Base64
import androidx.compose.foundation.*
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import kotlin.math.abs
import kotlin.math.min

@Composable internal fun RootPhoneScreen(state: RootPhoneState, busy: Boolean, error: String,
    command: suspend (String, JSONObject) -> Boolean, settings: () -> Unit, back: () -> Unit) {
    val dark = appearanceDark(); val activity = LocalContext.current as androidx.activity.ComponentActivity
    LaunchedEffect(dark) { activity.systemBarsFor(dark) }
    PulseTheme(dark = dark) {
        val colors = PulseTheme.colors; val scope = rememberCoroutineScope(); val display = remember(state.display) { JSONObject(state.display) }
        val created = display.optBoolean("created"); val manual = state.phase != "running" && !busy
        var picking by remember { mutableStateOf(false) }; var input by remember { mutableStateOf("") }
        val image by produceState<ImageBitmap?>(null, state.image) { value = withContext(Dispatchers.Default) { if (state.image.isBlank()) null else runCatching { val bytes = Base64.decode(state.image, Base64.DEFAULT); BitmapFactory.decodeByteArray(bytes, 0, bytes.size)?.asImageBitmap() }.getOrNull() } }
        fun act(op: String, args: JSONObject = JSONObject()) { scope.launch { command(op, args) } }
        Column(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background).windowInsetsPadding(WindowInsets.systemBars.union(WindowInsets.ime))) {
            Row(Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = back, Modifier.semantics { contentDescription = "返回私人空间" }) { PulseIcon("back", colors.ink) }
                Column(Modifier.padding(start = 9.dp).weight(1f)) { Text("手机工作区", color = colors.ink, fontWeight = FontWeight.SemiBold, fontSize = 19.sp); Text(if (created) "独立屏 #${display.optInt("displayId")} · Root" else "此手机", color = colors.ink2, fontSize = 11.sp) }
                if (state.active) TextButton(onClick = { act("stop") }) { Text("停止", color = MaterialTheme.colorScheme.error) }
            }
            Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal = 22.dp).padding(bottom = 28.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
                Column(Modifier.fillMaxWidth().padding(top = 16.dp, bottom = 6.dp)) {
                    Text("YOUR PHONE, YOUR WORKSPACE", color = colors.accent, fontSize = 10.sp)
                    Text("在独立屏里，\n把事情做完。", Modifier.padding(top = 13.dp), color = colors.ink, fontWeight = FontWeight.SemiBold, fontSize = 30.sp, lineHeight = 40.sp)
                    Text("任务、模型和执行记录都在这个 App。\n手机操作只使用 Root 独立工作区。", Modifier.padding(top = 12.dp), color = colors.ink2, fontSize = 13.sp, lineHeight = 22.sp)
                }
                Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(colors.fill).border(1.dp, colors.hair, RoundedCornerShape(16.dp)).padding(15.dp), verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.size(7.dp).clip(RoundedCornerShape(4.dp)).background(if (state.root) Color(0xFF6BCEA1) else colors.ink2))
                    Text(if (busy) "正在连接…" else if (state.sdk < 34) "独立焦点需要 Android 14 或更高" else if (state.root) "Root 已连接 · uid 0" else "尚未授权 Root", Modifier.padding(start = 11.dp).weight(1f), color = colors.ink, fontSize = 12.sp)
                }
                if (!state.root) {
                    Box(Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp)).background(Brush.horizontalGradient(listOf(Color(0xFF7C5CFF), Color(0xFF3DAFCB)))).clickable(enabled = !busy && state.sdk >= 34) { act("connect") }.padding(16.dp), contentAlignment = Alignment.Center) { Text("连接 Root", color = Color.White, fontWeight = FontWeight.SemiBold) }
                    Text("首次连接需要在手机的 Root 管理器中授权。没有授权时，手机工具保持关闭。", color = colors.ink2, fontSize = 12.sp, lineHeight = 20.sp)
                } else Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        OutlinedButton(onClick = { act("create") }, enabled = !created && !busy, modifier = Modifier.weight(1f)) { Text("创建工作区") }
                        OutlinedButton(onClick = { act("apps"); picking = true }, enabled = created && manual, modifier = Modifier.weight(1f)) { Text("打开应用") }
                    }
                    if (created) {
                        val overlayShowing by FloatingVirtualDisplay.overlayVisible.collectAsState()
                        Box(
                            Modifier
                                .fillMaxWidth()
                                .clip(RoundedCornerShape(14.dp))
                                .background(Brush.horizontalGradient(listOf(Color(0xFF7C5CFF), Color(0xFF3DAFCB))))
                                .clickable {
                                    if (!FloatingVirtualDisplay.canOverlay(activity)) {
                                        FloatingVirtualDisplay.requestOverlayPermission(activity)
                                    } else {
                                        FloatingVirtualDisplay.show(activity, forceExpand = false)
                                        val home = android.content.Intent(android.content.Intent.ACTION_MAIN).addCategory(android.content.Intent.CATEGORY_HOME).addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)
                                        runCatching { activity.startActivity(home) }
                                    }
                                }
                                .padding(14.dp),
                            contentAlignment = Alignment.Center
                        ) {
                            Text(
                                if (!FloatingVirtualDisplay.canOverlay(activity)) "开启悬浮窗权限 · 让虚拟屏浮在桌面上"
                                else if (overlayShowing) "✨ 虚拟屏已浮在桌面 · 点此回桌面查看小窗"
                                else "✨ 弹出桌面悬浮小窗（回到桌面直接看）",
                                color = Color.White,
                                fontWeight = FontWeight.SemiBold,
                                fontSize = 13.sp
                            )
                        }
                    }
                }
                if (error.isNotBlank() || state.error.isNotBlank()) Text(error.ifBlank { state.error }, color = MaterialTheme.colorScheme.error, fontSize = 12.sp, lineHeight = 20.sp)
                if (created) {
                    val frame = remember(state.imageInfo, state.display) { runCatching { JSONObject(state.imageInfo.ifBlank { state.display }) }.getOrDefault(JSONObject()) }; val width = frame.optInt("width", display.optInt("width", 1080)).coerceAtLeast(1); val height = frame.optInt("height", display.optInt("height", 2400)).coerceAtLeast(1)
                    BoxWithConstraints(Modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(colors.fill).border(1.dp, colors.hair, RoundedCornerShape(18.dp)).padding(12.dp), contentAlignment = Alignment.Center) {
                        val h = min(440f, maxWidth.value * height / width).dp; val w = (h.value * width / height).dp
                        val bitmap = image
                        if (bitmap == null) Text("打开一个应用后，画面会出现在这里。", Modifier.padding(32.dp), color = colors.ink2, fontSize = 12.sp)
                        else Image(bitmap, "独立工作区实时画面", Modifier.size(w, h).clip(RoundedCornerShape(9.dp)).pointerInput(manual, state.imageInfo) {
                            if (!manual) return@pointerInput
                            awaitEachGesture {
                                val down = awaitFirstDown(requireUnconsumed = false); var end = down.position; var ended = false
                                do { val event = awaitPointerEvent(); val pointer = event.changes.firstOrNull { it.id == down.id } ?: break; end = pointer.position; pointer.consume(); if (!pointer.pressed) { ended = true; break } } while (true)
                                if (ended) {
                                    fun x(value: Float) = (value / size.width * width).toInt().coerceIn(0, width - 1)
                                    fun y(value: Float) = (value / size.height * height).toInt().coerceIn(0, height - 1)
                                    val args = JSONObject().put("display_id", frame.optInt("displayId")).put("expected_rotation", frame.optInt("rotation"))
                                    if (abs(end.x - down.position.x) + abs(end.y - down.position.y) < viewConfiguration.touchSlop) { args.put("x", x(end.x)).put("y", y(end.y)); act("tap", args) }
                                    else { args.put("x1", x(down.position.x)).put("y1", y(down.position.y)).put("x2", x(end.x)).put("y2", y(end.y)); act("swipe", args) }
                                }
                            }
                        })
                    }
                    Text(if (state.phase == "running") "助手正在执行。停止后可手动点按工作区。" else "点按或滑动画面即可操作独立屏。", color = colors.ink2, fontSize = 11.sp)
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) { OutlinedButton(onClick = { act("capture") }, enabled = !busy) { Text("刷新") }; OutlinedButton(onClick = { act("key", JSONObject().put("key", "back")) }, enabled = manual) { Text("返回") }; OutlinedButton(onClick = { act("release") }, enabled = manual && !state.active) { Text("释放") } }
                    OutlinedTextField(input, { input = it.take(4000) }, label = { Text("输入到工作区") }, modifier = Modifier.fillMaxWidth(), enabled = manual, maxLines = 3)
                    OutlinedButton(onClick = { scope.launch { if (command("text", JSONObject().put("text", input))) input = "" } }, enabled = manual && input.isNotEmpty(), modifier = Modifier.fillMaxWidth()) { Text("发送文字") }
                }
                if (state.active || state.result.isNotBlank()) {
                    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(colors.fill).padding(16.dp), verticalArrangement = Arrangement.spacedBy(9.dp)) {
                        Text(when (state.phase) { "running" -> "正在执行"; "waiting_user" -> "需要你接管"; "stopping" -> "正在停止"; "failed", "stopped" -> "执行已停止"; else -> "本轮结果" }, color = colors.ink, fontWeight = FontWeight.Medium)
                        Text(state.goal, color = colors.ink2, fontSize = 13.sp); if (state.result.isNotBlank()) Text(state.result, color = colors.ink, fontSize = 13.sp)
                        Text("已记录 ${org.json.JSONArray(state.trace).length()} 个工具动作 · 完整记录在对话中", color = colors.ink2, fontSize = 11.sp)
                        if (state.phase == "waiting_user") Button(onClick = { act("resume") }) { Text("我处理好了，继续") }
                    }
                }
                OutlinedButton(onClick = settings, modifier = Modifier.fillMaxWidth()) { Text(if (state.modelReady) "执行模型 · ${state.model}" else "配置支持视觉的执行模型") }
                Text("提醒权限", color = colors.ink2, fontSize = 12.sp)
                Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(colors.fill).padding(8.dp)) {
                    listOf(Triple("notifications", "通知授权", state.notifications), Triple("overlay", "全局提示卡", state.overlay), Triple("listener", "通知已读同步", state.listener), Triple("background", "后台云端连接", state.background)).forEach { (op, label, enabled) ->
                        TextButton(onClick = { act(op) }, modifier = Modifier.fillMaxWidth()) { Text(label, Modifier.weight(1f), color = colors.ink); Text(if (enabled) "已开启" else "设置", color = colors.ink2, fontSize = 12.sp) }
                    }
                }
                Text("创建工作区或启动手机任务后，虚拟屏会自动以小窗口浮在系统桌面上（点按展开直接触控、拖动手柄吸边收起）。", color = colors.ink2, fontSize = 12.sp, lineHeight = 20.sp)
            }
        }
        if (picking) AlertDialog(onDismissRequest = { picking = false }, title = { Text("在工作区打开") }, text = {
            LazyColumn(Modifier.heightIn(max = 400.dp)) { items(state.apps, key = { it.component }) { app -> TextButton(onClick = { picking = false; act("launch", JSONObject().put("component", app.component)) }, modifier = Modifier.fillMaxWidth()) { Text(app.name, Modifier.fillMaxWidth()) } }; if (state.apps.isEmpty()) item { Text("正在读取可启动的应用…") } }
        }, confirmButton = { TextButton(onClick = { picking = false }) { Text("关闭") } })
    }
}
