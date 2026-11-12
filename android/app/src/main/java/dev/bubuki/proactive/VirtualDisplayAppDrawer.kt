package dev.bubuki.proactive

import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.Drawable
import android.graphics.drawable.GradientDrawable
import android.text.Editable
import android.text.TextUtils
import android.text.TextWatcher
import android.util.LruCache
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import java.util.concurrent.Executors

internal typealias VirtualAppEntry = PhoneApp

internal object VirtualDisplayAppDrawer {
    private val iconCache = LruCache<String, Drawable>(120)
    private val iconLoader = Executors.newFixedThreadPool(2) { r ->
        Thread(r, "vd-app-icon").apply { isDaemon = true; priority = Thread.NORM_PRIORITY - 1 }
    }
    private val fallbackColors = intArrayOf(
        0xFFB568F7.toInt(),
        0xFF0A84FF.toInt(),
        0xFF2EC4B6.toInt(),
        0xFFFF8A3D.toInt(),
        0xFF30D158.toInt(),
        0xFFFF375F.toInt(),
        0xFF5E5CE6.toInt()
    )

    class DrawerController(
        val rootView: View,
        val updateApps: (List<VirtualAppEntry>, String?) -> Unit
    )

    fun createDrawerView(
        context: Context,
        dp: (Int) -> Int,
        topInsetPx: Int = dp(36),
        cornerRadiusPx: Float = dp(16).toFloat(),
        onLaunchApp: (String) -> Unit,
        onClose: () -> Unit
    ): DrawerController {
        var allApps: List<VirtualAppEntry> = emptyList()
        var currentPkg: String? = null
        var filterQuery: String = ""

        val root = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            background = GradientDrawable().apply {
                setColor(0xF50E1621.toInt())
                cornerRadius = cornerRadiusPx
            }
            setPadding(dp(10), topInsetPx, dp(10), dp(10))
            isClickable = true
        }

        // 1. Header Row: Title + "返回画面 ✕" button
        val headerRow = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(2), dp(2), dp(2), dp(6))
        }
        val titleView = TextView(context).apply {
            text = "📱 虚拟屏应用桌面"
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 12.5f)
            setTextColor(0xFFF8FAFC.toInt())
            typeface = Typeface.DEFAULT_BOLD
            maxLines = 1
            ellipsize = TextUtils.TruncateAt.END
        }
        headerRow.addView(titleView, LinearLayout.LayoutParams(0, -2, 1f))

        val closeBtn = TextView(context).apply {
            text = "返回画面 ✕"
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 10.5f)
            setTextColor(0xFF93C5FD.toInt())
            typeface = Typeface.DEFAULT_BOLD
            setPadding(dp(8), dp(4), dp(8), dp(4))
            background = GradientDrawable().apply {
                setColor(0x260A84FF)
                cornerRadius = dp(8).toFloat()
                setStroke(1, 0x4D0A84FF)
            }
            setOnClickListener { onClose() }
        }
        headerRow.addView(closeBtn, LinearLayout.LayoutParams(-2, -2))
        root.addView(headerRow, LinearLayout.LayoutParams(-1, -2))

        // 2. Search EditText
        val searchBox = EditText(context).apply {
            hint = "🔍 搜索应用名称或包名…"
            setHintTextColor(0xFF64748B.toInt())
            setTextColor(0xFFE2E8F0.toInt())
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 11f)
            setSingleLine(true)
            setPadding(dp(10), dp(6), dp(10), dp(6))
            background = GradientDrawable().apply {
                setColor(0xFF182533.toInt())
                cornerRadius = dp(10).toFloat()
                setStroke(1, 0x33FFFFFF)
            }
        }
        root.addView(searchBox, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(8) })

        // 3. ScrollView + 4-Column Grid Container
        val scrollView = ScrollView(context).apply {
            isVerticalScrollBarEnabled = false
        }
        val gridContainer = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
        }
        scrollView.addView(gridContainer, FrameLayout.LayoutParams(-1, -2))
        root.addView(scrollView, LinearLayout.LayoutParams(-1, 0, 1f))

        fun renderGrid() {
            gridContainer.removeAllViews()
            val q = filterQuery.trim().lowercase()
            val filtered = if (q.isEmpty()) {
                allApps
            } else {
                allApps.filter { app ->
                    val pkg = app.component.substringBefore("/").lowercase()
                    app.name.lowercase().contains(q) || pkg.contains(q)
                }
            }
            titleView.text = if (allApps.isEmpty()) "📱 虚拟屏应用桌面" else "📱 虚拟屏桌面 (${filtered.size})"

            if (filtered.isEmpty()) {
                val emptyTv = TextView(context).apply {
                    text = if (allApps.isEmpty()) "正在加载已安装应用…" else "未找到匹配「$filterQuery」的应用"
                    setTextSize(TypedValue.COMPLEX_UNIT_SP, 11.5f)
                    setTextColor(0xFF94A3B8.toInt())
                    gravity = Gravity.CENTER
                    setPadding(dp(8), dp(28), dp(8), dp(28))
                }
                gridContainer.addView(emptyTv, LinearLayout.LayoutParams(-1, -2))
                return
            }

            val cols = 4
            var rowIdx = 0
            while (rowIdx < filtered.size) {
                val rowLayout = LinearLayout(context).apply {
                    orientation = LinearLayout.HORIZONTAL
                    weightSum = cols.toFloat()
                    setPadding(0, dp(4), 0, dp(6))
                }
                for (c in 0 until cols) {
                    val itemIdx = rowIdx + c
                    if (itemIdx < filtered.size) {
                        val app = filtered[itemIdx]
                        val pkg = app.component.substringBefore("/")
                        val isCurrent = !currentPkg.isNullOrBlank() && pkg.equals(currentPkg, ignoreCase = true)
                        val cell = buildAppCell(context, dp, app, pkg, isCurrent) {
                            onLaunchApp(app.component)
                            onClose()
                        }
                        rowLayout.addView(cell, LinearLayout.LayoutParams(0, -2, 1f))
                    } else {
                        rowLayout.addView(View(context), LinearLayout.LayoutParams(0, 1, 1f))
                    }
                }
                gridContainer.addView(rowLayout, LinearLayout.LayoutParams(-1, -2))
                rowIdx += cols
            }
        }

        searchBox.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {
                val next = s?.toString().orEmpty()
                if (next != filterQuery) {
                    filterQuery = next
                    renderGrid()
                }
            }
            override fun afterTextChanged(s: Editable?) {}
        })

        return DrawerController(
            rootView = root,
            updateApps = { apps, activePkg ->
                allApps = apps
                currentPkg = activePkg
                renderGrid()
            }
        )
    }

    private fun buildAppCell(
        context: Context,
        dp: (Int) -> Int,
        app: VirtualAppEntry,
        pkg: String,
        isCurrent: Boolean,
        onClick: () -> Unit
    ): View {
        val cell = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            setPadding(dp(3), dp(4), dp(3), dp(4))
            isClickable = true
            background = GradientDrawable().apply {
                setColor(if (isCurrent) 0x260A84FF else Color.TRANSPARENT)
                cornerRadius = dp(10).toFloat()
                if (isCurrent) setStroke(1, 0x660A84FF)
            }
            setOnClickListener { onClick() }
        }

        val iconBoxSize = dp(44)
        val iconHolder = FrameLayout(context).apply {
            background = GradientDrawable().apply {
                setColor(0xFF182533.toInt())
                cornerRadius = dp(12).toFloat()
                setStroke(1, 0x22FFFFFF)
            }
        }

        val fallbackTv = TextView(context).apply {
            text = app.name.trim().take(1).uppercase().ifEmpty { "A" }
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
            setTextColor(Color.WHITE)
            typeface = Typeface.DEFAULT_BOLD
            gravity = Gravity.CENTER
            background = GradientDrawable().apply {
                shape = GradientDrawable.OVAL
                setColor(fallbackColors[(pkg.hashCode() and 0x7FFFFFFF) % fallbackColors.size])
            }
        }
        val fbSize = dp(30)
        iconHolder.addView(fallbackTv, FrameLayout.LayoutParams(fbSize, fbSize, Gravity.CENTER))

        val iconImg = ImageView(context).apply {
            scaleType = ImageView.ScaleType.FIT_CENTER
            val pad = dp(5)
            setPadding(pad, pad, pad, pad)
        }
        iconHolder.addView(iconImg, FrameLayout.LayoutParams(-1, -1, Gravity.CENTER))

        val cached = synchronized(iconCache) { iconCache.get(pkg) }
        if (cached != null) {
            fallbackTv.visibility = View.GONE
            iconImg.setImageDrawable(cached)
        } else {
            iconLoader.execute {
                val loaded = runCatching { context.packageManager.getApplicationIcon(pkg) }.getOrNull()
                if (loaded != null) {
                    synchronized(iconCache) { iconCache.put(pkg, loaded) }
                    iconImg.post {
                        fallbackTv.visibility = View.GONE
                        iconImg.setImageDrawable(loaded)
                    }
                }
            }
        }

        cell.addView(iconHolder, LinearLayout.LayoutParams(iconBoxSize, iconBoxSize))

        val nameTv = TextView(context).apply {
            text = app.name
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 11f)
            setTextColor(0xFFE2E8F0.toInt())
            gravity = Gravity.CENTER
            maxLines = 1
            ellipsize = TextUtils.TruncateAt.END
            setPadding(dp(1), dp(4), dp(1), 0)
        }
        cell.addView(nameTv, LinearLayout.LayoutParams(-1, -2))
        return cell
    }
}
