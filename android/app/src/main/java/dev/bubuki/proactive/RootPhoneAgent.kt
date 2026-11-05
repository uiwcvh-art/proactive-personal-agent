package dev.bubuki.proactive

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import okhttp3.Call
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/**
 * Our phone harness: OpenAI-compatible tools execute only in an authenticated Root workspace.
 * Strategy: UI Accessibility Node Tree (`phone_ui_tree` + `phone_tap_node` / `phone_type_node`) FIRST;
 * raw coordinate hard-tap (`phone_tap(x,y)`) and visual screenshot (`phone_capture`) LAST as fallback.
 */
internal class RootPhoneAgent {
    @Volatile private var currentCall: Call? = null
    private val client = OkHttpClient.Builder().connectTimeout(15, TimeUnit.SECONDS).readTimeout(90, TimeUnit.SECONDS).callTimeout(110, TimeUnit.SECONDS).build()
    fun cancel() { currentCall?.cancel() }
    data class Outcome(val text: String, val needsUser: Boolean = false)

    suspend fun run(goal: String, config: JSONObject, workspace: JSONObject, history: List<PulseMessage>,
        execute: suspend (String, JSONObject) -> JSONObject, onStep: suspend (JSONObject) -> Unit): Outcome = withContext(Dispatchers.IO) {
        val base = config.getString("base_url").trim().trimEnd('/')
        val endpoint = if (base.endsWith("/chat/completions")) base else "$base/chat/completions"
        require(endpoint.startsWith("https://") || endpoint.startsWith("http://")) { "执行模型地址须为 HTTP(S)" }
        val messages = JSONArray().put(JSONObject().put("role", "system").put("content", """
            你是私人助手内的手机执行引擎，所有操作通过本次 Root 独立虚拟屏工具完成。
            当前工作区：$workspace
            【核心操作优先级（无障碍节点树优先，坐标硬点放最后）】：
            1. 打开应用：调用 `phone_launch`（可直接传应用名如 "学习通" 或 package/.Activity）。
            2. 读取界面：启动或每次动作后，**优先调用 `phone_ui_tree`** 读取当前屏幕的无障碍 UI 节点树（返回 `@e1`, `@e2` 等编号、文字、角色与中心坐标，耗时仅几毫秒）。
            3. 点击与输入：**优先调用 `phone_tap_node`（传 `ref="@e1"` 或 `text="按钮文字"`）和 `phone_type_node`（传 `ref="@e2"` 和 `text="输入内容"`）**！
            4. 兜底手段（放最后）：**只有当 `phone_ui_tree` 节点为空、或者是图片/Canvas/WebView 自绘按钮找不到对应节点时**，才调用 `phone_capture` 截图并用 `phone_tap(x, y)` 坐标硬点！
            5. 遇到登录、图形验证码、支付或人脸识别，调用 `need_user` 请用户接管。任务完成时直接用简短中文交回结果。
        """.trimIndent()))
        history.takeLast(12).forEach { message -> messages.put(JSONObject().put("role", if (message.role == "user") "user" else "assistant").put("content", message.text.take(6000))) }
        messages.put(JSONObject().put("role", "user").put("content", goal))
        val tools = tools()
        repeat(24) { index ->
            currentCoroutineContext().ensureActive()
            val body = JSONObject().put("model", config.getString("model")).put("messages", messages).put("tools", tools).put("stream", false)
            val request = Request.Builder().url(endpoint).header("Authorization", "Bearer " + config.getString("api_key"))
                .post(body.toString().toRequestBody("application/json; charset=utf-8".toMediaType())).build()
            val call = client.newCall(request); currentCall = call
            val response = try { call.execute().use { reply ->
                if (!reply.isSuccessful) throw Exception("执行模型返回 HTTP ${reply.code}，请检查模型连接")
                val source = reply.body ?: throw Exception("执行模型没有返回内容")
                val bytes = Io.read(source.byteStream(), 8_000_000)
                JSONObject(String(bytes, Charsets.UTF_8)).getJSONArray("choices").getJSONObject(0).getJSONObject("message")
            } } catch (error: Exception) { currentCoroutineContext().ensureActive(); throw error } finally { currentCall = null }
            currentCoroutineContext().ensureActive()
            val calls = response.optJSONArray("tool_calls")
            if (calls == null || calls.length() == 0) return@withContext Outcome(response.optString("content").ifBlank { "本轮没有返回可执行动作，请补充一个具体目标。" })
            require(calls.length() <= 6) { "一次最多执行 6 个手机工具动作" }
            messages.put(response)
            val images = JSONArray()
            for (position in 0 until calls.length()) {
                currentCoroutineContext().ensureActive()
                val toolCall = calls.getJSONObject(position); val function = toolCall.getJSONObject("function")
                val name = function.getString("name"); val arguments = JSONObject(function.optString("arguments", "{}"))
                if (name == "need_user") return@withContext Outcome(arguments.optString("question").ifBlank { "请在工作区完成当前确认后继续。" }, true)
                val start = android.os.SystemClock.uptimeMillis()
                val result = try { execute(name, arguments) } catch (error: Exception) {
                    if (error is kotlinx.coroutines.CancellationException) throw error
                    JSONObject().put("error", MainActivity.safeError(error))
                }
                val clean = JSONObject(result.toString())
                val image = clean.optString("image")
                clean.remove("image")
                // Keep tool response compact: if `ui_tree` has `summary`, omit verbose `nodes` array in LLM tool message
                if (name == "phone_ui_tree" && clean.has("summary")) {
                    clean.remove("nodes")
                }
                val trace = JSONObject().put("step", index + 1).put("tool", name).put("arguments", arguments)
                    .put("result", clean).put("duration_ms", android.os.SystemClock.uptimeMillis() - start)
                onStep(trace)
                messages.put(JSONObject().put("role", "tool").put("tool_call_id", toolCall.getString("id")).put("content", clean.toString()))
                if (image.isNotBlank()) {
                    images.put(JSONObject().put("type", "text").put("text", "刚刚获取的工作区截图，坐标范围 ${clean.optInt("width")}×${clean.optInt("height")}，display_id=${clean.optInt("displayId")}。"))
                    images.put(JSONObject().put("type", "image_url").put("image_url", JSONObject().put("url", "data:image/png;base64,$image")))
                }
            }
            if (images.length() > 0) messages.put(JSONObject().put("role", "user").put("content", images))
        }
        Outcome("本轮已执行到 24 步并自动释放队列。你可以查看执行记录，或直接发送下一条指令。", false)
    }

    private fun tools(): JSONArray {
        fun field(type: String, description: String) = JSONObject().put("type", type).put("description", description)
        fun definition(name: String, description: String, fields: Map<String, JSONObject> = emptyMap(), required: List<String> = fields.keys.toList()): JSONObject =
            JSONObject().put("type", "function").put("function", JSONObject().put("name", name).put("description", description)
                .put("parameters", JSONObject().put("type", "object").put("properties", JSONObject(fields)).put("required", JSONArray(required)).put("additionalProperties", false)))
        val display = field("integer", "本次工作区 displayId，必须大于 0").put("minimum", 1)
        val x = field("integer", "最新截图中的水平坐标").put("minimum", 0)
        val y = field("integer", "最新截图中的垂直坐标").put("minimum", 0)
        return JSONArray(listOf(
            definition("phone_launch", "在独立虚拟屏启动应用（直接传应用名称如 '学习通' 或组件名）", mapOf("display_id" to display, "component" to field("string", "应用名称（如 '学习通'）或 package/.Activity")), listOf("component")),
            definition("phone_ui_tree", "【首选】读取当前独立虚拟屏的无障碍 UI 节点树，返回带 @e1, @e2 编号、角色、文字与中心坐标的紧凑元素列表", mapOf("display_id" to display), emptyList()),
            definition("phone_tap_node", "【首选】根据 phone_ui_tree 返回的节点编号 ref（如 '@e3'）或可见文字 text（如 '作业'）精准点击控件", mapOf("ref" to field("string", "节点编号，如 '@e1'"), "text" to field("string", "控件上的文字或描述，如 '签到' / '作业'"), "id" to field("string", "可选 resource_id")), emptyList()),
            definition("phone_type_node", "【首选】在指定输入框节点（ref 如 '@e2' 或 target 提示词）中输入文字", mapOf("ref" to field("string", "输入框节点编号，如 '@e2'"), "target" to field("string", "可选输入框提示文字"), "text" to field("string", "要输入的文字")), listOf("text")),
            definition("phone_swipe", "在独立虚拟屏滑动（如上下翻页）", mapOf("display_id" to display, "x1" to x, "y1" to y, "x2" to x, "y2" to y), listOf("x1", "y1", "x2", "y2")),
            definition("phone_key", "发送独立屏按键", mapOf("display_id" to display, "key" to field("string", "工作区按键").put("enum", JSONArray(listOf("back", "home", "enter", "tab", "delete", "up", "down", "left", "right")))), listOf("key")),
            definition("phone_apps", "列出此手机可启动的应用与组件名"),
            definition("phone_status", "读取当前 Root 工作区状态"),
            definition("phone_capture", "【兜底】仅当 phone_ui_tree 找不到节点（如纯图片/Canvas 界面）时，截图查看画面", mapOf("display_id" to display), emptyList()),
            definition("phone_tap", "【最后兜底硬点】仅当节点点击无效或无节点时，按坐标 (x, y) 硬点", mapOf("display_id" to display, "x" to x, "y" to y), listOf("x", "y")),
            definition("phone_text", "【兜底】在当前已聚焦的输入框粘贴输入文字", mapOf("display_id" to display, "text" to field("string", "待输入文字，最多 4000 字")), listOf("text")),
            definition("need_user", "需要用户完成登录、验证码或授权时停止并请求接管", mapOf("question" to field("string", "用户需要处理的具体事项")))
        ))
    }
}
