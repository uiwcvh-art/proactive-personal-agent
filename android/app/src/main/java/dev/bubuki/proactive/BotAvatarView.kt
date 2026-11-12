package dev.bubuki.proactive

import android.graphics.Paint
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.drawIntoCanvas
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.graphics.PathParser

val BOT_PRESET_COLORS: List<String> = listOf(
    "#B568F7",
    "#0A84FF",
    "#2EC4B6",
    "#FF8A3D",
    "#FF453A",
    "#30D158",
    "#FFD60A",
    "#FF375F",
    "#5E5CE6"
)

object BotAvatarView {
    val shapePaths: Map<String, String> = linkedMapOf(
        "flower" to "M50 4 C61 4 64 17 73 20 C82 23 93 16 97 25 C101 35 89 43 89 52 C89 61 100 68 95 78 C90 87 78 81 70 86 C62 91 61 98 50 98 C39 98 38 91 30 86 C22 81 10 87 5 78 C0 68 11 61 11 52 C11 43 -1 35 3 25 C7 16 18 23 27 20 C36 17 39 4 50 4 Z",
        "circle" to "M50 5 A45 45 0 1 0 50 95 A45 45 0 1 0 50 5 Z",
        "oval" to "M50 14 A44 36 0 1 0 50 86 A44 36 0 1 0 50 14 Z",
        "square" to "M22 8 H78 A14 14 0 0 1 92 22 V78 A14 14 0 0 1 78 92 H22 A14 14 0 0 1 8 78 V22 A14 14 0 0 1 22 8 Z",
        "pill" to "M34 6 H66 A28 28 0 0 1 94 34 V66 A28 28 0 0 1 66 94 H34 A28 28 0 0 1 6 66 V34 A28 28 0 0 1 34 6 Z",
        "triangle" to "M50 8 L92 84 A6 6 0 0 1 87 93 H13 A6 6 0 0 1 8 84 Z",
        "diamond" to "M50 5 L95 50 L50 95 L5 50 Z",
        "star" to "M50 6 L61 36 L93 38 L68 58 L76 90 L50 72 L24 90 L32 58 L7 38 L39 36 Z",
        "drop" to "M50 5 C71 29 88 47 88 67 A38 38 0 1 1 12 67 C12 47 29 29 50 5 Z",
        "whale" to "M14 58 C14 33 34 18 58 20 C76 22 88 35 90 52 C93 48 97 42 98 36 C99 47 95 59 86 64 C82 77 66 85 46 85 C26 85 14 74 14 58 Z",
        "whalechan" to "M50 6 C74 6 94 26 94 50 C94 74 74 94 50 94 C26 94 6 74 6 50 C6 26 26 6 50 6 Z"
    )

    val shapeLabels: List<Pair<String, String>> = listOf(
        "flower" to "六瓣花",
        "circle" to "正圆",
        "oval" to "椭圆",
        "square" to "方框",
        "pill" to "胶囊",
        "triangle" to "三角",
        "diamond" to "菱形",
        "star" to "五角星",
        "drop" to "水滴",
        "whale" to "鲸鱼",
        "whalechan" to "鲸宝图章"
    )

    fun resolveShapeKey(botId: String, rawShape: String): String {
        val clean = rawShape.trim().lowercase()
        if (clean.isNotEmpty() && clean in shapePaths) return clean
        return when (botId.trim().lowercase()) {
            "companion" -> "flower"
            "research" -> "circle"
            "contrarian" -> "diamond"
            "whalechan" -> "whalechan"
            else -> "flower"
        }
    }

    fun isWhalechanStamp(botId: String, rawShape: String): Boolean {
        val clean = rawShape.trim().lowercase()
        if (clean == "whalechan") return true
        if (clean.isNotEmpty() && clean in shapePaths) return false
        return botId.trim().equals("whalechan", ignoreCase = true)
    }

    fun parseColorSafe(hex: String, fallback: Color = Color(0xFFB568F7)): Color {
        val clean = hex.trim()
        if (clean.isEmpty()) return fallback
        return runCatching { Color(android.graphics.Color.parseColor(clean)) }.getOrDefault(fallback)
    }

    fun createPath(shapeKey: String): android.graphics.Path {
        val pathData = shapePaths[shapeKey] ?: shapePaths.getValue("flower")
        return PathParser.createPathFromPathData(pathData)
    }
}

@Composable
fun BotColorSwatchRow(
    selectedColorHex: String,
    onSelectColor: (String) -> Unit,
    modifier: Modifier = Modifier
) {
    LazyRow(
        modifier = modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(10.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        items(BOT_PRESET_COLORS) { hex ->
            val selected = selectedColorHex.trim().equals(hex, ignoreCase = true)
            val swatchColor = remember(hex) { BotAvatarView.parseColorSafe(hex) }
            Box(
                modifier = Modifier
                    .size(34.dp)
                    .clip(CircleShape)
                    .background(swatchColor, CircleShape)
                    .then(
                        if (selected) {
                            Modifier.border(2.5.dp, Color.White, CircleShape)
                        } else {
                            Modifier.border(1.dp, Color(0x33FFFFFF), CircleShape)
                        }
                    )
                    .clickable { onSelectColor(hex) },
                contentAlignment = Alignment.Center
            ) {
                if (selected) {
                    PulseIcon("check", Color.White, Modifier.size(16.dp))
                }
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun BotShapeSelectorGrid(
    selectedShape: String,
    previewColorHex: String,
    onSelectShape: (String) -> Unit,
    modifier: Modifier = Modifier
) {
    val activeShape = selectedShape.trim().lowercase().ifBlank { "flower" }
    val previewColor = remember(previewColorHex) { BotAvatarView.parseColorSafe(previewColorHex) }
    val fillPaint = remember { Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL } }

    FlowRow(
        modifier = modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        BotAvatarView.shapeLabels.forEach { (shapeKey, shapeLabel) ->
            val selected = activeShape == shapeKey
            val path = remember(shapeKey) { BotAvatarView.createPath(shapeKey) }
            Row(
                modifier = Modifier
                    .clip(RoundedCornerShape(10.dp))
                    .background(if (selected) previewColor.copy(alpha = 0.22f) else Color(0xFF2C2C2E))
                    .border(
                        width = if (selected) 1.5.dp else 0.8.dp,
                        color = if (selected) previewColor else Color(0x22FFFFFF),
                        shape = RoundedCornerShape(10.dp)
                    )
                    .clickable { onSelectShape(shapeKey) }
                    .padding(horizontal = 10.dp, vertical = 7.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(6.dp)
            ) {
                Canvas(Modifier.size(16.dp)) {
                    val scale = size.minDimension / 100f
                    fillPaint.color = android.graphics.Color.argb(
                        (previewColor.alpha * 255).toInt(),
                        (previewColor.red * 255).toInt(),
                        (previewColor.green * 255).toInt(),
                        (previewColor.blue * 255).toInt()
                    )
                    drawIntoCanvas { canvas ->
                        val native = canvas.nativeCanvas
                        native.save()
                        native.scale(scale, scale)
                        native.drawPath(path, fillPaint)
                        native.restore()
                    }
                }
                Text(
                    text = shapeLabel,
                    color = if (selected) Color.White else Color(0xFFE2E8F0),
                    fontSize = 12.sp,
                    fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal
                )
            }
        }
    }
}
