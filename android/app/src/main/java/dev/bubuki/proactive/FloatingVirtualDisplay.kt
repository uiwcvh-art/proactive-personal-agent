package dev.bubuki.proactive

import android.animation.ValueAnimator
import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.LayerDrawable
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.util.Base64
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import org.json.JSONObject
import kotlin.math.abs
import kotlin.math.pow
import kotlin.math.roundToInt

/**
 * [Module] FloatingVirtualDisplay
 * [Responsibility] System-wide WindowManager overlay lifecycle (`TYPE_APPLICATION_OVERLAY`),
 *                  three-tier adaptive layout calculation (`computeAdaptiveLayout`),
 *                  view hierarchy construction (`rebuildViewHierarchy`), and live bitmap
 *                  frame decoding (`updateWindowContent`).
 *                  Delegates vector icon controls, spring physics, touch/drag gesture handlers,
 *                  and the in-app Compose PiP fallback to FloatingVirtualDisplayControls.kt.
 * [Public API]
 *   - FloatingVirtualDisplay.overlayVisible / userDismissedFlow
 *   - canOverlay(context) / requestOverlayPermission(context)
 *   - show(context, forceExpand) / hide() / minimizeToSidePill(context)
 *   - cycleSizeTier(context) / currentTier() / isPreviewVisible()
 *   - onWorkspaceStateChanged(context, state) / computeAdaptiveLayout(...)
 */
object FloatingVirtualDisplay {
    enum class SizeTier(val rpcName: String, val badge: String, val portraitFrac: Float, val landscapeFrac: Float) {
        COMPACT("compact", "S", 0.44f, 0.54f),
        MEDIUM("balanced", "M", 0.58f, 0.70f),
        LARGE("large", "L", 0.76f, 0.86f);
        fun next(): SizeTier = when (this) {
            COMPACT -> MEDIUM
            MEDIUM -> LARGE
            LARGE -> COMPACT }
    }
    internal val mainHandler = Handler(Looper.getMainLooper())
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private var watchJob: Job? = null
    internal var windowManager: WindowManager? = null
    internal var rootView: FrameLayout? = null
    internal var params: WindowManager.LayoutParams? = null
    internal var expanded = false
    internal var minimizedToPill = false
    private var pickingApp = false
    private var userDismissed = false
    private var sizeTier = SizeTier.MEDIUM
    internal var customWidthFraction: Float? = null
    internal var snapCorner = PipSpringPhysics.Corner.TopEnd
    internal var springAnimator: ValueAnimator? = null
    private var screenImageView: ImageView? = null
    private var touchIndicatorView: View? = null
    private var emptyHintView: TextView? = null
    private var waitingCapsuleView: TextView? = null
    private var appDrawerController: VirtualDisplayAppDrawer.DrawerController? = null
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
            transitionSizeAndRebuild(context.applicationContext, RootPhoneHarness.changes.value) }
        scope.launch(Dispatchers.IO) {
            runCatching {
                RootPhoneHarness.command(
                    context.applicationContext,
                    JSONObject().put("op", "resize").put("tier", sizeTier.rpcName).put("manual", true)) }
        } }
    // Frame decoding cache
    @Volatile private var lastDecodedB64: String = ""
    @Volatile internal var currentBitmap: Bitmap? = null
    @Volatile private var isDecodingFrame: Boolean = false
    private val _overlayVisible = MutableStateFlow(false)
    val overlayVisible: StateFlow<Boolean> = _overlayVisible.asStateFlow()
    private val _userDismissed = MutableStateFlow(false)
    val userDismissedFlow: StateFlow<Boolean> = _userDismissed.asStateFlow()
    internal fun cachedBitmap(): Bitmap? = currentBitmap
    internal fun dp(context: Context, value: Float): Int =
        (value * context.resources.displayMetrics.density).roundToInt()
    internal fun dp(context: Context, value: Int): Int =
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
            Toast.makeText(context, "请开启「显示在其他应用上层」，虚拟屏小窗即可悬浮在桌面上", Toast.LENGTH_LONG).show() }
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
                mountOrUpdateWindow(appCtx, RootPhoneHarness.changes.value) }
        } }
    @JvmStatic
    fun hide() {
        userDismissed = true
        _userDismissed.value = true
        pickingApp = false
        expanded = false
        minimizedToPill = false
        mainHandler.post { removeWindow() } }
    @JvmStatic
    fun minimizeToSidePill(context: Context) {
        val appCtx = context.applicationContext
        mainHandler.post {
            if (rootView == null) return@post
            pickingApp = false
            expanded = false
            minimizedToPill = true
            if (!userDismissed && canOverlay(appCtx) && rootView != null) {
                transitionSizeAndRebuild(appCtx, RootPhoneHarness.changes.value) }
        } }
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
                return@post }
            if (!userDismissed && canOverlay(appCtx)) {
                mountOrUpdateWindow(appCtx, state) }
        } }
    private fun ensureWatching(context: Context) {
        if (watchJob?.isActive == true) return
        watchJob = scope.launch {
            RootPhoneHarness.changes.collect { state ->
                val created = runCatching { JSONObject(state.display).optBoolean("created") }.getOrDefault(false)
                if (!created || state.phase == "stopping" || state.phase == "stopped") {
                    removeWindow()
                } else if (!userDismissed && canOverlay(context)) {
                    mountOrUpdateWindow(context, state) }
            } }
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
        appDrawerController = null
        outerBorderDrawable = null
        _overlayVisible.value = false
        if (view != null && wm != null) {
            runCatching { wm.removeView(view) } }
    }
    private fun mountOrUpdateWindow(context: Context, state: RootPhoneState) {
        if (!canOverlay(context)) {
            removeWindow()
            return }
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
                dp(context, 92))
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
                y = initY }
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
                return }
        }
        updateWindowContent(context, state) }
    @SuppressLint("ClickableViewAccessibility")
    private fun rebuildViewHierarchy(context: Context, container: FrameLayout, state: RootPhoneState) {
        container.removeAllViews()
        screenImageView = null
        touchIndicatorView = null
        emptyHintView = null
        waitingCapsuleView = null
        appDrawerController = null
        sizeTierButton = null
        val layout = computeAdaptiveLayout(context, state, expanded, minimizedToPill)
        if (minimizedToPill) {
            val pill = LinearLayout(context).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                setPadding(dp(context, 13), dp(context, 8), dp(context, 10), dp(context, 8))
                background = GradientDrawable().apply {
                    setColor(0xE618181C.toInt())
                    cornerRadius = dp(context, 22).toFloat()
                    setStroke(dp(context, 1), borderColorFor(state)) }
                elevation = dp(context, 12).toFloat() }
            val dot = View(context).apply {
                background = GradientDrawable().apply {
                    shape = GradientDrawable.OVAL
                    setColor(dotColorFor(state)) }
            }
            pill.addView(dot, LinearLayout.LayoutParams(dp(context, 7), dp(context, 7)).apply { rightMargin = dp(context, 7) })
            val label = TextView(context).apply {
                text = if (state.phase == "waiting_user") "待你处理" else "虚拟屏"
                textSize = 12f
                setTextColor(0xF2FFFFFF.toInt())
                typeface = Typeface.DEFAULT_BOLD }
            pill.addView(label, LinearLayout.LayoutParams(-2, -2).apply { rightMargin = dp(context, 8) })
            val closeMiniBtn = PipVectorIconButton(context, PipVectorIconButton.IconKind.POWER_CLOSE, isDanger = true) {
                hide()
                scope.launch(Dispatchers.IO) {
                    runCatching { RootPhoneHarness.command(context, JSONObject().put("op", "release")) } }
            }
            pill.addView(closeMiniBtn, LinearLayout.LayoutParams(dp(context, 20), dp(context, 20)))
            attachSpringDragAndTap(context, pill, onTap = {
                minimizedToPill = false
                transitionSizeAndRebuild(context, RootPhoneHarness.changes.value)
                scope.launch(Dispatchers.IO) {
                    runCatching { RootPhoneHarness.command(context, JSONObject().put("op", "create").put("manual", true)) } }
            })
            container.addView(pill, FrameLayout.LayoutParams(-1, -1))
            return }
        val pad = layout.framePadPx
        val outerRadius = layout.outerRadiusPx
        val innerRadius = layout.innerRadiusPx
        val glassFrame = FrameLayout(context).apply {
            setPadding(pad, pad, pad, pad)
            val bg = GradientDrawable().apply {
                setColor(0xD918181C.toInt())
                cornerRadius = outerRadius
                setStroke(dp(context, 0.7f), borderColorFor(state)) }
            outerBorderDrawable = bg
            background = bg
            elevation = dp(context, if (expanded) 18 else 12).toFloat() }
        val innerBox = FrameLayout(context).apply {
            val innerBg = GradientDrawable().apply {
                setColor(0xFF0E0E12.toInt())
                cornerRadius = innerRadius }
            val innerRim = GradientDrawable().apply {
                setColor(Color.TRANSPARENT)
                cornerRadius = innerRadius
                setStroke(1, 0x22FFFFFF) }
            background = LayerDrawable(arrayOf(innerBg, innerRim))
            clipToOutline = true
            outlineProvider = object : android.view.ViewOutlineProvider() {
                override fun getOutline(view: View, outline: android.graphics.Outline) {
                    outline.setRoundRect(0, 0, view.width, view.height, innerRadius) }
            } }
        val img = ImageView(context).apply {
            scaleType = ImageView.ScaleType.FIT_CENTER }
        screenImageView = img
        innerBox.addView(img, FrameLayout.LayoutParams(-1, -1))
        val dotSize = layout.touchDotSizePx
        val coreSize = (dotSize / 4).coerceAtLeast(dp(context, 3))
        val touchDot = FrameLayout(context).apply {
            visibility = View.GONE
            background = GradientDrawable().apply {
                shape = GradientDrawable.OVAL
                setColor(0x18FFFFFF)
                setStroke(dp(context, 0.75f).coerceAtLeast(1), 0xCCFFFFFF.toInt()) }
            val centerCore = View(context).apply {
                background = GradientDrawable().apply {
                    shape = GradientDrawable.OVAL
                    setColor(0xE6FFFFFF.toInt()) }
            }
            addView(centerCore, FrameLayout.LayoutParams(coreSize, coreSize, Gravity.CENTER)) }
        touchIndicatorView = touchDot
        innerBox.addView(touchDot, FrameLayout.LayoutParams(dotSize, dotSize))
        val hint = TextView(context).apply {
            text = if (expanded) "点右上角网格图标\n选择应用启动" else "虚拟屏"
            textSize = layout.hintSp
            setTextColor(0x99FFFFFF.toInt())
            gravity = Gravity.CENTER
            setPadding(dp(context, 8), dp(context, 8), dp(context, 8), dp(context, 8)) }
        emptyHintView = hint
        innerBox.addView(hint, FrameLayout.LayoutParams(-1, -1, Gravity.CENTER))
        if (!expanded) {
            val miniCloseSize = dp(context, 20)
            val miniCloseBtn = PipVectorIconButton(context, PipVectorIconButton.IconKind.POWER_CLOSE, isDanger = true) {
                hide()
                scope.launch(Dispatchers.IO) {
                    runCatching { RootPhoneHarness.command(context, JSONObject().put("op", "release")) } }
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
                    cornerRadius = dp(context, 8).toFloat() }
                visibility = if (state.phase == "waiting_user") View.VISIBLE else View.GONE }
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
            val drawerCtrl = VirtualDisplayAppDrawer.createDrawerView(
                context = context,
                dp = { v -> dp(context, v) },
                topInsetPx = (layout.btnSizePx + layout.btnMarginPx * 2 + dp(context, 4)),
                cornerRadiusPx = innerRadius,
                onLaunchApp = { component ->
                    pickingApp = false
                    lastRenderedAppsKey = -1
                    scope.launch(Dispatchers.IO) {
                        runCatching {
                            RootPhoneHarness.command(
                                context,
                                JSONObject().put("op", "launch").put("component", component).put("manual", true)) }
                    }
                    updateWindowContent(context, RootPhoneHarness.changes.value)
                },
                onClose = {
                    pickingApp = false
                    lastRenderedAppsKey = -1
                    updateWindowContent(context, RootPhoneHarness.changes.value)
                })
            drawerCtrl.rootView.visibility = if (pickingApp) View.VISIBLE else View.GONE
            appDrawerController = drawerCtrl
            innerBox.addView(drawerCtrl.rootView, FrameLayout.LayoutParams(-1, -1))
            val btnSize = layout.btnSizePx
            val btnGap = (layout.btnMarginPx - dp(context, 1)).coerceAtLeast(dp(context, 4))
            val hidePillBtn = PipVectorIconButton(context, PipVectorIconButton.IconKind.EYE_OFF) {
                pickingApp = false
                expanded = false
                minimizedToPill = true
                transitionSizeAndRebuild(context, RootPhoneHarness.changes.value) }
            innerBox.addView(hidePillBtn, FrameLayout.LayoutParams(btnSize, btnSize, Gravity.TOP or Gravity.START).apply {
                leftMargin = layout.btnMarginPx
                topMargin = layout.btnMarginPx
            })
            val topRightRow = LinearLayout(context).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL }
            val appBtn = PipVectorIconButton(context, PipVectorIconButton.IconKind.APPS_GRID) {
                pickingApp = !pickingApp
                lastRenderedAppsKey = -1
                if (pickingApp) {
                    scope.launch(Dispatchers.IO) {
                        runCatching { RootPhoneHarness.listLaunchableApps(context) } }
                }
                updateWindowContent(context, RootPhoneHarness.changes.value) }
            topRightRow.addView(appBtn, LinearLayout.LayoutParams(btnSize, btnSize).apply { rightMargin = btnGap })
            val tierBtn = PipVectorIconButton(
                context,
                PipVectorIconButton.IconKind.SIZE_TIER,
                badgeText = sizeTier.badge
            ) {
                cycleSizeTier(context) }
            sizeTierButton = tierBtn
            topRightRow.addView(tierBtn, LinearLayout.LayoutParams(btnSize, btnSize).apply { rightMargin = btnGap })
            val shrinkBtn = PipVectorIconButton(context, PipVectorIconButton.IconKind.SHRINK) {
                pickingApp = false
                expanded = false
                transitionSizeAndRebuild(context, RootPhoneHarness.changes.value) }
            topRightRow.addView(shrinkBtn, LinearLayout.LayoutParams(btnSize, btnSize).apply { rightMargin = btnGap })
            val closeDisplayBtn = PipVectorIconButton(context, PipVectorIconButton.IconKind.POWER_CLOSE, isDanger = true) {
                hide()
                scope.launch(Dispatchers.IO) {
                    runCatching { RootPhoneHarness.command(context, JSONObject().put("op", "release")) } }
            }
            topRightRow.addView(closeDisplayBtn, LinearLayout.LayoutParams(btnSize, btnSize))
            innerBox.addView(topRightRow, FrameLayout.LayoutParams(-2, -2, Gravity.TOP or Gravity.END).apply {
                rightMargin = layout.btnMarginPx
                topMargin = layout.btnMarginPx
            })
            val bottomRow = LinearLayout(context).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL }
            val bottomCapsule = TextView(context).apply {
                text = if (state.phase == "waiting_user") "我处理好了，继续" else "‹ 返回"
                textSize = if (state.phase == "waiting_user") (layout.capsuleSp + 0.5f) else layout.capsuleSp
                setTextColor(if (state.phase == "waiting_user") Color.BLACK else 0xE6FFFFFF.toInt())
                typeface = Typeface.DEFAULT_BOLD
                setPadding(layout.capsulePadHPx, layout.capsulePadVPx, layout.capsulePadHPx, layout.capsulePadVPx)
                background = GradientDrawable().apply {
                    setColor(if (state.phase == "waiting_user") 0xFFFF9F0A.toInt() else 0x99141418.toInt())
                    cornerRadius = dp(context, 12 * layout.sigma).toFloat()
                    setStroke(1, 0x2EFFFFFF) }
                setOnClickListener {
                    val cur = RootPhoneHarness.changes.value
                    if (cur.phase == "waiting_user") {
                        expanded = false
                        transitionSizeAndRebuild(context, cur)
                        runCatching { RootPhoneHarness.resume(context) }
                    } else if (pickingApp) {
                        pickingApp = false
                        lastRenderedAppsKey = -1
                        updateWindowContent(context, cur)
                    } else {
                        scope.launch(Dispatchers.IO) {
                            runCatching {
                                RootPhoneHarness.command(context, JSONObject().put("op", "key").put("key", "back").put("manual", true)) }
                        } }
                } }
            waitingCapsuleView = bottomCapsule
            bottomRow.addView(bottomCapsule, LinearLayout.LayoutParams(-2, -2).apply { rightMargin = dp(context, 6) })
            val homeCapsule = TextView(context).apply {
                text = "⌂ 桌面"
                textSize = layout.capsuleSp
                setTextColor(0xE6FFFFFF.toInt())
                typeface = Typeface.DEFAULT_BOLD
                setPadding(layout.capsulePadHPx, layout.capsulePadVPx, layout.capsulePadHPx, layout.capsulePadVPx)
                background = GradientDrawable().apply {
                    setColor(0x99141418.toInt())
                    cornerRadius = dp(context, 12 * layout.sigma).toFloat()
                    setStroke(1, 0x2EFFFFFF) }
                setOnClickListener {
                    pickingApp = false
                    lastRenderedAppsKey = -1
                    updateWindowContent(context, RootPhoneHarness.changes.value)
                    scope.launch(Dispatchers.IO) {
                        runCatching {
                            RootPhoneHarness.command(context, JSONObject().put("op", "key").put("key", "home").put("manual", true))
                        }
                    }
                }
            }
            bottomRow.addView(homeCapsule, LinearLayout.LayoutParams(-2, -2))
            innerBox.addView(bottomRow, FrameLayout.LayoutParams(-2, -2, Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL).apply {
                bottomMargin = layout.btnMarginPx
            })
            val resizeGrip = ResizeGripView(context)
            val gripSize = dp(context, 24 * layout.sigma).coerceIn(dp(context, 20), dp(context, 32))
            attachCornerResizeTouch(context, resizeGrip)
            innerBox.addView(resizeGrip, FrameLayout.LayoutParams(gripSize, gripSize, Gravity.BOTTOM or Gravity.END)) }
        lastBorderColor = borderColorFor(state)
        lastRenderedAppsKey = -1
        glassFrame.addView(innerBox, FrameLayout.LayoutParams(-1, -1))
        container.addView(glassFrame, FrameLayout.LayoutParams(-1, -1)) }
    internal fun transitionSizeAndRebuild(context: Context, state: RootPhoneState) {
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
                    dp(appCtx, 92))
                lp.width = targetW
                lp.height = targetH
                lp.x = cornerX
                lp.y = cornerY
                rebuildViewHierarchy(appCtx, container, state)
                updateWindowContent(appCtx, state)
                wm.updateViewLayout(container, lp) }
        } }
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
                sigma = 0.8f) }
        val bmp = currentBitmap
        val aspect = if (bmp != null && bmp.width > 0 && bmp.height > 0) {
            bmp.height.toFloat() / bmp.width.toFloat()
        } else {
            val info = runCatching { JSONObject(state.imageInfo.ifBlank { state.display }) }.getOrDefault(JSONObject())
            val vw = info.optInt("width", 0)
            val vh = info.optInt("height", 0)
            if (vw > 0 && vh > 0) vh.toFloat() / vw.toFloat() else mainScreenAspect(context) }
        val metrics = context.resources.displayMetrics
        val shortSidePx = minOf(metrics.widthPixels, metrics.heightPixels).coerceAtLeast(dp(context, 280))
        val isLandscape = aspect < 1f
        val frac = when {
            !isExpanded -> if (isLandscape) 0.44f else 0.27f
            customWidthFraction != null -> customWidthFraction!!
            else -> if (isLandscape) sizeTier.landscapeFrac else sizeTier.portraitFrac }
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
            sigma = sigma) }
    internal fun computeWindowSizePx(context: Context, state: RootPhoneState, isExpanded: Boolean, isPill: Boolean): Pair<Int, Int> {
        val m = computeAdaptiveLayout(context, state, isExpanded, isPill)
        return m.winW to m.winH }
    internal fun updateWindowContent(context: Context, state: RootPhoneState) {
        val container = rootView ?: return
        val lp = params ?: return
        val wm = windowManager ?: return
        val newBorderColor = borderColorFor(state)
        if (newBorderColor != lastBorderColor) {
            lastBorderColor = newBorderColor
            outerBorderDrawable?.setStroke(dp(context, 0.7f), newBorderColor) }
        if (!minimizedToPill) {
            if (!expanded) {
                waitingCapsuleView?.visibility = if (state.phase == "waiting_user") View.VISIBLE else View.GONE
            } else {
                val capText = if (state.phase == "waiting_user") "我处理好了，继续" else "‹ 返回"
                if (waitingCapsuleView?.text != capText) {
                    waitingCapsuleView?.text = capText }
            } }
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
            dot.animate().alpha(0f).scaleX(1.18f).scaleY(1.18f).setDuration(360L).withEndAction { dot.visibility = View.GONE }.start() }
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
                                if (rootView === container) runCatching { wm.updateViewLayout(container, lp) } }
                        } }
                } finally {
                    isDecodingFrame = false }
            }
        } else if (state.image.isBlank()) {
            if (currentBitmap == null) {
                emptyHintView?.visibility = View.VISIBLE }
        } else if (currentBitmap != null && screenImageView?.drawable == null) {
            screenImageView?.setImageBitmap(currentBitmap)
            emptyHintView?.visibility = View.GONE }
        val drawer = appDrawerController
        drawer?.rootView?.visibility = if (expanded && pickingApp) View.VISIBLE else View.GONE
        val needFocusable = expanded && pickingApp
        val curNotFocusable = (lp.flags and WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE) != 0
        if (needFocusable == curNotFocusable) {
            lp.flags = if (needFocusable) {
                lp.flags and WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE.inv()
            } else {
                lp.flags or WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE }
            runCatching { wm.updateViewLayout(container, lp) } }
        val appsKey = if (expanded && pickingApp) (state.apps.hashCode() * 31 + (state.currentPackage?.hashCode() ?: 0)) else -1
        if (expanded && pickingApp && drawer != null && appsKey != lastRenderedAppsKey) {
            lastRenderedAppsKey = appsKey
            if (state.apps.isEmpty()) {
                scope.launch(Dispatchers.IO) {
                    runCatching { RootPhoneHarness.listLaunchableApps(context) } }
            }
            drawer.updateApps(state.apps, state.currentPackage) }
        val (targetW, targetH) = computeWindowSizePx(context, state, expanded, minimizedToPill)
        if (abs(lp.width - targetW) > 1 || abs(lp.height - targetH) > 1) {
            lp.width = targetW
            lp.height = targetH
            runCatching { wm.updateViewLayout(container, lp) } }
    }
}
