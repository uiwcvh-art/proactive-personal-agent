package dev.bubuki.proactive

import android.animation.ValueAnimator
import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.Typeface
import android.util.Base64
import android.view.MotionEvent
import android.view.VelocityTracker
import android.view.View
import android.view.ViewConfiguration
import android.view.animation.LinearInterpolator
import android.widget.ImageView
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
import org.json.JSONObject
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.exp
import kotlin.math.hypot
import kotlin.math.roundToInt
/**
 * [Module] FloatingVirtualDisplayControls
 * [Responsibility] Custom-drawn vector icon views (`PipVectorIconButton`, `ResizeGripView`),
 *                  critical-damped spring physics (`PipSpringPhysics`), adaptive layout metrics
 *                  model (`PipLayoutMetrics`), overlay touch/drag/resize gesture handlers, and
 *                  the Compose in-app PiP widget (`InAppFloatingVirtualDisplay`).
 * [Public API]
 *   - PipSpringPhysics: project / cornerPosition / nearestCorner / stepCriticalSpring
 *   - PipVectorIconButton / ResizeGripView / PipLayoutMetrics
 *   - FloatingVirtualDisplay.attachCornerResizeTouch / attachSpringDragAndTap / attachExpandedScreenTouchAndDrag
 *   - FloatingVirtualDisplay.animateSpringToNearestCorner / borderColorFor / dotColorFor / mainScreenAspect
 *   - InAppFloatingVirtualDisplay()
 */
internal object PipSpringPhysics {
    private const val DECEL_FACTOR = 0.992f / (1000f * (1f - 0.992f)) // ~0.124s
    private const val OMEGA = (2.0 * PI / 0.4).toFloat() // response = 0.4s
    enum class Corner(val right: Boolean, val bottom: Boolean) {
        TopStart(false, false),
        TopEnd(true, false),
        BottomStart(false, true),
        BottomEnd(true, true), }
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
        return x to y }
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
        dx * dx + dy * dy }
    /** Analytical solution of critical-damped harmonic oscillator at time t (seconds). */
    fun stepCriticalSpring(start: Float, target: Float, initialVelocity: Float, tSec: Float): Float {
        val d0 = start - target
        val decay = exp((-OMEGA * tSec).toDouble()).toFloat()
        return target + (d0 + (initialVelocity + OMEGA * d0) * tSec) * decay }
}
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
        color = if (isDanger) 0xCC3B171B.toInt() else 0xA6141418.toInt() }
    private val strokePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = context.resources.displayMetrics.density * 0.8f
        color = if (isDanger) 0x66FF6961 else 0x38FFFFFF }
    private val iconPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
        strokeJoin = Paint.Join.ROUND
        strokeWidth = context.resources.displayMetrics.density * 1.5f
        color = if (isDanger) 0xFFFF7A72.toInt() else 0xF2FFFFFF.toInt() }
    private val textPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
        color = 0xF2FFFFFF.toInt()
        textAlign = Paint.Align.CENTER
        typeface = Typeface.DEFAULT_BOLD }
    private val rectF = RectF()
    init {
        isClickable = true
        setOnClickListener { onClick() } }
    fun updateBadge(text: String) {
        if (badgeText != text) {
            badgeText = text
            invalidate() }
    }
    fun setBadgeText(text: String) = updateBadge(text)
    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val w = width.toFloat()
        val h = height.toFloat()
        val minSide = minOf(w, h)
        if (minSide <= 2f) return
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
                canvas.drawLine(cx - 5.2f * u, cy + 4.5f * u, cx + 5.2f * u, cy - 4.5f * u, iconPaint) }
            IconKind.SIZE_TIER -> {
                textPaint.textSize = 10.2f * u
                val fm = textPaint.fontMetrics
                val baseline = cy - (fm.ascent + fm.descent) / 2f
                canvas.drawText(badgeText, cx, baseline, textPaint) }
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
                canvas.drawRoundRect(rectF, cr, cr, iconPaint) }
            IconKind.SHRINK -> {
                val arm = 3.2f * u
                canvas.drawLine(cx + 1.2f * u, cy - 1.2f * u, cx + 4.8f * u, cy - 4.8f * u, iconPaint)
                canvas.drawLine(cx + 1.2f * u, cy - 1.2f * u, cx + 1.2f * u + arm, cy - 1.2f * u, iconPaint)
                canvas.drawLine(cx + 1.2f * u, cy - 1.2f * u, cx + 1.2f * u, cy - 1.2f * u - arm, iconPaint)
                canvas.drawLine(cx - 1.2f * u, cy + 1.2f * u, cx - 4.8f * u, cy + 4.8f * u, iconPaint)
                canvas.drawLine(cx - 1.2f * u, cy + 1.2f * u, cx - 1.2f * u - arm, cy + 1.2f * u, iconPaint)
                canvas.drawLine(cx - 1.2f * u, cy + 1.2f * u, cx - 1.2f * u, cy + 1.2f * u + arm, iconPaint) }
            IconKind.POWER_CLOSE -> {
                val len = 3.8f * u
                canvas.drawLine(cx - len, cy - len, cx + len, cy + len, iconPaint)
                canvas.drawLine(cx + len, cy - len, cx - len, cy + len, iconPaint) }
        } }
}
internal class ResizeGripView(context: Context) : View(context) {
    private val gripPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
        color = 0x80FFFFFF.toInt() }
    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val w = width.toFloat()
        val h = height.toFloat()
        val u = minOf(w, h) / 22f
        gripPaint.strokeWidth = (1.4f * u).coerceAtLeast(1f)
        val pad = 4.2f * u
        canvas.drawLine(w - pad - 7.5f * u, h - pad, w - pad, h - pad - 7.5f * u, gripPaint)
        canvas.drawLine(w - pad - 3.8f * u, h - pad, w - pad, h - pad - 3.8f * u, gripPaint) }
}
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
    val sigma: Float,
    val useSideRail: Boolean = false,
    val sideRailWidthPx: Int = 0
)
internal fun FloatingVirtualDisplay.mainScreenAspect(context: Context): Float {
    val m = context.resources.displayMetrics
    val w = minOf(m.widthPixels, m.heightPixels)
    val h = maxOf(m.widthPixels, m.heightPixels)
    return if (w > 0 && h > 0) h.toFloat() / w.toFloat() else 2.15f
}
internal fun FloatingVirtualDisplay.borderColorFor(state: RootPhoneState): Int = when (state.phase) {
    "running" -> 0xB334C759.toInt()
    "waiting_user" -> 0xEBFF9F0A.toInt()
    else -> 0x38FFFFFF
}
internal fun FloatingVirtualDisplay.dotColorFor(state: RootPhoneState): Int = when (state.phase) {
    "running" -> 0xFF34C759.toInt()
    "waiting_user" -> 0xFFFF9F0A.toInt()
    else -> 0x99FFFFFF.toInt()
}
@SuppressLint("ClickableViewAccessibility")
internal fun FloatingVirtualDisplay.attachCornerResizeTouch(context: Context, gripView: View) {
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
                    resizing = false }
                MotionEvent.ACTION_MOVE -> {
                    val dx = event.rawX - downRawX
                    val dy = event.rawY - downRawY
                    if (!resizing && hypot(dx, dy) > slop) {
                        resizing = true }
                    if (resizing) {
                        val state = RootPhoneHarness.changes.value
                        val bmp = currentBitmap
                        val rawAspect = if (bmp != null && bmp.width > 0 && bmp.height > 0) {
                            bmp.height.toFloat() / bmp.width.toFloat()
                        } else {
                            mainScreenAspect(context) }
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
                            wm.updateViewLayout(container, lp) }
                    } }
                MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                    resizing = false }
            } }
        true }
}
@SuppressLint("ClickableViewAccessibility")
internal fun FloatingVirtualDisplay.attachSpringDragAndTap(context: Context, handleView: View, onTap: (() -> Unit)?) {
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
                true }
            MotionEvent.ACTION_MOVE -> {
                tracker?.addMovement(event)
                val dx = event.rawX - downRawX
                val dy = event.rawY - downRawY
                if (!dragging && hypot(dx, dy) > slop) {
                    dragging = true }
                if (dragging) {
                    val metrics = context.resources.displayMetrics
                    lp.x = (startWinX + dx.roundToInt()).coerceIn(
                        dp(context, 6),
                        (metrics.widthPixels - lp.width - dp(context, 6)).coerceAtLeast(dp(context, 6)))
                    lp.y = (startWinY + dy.roundToInt()).coerceIn(
                        dp(context, 36),
                        (metrics.heightPixels - lp.height - dp(context, 36)).coerceAtLeast(dp(context, 36)))
                    runCatching { wm.updateViewLayout(container, lp) } }
                true }
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
                    mainHandler.post { onTap?.invoke() } }
                dragging = false
                true }
            else -> false }
    }
}
@SuppressLint("ClickableViewAccessibility")
internal fun FloatingVirtualDisplay.attachExpandedScreenTouchAndDrag(
    context: Context,
    imageView: ImageView,
    dragZonePx: Int = dp(context, 38)
) {
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
        val dispW = info.optInt("width", 1080).coerceAtLeast(1)
        val dispH = info.optInt("height", 2400).coerceAtLeast(1)
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
                windowDragMode = dragZonePx > 0 && downY <= dragZonePx
                draggingWindow = false
                liveTouchStarted = false
                if (windowDragMode) {
                    tracker?.recycle()
                    tracker = VelocityTracker.obtain().also { it.addMovement(event) }
                } else {
                    val tx = mapX(downX)
                    val ty = mapY(downY)
                    liveTouchStarted = true
                    lastSentX = tx
                    lastSentY = ty
                    lastMoveSentAt = android.os.SystemClock.uptimeMillis()
                    RootPhoneHarness.sendLiveTouch(context, "down", tx, ty) }
                true }
            MotionEvent.ACTION_MOVE -> {
                if (windowDragMode) {
                    tracker?.addMovement(event)
                    val dx = event.rawX - downRawX
                    val dy = event.rawY - downRawY
                    if (!draggingWindow && hypot(dx, dy) > slop) {
                        draggingWindow = true }
                    if (draggingWindow) {
                        val metrics = context.resources.displayMetrics
                        lp.x = (startWinX + dx.roundToInt()).coerceIn(
                            dp(context, 6),
                            (metrics.widthPixels - lp.width - dp(context, 6)).coerceAtLeast(dp(context, 6)))
                        lp.y = (startWinY + dy.roundToInt()).coerceIn(
                            dp(context, 36),
                            (metrics.heightPixels - lp.height - dp(context, 36)).coerceAtLeast(dp(context, 36)))
                        runCatching { wm.updateViewLayout(container, lp) } }
                } else if (liveTouchStarted) {
                    val now = android.os.SystemClock.uptimeMillis()
                    val tx = mapX(event.x)
                    val ty = mapY(event.y)
                    if (now - lastMoveSentAt >= 10L || abs(tx - lastSentX) + abs(ty - lastSentY) >= 2) {
                        lastMoveSentAt = now
                        lastSentX = tx
                        lastSentY = ty
                        RootPhoneHarness.sendLiveTouch(context, "move", tx, ty) }
                }
                true }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                if (windowDragMode) {
                    tracker?.addMovement(event)
                    tracker?.computeCurrentVelocity(1000)
                    val vx = tracker?.xVelocity ?: 0f
                    val vy = tracker?.yVelocity ?: 0f
                    tracker?.recycle()
                    tracker = null
                    if (draggingWindow) {
                        animateSpringToNearestCorner(context, vx, vy) }
                    draggingWindow = false
                    return@setOnTouchListener true }
                if (liveTouchStarted) {
                    liveTouchStarted = false
                    val tx = mapX(event.x)
                    val ty = mapY(event.y)
                    val act = if (event.actionMasked == MotionEvent.ACTION_CANCEL) "cancel" else "up"
                    RootPhoneHarness.sendLiveTouch(context, act, tx, ty) }
                true }
            else -> true }
    }
}
internal fun FloatingVirtualDisplay.animateSpringToNearestCorner(context: Context, vx: Float, vy: Float) {
    val lp = params ?: return
    val container = rootView ?: return
    val wm = windowManager ?: return
    val metrics = context.resources.displayMetrics
    val hostLandscape = metrics.widthPixels > metrics.heightPixels
    val marginX = dp(context, 12)
    val topMargin = dp(context, if (hostLandscape) 14 else 56)
    val bottomMargin = dp(context, if (hostLandscape) 14 else 92)
    val projX = lp.x + PipSpringPhysics.project(vx)
    val projY = lp.y + PipSpringPhysics.project(vy)
    snapCorner = PipSpringPhysics.nearestCorner(
        projX, projY,
        metrics.widthPixels, metrics.heightPixels,
        lp.width, lp.height,
        marginX, topMargin, bottomMargin)
    val (targetX, targetY) = PipSpringPhysics.cornerPosition(
        snapCorner,
        metrics.widthPixels,
        metrics.heightPixels,
        lp.width, lp.height,
        marginX, topMargin, bottomMargin)
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
                runCatching { wm.updateViewLayout(container, lp) } }
        }
        start() }
}
@Composable
internal fun InAppFloatingVirtualDisplay() {
    val context = LocalContext.current
    if (FloatingVirtualDisplay.canOverlay(context)) return
    val state by RootPhoneHarness.changes.collectAsState()
    val systemOverlayShowing by FloatingVirtualDisplay.overlayVisible.collectAsState()
    val userDismissed by FloatingVirtualDisplay.userDismissedFlow.collectAsState()
    val disp = remember(state.display) { runCatching { JSONObject(state.display) }.getOrDefault(JSONObject()) }
    val created = disp.optBoolean("created")
    val visible = created && !userDismissed && state.phase != "stopping" && state.phase != "stopped" && !systemOverlayShowing
    if (!visible) return
    var expanded by remember { mutableStateOf(true) }
    var hiddenToPill by remember { mutableStateOf(false) }
    LaunchedEffect(created, state.active, state.phase, userDismissed) {
        if (!created || userDismissed || state.phase == "stopping" || state.phase == "stopped") {
            expanded = true
            hiddenToPill = false }
    }
    val bitmap by produceState<androidx.compose.ui.graphics.ImageBitmap?>(null, state.image) {
        value = withContext(Dispatchers.Default) {
            if (state.image.isBlank()) null
            else {
                FloatingVirtualDisplay.cachedBitmap()?.asImageBitmap() ?: runCatching {
                    val bytes = Base64.decode(state.image, Base64.DEFAULT)
                    BitmapFactory.decodeByteArray(bytes, 0, bytes.size)?.asImageBitmap()
                }.getOrNull() }
        } }
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
                else -> androidx.compose.ui.graphics.Color(0x38FFFFFF) }
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
                            fontWeight = FontWeight.Medium)
                        ComposePipCircleButton(
                            modifier = Modifier.size(20.dp),
                            isDanger = true,
                            onClick = {
                                hiddenToPill = false
                                expanded = false
                                FloatingVirtualDisplay.hide()
                                scope.launch(Dispatchers.IO) {
                                    runCatching { RootPhoneHarness.command(context, JSONObject().put("op", "release").put("user_close", true).put("destroy", true)) } }
                            }
                        ) {
                            val strokeW = 1.4.dp.toPx()
                            val c = androidx.compose.ui.graphics.Color(0xFFFF7A72)
                            drawLine(c, Offset(size.width * 0.32f, size.height * 0.32f), Offset(size.width * 0.68f, size.height * 0.68f), strokeW, StrokeCap.Round)
                            drawLine(c, Offset(size.width * 0.68f, size.height * 0.32f), Offset(size.width * 0.32f, size.height * 0.68f), strokeW, StrokeCap.Round) }
                    } }
                return@BoxWithConstraints }
            val info = remember(state.imageInfo, state.display) {
                runCatching { JSONObject(state.imageInfo.ifBlank { state.display }) }.getOrDefault(JSONObject()) }
            val dispW = info.optInt("width", 1080).coerceAtLeast(1)
            val dispH = info.optInt("height", 2400).coerceAtLeast(1)
            val bmp = bitmap
            val aspect = if (bmp != null && bmp.width > 0 && bmp.height > 0) {
                bmp.height.toFloat() / bmp.width.toFloat()
            } else {
                dispH.toFloat() / dispW.toFloat() }
            val tierState by remember { mutableStateOf(FloatingVirtualDisplay.currentTier()) }
            val sizeCurve = remember { CubicBezierEasing(0.2f, 0.8f, 0.2f, 1f) }
            val padDp = if (expanded) 4.dp else 3.5.dp
            val availWDp = (maxWidth - 24.dp - padDp * 2).coerceAtLeast(80.dp)
            val availHDp = (maxHeight - 160.dp - padDp * 2).coerceAtLeast(120.dp)
            val shortSideDp = minOf(maxWidth, maxHeight)
            val frac = when {
                !expanded -> if (aspect < 1f) 0.44f else 0.27f
                aspect < 1f -> tierState.landscapeFrac
                else -> tierState.portraitFrac }
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
                            (anchorY + dragOffset.y.roundToInt()).coerceIn(topSafePx, (maxH - hPx - bottomSafePx).coerceAtLeast(topSafePx))) }
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
                                    dragged = true }
                                if (dragged) {
                                    change.consume()
                                    dragOffset += delta }
                            }
                            if (dragged) {
                                val v = velocityTracker.calculateVelocity()
                                val curX = anchorX + dragOffset.x
                                val curY = anchorY + dragOffset.y
                                val targetCorner = PipSpringPhysics.nearestCorner(
                                    curX + PipSpringPhysics.project(v.x),
                                    curY + PipSpringPhysics.project(v.y),
                                    maxW, maxH, wPx, hPx, marginPx, topSafePx, bottomSafePx)
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
                                            break }
                                        val nx = PipSpringPhysics.stepCriticalSpring(startDx, 0f, v.x, tSec)
                                        val ny = PipSpringPhysics.stepCriticalSpring(startDy, 0f, v.y, tSec)
                                        dragOffset = Offset(nx, ny) }
                                }
                            } else {
                                expanded = true }
                        } }
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
                                    if (!expanded) return@pointerInput
                                    awaitEachGesture {
                                        val down = awaitFirstDown(requireUnconsumed = false)
                                        fun mx(v: Float) = (v / size.width.coerceAtLeast(1) * dispW).toInt().coerceIn(0, dispW - 1)
                                        fun my(v: Float) = (v / size.height.coerceAtLeast(1) * dispH).toInt().coerceIn(0, dispH - 1)
                                        RootPhoneHarness.sendLiveTouch(context, "down", mx(down.position.x), my(down.position.y))
                                        var end = down.position
                                        var lastMoveAt = android.os.SystemClock.uptimeMillis()
                                        var lastX = mx(down.position.x)
                                        var lastY = my(down.position.y)
                                        do {
                                            val ev = awaitPointerEvent()
                                            val ptr = ev.changes.firstOrNull { it.id == down.id } ?: break
                                            end = ptr.position
                                            ptr.consume()
                                            if (ptr.pressed) {
                                                val now = android.os.SystemClock.uptimeMillis()
                                                val tx = mx(end.x)
                                                val ty = my(end.y)
                                                if (now - lastMoveAt >= 10L || abs(tx - lastX) + abs(ty - lastY) >= 2) {
                                                    lastMoveAt = now
                                                    lastX = tx
                                                    lastY = ty
                                                    RootPhoneHarness.sendLiveTouch(context, "move", tx, ty) }
                                            }
                                            if (!ptr.pressed) {
                                                break }
                                        } while (true)
                                        RootPhoneHarness.sendLiveTouch(context, "up", mx(end.x), my(end.y))
                                    }
                                },
                            contentScale = ContentScale.Fit)
                    } else {
                        Text(
                            if (expanded) "点此打开工作台启动应用" else "虚拟屏",
                            color = androidx.compose.ui.graphics.Color(0x99FFFFFF),
                            fontSize = if (expanded) 10.sp else 9.sp,
                            modifier = Modifier
                                .align(Alignment.Center)
                                .clickable(enabled = expanded) {
                                    context.startActivity(Intent(context, NativePhoneActivity::class.java)) }
                                .padding(8.dp)) }
                    if (!expanded) {
                        ComposePipCircleButton(
                            modifier = Modifier.align(Alignment.TopEnd).padding(4.dp).size(20.dp),
                            isDanger = true,
                            onClick = {
                                expanded = false
                                FloatingVirtualDisplay.hide()
                                scope.launch(Dispatchers.IO) {
                                    runCatching { RootPhoneHarness.command(context, JSONObject().put("op", "release").put("user_close", true).put("destroy", true)) } }
                            }
                        ) {
                            val strokeW = 1.4.dp.toPx()
                            val c = androidx.compose.ui.graphics.Color(0xFFFF7A72)
                            drawLine(c, Offset(size.width * 0.32f, size.height * 0.32f), Offset(size.width * 0.68f, size.height * 0.68f), strokeW, StrokeCap.Round)
                            drawLine(c, Offset(size.width * 0.68f, size.height * 0.32f), Offset(size.width * 0.32f, size.height * 0.68f), strokeW, StrokeCap.Round) }
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
                            Text("待你处理", color = androidx.compose.ui.graphics.Color.Black, fontSize = 9.sp, fontWeight = FontWeight.Bold) }
                    }
                    if (expanded) {
                        ComposePipCircleButton(
                            modifier = Modifier.align(Alignment.TopStart).padding(6.dp),
                            onClick = { expanded = false; hiddenToPill = true }
                        ) {
                            val strokeW = 1.5.dp.toPx()
                            drawOval(
                                color = androidx.compose.ui.graphics.Color(0xF2FFFFFF),
                                topLeft = Offset(size.width * 0.24f, size.height * 0.34f),
                                size = Size(size.width * 0.52f, size.height * 0.32f),
                                style = Stroke(width = strokeW))
                            drawLine(
                                color = androidx.compose.ui.graphics.Color(0xF2FFFFFF),
                                start = Offset(size.width * 0.26f, size.height * 0.72f),
                                end = Offset(size.width * 0.74f, size.height * 0.28f),
                                strokeWidth = strokeW,
                                cap = StrokeCap.Round) }
                        Row(
                            Modifier.align(Alignment.TopEnd).padding(6.dp),
                            horizontalArrangement = Arrangement.spacedBy(5.dp)
                        ) {
                            ComposePipCircleButton(onClick = { expanded = false }) {
                                val strokeW = 1.5.dp.toPx()
                                val c = androidx.compose.ui.graphics.Color(0xF2FFFFFF)
                                drawLine(c, Offset(size.width * 0.55f, size.height * 0.45f), Offset(size.width * 0.74f, size.height * 0.26f), strokeW, StrokeCap.Round)
                                drawLine(c, Offset(size.width * 0.45f, size.height * 0.55f), Offset(size.width * 0.26f, size.height * 0.74f), strokeW, StrokeCap.Round) }
                            ComposePipCircleButton(
                                isDanger = true,
                                onClick = {
                                    expanded = false
                                    FloatingVirtualDisplay.hide()
                                    scope.launch(Dispatchers.IO) {
                                        runCatching { RootPhoneHarness.command(context, JSONObject().put("op", "release").put("user_close", true).put("destroy", true)) } }
                                }
                            ) {
                                val strokeW = 1.5.dp.toPx()
                                val c = androidx.compose.ui.graphics.Color(0xFFFF7A72)
                                drawLine(c, Offset(size.width * 0.32f, size.height * 0.32f), Offset(size.width * 0.68f, size.height * 0.68f), strokeW, StrokeCap.Round)
                                drawLine(c, Offset(size.width * 0.68f, size.height * 0.32f), Offset(size.width * 0.32f, size.height * 0.68f), strokeW, StrokeCap.Round) }
                        }
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
                                                RootPhoneHarness.command(context, JSONObject().put("op", "key").put("key", "back").put("manual", true)) }
                                        } }
                                }
                                .padding(horizontal = 10.dp, vertical = 4.dp)
                        ) {
                            Text(
                                if (state.phase == "waiting_user") "我处理好了，继续" else "‹ 返回",
                                color = if (state.phase == "waiting_user") androidx.compose.ui.graphics.Color.Black else androidx.compose.ui.graphics.Color(0xE6FFFFFF),
                                fontSize = 10.sp,
                                fontWeight = FontWeight.Bold) }
                    } }
            } }
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
                CircleShape)
            .clickable(onClick = onClick),
        contentAlignment = Alignment.Center
    ) {
        ComposeCanvas(Modifier.fillMaxSize(), onDraw = drawIcon) }
}
