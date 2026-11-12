// Adapted from wangzan101/Mika, e9daf53 (Apache-2.0). See assets/mika-LICENSE.txt.
package dev.bubuki.proactive.markdown

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.withLink
import androidx.compose.ui.text.withStyle
import org.commonmark.ext.gfm.strikethrough.Strikethrough
import org.commonmark.ext.gfm.strikethrough.StrikethroughExtension
import org.commonmark.ext.gfm.tables.TableBlock
import org.commonmark.ext.gfm.tables.TableCell
import org.commonmark.ext.gfm.tables.TableHead
import org.commonmark.ext.gfm.tables.TableRow
import org.commonmark.ext.gfm.tables.TablesExtension
import org.commonmark.node.BlockQuote
import org.commonmark.node.BulletList
import org.commonmark.node.Code
import org.commonmark.node.Emphasis
import org.commonmark.node.FencedCodeBlock
import org.commonmark.node.HardLineBreak
import org.commonmark.node.HtmlBlock
import org.commonmark.node.HtmlInline
import org.commonmark.node.Image
import org.commonmark.node.IndentedCodeBlock
import org.commonmark.node.Link
import org.commonmark.node.LinkReferenceDefinition
import org.commonmark.node.ListBlock
import org.commonmark.node.ListItem
import org.commonmark.node.Node
import org.commonmark.node.OrderedList
import org.commonmark.node.Paragraph
import org.commonmark.node.SoftLineBreak
import org.commonmark.node.StrongEmphasis
import org.commonmark.node.Text
import org.commonmark.node.ThematicBreak
import org.commonmark.parser.Parser
import dev.bubuki.proactive.LightColors
import dev.bubuki.proactive.PulseColors

/** 回答里的 Markdown 解析成的显示模型:块级结构 + 带样式的行内文字。纯数据,不依赖界面。 */
sealed interface MdBlock {
    data class Para(val text: AnnotatedString) : MdBlock
    data class Heading(val level: Int, val text: AnnotatedString) : MdBlock
    data class Code(val info: String, val code: String) : MdBlock
    data class Quote(val blocks: List<MdBlock>) : MdBlock
    /** [start] 为 null 是无序列表;每个列表项是一组块。 */
    data class ListBlock(val start: Int?, val tight: Boolean, val items: List<List<MdBlock>>) : MdBlock
    /** 每行的格数都补齐到表头的列数。 */
    data class Table(val header: List<AnnotatedString>, val rows: List<List<AnnotatedString>>) : MdBlock
    data object Rule : MdBlock
}

object MarkdownModel {
    /** 嵌套再深的引用、列表就按纯文字显示,免得递归太深。 */
    private const val MAX_DEPTH = 12
    private val LINK_SCHEMES = setOf("http", "https", "mailto", "tel")

    /** 行内代码和链接的样式;颜色跟着深浅色,由显示的地方按当前配色给。 */
    class Styles(val code: SpanStyle, val link: TextLinkStyles) {
        companion object {
            fun of(c: PulseColors) = Styles(
                code = SpanStyle(fontFamily = FontFamily.Monospace, background = c.fill),
                link = TextLinkStyles(SpanStyle(color = c.accent)),
            )

            val Light = of(LightColors)
        }
    }

    private val parser: Parser = Parser.builder()
        .extensions(listOf(TablesExtension.create(), StrikethroughExtension.create()))
        .build()

    private val rawSvgRegex = Regex("""(?is)<svg\b[\s\S]*?</svg>""")

    private fun normalizeInlineSvgBlocks(markdown: String): String {
        if (!markdown.contains("<svg", ignoreCase = true) || !markdown.contains("</svg>", ignoreCase = true)) {
            return markdown
        }
        // Only wrap <svg>...</svg> that are not already inside fenced ``` blocks
        val parts = markdown.split("```")
        if (parts.size == 1) {
            return rawSvgRegex.replace(markdown) { m -> "\n```svg\n${m.value.trim()}\n```\n" }
        }
        val sb = StringBuilder()
        for (i in parts.indices) {
            if (i % 2 == 0) {
                sb.append(rawSvgRegex.replace(parts[i]) { m -> "\n```svg\n${m.value.trim()}\n```\n" })
            } else {
                sb.append("```").append(parts[i]).append("```")
            }
        }
        return sb.toString()
    }

    /** 解析失败(包括写到一半的流式文字导致的任何意外)时,整段按纯文字一段显示。 */
    fun parse(markdown: String, styles: Styles = Styles.Light): List<MdBlock> = try {
        blocks(parser.parse(normalizeInlineSvgBlocks(markdown)), 0, styles)
    } catch (e: Exception) {
        plain(markdown)
    } catch (e: StackOverflowError) {
        plain(markdown)
    }

    /** 流式片段可停在语法中间；只在显示时补全尾部，不修改实际回答。 */
    fun parseStreaming(markdown: String, styles: Styles = Styles.Light): List<MdBlock> = parse(StreamingMarkdown.preview(markdown), styles)

    /**
     * 回答去掉 Markdown 符号后的纯文字(复制、引用用):段落、标题、代码照原文,各块之间空一行;
     * 列表每项一行,前面「• 」或「1. 」;引用每行前面「> 」;表格每行一行、格子之间用制表符;分隔线不要。
     */
    fun toPlainText(markdown: String): String = blocksText(parse(markdown)).trim()

    private fun blocksText(blocks: List<MdBlock>, sep: String = "\n\n"): String = blocks.mapNotNull { b ->
        when (b) {
            is MdBlock.Para -> b.text.text
            is MdBlock.Heading -> b.text.text
            is MdBlock.Code -> b.code
            is MdBlock.Quote -> blocksText(b.blocks).lines().joinToString("\n") { "> $it".trimEnd() }
            is MdBlock.ListBlock -> b.items.mapIndexed { i, item ->
                val mark = if (b.start == null) "• " else "${b.start + i}. "
                mark + blocksText(item, "\n").lines().joinToString("\n" + " ".repeat(mark.length))
            }.joinToString("\n")
            is MdBlock.Table -> (listOf(b.header) + b.rows).joinToString("\n") { r -> r.joinToString("\t") { it.text } }
            MdBlock.Rule -> null
        }?.takeIf { it.isNotBlank() }
    }.joinToString(sep)

    private fun plain(s: String): List<MdBlock> = if (s.isBlank()) emptyList() else listOf(MdBlock.Para(AnnotatedString(s.trim())))

    private fun children(n: Node): Sequence<Node> = generateSequence(n.firstChild) { it.next }

    private fun blocks(parent: Node, depth: Int, s: Styles): List<MdBlock> = children(parent).mapNotNull { block(it, depth, s) }.toList()

    private fun block(n: Node, depth: Int, s: Styles): MdBlock? {
        if (depth > MAX_DEPTH) return MdBlock.Para(AnnotatedString(plainText(n)))
        return when (n) {
            is Paragraph -> MdBlock.Para(inlines(n, s))
            is org.commonmark.node.Heading -> MdBlock.Heading(n.level, inlines(n, s))
            is FencedCodeBlock -> MdBlock.Code(n.info.orEmpty().trim(), n.literal.orEmpty().trimEnd('\n'))
            is IndentedCodeBlock -> MdBlock.Code("", n.literal.orEmpty().trimEnd('\n'))
            is HtmlBlock -> MdBlock.Para(AnnotatedString(n.literal.orEmpty().trimEnd('\n')))
            is BlockQuote -> MdBlock.Quote(blocks(n, depth + 1, s))
            is ListBlock -> MdBlock.ListBlock(
                start = (n as? OrderedList)?.let { it.markerStartNumber ?: 1 },
                tight = n.isTight,
                items = children(n).filterIsInstance<ListItem>().map { blocks(it, depth + 1, s) }.toList(),
            )
            is TableBlock -> table(n, s)
            is ThematicBreak -> MdBlock.Rule
            is LinkReferenceDefinition -> null
            else -> plainText(n).takeIf { it.isNotBlank() }?.let { MdBlock.Para(AnnotatedString(it)) }
        }
    }

    private fun table(t: TableBlock, s: Styles): MdBlock.Table {
        val rows = mutableListOf<List<AnnotatedString>>()
        var header: List<AnnotatedString> = emptyList()
        for (section in children(t)) for (row in children(section).filterIsInstance<TableRow>()) {
            val cells = children(row).filterIsInstance<TableCell>().map { inlines(it, s) }.toList()
            if (section is TableHead && header.isEmpty()) header = cells else rows += cells
        }
        val cols = maxOf(header.size, rows.maxOfOrNull { it.size } ?: 0)
        fun pad(r: List<AnnotatedString>) = r + List(cols - r.size) { AnnotatedString("") }
        return MdBlock.Table(pad(header), rows.map(::pad))
    }

    private val highlightSpanStyle = SpanStyle(
        background = Color(0x38F59E0B),
        fontWeight = FontWeight.Medium
    )
    private val eqHighlightRegex = Regex("""==([^\n=]{1,200}?)=""")
    private val htmlColorAttrRegex = Regex("""(?i)(?:style\s*=\s*["'][^"']*color\s*:\s*([#a-zA-Z0-9]+)|color\s*=\s*["']([#a-zA-Z0-9]+)["'])""")

    private fun parseNamedOrHexColor(raw: String): Color? {
        val c = raw.trim().lowercase()
        return when (c) {
            "red", "crimson", "danger" -> Color(0xFFE11D48)
            "green", "emerald", "success" -> Color(0xFF10B981)
            "blue", "royalblue", "info" -> Color(0xFF3B82F6)
            "orange", "amber", "warning" -> Color(0xFFD97706)
            "purple", "violet" -> Color(0xFF8B5CF6)
            "pink", "rose" -> Color(0xFFEC4899)
            "cyan", "teal" -> Color(0xFF06B6D4)
            "gray", "grey", "muted" -> Color(0xFF6B7280)
            else -> {
                if (c.startsWith("#")) {
                    val hex = c.removePrefix("#")
                    try {
                        when (hex.length) {
                            3 -> {
                                val r = hex[0].toString().repeat(2).toInt(16)
                                val g = hex[1].toString().repeat(2).toInt(16)
                                val b = hex[2].toString().repeat(2).toInt(16)
                                Color(r, g, b)
                            }
                            6 -> Color(0xFF000000 or hex.toLong(16))
                            8 -> Color(hex.toLong(16))
                            else -> null
                        }
                    } catch (_: Exception) { null }
                } else null
            }
        }
    }

    private fun AnnotatedString.Builder.appendTextWithHighlights(literal: String) {
        if (!literal.contains("==")) {
            append(literal)
            return
        }
        var lastIdx = 0
        for (m in eqHighlightRegex.findAll(literal)) {
            if (m.range.first > lastIdx) {
                append(literal.substring(lastIdx, m.range.first))
            }
            withStyle(highlightSpanStyle) {
                append(m.groupValues[1])
            }
            lastIdx = m.range.last + 1
        }
        if (lastIdx < literal.length) {
            append(literal.substring(lastIdx))
        }
    }

    private fun inlines(parent: Node, s: Styles): AnnotatedString = buildAnnotatedString { appendInlines(parent, s) }

    private fun AnnotatedString.Builder.appendInlines(parent: Node, s: Styles) {
        val openHtmlStyles = ArrayDeque<Int>()
        for (n in children(parent)) when (n) {
            is Text -> appendTextWithHighlights(n.literal)
            is SoftLineBreak, is HardLineBreak -> append('\n')
            is Code -> withStyle(s.code) { append(n.literal) }
            is Emphasis -> withStyle(SpanStyle(fontStyle = FontStyle.Italic)) { appendInlines(n, s) }
            is StrongEmphasis -> withStyle(SpanStyle(fontWeight = FontWeight.SemiBold)) { appendInlines(n, s) }
            is Strikethrough -> withStyle(SpanStyle(textDecoration = TextDecoration.LineThrough)) { appendInlines(n, s) }
            is Link -> link(n.destination, s) { appendInlines(n, s) }
            // 聊天里不显示图片,只留替代文字(有可打开的地址就做成链接)
            is Image -> link(n.destination, s) { if (n.firstChild != null) appendInlines(n, s) else append(n.destination.orEmpty()) }
            is HtmlInline -> {
                val tag = n.literal.trim()
                val lower = tag.lowercase()
                when {
                    lower == "<mark>" || lower.startsWith("<mark ") -> {
                        pushStyle(highlightSpanStyle)
                        openHtmlStyles.addLast(1)
                    }
                    lower == "</mark>" || lower == "</span>" || lower == "</font>" -> {
                        if (openHtmlStyles.isNotEmpty()) {
                            openHtmlStyles.removeLast()
                            try { pop() } catch (_: Exception) {}
                        }
                    }
                    lower.startsWith("<span") || lower.startsWith("<font") -> {
                        val match = htmlColorAttrRegex.find(tag)
                        val colorStr = match?.groupValues?.getOrNull(1)?.takeIf { it.isNotBlank() }
                            ?: match?.groupValues?.getOrNull(2)?.takeIf { it.isNotBlank() }
                        val parsedColor = colorStr?.let { parseNamedOrHexColor(it) }
                        if (parsedColor != null) {
                            pushStyle(SpanStyle(color = parsedColor))
                            openHtmlStyles.addLast(1)
                        }
                    }
                    lower == "<br>" || lower == "<br/>" || lower == "<br />" -> append('\n')
                    else -> {}
                }
            }
            else -> appendInlines(n, s)
        }
        while (openHtmlStyles.isNotEmpty()) {
            openHtmlStyles.removeLast()
            try { pop() } catch (_: Exception) {}
        }
    }

    private inline fun AnnotatedString.Builder.link(dest: String?, s: Styles, content: AnnotatedString.Builder.() -> Unit) {
        val url = dest?.trim().orEmpty()
        if (url.startsWith("#vault:") || url.substringBefore(':', "").lowercase() in LINK_SCHEMES) withLink(LinkAnnotation.Url(url, s.link)) { content() } else content()
    }

    private fun plainText(n: Node): String = buildString {
        fun walk(x: Node) {
            when (x) {
                is Text -> append(x.literal)
                is Code -> append(x.literal)
                is FencedCodeBlock -> append(x.literal)
                is IndentedCodeBlock -> append(x.literal)
                is HtmlBlock -> append(x.literal)
                is HtmlInline -> append(x.literal)
                is SoftLineBreak, is HardLineBreak -> append('\n')
                else -> {
                    children(x).forEach(::walk)
                    if (x is Paragraph || x is org.commonmark.node.Heading) append('\n')
                }
            }
        }
        walk(n)
    }.trim()
}
