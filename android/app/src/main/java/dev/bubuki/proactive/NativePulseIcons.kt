package dev.bubuki.proactive

import android.graphics.Paint
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.keyframes
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.drawIntoCanvas
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.core.graphics.PathParser

private val nativePaths = mapOf(
    "back" to "M15 5L8 12L15 19",
    "next" to "M9 6L15 12L9 18",
    "chevron_down" to "M6 9L12 15L18 9",
    "chevron_up" to "M6 15L12 9L18 15",
    "plus" to "M12 5V19M5 12H19",
    "close" to "M6.5 6.5L17.5 17.5M17.5 6.5L6.5 17.5",
    "search" to "M16.5 10.5A6 6 0 1 1 4.5 10.5A6 6 0 1 1 16.5 10.5M15.5 15.5L20 20",
    "send" to "M12 19V5M6 11L12 5L18 11",
    "more" to "M5 12h0.1M12 12h0.1M19 12h0.1",
    "reply" to "M9 14L4 9L9 4M4 9H14.5A5.5 5.5 0 0 1 20 14.5V19",
    "check" to "M4.5 12.5L9.2 17L19.5 6.5",
    "check_double" to "M2.5 12.5L6.8 16.8L15.5 7.5M9.5 12.5L13.8 16.8L22 8",
    "attach" to "M21.4 11.1L12.2 20.3A6 6 0 0 1 3.7 11.8L12.9 2.6A4 4 0 0 1 18.6 8.3L9.4 17.5A2 2 0 0 1 6.6 14.7L15.1 6.2",
    "sticker" to "M12 3A9 9 0 1 0 21 12C21 10.5 20.5 9 19.5 8M15 20.5C15 16.5 16.5 15 20.5 15M9 10H9.1M15 10H15.1M9.5 15C10.8 16.3 13.2 16.3 14.5 15",
    "edit" to "M16.5 3.5A2.1 2.1 0 0 1 19.5 6.5L7 19L3 20L4 16L16.5 3.5Z",
    "broom" to "M19 5L14 10M4 20L9 15M14.5 6.5L17.5 9.5L9 18L5 19L6 15L14.5 6.5Z",
    "stop" to "M6.5 6.5H17.5V17.5H6.5Z",
    "user" to "M12 11A4 4 0 1 0 12 3A4 4 0 1 0 12 11ZM5 20C5 16.5 8.1 14 12 14C15.9 14 19 16.5 19 20",
    "cloud" to "M6.5 18H17.5A4 4 0 0 0 18 10A5.5 5.5 0 0 0 7.5 8.5A4.5 4.5 0 0 0 6.5 18Z",
    "screen" to "M4 5A2 2 0 0 1 6 3H18A2 2 0 0 1 20 5V15A2 2 0 0 1 18 17H6A2 2 0 0 1 4 15V5ZM9 21H15M12 17V21",
    "spark" to "M12 3L14.2 9.8L21 12L14.2 14.2L12 21L9.8 14.2L3 12L9.8 9.8Z",
    "chat" to "M20 11.5C20 15.8 16.9 18.5 12.5 18.5H7.5L4 21.5V11.5C4 7.2 7.1 4.5 11.5 4.5H12.5C16.9 4.5 20 7.2 20 11.5Z",
    "tools" to "M12 2L20.5 6.8V17.2L12 22L3.5 17.2V6.8L12 2ZM3.5 6.8L12 11.6L20.5 6.8M12 11.6V22",
    "folder" to "M3.5 7V5.5A1.5 1.5 0 0 1 5 4H9.5L12 6.5H19A1.5 1.5 0 0 1 20.5 8V18.5A1.5 1.5 0 0 1 19 20H5A1.5 1.5 0 0 1 3.5 18.5Z",
    "file" to "M14 3.5H6.5A2 2 0 0 0 4.5 5.5V18.5A2 2 0 0 0 6.5 20.5H17.5A2 2 0 0 0 19.5 18.5V9L14 3.5ZM14 3.5V9H19.5M8.5 13H15.5M8.5 16.5H13",
    "clock" to "M20.5 12A8.5 8.5 0 1 1 3.5 12A8.5 8.5 0 1 1 20.5 12M12 7V12L15.5 14",
    "settings" to "M4 7.5H7M13 7.5H20M4 16.5H11M17 16.5H20M13 7.5A3 3 0 1 1 7 7.5A3 3 0 1 1 13 7.5M17 16.5A3 3 0 1 1 11 16.5A3 3 0 1 1 17 16.5",
    "copy" to "M9.5 8.5H17.5A1.5 1.5 0 0 1 19 10V18.5A1.5 1.5 0 0 1 17.5 20H9.5A1.5 1.5 0 0 1 8 18.5V10A1.5 1.5 0 0 1 9.5 8.5ZM15.5 8.5V5.5A1.5 1.5 0 0 0 14 4H5.5A1.5 1.5 0 0 0 4 5.5V14A1.5 1.5 0 0 0 5.5 15.5H8",
    "pin" to "M8.5 3.5H15.5L14.5 9.5L18 13H6L9.5 9.5ZM12 13V20.5",
    "hide" to "M3.5 3.5L20.5 20.5M10.6 5.2L12 5C18 5 21.5 12 21.5 12L18.8 15.3M6.6 6.6C4.2 8.9 2.5 12 2.5 12C2.5 12 6 19 12 19C14.7 19 17 17.7 17 17.7",
    "vault" to "M12 3.5L20.5 8L12 12.5L3.5 8L12 3.5ZM3.5 12L12 16.5L20.5 12M3.5 16L12 20.5L20.5 16",
    "moon" to "M20.2 13.4C16.5 14.3 10.2 10.5 11.1 3.8C2.4 4.7 1.4 19.2 12.2 20.4C18.5 20.4 19.8 15.5 20.2 13.4Z",
    "bell" to "M5.5 10C5.5 2 18.5 2 18.5 10C18.5 16.2 21 16.8 21 16.8H3C3 16.8 5.5 16.2 5.5 10M9.5 19.5C9.5 22.5 14.5 22.5 14.5 19.5"
)

@Composable
internal fun PulseIcon(name: String, color: Color, modifier: Modifier = Modifier.size(20.dp)) {
    val path = remember(name) { PathParser.createPathFromPathData(nativePaths[name] ?: nativePaths.getValue("next")) }
    val paint = remember {
        Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.STROKE
            strokeCap = Paint.Cap.ROUND
            strokeJoin = Paint.Join.ROUND
        }
    }
    Canvas(modifier) {
        val scale = size.minDimension / 24f
        paint.color = android.graphics.Color.argb(
            (color.alpha * 255).toInt(),
            (color.red * 255).toInt(),
            (color.green * 255).toInt(),
            (color.blue * 255).toInt()
        )
        paint.strokeWidth = when (name) {
            "more" -> 3.0f
            "send", "plus", "back", "reply", "check_double" -> 2.1f
            else -> 1.8f
        }
        drawIntoCanvas { canvas ->
            val native = canvas.nativeCanvas
            native.save()
            native.translate((size.width - 24f * scale) / 2f, (size.height - 24f * scale) / 2f)
            native.scale(scale, scale)
            native.drawPath(path, paint)
            native.restore()
        }
    }
}

private val shapePaths = mapOf(
    "flower" to "M50 4 C61 4 64 17 73 20 C82 23 93 16 97 25 C101 35 89 43 89 52 C89 61 100 68 95 78 C90 87 78 81 70 86 C62 91 61 98 50 98 C39 98 38 91 30 86 C22 81 10 87 5 78 C0 68 11 61 11 52 C11 43 -1 35 3 25 C7 16 18 23 27 20 C36 17 39 4 50 4 Z",
    "circle" to "M50 5 A45 45 0 1 0 50 95 A45 45 0 1 0 50 5 Z",
    "star" to "M50 4 C54 33 67 46 96 50 C67 54 54 67 50 96 C46 67 33 54 4 50 C33 46 46 33 50 4 Z",
    "drop" to "M50 5 C71 29 88 47 88 67 A38 38 0 1 1 12 67 C12 47 29 29 50 5 Z",
    "diamond" to "M50 5 L95 50 L50 95 L5 50 Z",
    "square" to "M22 8 H78 A14 14 0 0 1 92 22 V78 A14 14 0 0 1 78 92 H22 A14 14 0 0 1 8 78 V22 A14 14 0 0 1 22 8 Z",
    "pill" to "M34 6 H66 A28 28 0 0 1 94 34 V66 A28 28 0 0 1 66 94 H34 A28 28 0 0 1 6 66 V34 A28 28 0 0 1 34 6 Z"
)

@Composable
internal fun PulseAvatar(
    bot: PulseBot,
    state: NativePulseState,
    diameter: Dp = 44.dp,
    showStatusDot: Boolean = false,
    ringColor: Color = Color(0xFF1C1C1E)
) {
    val radius = diameter * 0.36f
    val image = state.avatars[bot.id]
    val isBusy = state.streamBotId == bot.id || state.workingBot == bot.id || state.busyBot == bot.id
    val dotSize = (diameter * 0.26f).coerceIn(10.dp, 15.dp)

    Box(Modifier.size(diameter)) {
        if (image != null && bot.avatarVersion.isNotBlank()) {
            Image(image, null, Modifier.size(diameter).clip(RoundedCornerShape(radius)), contentScale = ContentScale.Crop)
        } else {
            val isPrimaryCompanion = bot.id == "companion" || bot.shape == "flower"
            val shapeKey = remember(bot.shape, bot.id) {
                when {
                    bot.id == "companion" -> "flower"
                    bot.shape in shapePaths -> bot.shape
                    bot.id == "research" -> "circle"
                    else -> "flower"
                }
            }
            val fill = remember(shapeKey) { PathParser.createPathFromPathData(shapePaths[shapeKey] ?: shapePaths.getValue("flower")) }
            val fillPaint = remember { Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL } }
            val baseColor = remember(bot.color, bot.id, isPrimaryCompanion) {
                if (bot.id == "companion" && (bot.color.isBlank() || bot.color.equals("#4D6BFE", ignoreCase = true) || bot.color.equals("#2B6EF6", ignoreCase = true) || bot.color.equals("#7C5CFF", ignoreCase = true))) {
                    Color(0xFFB568F7)
                } else if (isPrimaryCompanion && bot.color.isBlank()) {
                    Color(0xFFB568F7)
                } else {
                    runCatching { Color(android.graphics.Color.parseColor(bot.color)) }.getOrDefault(Color(0xFFB568F7))
                }
            }
            val isSpeaking = state.streamSpeakerId == bot.id || isBusy
            val animateEyes = state.motion && isSpeaking
            val eyeScaleY: Float
            val eyeShiftX: Float
            if (animateEyes) {
                val infinite = rememberInfiniteTransition(label = "avatar_${bot.id}")
                val blinkVal by infinite.animateFloat(
                    initialValue = 1f,
                    targetValue = 1f,
                    animationSpec = infiniteRepeatable(
                        animation = keyframes {
                            durationMillis = 4200
                            1f at 0
                            1f at 3750
                            0.15f at 3880 using FastOutSlowInEasing
                            1f at 4020 using FastOutSlowInEasing
                            1f at 4200
                        },
                        repeatMode = RepeatMode.Restart
                    ),
                    label = "blink"
                )
                val shiftVal by infinite.animateFloat(
                    initialValue = 0f,
                    targetValue = 3.5f,
                    animationSpec = infiniteRepeatable(
                        animation = keyframes {
                            durationMillis = 1200
                            0f at 0
                            3.5f at 600 using FastOutSlowInEasing
                            0f at 1200
                        },
                        repeatMode = RepeatMode.Restart
                    ),
                    label = "glance"
                )
                eyeScaleY = blinkVal
                eyeShiftX = shiftVal
            } else {
                eyeScaleY = 1f
                eyeShiftX = 0f
            }
            Canvas(Modifier.size(diameter)) {
                val s = size.minDimension / 100f
                fillPaint.color = android.graphics.Color.argb(
                    (baseColor.alpha * 255).toInt(),
                    (baseColor.red * 255).toInt(),
                    (baseColor.green * 255).toInt(),
                    (baseColor.blue * 255).toInt()
                )
                drawIntoCanvas {
                    val native = it.nativeCanvas
                    native.save()
                    native.scale(s, s)
                    native.drawPath(fill, fillPaint)
                    native.restore()
                }
                val eyeH = (23f * eyeScaleY).coerceAtLeast(3.5f) * s
                val eyeYOffset = (23f * s - eyeH) / 2f
                rotate(-26f, Offset((58f + eyeShiftX) * s, 44f * s)) {
                    drawRoundRect(
                        Color.White,
                        Offset((44.5f + eyeShiftX) * s, 32.5f * s + eyeYOffset),
                        Size(8.6f * s, eyeH),
                        CornerRadius(5f * s, 5f * s)
                    )
                    drawRoundRect(
                        Color.White,
                        Offset((59.5f + eyeShiftX) * s, 32.5f * s + eyeYOffset),
                        Size(8.6f * s, eyeH),
                        CornerRadius(5f * s, 5f * s)
                    )
                }
            }
        }
        if (showStatusDot && (state.online || isBusy)) {
            Box(
                Modifier.align(Alignment.BottomEnd)
                    .size(dotSize)
                    .clip(CircleShape)
                    .background(if (isBusy) Color(0xFF0A84FF) else Color(0xFF30D158))
                    .border(2.dp, ringColor, CircleShape)
            )
        }
    }
}
