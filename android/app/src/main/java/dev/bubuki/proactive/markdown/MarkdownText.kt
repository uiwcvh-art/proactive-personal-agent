// Adapted from wangzan101/Mika, e9daf53 (Apache-2.0). See assets/mika-LICENSE.txt.
package dev.bubuki.proactive.markdown

import android.content.ActivityNotFoundException
import android.content.Intent
import android.graphics.Color as AndroidColor
import android.net.Uri
import android.webkit.WebView
import android.widget.Toast
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
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
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.Layout
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.platform.UriHandler
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
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
                Box(
                    Modifier.fillMaxWidth().background(PulseTheme.colors.fill, RoundedCornerShape(12.dp))
                        .horizontalScroll(rememberScrollState()).padding(horizontal = 13.dp, vertical = 11.dp),
                ) {
                    Text(b.code, style = style.copy(fontFamily = FontFamily.Monospace, fontSize = 13.5.sp, lineHeight = 20.sp), softWrap = false)
                }
            }
        }
        is MdBlock.Quote -> Row(Modifier.height(IntrinsicSize.Min)) {
            Box(Modifier.width(3.dp).fillMaxHeight().background(PulseTheme.colors.accent.copy(alpha = 0.45f), RoundedCornerShape(2.dp)))
            Blocks(b.blocks, style.copy(color = PulseTheme.colors.ink2), Modifier.padding(start = 12.dp), depth + 1)
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
