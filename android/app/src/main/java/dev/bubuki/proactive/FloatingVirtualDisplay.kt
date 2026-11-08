package dev.bubuki.proactive

import android.animation.ValueAnimator
import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.PixelFormat
import android.graphics.RectF
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.LayerDrawable
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.util.Base64
import android.view.Gravity
import android.view.MotionEvent
import android.view.VelocityTracker
import android.view.View
import android.view.ViewConfiguration
import android.view.WindowManager
import android.view.animation.LinearInterpolator
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.CubicBezierEasing
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.Canvas as ComposeCanvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.input.pointer.positionChange
import androidx.compose.ui.input.pointer.util.VelocityTracker as ComposeVelocityTracker
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import org.json.JSONObject
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.exp
import kotlin.math.hypot
import kotlin.math.pow
import kotlin.math.roundToInt

/**
 * Analytical critical-damped spring + velocity projection physics modeled directly
 * after Mika's PipPhysics.kt:
 * - Projects fling landing point using v * (d / (1000 * (1 - d))) where d = 0.992f (~0.124s)
 * - Snaps to the nearest of the 4 screen corners (TopStart, TopEnd, BottomStart, BottomEnd)
 * - Animates with a critical-damped spring (response = 0.4f, dampingRatio = 1f) preserving initial velocity
 */
internal object PipSpringPhysics {
    private const val DECEL_FACTOR = 0.992f / (1000f * (1f - 0.992f)) // ~0.124s
    private const val OMEGA = (2.0 * PI / 0.4).toFloat() // response = 0.4s

    enum class Corner(val right: Boolean, val bottom: Boolean) {
        TopStart(false, false),
        TopEnd(true, false),
        BottomStart(false, true),
        BottomEnd(true, true),
    }

    fun project(velocityPxPerSec: Float): Float = velocityPxPerSec * DECEL_FACTOR

    fun cornerPosition(
        corner: Corner,
        screenW: Int,
        screenH: Int,
        pipW: Int,
        pipH: Int,
        marginX: Int,
        topMargin: Int,
        bottomMargin: Int
    ): Pair<Int, Int> {
        val x = if (corner.right) (screenW - pipW - marginX).coerceAtLeast(marginX) else marginX
        val y = if (corner.bottom) (screenH - pipH - bottomMargin).coerceAtLeast(topMargin) else topMargin
        return x to y
    }

    fun nearestCorner(
        projectedX: Float,
        projectedY: Float,
        screenW: Int,
        screenH: Int,
        pipW: Int,
        pipH: Int,
        marginX: Int,
        topMargin: Int,
        bottomMargin: Int
    ): Corner = Corner.entries.minBy { c ->
        val (cx, cy) = cornerPosition(c, screenW, screenH, pipW, pipH, marginX, topMargin, bottomMargin)
        val dx = projectedX - cx
        val dy = projectedY - cy
        dx * dx + dy * dy
    }

    /** Analytical solution of critical-damped harmonic oscillator at time t (seconds). */
    fun stepCriticalSpring(start: Float, target: Float, initialVelocity: Float, tSec: Float): Float {
        val d0 = start - target
        val decay = exp((-OMEGA * tSec).toDouble()).toFloat()
        return target + (d0 + (initialVelocity + OMEGA * d0) * tSec) * decay
    }
}

/**
 * Custom-drawn crisp vector icon view for the system overlay PiP buttons
 * (replaces ugly text characters like "◡", "⊞", "×").
 */
internal class PipVectorIconButton(
    context: Context,
    private val kind: IconKind,
    private val isDanger: Boolean = false,
    private var badgeText: String = "M",
    onClick: () -> Unit
) : View(context) {
    enum class IconKind { EYE_OFF, SIZE_TIER, APPS_GRID, SHRINK, POWER_CLOSE }

    private val bgPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
        color = if (isDanger) 0xCC3B171B.toInt() else 0xA6141418.toInt()
    }
    private val strokePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = context.resources.displayMetrics.density * 0.8f
        color = if (isDanger) 0x66FF6961 else 0x38FFFFFF
    }
    private val iconPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
        strokeJoin = Paint.Join.ROUND
        strokeWidth = context.resources.displayMetrics.density * 1.5f
        color = if (isDanger) 0xFFFF7A72.toInt() else 0xF2FFFFFF.toInt()
    }
    private val textPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
        color = 0xF2FFFFFF.toInt()
        textAlign = Paint.Align.CENTER
        typeface = Typeface.DEFAULT_BOLD
    }
    private val rectF = RectF()

    init {
        isClickable = true
        setOnClickListener { onClick() }
    }

    fun updateBadge(text: String) {
        if (badgeText != text) {
            badgeText = text
            invalidate()
        }
    }

    fun setBadgeText(text: String) = updateBadge(text)

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val w = width.toFloat()
        val h = height.toFloat()
        val minSide = minOf(w, h)
        if (minSide <= 2f) return
        // Proportional unit scaled relative to reference 26dp button size
        val u = minSide / 26f
        strokePaint.strokeWidth = (0.85f * u).coerceAtLeast(1f)
        iconPaint.strokeWidth = (1.55f * u).coerceAtLeast(1.2f)

        val cx = w / 2f
        val cy = h / 2f
        val r = (minSide / 2f) - strokePaint.strokeWidth
        canvas.drawCircle(cx, cy, r, bgPaint)
        canvas.drawCircle(cx, cy, r, strokePaint)

        when (kind) {
            IconKind.EYE_OFF -> {
                rectF.set(cx - 5.5f * u, cy - 3.2f * u, cx + 5.5f * u, cy + 3.2f * u)
                canvas.drawOval(rectF, iconPaint)
                canvas.drawLine(cx - 5.2f * u, cy + 4.5f * u, cx + 5.2f * u, cy - 4.5f * u, iconPaint)
            }
            IconKind.SIZE_TIER -> {
                textPaint.textSize = 10.2f * u
                val fm = textPaint.fontMetrics
                val baseline = cy - (fm.ascent + fm.descent) / 2f
                canvas.drawText(badgeText, cx, baseline, textPaint)
            }
            IconKind.APPS_GRID -> {
                val s = 3.4f * u
                val gap = 1.2f * u
                val cr = 0.9f * u
                rectF.set(cx - gap / 2 - s, cy - gap / 2 - s, cx - gap / 2, cy - gap / 2)
                canvas.drawRoundRect(rectF, cr, cr, iconPaint)
                rectF.set(cx + gap / 2, cy - gap / 2 - s, cx + gap / 2 + s, cy - gap / 2)
                canvas.drawRoundRect(rectF, cr, cr, iconPaint)
                rectF.set(cx - gap / 2 - s, cy + gap / 2, cx - gap / 2, cy + gap / 2 + s)
                canvas.drawRoundRect(rectF, cr, cr, iconPaint)
                rectF.set(cx + gap / 2, cy + gap / 2, cx + gap / 2 + s, cy + gap / 2 + s)
                canvas.drawRoundRect(rectF, cr, cr, iconPaint)
            }
            IconKind.SHRINK -> {
                val arm = 3.2f * u
                canvas.drawLine(cx + 1.2f * u, cy - 1.2f * u, cx + 4.8f * u, cy - 4.8f * u, iconPaint)
                canvas.drawLine(cx + 1.2f * u, cy - 1.2f * u, cx + 1.2f * u + arm, cy - 1.2f * u, iconPaint)
                canvas.drawLine(cx + 1.2f * u, cy - 1.2f * u, cx + 1.2f * u, cy - 1.2f * u - arm, iconPaint)
                canvas.drawLine(cx - 1.2f * u, cy + 1.2f * u, cx - 4.8f * u, cy + 4.8f * u, iconPaint)
                canvas.drawLine(cx - 1.2f * u, cy + 1.2f * u, cx - 1.2f * u - arm, cy + 1.2f * u, iconPaint)
                canvas.drawLine(cx - 1.2f * u, cy + 1.2f * u, cx - 1.2f * u, cy + 1.2f * u + arm, iconPaint)
            }
            IconKind.POWER_CLOSE -> {
                val len = 3.8f * u
                canvas.drawLine(cx - len, cy - len, cx + len, cy + len, iconPaint)
                canvas.drawLine(cx + len, cy - len, cx - len, cy + len, iconPaint)
            }
        }
    }
}

/**
 * Subtle bottom-right diagonal resize grip for smooth free-form resizing of the floating display.
 */
internal class ResizeGripView(context: Context) : View(context) {
    private val gripPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
        color = 0x80FFFFFF.toInt()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val w = width.toFloat()
        val h = height.toFloat()
        val u = minOf(w, h) / 22f
        gripPaint.strokeWidth = (1.4f * u).coerceAtLeast(1f)
        val pad = 4.2f * u
        canvas.drawLine(w - pad - 7.5f * u, h - pad, w - pad, h - pad - 7.5f * u, gripPaint)
        canvas.drawLine(w - pad - 3.8f * u, h - pad, w - pad, h - pad - 3.8f * u, gripPaint)
    }
}

/**
 * Layer 2 Continuous Power-Law Adaptive Layout Metrics for the Floating Virtual Display:
 * Scales window dimensions, glass bezel, corner radii, button sizes, icon strokes, capsules,
 * touch dot, and app picker proportionally to sigma = (innerWidthDp / 240f)^0.65.
 */
internal data class PipLayoutMetrics(
    val winW: Int,
    val winH: Int,
    val innerW: Int,
    val innerH: Int,
    val framePadPx: Int,
    val outerRadiusPx: Float,
    val innerRadiusPx: Float,
    val btnSizePx: Int,
    val btnMarginPx: Int,
    val dragZoneHeightPx: Int,
    val touchDotSizePx: Int,
    val capsulePadHPx: Int,
    val capsulePadVPx: Int,
    val capsuleSp: Float,
    val hintSp: Float,
    val pickerItemSp: Float,
    val sigma: Float
)

/**
 * Mika-style floating AgentPip window (TYPE_APPLICATION_OVERLAY) on the Android desktop:
 * - Zero black bars: outer window size matches exact bitmap aspect ratio + bezel inset, using ScaleType.FIT_XY
 * - Subtle frosted dark glass bezel (3.5dp inset, 0.5dp inner glass rim, 1.2dp soft outer status ring)
 * - 1:1 finger drag with VelocityTracker + critical-damped spring snapping to the 4 screen corners
 * - Vector Canvas icons for Hide-to-Pill, App Picker, Collapse, and Close Virtual Display (`release`)
 */
object FloatingVirtualDisplay {
    enum class SizeTier(val rpcName: String, val badge: String, val portraitFrac: Float, val landscapeFrac: Float) {
        COMPACT("compact", "S", 0.44f, 0.54f),
        MEDIUM("balanced", "M", 0.58f, 0.70f),
        LARGE("large", "L", 0.76f, 0.86f);

        fun next(): SizeTier = when (this) {
            COMPACT -> MEDIUM
            MEDIUM -> LARGE
            LARGE -> COMPACT
        }
    }

    private val mainHandler = Handler(Looper.getMainLooper())
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private var watchJob: Job? = null
    private var windowManager: WindowManager? = null
    private var rootView: FrameLayout? = null
    private var params: WindowManager.LayoutParams? = null

    private var expanded = false
    private var minimizedToPill = false
    private var pickingApp = false
    private var userDismissed = false
    private var sizeTier = SizeTier.MEDIUM
    private var customWidthFraction: Float? = null
    private var snapCorner = PipSpringPhysics.Corner.TopEnd
    private var springAnimator: ValueAnimator? = null

    private var screenImageView: ImageView? = null
    private var touchIndicatorView: View? = null
    private var emptyHintView: TextView? = null
    private var waitingCapsuleView: TextView? = null
    private var appPickerContainer: ScrollView? = null
    private var appPickerList: LinearLayout? = null
    private var sizeTierButton: PipVectorIconButton? = null
    private var outerBorderDrawable: GradientDrawable? = null
    private var lastBorderColor: Int = 0
    private var lastRenderedAppsKey: Int = -1
    private var lastTouchShownTime: Long = 0L

    @JvmStatic
    fun currentTier(): SizeTier = sizeTier

    @JvmStatic
    fun isPreviewVisible(): Boolean = rootView != null && !minimizedToPill && !userDismissed

    @JvmStatic
    fun cycleSizeTier(context: Context) {
        sizeTier = sizeTier.next()
        customWidthFraction = null
        mainHandler.post {
            sizeTierButton?.setBadgeText(sizeTier.badge)
            transitionSizeAndRebuild(context.applicationContext, RootPhoneHarness.changes.value)
        }
        scope.launch(Dispatchers.IO) {
            runCatching {
                RootPhoneHarness.command(
                    context.applicationContext,
                    JSONObject().put("op", "resize").put("tier", sizeTier.rpcName).put("manual", true)
                )
            }
        }
    }

    // Frame decoding cache
    @Volatile private var lastDecodedB64: String = ""
    @Volatile private var currentBitmap: Bitmap? = null
    @Volatile private var isDecodingFrame: Boolean = false

    private val _overlayVisible = MutableStateFlow(false)
    val overlayVisible: StateFlow<Boolean> = _overlayVisible.asStateFlow()
    private val _userDismissed = MutableStateFlow(false)
    val userDismissedFlow: StateFlow<Boolean> = _userDismissed.asStateFlow()

    internal fun cachedBitmap(): Bitmap? = currentBitmap

    private fun dp(context: Context, value: Float): Int =
        (value * context.resources.displayMetrics.density).roundToInt()

    private fun dp(context: Context, value: Int): Int =
        (value * context.resources.displayMetrics.density).roundToInt()

    @JvmStatic
    fun canOverlay(context: Context): Boolean = Settings.canDrawOverlays(context)

    @JvmStatic
    fun requestOverlayPermission(context: Context) {
        runCatching {
            val intent = Intent(
                Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                Uri.parse("package:${context.packageName}")
            ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            context.startActivity(intent)
            Toast.makeText(context, "请开启「显示在其他应用上层」，虚拟屏小窗即可悬浮在桌面上", Toast.LENGTH_LONG).show()
        }
    }

    @JvmStatic
    fun show(context: Context, forceExpand: Boolean = false) {
        val appCtx = context.applicationContext
        userDismissed = false
        _userDismissed.value = false
        minimizedToPill = false
        mainHandler.post {
            if (forceExpand) expanded = true
            ensureWatching(appCtx)
            if (canOverlay(appCtx)) {
                mountOrUpdateWindow(appCtx, RootPhoneHarness.changes.value)
            }
        }
    }

    @JvmStatic
    fun hide() {
        userDismissed = true
        _userDismissed.value = true
        pickingApp = false
        expanded = false
        minimizedToPill = false
        mainHandler.post { removeWindow() }
    }

    @JvmStatic
    fun minimizeToSidePill(context: Context) {
        val appCtx = context.applicationContext
        mainHandler.post {
            if (rootView == null) return@post
            pickingApp = false
            expanded = false
            minimizedToPill = true
            if (!userDismissed && canOverlay(appCtx) && rootView != null) {
                transitionSizeAndRebuild(appCtx, RootPhoneHarness.changes.value)
            }
        }
    }

    internal fun onWorkspaceStateChanged(context: Context, state: RootPhoneState) {
        val appCtx = context.applicationContext
        mainHandler.post {
            ensureWatching(appCtx)
            val created = runCatching { JSONObject(state.display).optBoolean("created") }.getOrDefault(false)
            if (!created || state.phase == "stopping" || state.phase == "stopped") {
                userDismissed = false
                _userDismissed.value = false
                minimizedToPill = false
                expanded = false
                pickingApp = false
                removeWindow()
                return@post
            }
            if (!userDismissed && canOverlay(appCtx)) {
                mountOrUpdateWindow(appCtx, state)
            }
        }
    }

    private fun ensureWatching(context: Context) {
        if (watchJob?.isActive == true) return
        watchJob = scope.launch {
            RootPhoneHarness.changes.collect { state ->
                val created = runCatching { JSONObject(state.display).optBoolean("created") }.getOrDefault(false)
                if (!created || state.phase == "stopping" || state.phase == "stopped") {
                    removeWindow()
                } else if (!userDismissed && canOverlay(context)) {
                    mountOrUpdateWindow(context, state)
                }
            }
        }
    }

    private fun removeWindow() {
        springAnimator?.cancel()
        springAnimator = null
        val view = rootView
        val wm = windowManager
        rootView = null
        params = null
        screenImageView = null
        touchIndicatorView = null
        emptyHintView = null
        waitingCapsuleView = null
        appPickerContainer = null
        appPickerList = null
        outerBorderDrawable = null
        _overlayVisible.value = false
        if (view != null && wm != null) {
            runCatching { wm.removeView(view) }
        }
    }

    private fun mountOrUpdateWindow(context: Context, state: RootPhoneState) {
        if (!canOverlay(context)) {
            removeWindow()
            return
        }
        val wm = windowManager ?: context.getSystemService(WindowManager::class.java).also { windowManager = it }
        if (rootView == null) {
            val metrics = context.resources.displayMetrics
            val (initW, initH) = computeWindowSizePx(context, state, expanded, minimizedToPill)
            val (initX, initY) = PipSpringPhysics.cornerPosition(
                snapCorner,
                metrics.widthPixels,
                metrics.heightPixels,
                initW,
                initH,
                dp(context, 12),
                dp(context, 56),
                dp(context, 92)
            )
            val lp = WindowManager.LayoutParams(
                initW,
                initH,
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                    WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
                    WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
                PixelFormat.TRANSLUCENT
            ).apply {
                gravity = Gravity.TOP or Gravity.START
                x = initX
                y = initY
            }
            val container = FrameLayout(context)
            params = lp
            rootView = container
            rebuildViewHierarchy(context, container, state)
            runCatching {
                wm.addView(container, lp)
                _overlayVisible.value = true
            }.onFailure {
                rootView = null
                params = null
                _overlayVisible.value = false
                return
            }
        }
        updateWindowContent(context, state)
    }

    @SuppressLint("ClickableViewAccessibility")
    private fun rebuildViewHierarchy(context: Context, container: FrameLayout, state: RootPhoneState) {
        container.removeAllViews()
        screenImageView = null
        touchIndicatorView = null
        emptyHintView = null
        waitingCapsuleView = null
        appPickerContainer = null
        appPickerList = null
        sizeTierButton = null

        val layout = computeAdaptiveLayout(context, state, expanded, minimizedToPill)

        // State 1: Minimized Restore Pill (Mika's RestorePill in bottom-right)
        if (minimizedToPill) {
            val pill = LinearLayout(context).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                setPadding(dp(context, 13), dp(context, 8), dp(context, 10), dp(context, 8))
                background = GradientDrawable().apply {
                    setColor(0xE618181C.toInt())
                    cornerRadius = dp(context, 22).toFloat()
                    setStroke(dp(context, 1), borderColorFor(state))
                }
                elevation = dp(context, 12).toFloat()
            }
            val dot = View(context).apply {
                background = GradientDrawable().apply {
                    shape = GradientDrawable.OVAL
                    setColor(dotColorFor(state))
                }
            }
            pill.addView(dot, LinearLayout.LayoutParams(dp(context, 7), dp(context, 7)).apply { rightMargin = dp(context, 7) })
            val label = TextView(context).apply {
                text = if (state.phase == "waiting_user") "待你处理" else "虚拟屏"
                textSize = 12f
                setTextColor(0xF2FFFFFF.toInt())
                typeface = Typeface.DEFAULT_BOLD
            }
            pill.addView(label, LinearLayout.LayoutParams(-2, -2).apply { rightMargin = dp(context, 8) })

            val closeMiniBtn = PipVectorIconButton(context, PipVectorIconButton.IconKind.POWER_CLOSE, isDanger = true) {
                hide()
                scope.launch(Dispatchers.IO) {
                    runCatching { RootPhoneHarness.command(context, JSONObject().put("op", "release")) }
                }
            }
            pill.addView(closeMiniBtn, LinearLayout.LayoutParams(dp(context, 20), dp(context, 20)))

            attachSpringDragAndTap(context, pill, onTap = {
                minimizedToPill = false
                transitionSizeAndRebuild(context, RootPhoneHarness.changes.value)
                scope.launch(Dispatchers.IO) {
                    runCatching { RootPhoneHarness.command(context, JSONObject().put("op", "create").put("manual", true)) }
                }
            })
            container.addView(pill, FrameLayout.LayoutParams(-1, -1))
            return
        }

        // State 2 & 3: Adaptive Frosted-Glass Frame (scales all radii, padding, buttons & font sizes by power-law sigma)
        val pad = layout.framePadPx
        val outerRadius = layout.outerRadiusPx
        val innerRadius = layout.innerRadiusPx

        val glassFrame = FrameLayout(context).apply {
            setPadding(pad, pad, pad, pad)
            val bg = GradientDrawable().apply {
                setColor(0xD918181C.toInt()) // Deep translucent obsidian glass
                cornerRadius = outerRadius
                setStroke(dp(context, 0.7f), borderColorFor(state))
            }
            outerBorderDrawable = bg
            background = bg
            elevation = dp(context, if (expanded) 18 else 12).toFloat()
        }

        val innerBox = FrameLayout(context).apply {
            val innerBg = GradientDrawable().apply {
                setColor(0xFF0E0E12.toInt())
                cornerRadius = innerRadius
            }
            val innerRim = GradientDrawable().apply {
                setColor(Color.TRANSPARENT)
                cornerRadius = innerRadius
                setStroke(1, 0x22FFFFFF)
            }
            background = LayerDrawable(arrayOf(innerBg, innerRim))
            clipToOutline = true
            outlineProvider = object : android.view.ViewOutlineProvider() {
                override fun getOutline(view: View, outline: android.graphics.Outline) {
                    outline.setRoundRect(0, 0, view.width, view.height, innerRadius)
                }
            }
        }

        val img = ImageView(context).apply {
            // Exact proportional container + FIT_CENTER guarantees both zero black bars AND zero text stretching!
            scaleType = ImageView.ScaleType.FIT_CENTER
        }
        screenImageView = img
        innerBox.addView(img, FrameLayout.LayoutParams(-1, -1))

        val dotSize = layout.touchDotSizePx
        val coreSize = (dotSize / 4).coerceAtLeast(dp(context, 3))
        val touchDot = FrameLayout(context).apply {
            visibility = View.GONE
            background = GradientDrawable().apply {
                shape = GradientDrawable.OVAL
                setColor(0x18FFFFFF)
                setStroke(dp(context, 0.75f).coerceAtLeast(1), 0xCCFFFFFF.toInt())
            }
            val centerCore = View(context).apply {
                background = GradientDrawable().apply {
                    shape = GradientDrawable.OVAL
                    setColor(0xE6FFFFFF.toInt())
                }
            }
            addView(centerCore, FrameLayout.LayoutParams(coreSize, coreSize, Gravity.CENTER))
        }
        touchIndicatorView = touchDot
        innerBox.addView(touchDot, FrameLayout.LayoutParams(dotSize, dotSize))

        val hint = TextView(context).apply {
            text = if (expanded) "点右上角网格图标\n选择应用启动" else "虚拟屏"
            textSize = layout.hintSp
            setTextColor(0x99FFFFFF.toInt())
            gravity = Gravity.CENTER
            setPadding(dp(context, 8), dp(context, 8), dp(context, 8), dp(context, 8))
        }
        emptyHintView = hint
        innerBox.addView(hint, FrameLayout.LayoutParams(-1, -1, Gravity.CENTER))

        if (!expanded) {
            val miniCloseSize = dp(context, 20)
            val miniCloseBtn = PipVectorIconButton(context, PipVectorIconButton.IconKind.POWER_CLOSE, isDanger = true) {
                hide()
                scope.launch(Dispatchers.IO) {
                    runCatching { RootPhoneHarness.command(context, JSONObject().put("op", "release")) }
                }
            }
            innerBox.addView(miniCloseBtn, FrameLayout.LayoutParams(miniCloseSize, miniCloseSize, Gravity.TOP or Gravity.END).apply {
                rightMargin = dp(context, 4)
                topMargin = dp(context, 4)
            })

            val waitTag = TextView(context).apply {
                text = "待你处理"
                textSize = layout.capsuleSp
                setTextColor(Color.BLACK)
                typeface = Typeface.DEFAULT_BOLD
                setPadding(dp(context, 6), dp(context, 2), dp(context, 6), dp(context, 2))
                background = GradientDrawable().apply {
                    setColor(0xFFFF9F0A.toInt())
                    cornerRadius = dp(context, 8).toFloat()
                }
                visibility = if (state.phase == "waiting_user") View.VISIBLE else View.GONE
            }
            waitingCapsuleView = waitTag
            innerBox.addView(waitTag, FrameLayout.LayoutParams(-2, -2, Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL).apply {
                bottomMargin = dp(context, 5)
            })

            attachSpringDragAndTap(context, glassFrame, onTap = {
                expanded = true
                transitionSizeAndRebuild(context, RootPhoneHarness.changes.value)
            })
        } else {
            attachExpandedScreenTouchAndDrag(context, img, layout.dragZoneHeightPx)

            // App Picker overlay inside the screen
            val pickerScroll = ScrollView(context).apply {
                visibility = if (pickingApp) View.VISIBLE else View.GONE
                background = GradientDrawable().apply {
                    setColor(0xF0121318.toInt())
                    cornerRadius = innerRadius
                }
            }
            val pickerLinear = LinearLayout(context).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(
                    dp(context, 10),
                    (layout.btnSizePx + layout.btnMarginPx * 2 + dp(context, 4)),
                    dp(context, 10),
                    dp(context, 12)
                )
            }
            appPickerContainer = pickerScroll
            appPickerList = pickerLinear
            pickerScroll.addView(pickerLinear, FrameLayout.LayoutParams(-1, -2))
            innerBox.addView(pickerScroll, FrameLayout.LayoutParams(-1, -1))

            val btnSize = layout.btnSizePx
            val btnGap = (layout.btnMarginPx - dp(context, 1)).coerceAtLeast(dp(context, 4))
            // Top-Start: Minimize to corner pill (Vector EyeOff icon)
            val hidePillBtn = PipVectorIconButton(context, PipVectorIconButton.IconKind.EYE_OFF) {
                pickingApp = false
                expanded = false
                minimizedToPill = true
                transitionSizeAndRebuild(context, RootPhoneHarness.changes.value)
            }
            innerBox.addView(hidePillBtn, FrameLayout.LayoutParams(btnSize, btnSize, Gravity.TOP or Gravity.START).apply {
                leftMargin = layout.btnMarginPx
                topMargin = layout.btnMarginPx
            })

            // Top-End group: App launcher (Grid) + Size Tier (S/M/L) + Shrink to mini-PiP + Close Virtual Display (Power/X)
            val topRightRow = LinearLayout(context).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
            }
            val appBtn = PipVectorIconButton(context, PipVectorIconButton.IconKind.APPS_GRID) {
                pickingApp = !pickingApp
                if (pickingApp) {
                    scope.launch(Dispatchers.IO) {
                        runCatching { RootPhoneHarness.command(context, JSONObject().put("op", "apps").put("manual", true)) }
                    }
                }
                updateWindowContent(context, RootPhoneHarness.changes.value)
            }
            topRightRow.addView(appBtn, LinearLayout.LayoutParams(btnSize, btnSize).apply { rightMargin = btnGap })

            val tierBtn = PipVectorIconButton(
                context,
                PipVectorIconButton.IconKind.SIZE_TIER,
                badgeText = sizeTier.badge
            ) {
                cycleSizeTier(context)
            }
            sizeTierButton = tierBtn
            topRightRow.addView(tierBtn, LinearLayout.LayoutParams(btnSize, btnSize).apply { rightMargin = btnGap })

            val shrinkBtn = PipVectorIconButton(context, PipVectorIconButton.IconKind.SHRINK) {
                pickingApp = false
                expanded = false
                transitionSizeAndRebuild(context, RootPhoneHarness.changes.value)
            }
            topRightRow.addView(shrinkBtn, LinearLayout.LayoutParams(btnSize, btnSize).apply { rightMargin = btnGap })

            val closeDisplayBtn = PipVectorIconButton(context, PipVectorIconButton.IconKind.POWER_CLOSE, isDanger = true) {
                hide()
                scope.launch(Dispatchers.IO) {
                    runCatching { RootPhoneHarness.command(context, JSONObject().put("op", "release")) }
                }
            }
            topRightRow.addView(closeDisplayBtn, LinearLayout.LayoutParams(btnSize, btnSize))

            innerBox.addView(topRightRow, FrameLayout.LayoutParams(-2, -2, Gravity.TOP or Gravity.END).apply {
                rightMargin = layout.btnMarginPx
                topMargin = layout.btnMarginPx
            })

            // Bottom floating capsule: either "我处理好了，继续" when waiting_user, or subtle Back pill
            val bottomCapsule = TextView(context).apply {
                text = if (state.phase == "waiting_user") "我处理好了，继续" else "‹ 返回"
                textSize = if (state.phase == "waiting_user") (layout.capsuleSp + 0.5f) else layout.capsuleSp
                setTextColor(if (state.phase == "waiting_user") Color.BLACK else 0xE6FFFFFF.toInt())
                typeface = Typeface.DEFAULT_BOLD
                setPadding(layout.capsulePadHPx, layout.capsulePadVPx, layout.capsulePadHPx, layout.capsulePadVPx)
                background = GradientDrawable().apply {
                    setColor(if (state.phase == "waiting_user") 0xFFFF9F0A.toInt() else 0x99141418.toInt())
                    cornerRadius = dp(context, 12 * layout.sigma).toFloat()
                    setStroke(1, 0x2EFFFFFF)
                }
                setOnClickListener {
                    val cur = RootPhoneHarness.changes.value
                    if (cur.phase == "waiting_user") {
                        expanded = false
                        transitionSizeAndRebuild(context, cur)
                        runCatching { RootPhoneHarness.resume(context) }
                    } else {
                        scope.launch(Dispatchers.IO) {
                            runCatching {
                                RootPhoneHarness.command(context, JSONObject().put("op", "key").put("key", "back").put("manual", true))
                            }
                        }
                    }
                }
            }
            waitingCapsuleView = bottomCapsule
            innerBox.addView(bottomCapsule, FrameLayout.LayoutParams(-2, -2, Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL).apply {
                bottomMargin = layout.btnMarginPx
            })

            // Bottom-End: Free-form corner resize grip (stepless aspect-locked resizing)
            val resizeGrip = ResizeGripView(context)
            val gripSize = dp(context, 24 * layout.sigma).coerceIn(dp(context, 20), dp(context, 32))
            attachCornerResizeTouch(context, resizeGrip)
            innerBox.addView(resizeGrip, FrameLayout.LayoutParams(gripSize, gripSize, Gravity.BOTTOM or Gravity.END))
        }

        lastBorderColor = borderColorFor(state)
        lastRenderedAppsKey = -1
        glassFrame.addView(innerBox, FrameLayout.LayoutParams(-1, -1))
        container.addView(glassFrame, FrameLayout.LayoutParams(-1, -1))
    }

    @SuppressLint("ClickableViewAccessibility")
    private fun attachCornerResizeTouch(context: Context, gripView: View) {
        val slop = ViewConfiguration.get(context).scaledTouchSlop
        var downRawX = 0f
        var downRawY = 0f
        var startInnerW = 0
        var startWinX = 0
        var startRightEdge = 0
        var resizing = false
        gripView.setOnTouchListener { _, event ->
            runCatching {
                val lp = params ?: return@setOnTouchListener false
                val container = rootView ?: return@setOnTouchListener false
                val wm = windowManager ?: return@setOnTouchListener false
                val metrics = context.resources.displayMetrics
                val shortSidePx = minOf(metrics.widthPixels, metrics.heightPixels).coerceAtLeast(1)
                when (event.actionMasked) {
                    MotionEvent.ACTION_DOWN -> {
                        springAnimator?.cancel()
                        downRawX = event.rawX
                        downRawY = event.rawY
                        val curLayout = computeAdaptiveLayout(context, RootPhoneHarness.changes.value, expanded, minimizedToPill)
                        startInnerW = curLayout.innerW
                        startWinX = lp.x
                        startRightEdge = lp.x + lp.width
                        resizing = false
                    }
                    MotionEvent.ACTION_MOVE -> {
                        val dx = event.rawX - downRawX
                        val dy = event.rawY - downRawY
                        if (!resizing && hypot(dx, dy) > slop) {
                            resizing = true
                        }
                        if (resizing) {
                            val state = RootPhoneHarness.changes.value
                            val bmp = currentBitmap
                            val rawAspect = if (bmp != null && bmp.width > 0 && bmp.height > 0) {
                                bmp.height.toFloat() / bmp.width.toFloat()
                            } else {
                                mainScreenAspect(context)
                            }
                            val aspect = if (rawAspect.isFinite() && rawAspect > 0.2f) rawAspect else 2.15f
                            val dwFromY = dy / aspect
                            val deltaW = if (abs(dwFromY) >= abs(dx)) dwFromY else dx
                            val minInnerW = (shortSidePx * 0.36f).roundToInt().coerceAtLeast(dp(context, 96))
                            val maxInnerW = (shortSidePx * 0.84f).roundToInt().coerceAtLeast(minInnerW + 1)
                            val newInnerW = (startInnerW + deltaW.roundToInt()).coerceIn(minInnerW, maxInnerW)
                            customWidthFraction = (newInnerW.toFloat() / shortSidePx.toFloat()).coerceIn(0.36f, 0.84f)
                            val (targetW, targetH) = computeWindowSizePx(context, state, expanded, minimizedToPill)
                            if (abs(lp.width - targetW) > 1 || abs(lp.height - targetH) > 1) {
                                lp.width = targetW
                                lp.height = targetH
                                val desiredX = if (snapCorner.right) (startRightEdge - targetW) else startWinX
                                val minX = dp(context, 6)
                                val maxX = (metrics.widthPixels - lp.width - minX).coerceAtLeast(minX)
                                val minY = dp(context, 36)
                                val maxY = (metrics.heightPixels - lp.height - minY).coerceAtLeast(minY)
                                lp.x = desiredX.coerceIn(minX, maxX)
                                lp.y = lp.y.coerceIn(minY, maxY)
                                wm.updateViewLayout(container, lp)
                            }
                        }
                    }
                    MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                        resizing = false
                    }
                }
            }
            true
        }
    }

    private fun transitionSizeAndRebuild(context: Context, state: RootPhoneState) {
        val appCtx = context.applicationContext
        mainHandler.post {
            runCatching {
                val container = rootView ?: return@post
                val lp = params ?: return@post
                val wm = windowManager ?: return@post
                val metrics = appCtx.resources.displayMetrics
                val (targetW, targetH) = computeWindowSizePx(appCtx, state, expanded, minimizedToPill)
                val (cornerX, cornerY) = PipSpringPhysics.cornerPosition(
                    if (minimizedToPill) PipSpringPhysics.Corner.BottomEnd else snapCorner,
                    metrics.widthPixels,
                    metrics.heightPixels,
                    targetW,
                    targetH,
                    dp(appCtx, 12),
                    dp(appCtx, 56),
                    dp(appCtx, 92)
                )
                lp.width = targetW
                lp.height = targetH
                lp.x = cornerX
                lp.y = cornerY
                rebuildViewHierarchy(appCtx, container, state)
                updateWindowContent(appCtx, state)
                wm.updateViewLayout(container, lp)
            }
        }
    }

    private fun mainScreenAspect(context: Context): Float {
        val m = context.resources.displayMetrics
        val w = minOf(m.widthPixels, m.heightPixels)
        val h = maxOf(m.widthPixels, m.heightPixels)
        return if (w > 0 && h > 0) h.toFloat() / w.toFloat() else 2.15f
    }

    /**
     * Continuous power-law layout algorithm:
     * Computes outer window dimensions (preserving exact virtual display aspect ratio) AND
     * scales all internal UI elements by sigma = (innerWidthDp / 240f)^0.65f.
     */
    internal fun computeAdaptiveLayout(
        context: Context,
        state: RootPhoneState,
        isExpanded: Boolean,
        isPill: Boolean
    ): PipLayoutMetrics {
        if (isPill) {
            return PipLayoutMetrics(
                winW = dp(context, 124),
                winH = dp(context, 38),
                innerW = dp(context, 118),
                innerH = dp(context, 32),
                framePadPx = dp(context, 3),
                outerRadiusPx = dp(context, 22).toFloat(),
                innerRadiusPx = dp(context, 18).toFloat(),
                btnSizePx = dp(context, 20),
                btnMarginPx = dp(context, 6),
                dragZoneHeightPx = dp(context, 28),
                touchDotSizePx = dp(context, 10),
                capsulePadHPx = dp(context, 8),
                capsulePadVPx = dp(context, 3),
                capsuleSp = 9.5f,
                hintSp = 9f,
                pickerItemSp = 11f,
                sigma = 0.8f
            )
        }
        val bmp = currentBitmap
        val aspect = if (bmp != null && bmp.width > 0 && bmp.height > 0) {
            bmp.height.toFloat() / bmp.width.toFloat()
        } else {
            val info = runCatching { JSONObject(state.imageInfo.ifBlank { state.display }) }.getOrDefault(JSONObject())
            val vw = info.optInt("width", 0)
            val vh = info.optInt("height", 0)
            if (vw > 0 && vh > 0) vh.toFloat() / vw.toFloat() else mainScreenAspect(context)
        }
        val metrics = context.resources.displayMetrics
        val shortSidePx = minOf(metrics.widthPixels, metrics.heightPixels).coerceAtLeast(dp(context, 280))
        val isLandscape = aspect < 1f
        val frac = when {
            !isExpanded -> if (isLandscape) 0.44f else 0.27f
            customWidthFraction != null -> customWidthFraction!!
            else -> if (isLandscape) sizeTier.landscapeFrac else sizeTier.portraitFrac
        }
        val prelimInnerW = (shortSidePx * frac).roundToInt()
        val prelimInnerDp = prelimInnerW / metrics.density.coerceAtLeast(1f)
        val prelimSigma = (prelimInnerDp / 240f).pow(0.65f).coerceIn(0.66f, 1.35f)
        val pad = dp(context, 4f * prelimSigma).coerceIn(dp(context, 3), dp(context, 6))

        val availW = (metrics.widthPixels - dp(context, 20) - pad * 2).coerceAtLeast(dp(context, 80))
        val availH = (metrics.heightPixels - dp(context, 136) - pad * 2).coerceAtLeast(dp(context, 120))
        val innerW = minOf(
            prelimInnerW,
            availW,
            (availH / aspect).roundToInt().coerceAtLeast(dp(context, 68))
        ).coerceAtLeast(dp(context, 68))
        val innerH = (innerW * aspect).roundToInt().coerceAtLeast(dp(context, 68))

        val innerDp = innerW / metrics.density.coerceAtLeast(1f)
        val sigma = (innerDp / 240f).pow(0.65f).coerceIn(0.66f, 1.35f)
        val outerRadius = dp(context, 20f * sigma).coerceIn(dp(context, 14), dp(context, 26)).toFloat()
        val innerRadius = (outerRadius - pad).coerceAtLeast(dp(context, 10).toFloat())
        val btnSize = dp(context, 27f * sigma).coerceIn(dp(context, 22), dp(context, 34))
        val btnMargin = dp(context, 6f * sigma).coerceIn(dp(context, 4), dp(context, 9))
        val dragZoneH = (btnSize + btnMargin * 2 + dp(context, 4)).coerceIn(dp(context, 32), dp(context, 48))
        val dotSize = dp(context, 13f * sigma).coerceIn(dp(context, 10), dp(context, 18))

        return PipLayoutMetrics(
            winW = innerW + pad * 2,
            winH = innerH + pad * 2,
            innerW = innerW,
            innerH = innerH,
            framePadPx = pad,
            outerRadiusPx = outerRadius,
            innerRadiusPx = innerRadius,
            btnSizePx = btnSize,
            btnMarginPx = btnMargin,
            dragZoneHeightPx = dragZoneH,
            touchDotSizePx = dotSize,
            capsulePadHPx = dp(context, 10.5f * sigma).coerceIn(dp(context, 7), dp(context, 14)),
            capsulePadVPx = dp(context, 4f * sigma).coerceIn(dp(context, 2), dp(context, 6)),
            capsuleSp = (10f * sigma).coerceIn(8.5f, 12.5f),
            hintSp = (10f * sigma).coerceIn(8.5f, 12f),
            pickerItemSp = (11.5f * sigma).coerceIn(10f, 14f),
            sigma = sigma
        )
    }

    private fun computeWindowSizePx(context: Context, state: RootPhoneState, isExpanded: Boolean, isPill: Boolean): Pair<Int, Int> {
        val m = computeAdaptiveLayout(context, state, isExpanded, isPill)
        return m.winW to m.winH
    }

    private fun borderColorFor(state: RootPhoneState): Int = when (state.phase) {
        "running" -> 0xB334C759.toInt() // Soft refined emerald border (not harsh neon)
        "waiting_user" -> 0xEBFF9F0A.toInt() // Warm amber when waiting for user
        else -> 0x38FFFFFF // Subtle glass edge when idle
    }

    private fun dotColorFor(state: RootPhoneState): Int = when (state.phase) {
        "running" -> 0xFF34C759.toInt()
        "waiting_user" -> 0xFFFF9F0A.toInt()
        else -> 0x99FFFFFF.toInt()
    }

    private fun updateWindowContent(context: Context, state: RootPhoneState) {
        val container = rootView ?: return
        val lp = params ?: return
        val wm = windowManager ?: return

        val newBorderColor = borderColorFor(state)
        if (newBorderColor != lastBorderColor) {
            lastBorderColor = newBorderColor
            outerBorderDrawable?.setStroke(dp(context, 0.7f), newBorderColor)
        }

        if (!minimizedToPill) {
            if (!expanded) {
                waitingCapsuleView?.visibility = if (state.phase == "waiting_user") View.VISIBLE else View.GONE
            } else {
                val capText = if (state.phase == "waiting_user") "我处理好了，继续" else "‹ 返回"
                if (waitingCapsuleView?.text != capText) {
                    waitingCapsuleView?.text = capText
                }
            }
        }

        // Update touch indicator dot only when a new touch event occurred
        val dot = touchIndicatorView
        val imgView = screenImageView
        if (dot != null && imgView != null && state.lastTouchX >= 0 && state.lastTouchY >= 0 && state.lastTouchTime != lastTouchShownTime && System.currentTimeMillis() - state.lastTouchTime < 1200L && imgView.width > 0 && imgView.height > 0) {
            lastTouchShownTime = state.lastTouchTime
            val info = runCatching { JSONObject(state.imageInfo.ifBlank { state.display }) }.getOrDefault(JSONObject())
            val dispW = info.optInt("width", 540).coerceAtLeast(1)
            val dispH = info.optInt("height", 1200).coerceAtLeast(1)
            val dotRadius = dp(context, 6)
            val px = (state.lastTouchX.toFloat() / dispW * imgView.width).roundToInt() - dotRadius
            val py = (state.lastTouchY.toFloat() / dispH * imgView.height).roundToInt() - dotRadius
            dot.translationX = px.toFloat()
            dot.translationY = py.toFloat()
            dot.scaleX = 0.85f
            dot.scaleY = 0.85f
            dot.visibility = View.VISIBLE
            dot.alpha = 0.92f
            dot.animate().alpha(0f).scaleX(1.18f).scaleY(1.18f).setDuration(360L).withEndAction { dot.visibility = View.GONE }.start()
        }

        // Decode frame if changed (skip decoding when minimized to pill, dismissed, or already decoding to prevent OOM/jank)
        if (!minimizedToPill && !userDismissed && state.image.isNotBlank() && state.image != lastDecodedB64 && !isDecodingFrame) {
            isDecodingFrame = true
            lastDecodedB64 = state.image
            val b64 = state.image
            scope.launch(Dispatchers.Default) {
                try {
                    val bmp = runCatching {
                        val bytes = Base64.decode(b64, Base64.DEFAULT)
                        BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                    }.getOrNull()
                    if (bmp != null) {
                        withContext(Dispatchers.Main) {
                            currentBitmap = bmp
                            screenImageView?.setImageBitmap(bmp)
                            emptyHintView?.visibility = View.GONE
                            val (tw, th) = computeWindowSizePx(context, state, expanded, minimizedToPill)
                            if (abs(lp.width - tw) > 1 || abs(lp.height - th) > 1) {
                                lp.width = tw
                                lp.height = th
                                if (rootView === container) runCatching { wm.updateViewLayout(container, lp) }
                            }
                        }
                    }
                } finally {
                    isDecodingFrame = false
                }
            }
        } else if (state.image.isBlank()) {
            if (currentBitmap == null) {
                emptyHintView?.visibility = View.VISIBLE
            }
        } else if (currentBitmap != null && screenImageView?.drawable == null) {
            screenImageView?.setImageBitmap(currentBitmap)
            emptyHintView?.visibility = View.GONE
        }

        // App picker list in expanded mode (only rebuild when app list or picker visibility changes!)
        appPickerContainer?.visibility = if (expanded && pickingApp) View.VISIBLE else View.GONE
        val list = appPickerList
        val appsKey = if (expanded && pickingApp) state.apps.size else -1
        if (expanded && pickingApp && list != null && appsKey != lastRenderedAppsKey) {
            lastRenderedAppsKey = appsKey
            list.removeAllViews()
            if (state.apps.isEmpty()) {
                val loading = TextView(context).apply {
                    text = "读取应用中…"
                    textSize = 11f
                    setTextColor(Color.WHITE)
                    setPadding(dp(context, 4), dp(context, 8), dp(context, 4), dp(context, 8))
                }
                list.addView(loading)
            } else {
                state.apps.forEach { app ->
                    val item = TextView(context).apply {
                        text = app.name
                        textSize = 11.5f
                        setTextColor(0xF2FFFFFF.toInt())
                        setPadding(dp(context, 8), dp(context, 7), dp(context, 8), dp(context, 7))
                        setOnClickListener {
                            pickingApp = false
                            lastRenderedAppsKey = -1
                            appPickerContainer?.visibility = View.GONE
                            scope.launch(Dispatchers.IO) {
                                runCatching {
                                    RootPhoneHarness.command(context, JSONObject().put("op", "launch").put("component", app.component).put("manual", true))
                                }
                            }
                        }
                    }
                    list.addView(item)
                }
            }
        }

        val (targetW, targetH) = computeWindowSizePx(context, state, expanded, minimizedToPill)
        if (abs(lp.width - targetW) > 1 || abs(lp.height - targetH) > 1) {
            lp.width = targetW
            lp.height = targetH
            runCatching { wm.updateViewLayout(container, lp) }
        }
    }

    @SuppressLint("ClickableViewAccessibility")
    private fun attachSpringDragAndTap(context: Context, handleView: View, onTap: (() -> Unit)?) {
        val slop = ViewConfiguration.get(context).scaledTouchSlop
        var downRawX = 0f
        var downRawY = 0f
        var startWinX = 0
        var startWinY = 0
        var dragging = false
        var tracker: VelocityTracker? = null

        handleView.setOnTouchListener { _, event ->
            val lp = params ?: return@setOnTouchListener false
            val container = rootView ?: return@setOnTouchListener false
            val wm = windowManager ?: return@setOnTouchListener false
            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    springAnimator?.cancel()
                    tracker?.recycle()
                    tracker = VelocityTracker.obtain().also { it.addMovement(event) }
                    downRawX = event.rawX
                    downRawY = event.rawY
                    startWinX = lp.x
                    startWinY = lp.y
                    dragging = false
                    true
                }
                MotionEvent.ACTION_MOVE -> {
                    tracker?.addMovement(event)
                    val dx = event.rawX - downRawX
                    val dy = event.rawY - downRawY
                    if (!dragging && hypot(dx, dy) > slop) {
                        dragging = true
                    }
                    if (dragging) {
                        val metrics = context.resources.displayMetrics
                        lp.x = (startWinX + dx.roundToInt()).coerceIn(
                            dp(context, 6),
                            (metrics.widthPixels - lp.width - dp(context, 6)).coerceAtLeast(dp(context, 6))
                        )
                        lp.y = (startWinY + dy.roundToInt()).coerceIn(
                            dp(context, 36),
                            (metrics.heightPixels - lp.height - dp(context, 36)).coerceAtLeast(dp(context, 36))
                        )
                        runCatching { wm.updateViewLayout(container, lp) }
                    }
                    true
                }
                MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                    tracker?.addMovement(event)
                    tracker?.computeCurrentVelocity(1000)
                    val vx = tracker?.xVelocity ?: 0f
                    val vy = tracker?.yVelocity ?: 0f
                    tracker?.recycle()
                    tracker = null
                    if (dragging) {
                        animateSpringToNearestCorner(context, vx, vy)
                    } else if (event.actionMasked == MotionEvent.ACTION_UP) {
                        mainHandler.post { onTap?.invoke() }
                    }
                    dragging = false
                    true
                }
                else -> false
            }
        }
    }

    @SuppressLint("ClickableViewAccessibility")
    private fun attachExpandedScreenTouchAndDrag(context: Context, imageView: ImageView, dragZonePx: Int = dp(context, 38)) {
        val slop = ViewConfiguration.get(context).scaledTouchSlop
        var downX = 0f
        var downY = 0f
        var downRawX = 0f
        var downRawY = 0f
        var startWinX = 0
        var startWinY = 0
        var windowDragMode = false
        var draggingWindow = false
        var liveTouchStarted = false
        var lastMoveSentAt = 0L
        var lastSentX = -1
        var lastSentY = -1
        var tracker: VelocityTracker? = null

        imageView.setOnTouchListener { v, event ->
            val lp = params ?: return@setOnTouchListener false
            val container = rootView ?: return@setOnTouchListener false
            val wm = windowManager ?: return@setOnTouchListener false
            val state = RootPhoneHarness.changes.value

            val vw = v.width.coerceAtLeast(1)
            val vh = v.height.coerceAtLeast(1)
            val info = runCatching { JSONObject(state.imageInfo.ifBlank { state.display }) }.getOrDefault(JSONObject())
            val dispW = info.optInt("width", 540).coerceAtLeast(1)
            val dispH = info.optInt("height", 1200).coerceAtLeast(1)
            val displayId = info.optInt("displayId", runCatching { JSONObject(state.display).optInt("displayId", -1) }.getOrDefault(-1))

            fun mapX(px: Float) = (px / vw * dispW).toInt().coerceIn(0, dispW - 1)
            fun mapY(py: Float) = (py / vh * dispH).toInt().coerceIn(0, dispH - 1)

            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    springAnimator?.cancel()
                    downX = event.x
                    downY = event.y
                    downRawX = event.rawX
                    downRawY = event.rawY
                    startWinX = lp.x
                    startWinY = lp.y
                    windowDragMode = (downY <= dragZonePx) || (state.phase == "running")
                    draggingWindow = false
                    liveTouchStarted = false
                    if (windowDragMode) {
                        tracker?.recycle()
                        tracker = VelocityTracker.obtain().also { it.addMovement(event) }
                    } else if (displayId > 0) {
                        val tx = mapX(downX)
                        val ty = mapY(downY)
                        liveTouchStarted = true
                        lastSentX = tx
                        lastSentY = ty
                        lastMoveSentAt = android.os.SystemClock.uptimeMillis()
                        RootPhoneHarness.sendLiveTouch(context, "down", tx, ty)
                    }
                    true
                }
                MotionEvent.ACTION_MOVE -> {
                    if (windowDragMode) {
                        tracker?.addMovement(event)
                        val dx = event.rawX - downRawX
                        val dy = event.rawY - downRawY
                        if (!draggingWindow && hypot(dx, dy) > slop) {
                            draggingWindow = true
                        }
                        if (draggingWindow) {
                            val metrics = context.resources.displayMetrics
                            lp.x = (startWinX + dx.roundToInt()).coerceIn(
                                dp(context, 6),
                                (metrics.widthPixels - lp.width - dp(context, 6)).coerceAtLeast(dp(context, 6))
                            )
                            lp.y = (startWinY + dy.roundToInt()).coerceIn(
                                dp(context, 36),
                                (metrics.heightPixels - lp.height - dp(context, 36)).coerceAtLeast(dp(context, 36))
                            )
                            runCatching { wm.updateViewLayout(container, lp) }
                        }
                    } else if (liveTouchStarted && displayId > 0) {
                        val now = android.os.SystemClock.uptimeMillis()
                        val tx = mapX(event.x)
                        val ty = mapY(event.y)
                        if (now - lastMoveSentAt >= 10L || abs(tx - lastSentX) + abs(ty - lastSentY) >= 2) {
                            lastMoveSentAt = now
                            lastSentX = tx
                            lastSentY = ty
                            RootPhoneHarness.sendLiveTouch(context, "move", tx, ty)
                        }
                    }
                    true
                }
                MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                    if (windowDragMode) {
                        tracker?.addMovement(event)
                        tracker?.computeCurrentVelocity(1000)
                        val vx = tracker?.xVelocity ?: 0f
                        val vy = tracker?.yVelocity ?: 0f
                        tracker?.recycle()
                        tracker = null
                        if (draggingWindow) {
                            animateSpringToNearestCorner(context, vx, vy)
                        }
                        draggingWindow = false
                        return@setOnTouchListener true
                    }
                    if (liveTouchStarted && displayId > 0) {
                        liveTouchStarted = false
                        val tx = mapX(event.x)
                        val ty = mapY(event.y)
                        val act = if (event.actionMasked == MotionEvent.ACTION_CANCEL) "cancel" else "up"
                        RootPhoneHarness.sendLiveTouch(context, act, tx, ty)
                    }
                    true
                }
                else -> true
            }
        }
    }

    private fun animateSpringToNearestCorner(context: Context, vx: Float, vy: Float) {
        val lp = params ?: return
        val container = rootView ?: return
        val wm = windowManager ?: return
        val metrics = context.resources.displayMetrics
        val marginX = dp(context, 12)
        val topMargin = dp(context, 56)
        val bottomMargin = dp(context, 92)

        val projX = lp.x + PipSpringPhysics.project(vx)
        val projY = lp.y + PipSpringPhysics.project(vy)
        snapCorner = PipSpringPhysics.nearestCorner(
            projX, projY,
            metrics.widthPixels, metrics.heightPixels,
            lp.width, lp.height,
            marginX, topMargin, bottomMargin
        )
        val (targetX, targetY) = PipSpringPhysics.cornerPosition(
            snapCorner,
            metrics.widthPixels,
            metrics.heightPixels,
            lp.width, lp.height,
            marginX, topMargin, bottomMargin
        )
        val startX = lp.x.toFloat()
        val startY = lp.y.toFloat()
        if (abs(startX - targetX) < 1f && abs(startY - targetY) < 1f) return

        springAnimator?.cancel()
        springAnimator = ValueAnimator.ofFloat(0f, 0.42f).apply {
            duration = 420L
            interpolator = LinearInterpolator()
            addUpdateListener { anim ->
                val tSec = anim.animatedValue as Float
                lp.x = PipSpringPhysics.stepCriticalSpring(startX, targetX.toFloat(), vx, tSec).roundToInt()
                lp.y = PipSpringPhysics.stepCriticalSpring(startY, targetY.toFloat(), vy, tSec).roundToInt()
                if (rootView === container) {
                    runCatching { wm.updateViewLayout(container, lp) }
                }
            }
            start()
        }
    }
}

/**
 * In-app Mika-style AgentPip fallback when system-wide overlay permission is not granted yet.
 * Uses exact aspect-ratio matching (zero black bars), refined glass edge, Canvas vector icons,
 * and `withFrameNanos` critical-damped spring physics!
 */
@Composable
internal fun InAppFloatingVirtualDisplay() {
    val context = LocalContext.current
    val state by RootPhoneHarness.changes.collectAsState()
    val systemOverlayShowing by FloatingVirtualDisplay.overlayVisible.collectAsState()
    val userDismissed by FloatingVirtualDisplay.userDismissedFlow.collectAsState()
    val disp = remember(state.display) { runCatching { JSONObject(state.display) }.getOrDefault(JSONObject()) }
    val created = disp.optBoolean("created")

    val visible = created && !userDismissed && state.phase != "stopping" && state.phase != "stopped" && !systemOverlayShowing && !FloatingVirtualDisplay.canOverlay(context)
    var expanded by remember { mutableStateOf(false) }
    var hiddenToPill by remember { mutableStateOf(false) }
    LaunchedEffect(created, state.active, state.phase, userDismissed) {
        if (!created || userDismissed || state.phase == "stopping" || state.phase == "stopped") {
            expanded = false
            hiddenToPill = false
        }
    }

    val bitmap by produceState<androidx.compose.ui.graphics.ImageBitmap?>(null, state.image) {
        value = withContext(Dispatchers.Default) {
            if (state.image.isBlank()) null
            else {
                FloatingVirtualDisplay.cachedBitmap()?.asImageBitmap() ?: runCatching {
                    val bytes = Base64.decode(state.image, Base64.DEFAULT)
                    BitmapFactory.decodeByteArray(bytes, 0, bytes.size)?.asImageBitmap()
                }.getOrNull()
            }
        }
    }

    AnimatedVisibility(
        visible = visible,
        enter = fadeIn(tween(220)),
        exit = fadeOut(tween(180))
    ) {
        BoxWithConstraints(Modifier.fillMaxSize()) {
            val density = LocalDensity.current
            val scope = rememberCoroutineScope()
            val borderColor = when (state.phase) {
                "running" -> androidx.compose.ui.graphics.Color(0xB334C759)
                "waiting_user" -> androidx.compose.ui.graphics.Color(0xEBFF9F0A)
                else -> androidx.compose.ui.graphics.Color(0x38FFFFFF)
            }

            if (hiddenToPill) {
                Box(
                    Modifier
                        .align(Alignment.BottomEnd)
                        .padding(end = 12.dp, bottom = 92.dp)
                        .shadow(12.dp, RoundedCornerShape(22.dp))
                        .clip(RoundedCornerShape(22.dp))
                        .background(androidx.compose.ui.graphics.Color(0xE618181C))
                        .border(1.dp, borderColor, RoundedCornerShape(22.dp))
                        .clickable { hiddenToPill = false }
                        .padding(start = 13.dp, end = 8.dp, top = 7.dp, bottom = 7.dp)
                ) {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(7.dp)) {
                        Box(Modifier.size(7.dp).clip(CircleShape).background(borderColor))
                        Text(
                            if (state.phase == "waiting_user") "待你处理" else "虚拟屏",
                            color = androidx.compose.ui.graphics.Color(0xF2FFFFFF),
                            fontSize = 12.sp,
                            fontWeight = FontWeight.Medium
                        )
                        ComposePipCircleButton(
                            modifier = Modifier.size(20.dp),
                            isDanger = true,
                            onClick = {
                                hiddenToPill = false
                                expanded = false
                                FloatingVirtualDisplay.hide()
                                scope.launch(Dispatchers.IO) {
                                    runCatching { RootPhoneHarness.command(context, JSONObject().put("op", "release")) }
                                }
                            }
                        ) {
                            val strokeW = 1.4.dp.toPx()
                            val c = androidx.compose.ui.graphics.Color(0xFFFF7A72)
                            drawLine(c, Offset(size.width * 0.32f, size.height * 0.32f), Offset(size.width * 0.68f, size.height * 0.68f), strokeW, StrokeCap.Round)
                            drawLine(c, Offset(size.width * 0.68f, size.height * 0.32f), Offset(size.width * 0.32f, size.height * 0.68f), strokeW, StrokeCap.Round)
                        }
                    }
                }
                return@BoxWithConstraints
            }

            val info = remember(state.imageInfo, state.display) {
                runCatching { JSONObject(state.imageInfo.ifBlank { state.display }) }.getOrDefault(JSONObject())
            }
            val dispW = info.optInt("width", 1080).coerceAtLeast(1)
            val dispH = info.optInt("height", 2400).coerceAtLeast(1)
            val bmp = bitmap
            val aspect = if (bmp != null && bmp.width > 0 && bmp.height > 0) {
                bmp.height.toFloat() / bmp.width.toFloat()
            } else {
                dispH.toFloat() / dispW.toFloat()
            }

            var tierState by remember { mutableStateOf(FloatingVirtualDisplay.currentTier()) }
            val sizeCurve = remember { CubicBezierEasing(0.2f, 0.8f, 0.2f, 1f) }
            val padDp = if (expanded) 4.dp else 3.5.dp
            val availWDp = (maxWidth - 24.dp - padDp * 2).coerceAtLeast(80.dp)
            val availHDp = (maxHeight - 160.dp - padDp * 2).coerceAtLeast(120.dp)
            val shortSideDp = minOf(maxWidth, maxHeight)
            val frac = when {
                !expanded -> if (aspect < 1f) 0.44f else 0.27f
                aspect < 1f -> tierState.landscapeFrac
                else -> tierState.portraitFrac
            }
            val baseInnerW = shortSideDp * frac
            val innerW = minOf(baseInnerW, availWDp, availHDp / aspect).coerceAtLeast(68.dp)
            val innerH = (innerW * aspect).coerceAtLeast(68.dp)
            val w by animateDpAsState(innerW + padDp * 2, tween(280, easing = sizeCurve), label = "pipW")
            val h by animateDpAsState(innerH + padDp * 2, tween(280, easing = sizeCurve), label = "pipH")

            val maxW = constraints.maxWidth
            val maxH = constraints.maxHeight
            val wPx = with(density) { w.roundToPx() }
            val hPx = with(density) { h.roundToPx() }
            val marginPx = with(density) { 12.dp.roundToPx() }
            val topSafePx = with(density) { 64.dp.roundToPx() }
            val bottomSafePx = with(density) { 92.dp.roundToPx() }

            var corner by remember { mutableStateOf(PipSpringPhysics.Corner.TopEnd) }
            val (anchorX, anchorY) = PipSpringPhysics.cornerPosition(corner, maxW, maxH, wPx, hPx, marginPx, topSafePx, bottomSafePx)
            var dragOffset by remember { mutableStateOf(Offset.Zero) }
            var springJob by remember { mutableStateOf<Job?>(null) }
            val outerShape = RoundedCornerShape(if (expanded) 20.dp else 17.dp)
            val innerShape = RoundedCornerShape(if (expanded) 16.dp else 13.5.dp)

            Box(
                Modifier
                    .offset {
                        IntOffset(
                            (anchorX + dragOffset.x.roundToInt()).coerceIn(marginPx, (maxW - wPx - marginPx).coerceAtLeast(marginPx)),
                            (anchorY + dragOffset.y.roundToInt()).coerceIn(topSafePx, (maxH - hPx - bottomSafePx).coerceAtLeast(topSafePx))
                        )
                    }
                    .size(w, h)
                    .shadow(if (expanded) 18.dp else 12.dp, outerShape)
                    .clip(outerShape)
                    .background(androidx.compose.ui.graphics.Color(0xD918181C), outerShape)
                    .border(1.2.dp, borderColor, outerShape)
                    .pointerInput(expanded, maxW, maxH, wPx, hPx) {
                        if (expanded) return@pointerInput
                        val velocityTracker = ComposeVelocityTracker()
                        awaitEachGesture {
                            val down = awaitFirstDown(requireUnconsumed = false)
                            springJob?.cancel()
                            velocityTracker.resetTracking()
                            velocityTracker.addPosition(down.uptimeMillis, down.position)
                            var dragged = false
                            var total = Offset.Zero
                            while (true) {
                                val event = awaitPointerEvent()
                                val change = event.changes.firstOrNull { it.id == down.id } ?: break
                                if (!change.pressed) break
                                velocityTracker.addPosition(change.uptimeMillis, change.position)
                                val delta = change.positionChange()
                                total += delta
                                if (!dragged && total.getDistance() > viewConfiguration.touchSlop) {
                                    dragged = true
                                }
                                if (dragged) {
                                    change.consume()
                                    dragOffset += delta
                                }
                            }
                            if (dragged) {
                                val v = velocityTracker.calculateVelocity()
                                val curX = anchorX + dragOffset.x
                                val curY = anchorY + dragOffset.y
                                val targetCorner = PipSpringPhysics.nearestCorner(
                                    curX + PipSpringPhysics.project(v.x),
                                    curY + PipSpringPhysics.project(v.y),
                                    maxW, maxH, wPx, hPx, marginPx, topSafePx, bottomSafePx
                                )
                                val (newAnchorX, newAnchorY) = PipSpringPhysics.cornerPosition(targetCorner, maxW, maxH, wPx, hPx, marginPx, topSafePx, bottomSafePx)
                                val startDx = curX - newAnchorX
                                val startDy = curY - newAnchorY
                                corner = targetCorner
                                dragOffset = Offset(startDx, startDy)
                                springJob = scope.launch {
                                    val startNanos = withFrameNanos { it }
                                    while (isActive) {
                                        val nowNanos = withFrameNanos { it }
                                        val tSec = (nowNanos - startNanos) / 1_000_000_000f
                                        if (tSec >= 0.42f) {
                                            dragOffset = Offset.Zero
                                            break
                                        }
                                        val nx = PipSpringPhysics.stepCriticalSpring(startDx, 0f, v.x, tSec)
                                        val ny = PipSpringPhysics.stepCriticalSpring(startDy, 0f, v.y, tSec)
                                        dragOffset = Offset(nx, ny)
                                    }
                                }
                            } else {
                                expanded = true
                            }
                        }
                    }
                    .padding(padDp)
            ) {
                Box(
                    Modifier
                        .fillMaxSize()
                        .clip(innerShape)
                        .background(androidx.compose.ui.graphics.Color(0xFF0E0E12))
                        .border(0.5.dp, androidx.compose.ui.graphics.Color(0x28FFFFFF), innerShape)
                ) {
                    if (bmp != null) {
                        Image(
                            bmp,
                            "虚拟屏实时画面",
                            Modifier
                                .fillMaxSize()
                                .pointerInput(expanded, state.phase, state.imageInfo) {
                                    if (!expanded || state.phase == "running") return@pointerInput
                                    awaitEachGesture {
                                        val down = awaitFirstDown(requireUnconsumed = false)
                                        val displayId = disp.optInt("displayId", -1)
                                        fun mx(v: Float) = (v / size.width.coerceAtLeast(1) * dispW).toInt().coerceIn(0, dispW - 1)
                                        fun my(v: Float) = (v / size.height.coerceAtLeast(1) * dispH).toInt().coerceIn(0, dispH - 1)
                                        if (displayId > 0) {
                                            RootPhoneHarness.sendLiveTouch(context, "down", mx(down.position.x), my(down.position.y))
                                        }
                                        var end = down.position
                                        var lastMoveAt = android.os.SystemClock.uptimeMillis()
                                        var lastX = mx(down.position.x)
                                        var lastY = my(down.position.y)
                                        do {
                                            val ev = awaitPointerEvent()
                                            val ptr = ev.changes.firstOrNull { it.id == down.id } ?: break
                                            end = ptr.position
                                            ptr.consume()
                                            if (ptr.pressed && displayId > 0) {
                                                val now = android.os.SystemClock.uptimeMillis()
                                                val tx = mx(end.x)
                                                val ty = my(end.y)
                                                if (now - lastMoveAt >= 10L || abs(tx - lastX) + abs(ty - lastY) >= 2) {
                                                    lastMoveAt = now
                                                    lastX = tx
                                                    lastY = ty
                                                    RootPhoneHarness.sendLiveTouch(context, "move", tx, ty)
                                                }
                                            }
                                            if (!ptr.pressed) {
                                                break
                                            }
                                        } while (true)
                                        if (displayId > 0) {
                                            RootPhoneHarness.sendLiveTouch(context, "up", mx(end.x), my(end.y))
                                        }
                                    }
                                },
                            // ContentScale.Fit inside exact aspect-ratio container leaves zero black bars and zero text distortion!
                            contentScale = ContentScale.Fit
                        )
                    } else {
                        Text(
                            if (expanded) "点此打开工作台启动应用" else "虚拟屏",
                            color = androidx.compose.ui.graphics.Color(0x99FFFFFF),
                            fontSize = if (expanded) 10.sp else 9.sp,
                            modifier = Modifier
                                .align(Alignment.Center)
                                .clickable(enabled = expanded) {
                                    context.startActivity(Intent(context, NativePhoneActivity::class.java))
                                }
                                .padding(8.dp)
                        )
                    }

                    if (!expanded) {
                        ComposePipCircleButton(
                            modifier = Modifier.align(Alignment.TopEnd).padding(4.dp).size(20.dp),
                            isDanger = true,
                            onClick = {
                                expanded = false
                                FloatingVirtualDisplay.hide()
                                scope.launch(Dispatchers.IO) {
                                    runCatching { RootPhoneHarness.command(context, JSONObject().put("op", "release")) }
                                }
                            }
                        ) {
                            val strokeW = 1.4.dp.toPx()
                            val c = androidx.compose.ui.graphics.Color(0xFFFF7A72)
                            drawLine(c, Offset(size.width * 0.32f, size.height * 0.32f), Offset(size.width * 0.68f, size.height * 0.68f), strokeW, StrokeCap.Round)
                            drawLine(c, Offset(size.width * 0.68f, size.height * 0.32f), Offset(size.width * 0.32f, size.height * 0.68f), strokeW, StrokeCap.Round)
                        }
                    }

                    if (!expanded && state.phase == "waiting_user") {
                        Box(
                            Modifier
                                .align(Alignment.BottomCenter)
                                .padding(bottom = 5.dp)
                                .clip(RoundedCornerShape(8.dp))
                                .background(androidx.compose.ui.graphics.Color(0xFFFF9F0A))
                                .padding(horizontal = 6.dp, vertical = 2.dp)
                        ) {
                            Text("待你处理", color = androidx.compose.ui.graphics.Color.Black, fontSize = 9.sp, fontWeight = FontWeight.Bold)
                        }
                    }

                    if (expanded) {
                        // Top-Start: Vector Eye-Off (Minimize to RestorePill)
                        ComposePipCircleButton(
                            modifier = Modifier.align(Alignment.TopStart).padding(6.dp),
                            onClick = { expanded = false; hiddenToPill = true }
                        ) {
                            val strokeW = 1.5.dp.toPx()
                            drawOval(
                                color = androidx.compose.ui.graphics.Color(0xF2FFFFFF),
                                topLeft = Offset(size.width * 0.24f, size.height * 0.34f),
                                size = Size(size.width * 0.52f, size.height * 0.32f),
                                style = Stroke(width = strokeW)
                            )
                            drawLine(
                                color = androidx.compose.ui.graphics.Color(0xF2FFFFFF),
                                start = Offset(size.width * 0.26f, size.height * 0.72f),
                                end = Offset(size.width * 0.74f, size.height * 0.28f),
                                strokeWidth = strokeW,
                                cap = StrokeCap.Round
                            )
                        }

                        // Top-End: Shrink & Close Display buttons
                        Row(
                            Modifier.align(Alignment.TopEnd).padding(6.dp),
                            horizontalArrangement = Arrangement.spacedBy(5.dp)
                        ) {
                            ComposePipCircleButton(onClick = { expanded = false }) {
                                val strokeW = 1.5.dp.toPx()
                                val c = androidx.compose.ui.graphics.Color(0xF2FFFFFF)
                                drawLine(c, Offset(size.width * 0.55f, size.height * 0.45f), Offset(size.width * 0.74f, size.height * 0.26f), strokeW, StrokeCap.Round)
                                drawLine(c, Offset(size.width * 0.45f, size.height * 0.55f), Offset(size.width * 0.26f, size.height * 0.74f), strokeW, StrokeCap.Round)
                            }
                            ComposePipCircleButton(
                                isDanger = true,
                                onClick = {
                                    expanded = false
                                    FloatingVirtualDisplay.hide()
                                    scope.launch(Dispatchers.IO) {
                                        runCatching { RootPhoneHarness.command(context, JSONObject().put("op", "release")) }
                                    }
                                }
                            ) {
                                val strokeW = 1.5.dp.toPx()
                                val c = androidx.compose.ui.graphics.Color(0xFFFF7A72)
                                drawLine(c, Offset(size.width * 0.32f, size.height * 0.32f), Offset(size.width * 0.68f, size.height * 0.68f), strokeW, StrokeCap.Round)
                                drawLine(c, Offset(size.width * 0.68f, size.height * 0.32f), Offset(size.width * 0.32f, size.height * 0.68f), strokeW, StrokeCap.Round)
                            }
                        }

                        // Bottom capsule: "我处理好了，继续" when waiting_user, or "‹ 返回"
                        Box(
                            Modifier
                                .align(Alignment.BottomCenter)
                                .padding(bottom = 6.dp)
                                .clip(RoundedCornerShape(12.dp))
                                .background(if (state.phase == "waiting_user") androidx.compose.ui.graphics.Color(0xFFFF9F0A) else androidx.compose.ui.graphics.Color(0x99141418))
                                .border(0.8.dp, androidx.compose.ui.graphics.Color(0x2EFFFFFF), RoundedCornerShape(12.dp))
                                .clickable {
                                    if (state.phase == "waiting_user") {
                                        expanded = false
                                        runCatching { RootPhoneHarness.resume(context) }
                                    } else {
                                        scope.launch(Dispatchers.IO) {
                                            runCatching {
                                                RootPhoneHarness.command(context, JSONObject().put("op", "key").put("key", "back").put("manual", true))
                                            }
                                        }
                                    }
                                }
                                .padding(horizontal = 10.dp, vertical = 4.dp)
                        ) {
                            Text(
                                if (state.phase == "waiting_user") "我处理好了，继续" else "‹ 返回",
                                color = if (state.phase == "waiting_user") androidx.compose.ui.graphics.Color.Black else androidx.compose.ui.graphics.Color(0xE6FFFFFF),
                                fontSize = 10.sp,
                                fontWeight = FontWeight.Bold
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun ComposePipCircleButton(
    modifier: Modifier = Modifier,
    isDanger: Boolean = false,
    onClick: () -> Unit,
    drawIcon: androidx.compose.ui.graphics.drawscope.DrawScope.() -> Unit
) {
    Box(
        modifier
            .size(26.dp)
            .clip(CircleShape)
            .background(if (isDanger) androidx.compose.ui.graphics.Color(0xCC3B171B) else androidx.compose.ui.graphics.Color(0xA6141418))
            .border(
                0.8.dp,
                if (isDanger) androidx.compose.ui.graphics.Color(0x66FF6961) else androidx.compose.ui.graphics.Color(0x38FFFFFF),
                CircleShape
            )
            .clickable(onClick = onClick),
        contentAlignment = Alignment.Center
    ) {
        ComposeCanvas(Modifier.fillMaxSize(), onDraw = drawIcon)
    }
}
