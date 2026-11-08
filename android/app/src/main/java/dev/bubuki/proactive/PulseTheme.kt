package dev.bubuki.proactive

import android.content.Context
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.*
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.hapticfeedback.HapticFeedback
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalHapticFeedback

data class PulseColors(val fill: Color, val hair: Color, val ink: Color, val accent: Color) { val ink2: Color get() = ink.copy(alpha = .60f) }
val LightColors = PulseColors(Color(0xFFF2F2F7), Color(0xFFE5E5EA), Color(0xFF000000), Color(0xFF007AFF))
internal val DarkColors = PulseColors(Color(0xFF1C1C1E), Color(0xFF2C2C2E), Color(0xFFFFFFFF), Color(0xFF0A84FF))
internal val LocalPulseColors = staticCompositionLocalOf { DarkColors }
internal val QuietHaptics: HapticFeedback = object : HapticFeedback { override fun performHapticFeedback(hapticFeedbackType: HapticFeedbackType) {} }

fun animationsOff(context: Context): Boolean = Settings.Global.getFloat(context.contentResolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f) == 0f
@Composable fun appearanceDark(): Boolean {
    val settings by UiPreferences.changes(LocalContext.current).collectAsState()
    return when (settings.appearance) { "light" -> false; "dark" -> true; else -> isSystemInDarkTheme() }
}
fun ComponentActivity.systemBarsFor(dark: Boolean) {
    val transparent = android.graphics.Color.TRANSPARENT
    enableEdgeToEdge(statusBarStyle = if (dark) SystemBarStyle.dark(transparent) else SystemBarStyle.light(transparent, transparent),
        navigationBarStyle = if (dark) SystemBarStyle.dark(transparent) else SystemBarStyle.light(transparent, transparent))
}
object PulseTheme {
    val colors: PulseColors @Composable @ReadOnlyComposable get() = LocalPulseColors.current
    @Composable operator fun invoke(dark: Boolean, reduceMotion: Boolean = false, content: @Composable () -> Unit) {
        val settings by UiPreferences.changes(LocalContext.current).collectAsState()
        val feedback = LocalHapticFeedback.current
        val palette = if (dark) DarkColors else LightColors
        val scheme = if (dark) darkColorScheme(primary = palette.accent, background = Color(0xFF000000), surface = palette.fill, onSurface = palette.ink, outline = palette.hair)
            else lightColorScheme(primary = palette.accent, background = Color(0xFFF2F2F7), surface = palette.fill, onSurface = palette.ink, outline = palette.hair)
        CompositionLocalProvider(LocalPulseColors provides palette, LocalHapticFeedback provides if (settings.haptics) feedback else QuietHaptics) {
            MaterialTheme(colorScheme = scheme, content = content)
        }
    }
}
