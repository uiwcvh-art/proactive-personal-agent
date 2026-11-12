// Adapted from wangzan101/Mika, e9daf53 (Apache-2.0). See assets/mika-LICENSE.txt.
package dev.bubuki.proactive.markdown

import android.content.ActivityNotFoundException
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.graphics.Color as AndroidColor
import android.net.Uri
import android.webkit.WebView
import android.widget.Toast
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.Layout
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.platform.UriHandler
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import dev.bubuki.proactive.PulseTheme

/** 按 Markdown 显示一段回答；流式草稿用临时闭合的语法预览，始终保留上一帧防止闪烁。 */
@Composable
fun MarkdownText(markdown: String, style: TextStyle, modifier: Modifier = Modifier, streaming: Boolean = false, onOpenUri: ((String) -> Unit)? = null) {
    val colors = PulseTheme.colors
    val styles = remember(colors) { MarkdownModel.Styles.of(colors) }
    var streamingBlocks by remember { mutableStateOf<List<MdBlock>>(emptyList()) }
    LaunchedEffect(markdown, styles, streaming) {
        if (streaming) {
            if (streamingBlocks.isNotEmpty() && markdown.length > 160) {
                kotlinx.coroutines.delay(36L)
            }
            streamingBlocks = withContext(Dispatchers.Default) { MarkdownModel.parseStreaming(markdown, styles) }
        }
    }
    val blocks = if (streaming) {
        if (streamingBlocks.isEmpty() && markdown.isNotBlank() && markdown.length <= 400) remember(markdown, styles) { MarkdownModel.parseStreaming(markdown, styles) }
        else streamingBlocks
    } else remember(markdown, styles) { MarkdownModel.parse(markdown, styles) }
    val context = LocalContext.current
    val uriHandler = remember(context, onOpenUri) {
        object : UriHandler {
            override fun openUri(uri: String) {
                if (onOpenUri != null) { onOpenUri(uri); return }
                try {
                    context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(uri)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                } catch (e: ActivityNotFoundException) {
                    Toast.makeText(context, "没有可用的浏览器", Toast.LENGTH_SHORT).show()
                }
            }
        }
    }
    CompositionLocalProvider(LocalUriHandler provides uriHandler) {
        Blocks(blocks, style, modifier, depth = 0)
    }
}

@Composable
private fun Blocks(blocks: List<MdBlock>, style: TextStyle, modifier: Modifier = Modifier, depth: Int, gap: Int = 10) {
    Column(modifier, verticalArrangement = Arrangement.spacedBy(gap.dp)) {
        blocks.forEach { Block(it, style, depth) }
    }
}

@Composable
private fun Block(b: MdBlock, style: TextStyle, depth: Int) {
    when (b) {
        is MdBlock.Para -> Text(b.text, style = style)
        is MdBlock.Heading -> Text(
            b.text,
            Modifier.padding(top = 4.dp),
            style = style.copy(
                fontSize = when (b.level) { 1 -> 20.sp; 2 -> 18.sp; 3 -> 17.sp; else -> style.fontSize },
                lineHeight = when (b.level) { 1 -> 26.sp; 2 -> 24.sp; else -> style.lineHeight },
                fontWeight = FontWeight.SemiBold,
                letterSpacing = (-0.3).sp
            ),
        )
        is MdBlock.Code -> {
            val trimmed = b.code.trim()
            val isSvg = (b.info.equals("svg", ignoreCase = true) || trimmed.startsWith("<svg", ignoreCase = true)) &&
                trimmed.contains("</svg>", ignoreCase = true)
            if (isSvg) {
                SvgBlock(trimmed)
            } else {
                val context = LocalContext.current
                val langLabel = b.info.trim().lowercase().ifBlank { "code" }
                val highlightedCode = remember(b.code, langLabel) { highlightCodeBlock(b.code, langLabel) }
                Column(
                    Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(12.dp))
                        .background(PulseTheme.colors.fill)
                        .border(0.5.dp, PulseTheme.colors.hair, RoundedCornerShape(12.dp))
                ) {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .background(PulseTheme.colors.hair.copy(alpha = 0.28f))
                            .padding(horizontal = 12.dp, vertical = 6.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text(
                            text = langLabel,
                            style = style.copy(
                                fontFamily = FontFamily.Monospace,
                                fontSize = 11.5.sp,
                                fontWeight = FontWeight.SemiBold,
                                color = PulseTheme.colors.ink2
                            )
                        )
                        Spacer(Modifier.weight(1f))
                        Text(
                            text = "复制",
                            style = style.copy(
                                fontSize = 11.5.sp,
                                fontWeight = FontWeight.Medium,
                                color = PulseTheme.colors.accent
                            ),
                            modifier = Modifier
                                .clip(RoundedCornerShape(6.dp))
                                .clickable {
                                    val cm = context.getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager
                                    cm?.setPrimaryClip(ClipData.newPlainText("code", b.code))
                                    Toast.makeText(context, "代码已复制", Toast.LENGTH_SHORT).show()
                                }
                                .padding(horizontal = 6.dp, vertical = 2.dp)
                        )
                    }
                    Box(
                        Modifier
                            .fillMaxWidth()
                            .horizontalScroll(rememberScrollState())
                            .padding(horizontal = 13.dp, vertical = 10.dp),
                    ) {
                        Text(
                            text = highlightedCode,
                            style = style.copy(fontFamily = FontFamily.Monospace, fontSize = 13.sp, lineHeight = 19.5.sp),
                            softWrap = false
                        )
                    }
                }
            }
        }
        is MdBlock.Quote -> {
            val firstText = (b.blocks.firstOrNull() as? MdBlock.Para)?.text?.text?.trim().orEmpty()
            val calloutColor = when {
                firstText.startsWith("[!NOTE]", ignoreCase = true) -> Color(0xFF3B82F6)
                firstText.startsWith("[!TIP]", ignoreCase = true) -> Color(0xFF10B981)
                firstText.startsWith("[!IMPORTANT]", ignoreCase = true) -> Color(0xFF8B5CF6)
                firstText.startsWith("[!WARNING]", ignoreCase = true) -> Color(0xFFD97706)
                firstText.startsWith("[!CAUTION]", ignoreCase = true) -> Color(0xFFE11D48)
                else -> null
            }
            if (calloutColor != null) {
                Row(
                    Modifier
                        .fillMaxWidth()
                        .height(IntrinsicSize.Min)
                        .clip(RoundedCornerShape(10.dp))
                        .background(calloutColor.copy(alpha = 0.08f))
                        .padding(end = 10.dp, top = 8.dp, bottom = 8.dp)
                ) {
                    Box(Modifier.width(3.5.dp).fillMaxHeight().background(calloutColor, RoundedCornerShape(2.dp)))
                    Blocks(b.blocks, style.copy(color = PulseTheme.colors.ink), Modifier.padding(start = 10.dp), depth + 1)
                }
            } else {
                Row(Modifier.height(IntrinsicSize.Min)) {
                    Box(Modifier.width(3.dp).fillMaxHeight().background(PulseTheme.colors.accent.copy(alpha = 0.45f), RoundedCornerShape(2.dp)))
                    Blocks(b.blocks, style.copy(color = PulseTheme.colors.ink2), Modifier.padding(start = 12.dp), depth + 1)
                }
            }
        }
        is MdBlock.ListBlock -> Column(verticalArrangement = Arrangement.spacedBy(if (b.tight) 4.dp else 9.dp)) {
            b.items.forEachIndexed { i, item ->
                Row {
                    val marker = if (b.start != null) "${b.start + i}." else if (depth % 2 == 0) "•" else "◦"
                    Text(marker, Modifier.widthIn(min = 20.dp).padding(end = 6.dp), style = style.copy(color = PulseTheme.colors.ink2, fontWeight = FontWeight.Medium))
                    Blocks(item, style, depth = depth + 1, gap = if (b.tight) 4 else 9)
                }
            }
        }
        is MdBlock.Table -> TableBlock(b, style)
        MdBlock.Rule -> Box(Modifier.fillMaxWidth().padding(vertical = 6.dp).height(0.5.dp).background(PulseTheme.colors.hair))
    }
}

@Composable
private fun TableBlock(t: MdBlock.Table, style: TextStyle) {
    val cols = t.header.size
    if (cols == 0) return
    val cellStyle = style.copy(fontSize = 13.5.sp, lineHeight = 19.sp)
    val rows = listOf(t.header) + t.rows
    Box(Modifier.horizontalScroll(rememberScrollState())) {
        Layout(
            content = {
                rows.forEachIndexed { r, row ->
                    row.forEach { cell ->
                        Box(
                            Modifier.border(0.5.dp, PulseTheme.colors.hair)
                                .then(if (r == 0) Modifier.background(PulseTheme.colors.fill) else Modifier)
                                .padding(horizontal = 9.dp, vertical = 6.dp),
                        ) {
                            Text(cell, style = if (r == 0) cellStyle.copy(fontWeight = FontWeight.SemiBold) else cellStyle)
                        }
                    }
                }
            },
            modifier = Modifier.border(0.5.dp, PulseTheme.colors.hair, RoundedCornerShape(2.dp)),
        ) { measurables, _ ->
            val maxCol = 240.dp.roundToPx()
            val minCol = 48.dp.roundToPx()
            val widths = IntArray(cols)
            measurables.forEachIndexed { i, m ->
                val c = i % cols
                widths[c] = maxOf(widths[c], m.maxIntrinsicWidth(Constraints.Infinity).coerceIn(minCol, maxCol))
            }
            val heights = measurables.chunked(cols).map { row -> row.withIndex().maxOf { (c, m) -> m.minIntrinsicHeight(widths[c]) } }
            val placeables = measurables.mapIndexed { i, m -> m.measure(Constraints.fixed(widths[i % cols], heights[i / cols])) }
            layout(widths.sum(), heights.sum()) {
                var y = 0
                placeables.chunked(cols).forEachIndexed { r, row ->
                    var x = 0
                    row.forEachIndexed { c, p ->
                        p.place(x, y)
                        x += widths[c]
                    }
                    y += heights[r]
                }
            }
        }
    }
}

private val svgScriptRegex = Regex("""(?is)<script[\s\S]*?</script>""")
private val svgEventAttrRegex = Regex("""(?i)\son[a-z]+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)""")

@Composable
private fun SvgBlock(rawSvg: String) {
    val cleanSvg = remember(rawSvg) {
        rawSvg.replace(svgScriptRegex, "").replace(svgEventAttrRegex, "")
    }
    val htmlDoc = remember(cleanSvg) {
        """<!DOCTYPE html><html><head><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=2"><style>html,body{margin:0;padding:0;background:transparent;display:flex;align-items:center;justify-content:center;overflow:hidden}svg{max-width:100%;height:auto;max-height:300px;display:block}</style></head><body>$cleanSvg</body></html>"""
    }
    Box(
        Modifier.fillMaxWidth()
            .clip(RoundedCornerShape(14.dp))
            .background(PulseTheme.colors.fill)
            .border(0.5.dp, PulseTheme.colors.hair, RoundedCornerShape(14.dp))
            .padding(10.dp)
    ) {
        AndroidView(
            modifier = Modifier.fillMaxWidth().heightIn(min = 120.dp, max = 320.dp),
            factory = { ctx ->
                WebView(ctx).apply {
                    setBackgroundColor(AndroidColor.TRANSPARENT)
                    setLayerType(android.view.View.LAYER_TYPE_SOFTWARE, null)
                    settings.javaScriptEnabled = false
                    settings.allowFileAccess = false
                    settings.allowContentAccess = false
                    isVerticalScrollBarEnabled = false
                    isHorizontalScrollBarEnabled = false
                }
            },
            update = { webView ->
                if (webView.tag != htmlDoc) {
                    webView.tag = htmlDoc
                    webView.loadDataWithBaseURL(null, htmlDoc, "text/html", "UTF-8", null)
                }
            }
        )
    }
}

private val codeTokenRegex = Regex(
    """("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')|\b(def|class|return|import|from|async|await|if|elif|else|for|while|try|except|finally|with|as|pass|break|continue|True|False|None|const|let|var|function|export|default|switch|case|new|throw|catch|true|false|null|undefined|fun|val|object|interface|override|private|public|protected|internal|when|is|in|companion|data|sealed|suspend)\b|\b(\d+(?:\.\d+)?)\b"""
)

private fun highlightCodeBlock(code: String, lang: String): AnnotatedString = buildAnnotatedString {
    val lines = code.split('\n')
    val isDiff = lang == "diff" || lang == "patch"
    lines.forEachIndexed { idx, line ->
        val trimmed = line.trimStart()
        when {
            (isDiff || line.startsWith("+ ")) && line.startsWith("+") && !line.startsWith("+++") -> {
                withStyle(SpanStyle(color = Color(0xFF10B981), background = Color(0x1810B981))) { append(line) }
            }
            (isDiff || line.startsWith("- ")) && line.startsWith("-") && !line.startsWith("---") -> {
                withStyle(SpanStyle(color = Color(0xFFE11D48), background = Color(0x18E11D48))) { append(line) }
            }
            trimmed.startsWith("#") || trimmed.startsWith("//") -> {
                withStyle(SpanStyle(color = Color(0xFF6B7280))) { append(line) }
            }
            else -> {
                var pos = 0
                for (m in codeTokenRegex.findAll(line)) {
                    if (m.range.first > pos) {
                        append(line.substring(pos, m.range.first))
                    }
                    when {
                        m.groupValues[1].isNotEmpty() -> withStyle(SpanStyle(color = Color(0xFF059669))) { append(m.value) }
                        m.groupValues[2].isNotEmpty() -> withStyle(SpanStyle(color = Color(0xFF7C3AED), fontWeight = FontWeight.SemiBold)) { append(m.value) }
                        m.groupValues[3].isNotEmpty() -> withStyle(SpanStyle(color = Color(0xFFD97706))) { append(m.value) }
                        else -> append(m.value)
                    }
                    pos = m.range.last + 1
                }
                if (pos < line.length) {
                    append(line.substring(pos))
                }
            }
        }
        if (idx < lines.lastIndex) append('\n')
    }
}
