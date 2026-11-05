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
    onClick: () -> Unit
) : View(context) {
    enum class IconKind { EYE_OFF, APPS_GRID, SHRINK, POWER_CLOSE }

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
    private val rectF = RectF()

    init {
        isClickable = true
        setOnClickListener { onClick() }
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val w = width.toFloat()
        val h = height.toFloat()
        val cx = w / 2f
        val cy = h / 2f
        val r = (minOf(w, h) / 2f) - strokePaint.strokeWidth
        canvas.drawCircle(cx, cy, r, bgPaint)
        canvas.drawCircle(cx, cy, r, strokePaint)

        val d = resources.displayMetrics.density
        when (kind) {
            IconKind.EYE_OFF -> {
                // Sleek Eye-Off icon (Mika style)
                rectF.set(cx - 5.5f * d, cy - 3.2f * d, cx + 5.5f * d, cy + 3.2f * d)
                canvas.drawOval(rectF, iconPaint)
                canvas.drawLine(cx - 5.2f * d, cy + 4.5f * d, cx + 5.2f * d, cy - 4.5f * d, iconPaint)
            }
            IconKind.APPS_GRID -> {
                // 2x2 rounded grid
                val s = 3.4f * d
                val gap = 1.2f * d
                val cr = 0.9f * d
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
                // Minimize / collapse arrows
                val arm = 3.2f * d
                // Top-right inward arrow
                canvas.drawLine(cx + 1.2f * d, cy - 1.2f * d, cx + 4.8f * d, cy - 4.8f * d, iconPaint)
                canvas.drawLine(cx + 1.2f * d, cy - 1.2f * d, cx + 1.2f * d + arm, cy - 1.2f * d, iconPaint)
                canvas.drawLine(cx + 1.2f * d, cy - 1.2f * d, cx + 1.2f * d, cy - 1.2f * d - arm, iconPaint)
                // Bottom-left inward arrow
                canvas.drawLine(cx - 1.2f * d, cy + 1.2f * d, cx - 4.8f * d, cy + 4.8f * d, iconPaint)
                canvas.drawLine(cx - 1.2f * d, cy + 1.2f * d, cx - 1.2f * d - arm, cy + 1.2f * d, iconPaint)
                canvas.drawLine(cx - 1.2f * d, cy + 1.2f * d, cx - 1.2f * d, cy + 1.2f * d + arm, iconPaint)
            }
            IconKind.POWER_CLOSE -> {
                // Crisp X close icon
                val len = 3.8f * d
                canvas.drawLine(cx - len, cy - len, cx + len, cy + len, iconPaint)
                canvas.drawLine(cx + len, cy - len, cx - len, cy + len, iconPaint)
            }
        }
    }
}

/**
 * Mika-style floating AgentPip window (TYPE_APPLICATION_OVERLAY) on the Android desktop:
 * - Zero black bars: outer window size matches exact bitmap aspect ratio + bezel inset, using ScaleType.FIT_XY
 * - Subtle frosted dark glass bezel (3.5dp inset, 0.5dp inner glass rim, 1.2dp soft outer status ring)
 * - 1:1 finger drag with VelocityTracker + critical-damped spring snapping to the 4 screen corners
 * - Vector Canvas icons for Hide-to-Pill, App Picker, Collapse, and Close Virtual Display (`release`)
 */
object FloatingVirtualDisplay {
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
    private var snapCorner = PipSpringPhysics.Corner.TopEnd
    private var springAnimator: ValueAnimator? = null

    private var screenImageView: ImageView? = null
    private var touchIndicatorView: View? = null
    private var emptyHintView: TextView? = null
    private var waitingCapsuleView: TextView? = null
    private var appPickerContainer: ScrollView? = null
    private var appPickerList: LinearLayout? = null
    private var outerBorderDrawable: GradientDrawable? = null
    private var lastBorderColor: Int = 0
    private var lastRenderedAppsKey: Int = -1
    private var lastTouchShownTime: Long = 0L

    // Frame decoding cache
    @Volatile private var lastDecodedB64: String = ""
    @Volatile private var currentBitmap: Bitmap? = null

    private val _overlayVisible = MutableStateFlow(false)
    val overlayVisible: StateFlow<Boolean> = _overlayVisible.asStateFlow()

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
        mainHandler.post { removeWindow() }
    }

    internal fun onWorkspaceStateChanged(context: Context, state: RootPhoneState) {
        val appCtx = context.applicationContext
        mainHandler.post {
            ensureWatching(appCtx)
            val created = runCatching { JSONObject(state.display).optBoolean("created") }.getOrDefault(false)
            if (!created && !state.active) {
                userDismissed = false
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
                if (!created && !state.active) {
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
                scope.launch(Dispatchers.IO) {
                    runCatching { RootPhoneHarness.command(context, JSONObject().put("op", "release")) }
                }
            }
            pill.addView(closeMiniBtn, LinearLayout.LayoutParams(dp(context, 20), dp(context, 20)))

            attachSpringDragAndTap(context, pill, onTap = {
                minimizedToPill = false
                transitionSizeAndRebuild(context, RootPhoneHarness.changes.value)
            })
            container.addView(pill, FrameLayout.LayoutParams(-1, -1))
            return
        }

        // State 2 & 3: Mika's Sleek Frosted-Glass Frame (Collapsed 104dp or Expanded 226dp)
        val pad = dp(context, if (expanded) 4f else 3.5f)
        val outerRadius = dp(context, if (expanded) 20f else 17f).toFloat()
        val innerRadius = dp(context, if (expanded) 16f else 13.5f).toFloat()

        val glassFrame = FrameLayout(context).apply {
            setPadding(pad, pad, pad, pad)
            val bg = GradientDrawable().apply {
                setColor(0xD918181C.toInt()) // Deep translucent obsidian glass
                cornerRadius = outerRadius
                setStroke(dp(context, 1.2f), borderColorFor(state))
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
                setStroke(1, 0x28FFFFFF)
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

        val touchDot = View(context).apply {
            visibility = View.GONE
            background = GradientDrawable().apply {
                shape = GradientDrawable.OVAL
                setColor(0x4D34C759)
                setStroke(dp(context, 1.5f), 0xFF34C759.toInt())
            }
        }
        touchIndicatorView = touchDot
        innerBox.addView(touchDot, FrameLayout.LayoutParams(dp(context, 18), dp(context, 18)))

        val hint = TextView(context).apply {
            text = if (expanded) "点右上角网格图标\n选择应用启动" else "虚拟屏"
            textSize = if (expanded) 10f else 9f
            setTextColor(0x99FFFFFF.toInt())
            gravity = Gravity.CENTER
            setPadding(dp(context, 8), dp(context, 8), dp(context, 8), dp(context, 8))
        }
        emptyHintView = hint
        innerBox.addView(hint, FrameLayout.LayoutParams(-1, -1, Gravity.CENTER))

        if (!expanded) {
            val waitTag = TextView(context).apply {
                text = "待你处理"
                textSize = 9f
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
            attachExpandedScreenTouchAndDrag(context, img)

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
                setPadding(dp(context, 10), dp(context, 38), dp(context, 10), dp(context, 12))
            }
            appPickerContainer = pickerScroll
            appPickerList = pickerLinear
            pickerScroll.addView(pickerLinear, FrameLayout.LayoutParams(-1, -2))
            innerBox.addView(pickerScroll, FrameLayout.LayoutParams(-1, -1))

            val btnSize = dp(context, 26)
            // Top-Start: Minimize to corner pill (Vector EyeOff icon)
            val hidePillBtn = PipVectorIconButton(context, PipVectorIconButton.IconKind.EYE_OFF) {
                pickingApp = false
                expanded = false
                minimizedToPill = true
                transitionSizeAndRebuild(context, RootPhoneHarness.changes.value)
            }
            innerBox.addView(hidePillBtn, FrameLayout.LayoutParams(btnSize, btnSize, Gravity.TOP or Gravity.START).apply {
                leftMargin = dp(context, 6)
                topMargin = dp(context, 6)
            })

            // Top-End group: App launcher (Grid) + Shrink to mini-PiP + Close Virtual Display (Power/X)
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
            topRightRow.addView(appBtn, LinearLayout.LayoutParams(btnSize, btnSize).apply { rightMargin = dp(context, 5) })

            val shrinkBtn = PipVectorIconButton(context, PipVectorIconButton.IconKind.SHRINK) {
                pickingApp = false
                expanded = false
                transitionSizeAndRebuild(context, RootPhoneHarness.changes.value)
            }
            topRightRow.addView(shrinkBtn, LinearLayout.LayoutParams(btnSize, btnSize).apply { rightMargin = dp(context, 5) })

            val closeDisplayBtn = PipVectorIconButton(context, PipVectorIconButton.IconKind.POWER_CLOSE, isDanger = true) {
                pickingApp = false
                expanded = false
                scope.launch(Dispatchers.IO) {
                    runCatching { RootPhoneHarness.command(context, JSONObject().put("op", "release")) }
                }
            }
            topRightRow.addView(closeDisplayBtn, LinearLayout.LayoutParams(btnSize, btnSize))

            innerBox.addView(topRightRow, FrameLayout.LayoutParams(-2, -2, Gravity.TOP or Gravity.END).apply {
                rightMargin = dp(context, 6)
                topMargin = dp(context, 6)
            })

            // Bottom floating capsule: either "我处理好了，继续" when waiting_user, or subtle Back pill
            val bottomCapsule = TextView(context).apply {
                text = if (state.phase == "waiting_user") "我处理好了，继续" else "‹ 返回"
                textSize = if (state.phase == "waiting_user") 10f else 9.5f
                setTextColor(if (state.phase == "waiting_user") Color.BLACK else 0xE6FFFFFF.toInt())
                typeface = Typeface.DEFAULT_BOLD
                setPadding(dp(context, 10), dp(context, 3.5f), dp(context, 10), dp(context, 3.5f))
                background = GradientDrawable().apply {
                    setColor(if (state.phase == "waiting_user") 0xFFFF9F0A.toInt() else 0x99141418.toInt())
                    cornerRadius = dp(context, 12).toFloat()
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
                bottomMargin = dp(context, 6)
            })
        }

        lastBorderColor = borderColorFor(state)
        lastRenderedAppsKey = -1
        glassFrame.addView(innerBox, FrameLayout.LayoutParams(-1, -1))
        container.addView(glassFrame, FrameLayout.LayoutParams(-1, -1))
    }

    private fun transitionSizeAndRebuild(context: Context, state: RootPhoneState) {
        val container = rootView ?: return
        val lp = params ?: return
        val wm = windowManager ?: return
        val metrics = context.resources.displayMetrics
        val (targetW, targetH) = computeWindowSizePx(context, state, expanded, minimizedToPill)
        val (cornerX, cornerY) = PipSpringPhysics.cornerPosition(
            if (minimizedToPill) PipSpringPhysics.Corner.BottomEnd else snapCorner,
            metrics.widthPixels,
            metrics.heightPixels,
            targetW,
            targetH,
            dp(context, 12),
            dp(context, 56),
            dp(context, 92)
        )
        lp.width = targetW
        lp.height = targetH
        lp.x = cornerX
        lp.y = cornerY
        rebuildViewHierarchy(context, container, state)
        updateWindowContent(context, state)
        runCatching { wm.updateViewLayout(container, lp) }
    }

    private fun mainScreenAspect(context: Context): Float {
        val m = context.resources.displayMetrics
        val w = minOf(m.widthPixels, m.heightPixels)
        val h = maxOf(m.widthPixels, m.heightPixels)
        return if (w > 0 && h > 0) h.toFloat() / w.toFloat() else 2.15f
    }

    private fun computeWindowSizePx(context: Context, state: RootPhoneState, isExpanded: Boolean, isPill: Boolean): Pair<Int, Int> {
        if (isPill) {
            return dp(context, 124) to dp(context, 38)
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
        // Strict aspect-ratio preserving formula (modeled after Mika's AgentPip.kt):
        // Never clamps height independently of width so text is NEVER squished or stretched!
        val metrics = context.resources.displayMetrics
        val pad = dp(context, if (isExpanded) 4f else 3.5f)
        val availW = (metrics.widthPixels - dp(context, 24) - pad * 2).coerceAtLeast(dp(context, 80))
        val availH = (metrics.heightPixels - dp(context, 160) - pad * 2).coerceAtLeast(dp(context, 120))
        val isLandscape = aspect < 1f
        val baseInnerW = dp(
            context,
            when {
                isLandscape -> if (isExpanded) 310 else 176
                else -> if (isExpanded) 216 else 96
            }
        )
        val innerW = minOf(
            baseInnerW,
            availW,
            (availH / aspect).roundToInt().coerceAtLeast(dp(context, 60))
        ).coerceAtLeast(dp(context, 60))
        val innerH = (innerW * aspect).roundToInt().coerceAtLeast(dp(context, 60))
        return (innerW + pad * 2) to (innerH + pad * 2)
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
            outerBorderDrawable?.setStroke(dp(context, 1.2f), newBorderColor)
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
            val dispW = info.optInt("width", 1080).coerceAtLeast(1)
            val dispH = info.optInt("height", 2400).coerceAtLeast(1)
            val dotRadius = dp(context, 9)
            val px = (state.lastTouchX.toFloat() / dispW * imgView.width).roundToInt() - dotRadius
            val py = (state.lastTouchY.toFloat() / dispH * imgView.height).roundToInt() - dotRadius
            dot.translationX = px.toFloat()
            dot.translationY = py.toFloat()
            dot.visibility = View.VISIBLE
            dot.alpha = 1f
            dot.animate().alpha(0f).setDuration(550L).withEndAction { dot.visibility = View.GONE }.start()
        }

        // Decode frame if changed (with cache hit when unchanged)
        if (state.image.isNotBlank() && state.image != lastDecodedB64) {
            lastDecodedB64 = state.image
            val b64 = state.image
            scope.launch(Dispatchers.Default) {
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
            }
        } else if (state.image.isBlank()) {
            currentBitmap = null
            lastDecodedB64 = ""
            screenImageView?.setImageBitmap(null)
            emptyHintView?.visibility = View.VISIBLE
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
                        onTap?.invoke()
                    }
                    dragging = false
                    true
                }
                else -> false
            }
        }
    }

    @SuppressLint("ClickableViewAccessibility")
    private fun attachExpandedScreenTouchAndDrag(context: Context, imageView: ImageView) {
        val slop = ViewConfiguration.get(context).scaledTouchSlop
        val dragZonePx = dp(context, 38)
        var downX = 0f
        var downY = 0f
        var downRawX = 0f
        var downRawY = 0f
        var startWinX = 0
        var startWinY = 0
        var windowDragMode = false
        var draggingWindow = false
        var tracker: VelocityTracker? = null

        imageView.setOnTouchListener { v, event ->
            val lp = params ?: return@setOnTouchListener false
            val container = rootView ?: return@setOnTouchListener false
            val wm = windowManager ?: return@setOnTouchListener false
            val state = RootPhoneHarness.changes.value

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
                    if (windowDragMode) {
                        tracker?.recycle()
                        tracker = VelocityTracker.obtain().also { it.addMovement(event) }
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
                    if (event.actionMasked != MotionEvent.ACTION_UP || state.phase == "running") return@setOnTouchListener true

                    val upX = event.x
                    val upY = event.y
                    val vw = v.width.coerceAtLeast(1)
                    val vh = v.height.coerceAtLeast(1)
                    val info = runCatching { JSONObject(state.imageInfo.ifBlank { state.display }) }.getOrDefault(JSONObject())
                    val dispW = info.optInt("width", 1080).coerceAtLeast(1)
                    val dispH = info.optInt("height", 2400).coerceAtLeast(1)
                    val displayId = info.optInt("displayId", JSONObject(state.display).optInt("displayId", -1))
                    if (displayId <= 0) return@setOnTouchListener true

                    fun mapX(px: Float) = (px / vw * dispW).toInt().coerceIn(0, dispW - 1)
                    fun mapY(py: Float) = (py / vh * dispH).toInt().coerceIn(0, dispH - 1)

                    val isTap = abs(upX - downX) + abs(upY - downY) < slop
                    scope.launch(Dispatchers.IO) {
                        runCatching {
                            val cmd = JSONObject().put("display_id", displayId).put("manual", true)
                            if (info.has("rotation")) cmd.put("expected_rotation", info.optInt("rotation"))
                            if (isTap) {
                                cmd.put("op", "tap").put("x", mapX(upX)).put("y", mapY(upY))
                            } else {
                                cmd.put("op", "swipe")
                                    .put("x1", mapX(downX)).put("y1", mapY(downY))
                                    .put("x2", mapX(upX)).put("y2", mapY(upY))
                                    .put("duration_ms", 280)
                            }
                            RootPhoneHarness.command(context, cmd)
                        }
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
    val disp = remember(state.display) { runCatching { JSONObject(state.display) }.getOrDefault(JSONObject()) }
    val created = disp.optBoolean("created")

    val visible = (created || state.active) && !systemOverlayShowing
    var expanded by remember { mutableStateOf(false) }
    var hiddenToPill by remember { mutableStateOf(false) }
    LaunchedEffect(created, state.active) {
        if (!created && !state.active) {
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
                        .padding(horizontal = 13.dp, vertical = 8.dp)
                ) {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(7.dp)) {
                        Box(Modifier.size(7.dp).clip(CircleShape).background(borderColor))
                        Text(
                            if (state.phase == "waiting_user") "待你处理" else "虚拟屏",
                            color = androidx.compose.ui.graphics.Color(0xF2FFFFFF),
                            fontSize = 12.sp,
                            fontWeight = FontWeight.Medium
                        )
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

            val sizeCurve = remember { CubicBezierEasing(0.2f, 0.8f, 0.2f, 1f) }
            val padDp = if (expanded) 4.dp else 3.5.dp
            val availWDp = (maxWidth - 24.dp - padDp * 2).coerceAtLeast(80.dp)
            val availHDp = (maxHeight - 160.dp - padDp * 2).coerceAtLeast(120.dp)
            val baseInnerW = when {
                aspect < 1f -> if (expanded) 310.dp else 176.dp
                else -> if (expanded) 216.dp else 96.dp
            }
            val innerW = minOf(baseInnerW, availWDp, availHDp / aspect).coerceAtLeast(60.dp)
            val innerH = (innerW * aspect).coerceAtLeast(60.dp)
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
                                        var end = down.position
                                        var ended = false
                                        do {
                                            val ev = awaitPointerEvent()
                                            val ptr = ev.changes.firstOrNull { it.id == down.id } ?: break
                                            end = ptr.position
                                            ptr.consume()
                                            if (!ptr.pressed) {
                                                ended = true
                                                break
                                            }
                                        } while (true)
                                        if (ended) {
                                            val displayId = disp.optInt("displayId", -1)
                                            if (displayId > 0) {
                                                fun mx(v: Float) = (v / size.width * dispW).toInt().coerceIn(0, dispW - 1)
                                                fun my(v: Float) = (v / size.height * dispH).toInt().coerceIn(0, dispH - 1)
                                                val isTap = abs(end.x - down.position.x) + abs(end.y - down.position.y) < viewConfiguration.touchSlop
                                                scope.launch(Dispatchers.IO) {
                                                    runCatching {
                                                        val cmd = JSONObject().put("display_id", displayId).put("manual", true)
                                                        if (isTap) cmd.put("op", "tap").put("x", mx(end.x)).put("y", my(end.y))
                                                        else cmd.put("op", "swipe").put("x1", mx(down.position.x)).put("y1", my(down.position.y)).put("x2", mx(end.x)).put("y2", my(end.y))
                                                        RootPhoneHarness.command(context, cmd)
                                                    }
                                                }
                                            }
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
