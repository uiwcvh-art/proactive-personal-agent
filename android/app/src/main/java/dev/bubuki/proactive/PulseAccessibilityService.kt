package dev.bubuki.proactive

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.AccessibilityServiceInfo
import android.content.Intent
import android.graphics.Rect
import android.os.Bundle
import android.os.SystemClock
import android.util.SparseArray
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.ConcurrentHashMap

/**
 * High-speed Accessibility UI Tree reader & node action executor modeled after
 * Mika's ScreenAccessibility.kt + ScreenReader.kt + NodeIds.kt:
 * - Auto-enabled via Root (`settings put secure enabled_accessibility_services ...`)
 * - Reads `windowsOnAllDisplays.get(displayId)` strictly isolated to the virtual display (`displayId > 0`)
 * - Never falls back to Display 0 (`uiautomator dump`), ensuring the user's main screen never leaks into the tree
 * - Aggregates descendant labels for clickable containers (ListView/RecyclerView rows) like Mika's `ScreenReader.descendantLabel`
 */
class PulseAccessibilityService : AccessibilityService() {

    override fun onServiceConnected() {
        super.onServiceConnected()
        runCatching {
            serviceInfo?.let { info ->
                // Include TYPE_WINDOW_CONTENT_CHANGED (throttled to 250ms) so Chromium/XWeb/Nebula WebViews
                // in WeChat & Alipay initialize their accessibility DOM trees without TalkBack/high-frequency overhead.
                info.eventTypes = AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED or
                    AccessibilityEvent.TYPE_WINDOWS_CHANGED or
                    AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED
                info.feedbackType = AccessibilityServiceInfo.FEEDBACK_GENERIC
                info.notificationTimeout = 250L
                info.flags = (info.flags or
                    AccessibilityServiceInfo.FLAG_INCLUDE_NOT_IMPORTANT_VIEWS or
                    AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS or
                    AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS) and
                    AccessibilityServiceInfo.FLAG_REQUEST_ENHANCED_WEB_ACCESSIBILITY.inv()
                serviceInfo = info
            }
        }
        instance = this
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {}

    override fun onInterrupt() {}

    override fun onUnbind(intent: Intent?): Boolean {
        if (instance === this) instance = null
        return super.onUnbind(intent)
    }

    override fun onDestroy() {
        if (instance === this) instance = null
        super.onDestroy()
    }

    data class CachedNode(
        val ref: String,
        val role: String,
        val text: String,
        val desc: String,
        val hint: String,
        val resourceId: String,
        val pkg: String,
        val bounds: Rect,
        val centerX: Int,
        val centerY: Int,
        val clickable: Boolean,
        val editable: Boolean,
        val scrollable: Boolean,
        val checkable: Boolean,
        val checked: Boolean
    ) {
        fun toJson(): JSONObject = JSONObject()
            .put("ref", ref)
            .put("role", role)
            .put("text", text)
            .put("desc", desc)
            .put("hint", hint)
            .put("id", resourceId)
            .put("pkg", pkg)
            .put("bounds", JSONArray().put(bounds.left).put(bounds.top).put(bounds.right).put(bounds.bottom))
            .put("center_x", centerX)
            .put("center_y", centerY)
            .put("clickable", clickable)
            .put("editable", editable)
            .put("scrollable", scrollable)
            .put("checkable", checkable)
            .put("checked", checked)
    }

    data class CachedSnapshot(
        val displayId: Int,
        val timestampMs: Long,
        val treeHash: String,
        val activePackage: String,
        val nodes: List<CachedNode>,
        val summaryText: String,
        val byRef: Map<String, CachedNode>,
        val screenW: Int = 720,
        val screenH: Int = 1584,
        val layoutStable: Boolean = true,
        val settleMs: Long = 0L
    )

    companion object {
        @Volatile
        var instance: PulseAccessibilityService? = null
            private set

        private val snapshotCache = ConcurrentHashMap<Int, CachedSnapshot>()

        @JvmStatic
        fun isConnected(): Boolean = instance != null

        @JvmStatic
        fun getCachedSnapshot(displayId: Int): CachedSnapshot? = snapshotCache[displayId]

        @JvmStatic
        @JvmOverloads
        fun updateSnapshotFromExternal(
            displayId: Int,
            nodes: List<CachedNode>,
            activePkg: String,
            screenW: Int = 720,
            screenH: Int = 1584,
            layoutStable: Boolean = true,
            settleMs: Long = 0L
        ): CachedSnapshot {
            val hash = computeTreeHash(nodes, activePkg)
            val summary = formatCompactSummary(displayId, activePkg, hash, nodes, screenW, screenH)
            val snap = CachedSnapshot(
                displayId = displayId,
                timestampMs = System.currentTimeMillis(),
                treeHash = hash,
                activePackage = activePkg,
                nodes = nodes,
                summaryText = summary,
                byRef = nodes.associateBy { it.ref },
                screenW = screenW,
                screenH = screenH,
                layoutStable = layoutStable,
                settleMs = settleMs
            )
            snapshotCache[displayId] = snap
            return snap
        }

        private val SKIPPED_WINDOW_TYPES = setOf(
            AccessibilityWindowInfo.TYPE_INPUT_METHOD,
            AccessibilityWindowInfo.TYPE_ACCESSIBILITY_OVERLAY,
            AccessibilityWindowInfo.TYPE_MAGNIFICATION_OVERLAY,
            AccessibilityWindowInfo.TYPE_SPLIT_SCREEN_DIVIDER
        )

        @JvmStatic
        @JvmOverloads
        fun dumpTreeJson(
            displayId: Int,
            screenW: Int,
            screenH: Int,
            forceRefresh: Boolean = false,
            waitMs: Long = 750L
        ): JSONObject? = dumpDisplayTree(displayId, screenW, screenH, forceRefresh, waitMs)

        /**
         * Reads the Accessibility node tree strictly on [displayId] (`> 0`) using `windowsOnAllDisplays`.
         * Waits up to [waitMs] and performs two-phase layout stability sampling so transient mid-animation
         * or two-stage configuration reflow frames are settled before returning.
         */
        @JvmStatic
        @JvmOverloads
        fun dumpDisplayTree(
            displayId: Int,
            screenW: Int,
            screenH: Int,
            forceRefresh: Boolean = false,
            waitMs: Long = 750L
        ): JSONObject? {
            if (displayId <= 0) return null
            if (!forceRefresh) {
                val cached = snapshotCache[displayId]
                if (cached != null && System.currentTimeMillis() - cached.timestampMs < 240L && cached.nodes.isNotEmpty() && cached.layoutStable) {
                    return snapshotToJson(cached, fromCache = true)
                }
            }
            val startUptime = SystemClock.uptimeMillis()
            val deadline = startUptime + waitMs.coerceIn(0L, 1600L)
            val wSafe = screenW.coerceAtLeast(1)
            val hSafe = screenH.coerceAtLeast(1)
            val screenRect = Rect(0, 0, wSafe, hSafe)
            val maxContainerArea = (wSafe.toLong() * hSafe.toLong() * 65L) / 100L
            val maxRowHeight = (hSafe * 22) / 100
            var lastEmptySnap: CachedSnapshot? = null
            var prevNodes: List<CachedNode>? = null
            var prevPkg = ""

            while (true) {
                val svc = instance
                var hasAnyRoot = false
                if (svc != null) {
                    val allWindows: SparseArray<List<AccessibilityWindowInfo>>? = try {
                        svc.windowsOnAllDisplays
                    } catch (t: Throwable) {
                        null
                    }
                    val rawWindows = allWindows?.get(displayId).orEmpty()
                    val contentWindows = rawWindows
                        .filter { win ->
                            win.displayId == displayId && win.type !in SKIPPED_WINDOW_TYPES
                        }
                        .sortedByDescending { it.layer }

                    if (contentWindows.isNotEmpty()) {
                        val collected = ArrayList<CachedNode>(80)
                        var activePkg = ""
                        var nextIdx = 1
                        val hasExternalAppWin = contentWindows.any { win ->
                            val p = try { win.root?.packageName?.toString().orEmpty() } catch (_: Throwable) { "" }
                            p.isNotBlank() && p != "dev.bubuki.proactive"
                        }

                        fun collectFromWindows(strictVisible: Boolean) {
                            var visitedNodes = 0
                            for (win in contentWindows) {
                                val root = try { win.root } catch (t: Throwable) { null } ?: continue
                                hasAnyRoot = true
                                val winPkg = root.packageName?.toString().orEmpty()
                                // Skip our VirtualDisplay Home Screen (`DisplayTestActivity`) whenever an external app window is above it
                                if (winPkg == "dev.bubuki.proactive" && hasExternalAppWin) {
                                    continue
                                }
                                if (activePkg.isEmpty() && winPkg.isNotBlank() && win.type == AccessibilityWindowInfo.TYPE_APPLICATION) {
                                    activePkg = winPkg
                                } else if (activePkg.isEmpty() && winPkg.isNotBlank()) {
                                    activePkg = winPkg
                                }

                                fun traverse(node: AccessibilityNodeInfo?, depth: Int) {
                                    if (node == null || depth > 55 || collected.size >= 130 || ++visitedNodes > 2000) return
                                    val visible = try { node.isVisibleToUser } catch (t: Throwable) { true }

                                    val rawRect = Rect()
                                    try { node.getBoundsInScreen(rawRect) } catch (t: Throwable) {}
                                    // Prune off-screen ViewPager pages (e.g. WeChat/Alipay inactive tabs) so bottom tab bars are never cut off
                                    if (strictVisible && depth > 2 && !visible && rawRect.width() > 0 && rawRect.height() > 0 && !Rect.intersects(rawRect, screenRect)) {
                                        return
                                    }
                                    val rect = Rect(rawRect)
                                    val intersects = rect.intersect(screenRect) && rect.width() >= 6 && rect.height() >= 6

                                    if (!strictVisible || visible) {
                                        val rawClass = node.className?.toString().orEmpty()
                                        val editable = node.isEditable
                                        val checkable = node.isCheckable
                                        val clickable = node.isClickable || node.isLongClickable || node.isContextClickable
                                        val scrollable = node.isScrollable
                                        val checked = node.isChecked
                                        val role = simplifyRole(rawClass, editable, checkable)

                                        val ownText = node.text?.toString()?.trim().orEmpty()
                                        val ownDesc = node.contentDescription?.toString()?.trim().orEmpty()
                                            .ifEmpty { runCatching { node.stateDescription?.toString()?.trim().orEmpty() }.getOrDefault("") }
                                            .ifEmpty { runCatching { node.paneTitle?.toString()?.trim().orEmpty() }.getOrDefault("") }
                                            .ifEmpty { runCatching { node.tooltipText?.toString()?.trim().orEmpty() }.getOrDefault("") }
                                        val hint = node.hintText?.toString()?.trim().orEmpty()
                                        val resId = node.viewIdResourceName?.substringAfterLast('/')?.trim().orEmpty()

                                        val nodeArea = rect.width().toLong() * rect.height().toLong()
                                        val isFullscreenBlankContainer = nodeArea >= maxContainerArea &&
                                            !editable && !checkable &&
                                            ownText.isEmpty() && ownDesc.isEmpty() && hint.isEmpty()

                                        // Only aggregate descendant labels for single-row containers (height <= 22% of screen),
                                        // NEVER for multi-row lists whose centerY would point between different rows!
                                        val effectiveText = when {
                                            ownText.isNotEmpty() -> ownText
                                            ownDesc.isEmpty() && hint.isEmpty() && clickable && !scrollable &&
                                                !isFullscreenBlankContainer && rect.height() <= maxRowHeight -> descendantLabel(node, strictVisible)
                                            else -> ""
                                        }

                                        val hasLabel = effectiveText.isNotEmpty() || ownDesc.isNotEmpty() || hint.isNotEmpty()
                                        // Across ALL apps, reject unlabeled non-editable generic views (`[View] click` blind boxes)
                                        // so custom-drawn / obfuscated apps never jitter node counts or expose misleading empty shells!
                                        val meaningful = intersects && !isFullscreenBlankContainer && (
                                            editable || checkable || hasLabel || scrollable
                                            )
                                        if (meaningful) {
                                            val cx = rect.centerX().coerceIn(0, (wSafe - 1).coerceAtLeast(0))
                                            val cy = rect.centerY().coerceIn(0, (hSafe - 1).coerceAtLeast(0))
                                            val duplicate = collected.any { prev ->
                                                kotlin.math.abs(prev.centerX - cx) <= 12 &&
                                                    kotlin.math.abs(prev.centerY - cy) <= 12 &&
                                                    (
                                                        (prev.text == effectiveText && prev.desc == ownDesc && prev.hint == hint && prev.editable == editable && prev.checkable == checkable) ||
                                                        (prev.text.isNotEmpty() && effectiveText.isEmpty() && ownDesc.isEmpty() && hint.isEmpty() && !editable && !checkable && !clickable)
                                                    )
                                            }
                                            if (!duplicate) {
                                                val ref = "@e${nextIdx++}"
                                                collected.add(
                                                    CachedNode(
                                                        ref = ref,
                                                        role = role,
                                                        text = effectiveText.take(120),
                                                        desc = ownDesc.take(120),
                                                        hint = hint.take(80),
                                                        resourceId = resId,
                                                        pkg = node.packageName?.toString().orEmpty(),
                                                        bounds = rect,
                                                        centerX = cx,
                                                        centerY = cy,
                                                        clickable = clickable,
                                                        editable = editable,
                                                        scrollable = scrollable,
                                                        checkable = checkable,
                                                        checked = checked
                                                    )
                                                )
                                            }
                                        }
                                    }
                                    val count = try { node.childCount } catch (t: Throwable) { 0 }
                                    for (i in 0 until count) {
                                        val child = try { node.getChild(i) } catch (t: Throwable) { null }
                                        if (child != null) {
                                            traverse(child, depth + 1)
                                        }
                                    }
                                }
                                traverse(root, 0)
                            }
                        }

                        collectFromWindows(strictVisible = true)
                        if (collected.size < 2) {
                            collected.clear()
                            nextIdx = 1
                            collectFromWindows(strictVisible = false)
                        }
                        if (activePkg.isEmpty() || (activePkg == "dev.bubuki.proactive" && hasExternalAppWin)) {
                            val topPkg = try { DisplayInputHelper.topPackageOnDisplay(displayId) } catch (_: Throwable) { "" }
                            if (topPkg.isNotBlank() && topPkg != "dev.bubuki.proactive") {
                                activePkg = topPkg
                            }
                        }

                        if (collected.isNotEmpty() && activePkg.isNotBlank()) {
                            val isStable = if (!forceRefresh) {
                                true
                            } else if (prevNodes != null && activePkg == prevPkg && collected.size == prevNodes!!.size) {
                                val checkCount = minOf(6, collected.size)
                                (0 until checkCount).all { idx ->
                                    val a = prevNodes!![idx]
                                    val b = collected[idx]
                                    kotlin.math.abs(a.centerX - b.centerX) <= 6 &&
                                        kotlin.math.abs(a.centerY - b.centerY) <= 6 &&
                                        a.role == b.role &&
                                        a.text == b.text
                                }
                            } else {
                                false
                            }

                            if (isStable || SystemClock.uptimeMillis() + 135L >= deadline) {
                                val settledFlag = isStable || prevNodes == null
                                val elapsed = (SystemClock.uptimeMillis() - startUptime).coerceAtLeast(0L)
                                val snap = updateSnapshotFromExternal(displayId, collected, activePkg, wSafe, hSafe, layoutStable = settledFlag, settleMs = elapsed)
                                return snapshotToJson(snap, fromCache = false)
                            }
                            prevNodes = ArrayList(collected)
                            prevPkg = activePkg
                            try { Thread.sleep(140L) } catch (ignored: InterruptedException) { break }
                            continue
                        }

                        val elapsed = (SystemClock.uptimeMillis() - startUptime).coerceAtLeast(0L)
                        val snap = updateSnapshotFromExternal(displayId, collected, activePkg, wSafe, hSafe, layoutStable = false, settleMs = elapsed)
                        if (SystemClock.uptimeMillis() >= deadline) {
                            return snapshotToJson(snap, fromCache = false)
                        }
                        lastEmptySnap = snap
                    }
                }
                if (SystemClock.uptimeMillis() >= deadline) break
                try { Thread.sleep(if (!hasAnyRoot) 60L else 80L) } catch (ignored: InterruptedException) { break }
            }
            return lastEmptySnap?.let { snapshotToJson(it, fromCache = false) }
        }

        /**
         * Aggregates visible child text/description for clickable container rows (ListView/RecyclerView items),
         * modeled after Mika's `ScreenReader.descendantLabel`.
         */
        private fun descendantLabel(node: AccessibilityNodeInfo, strictVisible: Boolean = true): String {
            val parts = ArrayList<String>(4)
            var totalLen = 0
            fun walk(cur: AccessibilityNodeInfo?, depth: Int) {
                if (cur == null || depth > 5 || totalLen > 64) return
                val count = try { cur.childCount } catch (t: Throwable) { 0 }
                for (i in 0 until count) {
                    val child = try { cur.getChild(i) } catch (t: Throwable) { null } ?: continue
                    val vis = try { child.isVisibleToUser } catch (t: Throwable) { true }
                    if (strictVisible && !vis) {
                        walk(child, depth + 1)
                        continue
                    }
                    val t = child.text?.toString()?.trim()?.takeIf { it.isNotEmpty() }
                        ?: child.contentDescription?.toString()?.trim()?.takeIf { it.isNotEmpty() }
                    if (t != null && t !in parts) {
                        parts.add(t)
                        totalLen += t.length
                        if (totalLen > 64) return
                    }
                    walk(child, depth + 1)
                }
            }
            walk(node, 0)
            val joined = parts.joinToString(" ")
            return if (joined.length > 72) joined.take(72) + "…" else joined
        }

        /**
         * Resolves a target node from [ref] (`@e1`), [text], [desc], or [resourceId] in the cached snapshot
         * (or refreshes the tree if needed).
         */
        @JvmStatic
        @JvmOverloads
        fun resolveTargetNode(
            displayId: Int,
            screenW: Int,
            screenH: Int,
            ref: String?,
            text: String?,
            resourceId: String?,
            preferEditable: Boolean = false
        ): CachedNode? {
            var snap = snapshotCache[displayId]
            if (snap != null && snap.nodes.isNotEmpty() && System.currentTimeMillis() - snap.timestampMs <= 30000L) {
                matchNodeInSnapshot(snap, ref, text, resourceId, preferEditable)?.let { return it }
            }
            dumpDisplayTree(displayId, screenW, screenH, forceRefresh = true)
            snap = snapshotCache[displayId] ?: return null
            return matchNodeInSnapshot(snap, ref, text, resourceId, preferEditable)
        }

        @JvmOverloads
        internal fun matchNodeInSnapshot(
            snap: CachedSnapshot,
            ref: String?,
            text: String?,
            resourceId: String?,
            preferEditable: Boolean = false
        ): CachedNode? {
            val refRegex = Regex("^@?e\\d+$", RegexOption.IGNORE_CASE)
            val rawRefCandidate = listOfNotNull(ref?.trim(), text?.trim(), resourceId?.trim())
                .firstOrNull { it.isNotEmpty() && it.matches(refRegex) }
                .orEmpty()
            val cleanRef = if (rawRefCandidate.isNotEmpty()) {
                val digits = rawRefCandidate.removePrefix("@").lowercase()
                "@$digits"
            } else ""
            if (cleanRef.startsWith("@e")) {
                val byRefNode = snap.byRef[cleanRef]
                if (byRefNode != null) {
                    if (preferEditable && !byRefNode.editable && !byRefNode.role.equals("EditText", ignoreCase = true)) {
                        val childEdit = snap.nodes.firstOrNull {
                            (it.editable || it.role.equals("EditText", ignoreCase = true)) && (
                                Rect.intersects(byRefNode.bounds, it.bounds) ||
                                    kotlin.math.abs(it.centerY - byRefNode.centerY) <= 48
                                )
                        }
                        if (childEdit != null) return childEdit
                    }
                    return byRefNode
                }
            }
            val qText = text?.trim()?.takeIf { !it.matches(refRegex) }.orEmpty()
            val qId = resourceId?.trim()?.takeIf { !it.matches(refRegex) }?.substringAfterLast('/').orEmpty()
            val qTextAsId = qText.substringAfterLast('/')
            if (qText.isEmpty() && qId.isEmpty()) return null

            val screenArea = (snap.screenW.coerceAtLeast(1).toLong() * snap.screenH.coerceAtLeast(1).toLong()).coerceAtLeast(1L)
            val queryHasBreadcrumbSep = qText.contains(">") || qText.contains("›") || qText.contains("/")
            var bestNode: CachedNode? = null
            var bestScore = Int.MIN_VALUE

            for (n in snap.nodes) {
                var score = 0
                var matched = false
                val nodeResId = n.resourceId.substringAfterLast('/')

                if (qId.isNotEmpty() && nodeResId.isNotEmpty()) {
                    if (nodeResId.equals(qId, ignoreCase = true)) {
                        score += 950
                        matched = true
                    } else if (nodeResId.contains(qId, ignoreCase = true)) {
                        score += 480
                        matched = true
                    }
                }

                if (qText.isNotEmpty()) {
                    // Full-field match: also match qText against resourceId (19-1: e.g. target="sb_form_q" or "search_src_text")
                    if (nodeResId.isNotEmpty()) {
                        if (nodeResId.equals(qTextAsId, ignoreCase = true)) {
                            score += 950
                            matched = true
                        } else if (qTextAsId.length >= 3 && nodeResId.contains(qTextAsId, ignoreCase = true)) {
                            score += 480
                            matched = true
                        }
                    }

                    val labels = listOf(n.text, n.desc, n.hint).filter { it.isNotBlank() }
                    var bestTextMatch = 0
                    var matchedLabel = ""
                    for (lbl in labels) {
                        val mScore = when {
                            lbl.equals(qText, ignoreCase = true) -> 1000
                            lbl.startsWith(qText, ignoreCase = true) || lbl.endsWith(qText, ignoreCase = true) -> 550
                            lbl.contains(qText, ignoreCase = true) -> 350
                            else -> 0
                        }
                        if (mScore > bestTextMatch) {
                            bestTextMatch = mScore
                            matchedLabel = lbl
                        }
                    }
                    if (bestTextMatch > 0) {
                        score += bestTextMatch
                        matched = true
                        // Breadcrumb / path noise penalty (17-5): e.g. "显示与亮度 > 显示大小" when searching "显示与亮度"
                        if (!queryHasBreadcrumbSep && matchedLabel.length > qText.length &&
                            (matchedLabel.contains(">") || matchedLabel.contains("›") || matchedLabel.contains(" -> ") || matchedLabel.contains(" / "))
                        ) {
                            score -= 500
                        }
                        val lenDiff = (matchedLabel.length - qText.length).coerceAtLeast(0)
                        score -= (lenDiff * 5).coerceAtMost(250)
                    }
                }

                if (!matched) continue

                if (preferEditable) {
                    if (n.editable || n.role.equals("EditText", ignoreCase = true)) {
                        score += 850
                    } else {
                        score -= 400
                    }
                    if (nodeResId.contains("edit", ignoreCase = true) ||
                        nodeResId.contains("search", ignoreCase = true) ||
                        nodeResId.contains("input", ignoreCase = true) ||
                        nodeResId.contains("form", ignoreCase = true)
                    ) {
                        score += 220
                    }
                }

                if (n.clickable) score += 150
                when (n.role) {
                    "Button", "Text", "CheckBox", "EditText" -> score += 100
                    "LinearLayout", "ViewGroup", "FrameLayout", "RelativeLayout", "Scroll", "View" -> score -= 200
                }
                val area = n.bounds.width().toLong() * n.bounds.height().toLong()
                if (area > (screenArea * 15L) / 100L) {
                    score -= 150
                }

                if (score > bestScore) {
                    bestScore = score
                    bestNode = n
                }
            }

            if (preferEditable && bestNode != null && !bestNode.editable && !bestNode.role.equals("EditText", ignoreCase = true)) {
                val childEdit = snap.nodes.firstOrNull {
                    (it.editable || it.role.equals("EditText", ignoreCase = true)) && (
                        Rect.intersects(bestNode.bounds, it.bounds) ||
                            kotlin.math.abs(it.centerY - bestNode.centerY) <= 48
                        )
                }
                if (childEdit != null) return childEdit
            }
            return bestNode
        }

        /**
         * Attempts direct Accessibility `performAction(ACTION_CLICK)` or `ACTION_SET_TEXT` on the live node
         * matching [target] on [displayId]. Returns true if the Accessibility action succeeded.
         */
        @JvmStatic
        fun performAccessibilityAction(displayId: Int, target: CachedNode, action: String, inputText: String? = null): Boolean {
            if (displayId <= 0) return false
            val svc = instance ?: return false
            val windows = try { svc.windowsOnAllDisplays.get(displayId) } catch (t: Throwable) { null } ?: return false
            val contentWindows = windows
                .filter { it.displayId == displayId && it.type !in SKIPPED_WINDOW_TYPES }
                .sortedByDescending { it.layer }
            val hasExternalAppWin = contentWindows.any { win ->
                val p = try { win.root?.packageName?.toString().orEmpty() } catch (_: Throwable) { "" }
                p.isNotBlank() && p != "dev.bubuki.proactive"
            }
            for (win in contentWindows) {
                val root = try { win.root } catch (t: Throwable) { null } ?: continue
                val winPkg = root.packageName?.toString().orEmpty()
                if (winPkg == "dev.bubuki.proactive" && hasExternalAppWin) continue
                val rootRect = Rect()
                try { root.getBoundsInScreen(rootRect) } catch (t: Throwable) {}
                val screenClip = Rect(0, 0, rootRect.right.coerceAtLeast(1080), rootRect.bottom.coerceAtLeast(1920))
                val maxParentArea = (rootRect.width().coerceAtLeast(1080).toLong() * rootRect.height().coerceAtLeast(1920).toLong() * 70L) / 100L
                val visited = intArrayOf(0)
                val found = findLiveNode(root, target, 0, screenClip, visited)
                if (found != null) {
                    return try {
                        when (action) {
                            "click" -> {
                                var current: AccessibilityNodeInfo? = found
                                var clicked = false
                                var hops = 0
                                while (current != null && hops < 4) {
                                    if (current.isScrollable && hops > 0) break
                                    val cRect = Rect()
                                    try { current.getBoundsInScreen(cRect) } catch (t: Throwable) {}
                                    val cArea = cRect.width().toLong() * cRect.height().toLong()
                                    if (hops > 0 && cArea >= maxParentArea) break
                                    if (current.isClickable && current.isEnabled && current.performAction(AccessibilityNodeInfo.ACTION_CLICK)) {
                                        clicked = true
                                        break
                                    }
                                    current = current.parent
                                    hops++
                                }
                                clicked
                            }
                            "set_text" -> {
                                val editTarget = if (found.isEditable || found.className?.toString()?.contains("EditText", ignoreCase = true) == true) {
                                    found
                                } else {
                                    findEditableDescendant(found, 0) ?: found.parent?.let { findEditableDescendant(it, 0) } ?: found
                                }
                                editTarget.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
                                editTarget.performAction(AccessibilityNodeInfo.ACTION_CLICK)
                                val args = Bundle().apply {
                                    putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, inputText.orEmpty())
                                }
                                editTarget.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
                            }
                            else -> false
                        }
                    } catch (t: Throwable) {
                        false
                    }
                }
            }
            return false
        }

        private fun findEditableDescendant(node: AccessibilityNodeInfo?, depth: Int): AccessibilityNodeInfo? {
            if (node == null || depth > 8) return null
            if (node.isEditable || node.className?.toString()?.contains("EditText", ignoreCase = true) == true) {
                return node
            }
            val count = try { node.childCount } catch (t: Throwable) { 0 }
            for (i in 0 until count) {
                val child = try { node.getChild(i) } catch (t: Throwable) { null } ?: continue
                val found = findEditableDescendant(child, depth + 1)
                if (found != null) return found
            }
            return null
        }

        private fun findLiveNode(
            node: AccessibilityNodeInfo?,
            target: CachedNode,
            depth: Int,
            screenClip: Rect,
            visited: IntArray
        ): AccessibilityNodeInfo? {
            if (node == null || depth > 48 || ++visited[0] > 650) return null
            val raw = Rect()
            try { node.getBoundsInScreen(raw) } catch (t: Throwable) {}
            val r = Rect(raw)
            r.intersect(screenClip)
            val resId = node.viewIdResourceName?.substringAfterLast('/')?.trim().orEmpty()
            if (target.resourceId.isNotEmpty() && resId.equals(target.resourceId, ignoreCase = true) &&
                kotlin.math.abs(r.centerY() - target.centerY) <= 180
            ) {
                return node
            }
            if (kotlin.math.abs(r.centerX() - target.centerX) <= 28 && kotlin.math.abs(r.centerY() - target.centerY) <= 28) {
                val txt = node.text?.toString()?.trim().orEmpty()
                val desc = node.contentDescription?.toString()?.trim().orEmpty()
                val hint = node.hintText?.toString()?.trim().orEmpty()
                val clickable = node.isClickable || node.isLongClickable || node.isContextClickable
                if (
                    (target.resourceId.isNotEmpty() && resId.equals(target.resourceId, ignoreCase = true)) ||
                    (txt.isNotEmpty() && txt == target.text) ||
                    (desc.isNotEmpty() && desc == target.desc) ||
                    (hint.isNotEmpty() && hint == target.hint) ||
                    (node.isEditable && target.editable) ||
                    (clickable == target.clickable && kotlin.math.abs(r.width() - target.bounds.width()) <= 24)
                ) {
                    return node
                }
            }
            val count = try { node.childCount } catch (t: Throwable) { 0 }
            for (i in 0 until count) {
                val child = try { node.getChild(i) } catch (t: Throwable) { null }
                val match = findLiveNode(child, target, depth + 1, screenClip, visited)
                if (match != null) return match
            }
            return null
        }

        internal fun simplifyRole(className: String, editable: Boolean, checkable: Boolean): String {
            if (editable) return "EditText"
            if (checkable) return "CheckBox"
            val simple = className.substringAfterLast('.')
            return when {
                simple.contains("Button", ignoreCase = true) -> "Button"
                simple.contains("EditText", ignoreCase = true) -> "EditText"
                simple.contains("TextView", ignoreCase = true) -> "Text"
                simple.contains("Image", ignoreCase = true) -> "Image"
                simple.contains("RecyclerView", ignoreCase = true) || simple.contains("ListView", ignoreCase = true) || simple.contains("ScrollView", ignoreCase = true) -> "Scroll"
                simple.contains("WebView", ignoreCase = true) -> "WebView"
                simple.isBlank() -> "View"
                else -> simple
            }
        }

        internal fun computeTreeHash(nodes: List<CachedNode>, activePkg: String): String {
            var h = -0x7ee3623b // FNV-1a 32-bit offset basis
            fun feed(s: String) {
                for (ch in s) {
                    h = (h xor ch.code) * 0x01000193
                }
            }
            feed(activePkg)
            for (n in nodes) {
                feed(n.role); feed(n.text); feed(n.desc); feed(n.hint); feed(n.resourceId)
                feed("${n.centerX},${n.centerY},${n.clickable},${n.checked}")
            }
            return Integer.toHexString(h)
        }

        internal fun formatCompactSummary(displayId: Int, activePkg: String, hash: String, nodes: List<CachedNode>, screenW: Int = 720, screenH: Int = 1600): String {
            val sb = StringBuilder()
            val wSafe = screenW.coerceAtLeast(1)
            val hSafe = screenH.coerceAtLeast(1)
            sb.append("displayId=").append(displayId)
                .append(" package=").append(activePkg.ifBlank { "unknown" })
                .append(" treeHash=").append(hash)
                .append(" count=").append(nodes.size).append("\n")
            for (n in nodes) {
                val nx = ((n.centerX * 1000L) / wSafe).toInt().coerceIn(0, 1000)
                val ny = ((n.centerY * 1000L) / hSafe).toInt().coerceIn(0, 1000)
                sb.append(n.ref).append(" [").append(n.role).append("]")
                val label = listOf(n.text, n.desc, n.hint).firstOrNull { it.isNotBlank() }.orEmpty()
                if (label.isNotEmpty()) sb.append(" \"").append(label.replace("\n", " ")).append("\"")
                if (n.resourceId.isNotEmpty()) sb.append(" id=").append(n.resourceId)
                sb.append(" center=(").append(n.centerX).append(",").append(n.centerY).append(")")
                sb.append(" norm=(").append(nx).append(",").append(ny).append(")")
                val flags = mutableListOf<String>()
                if (n.clickable) flags.add("click")
                if (n.editable) flags.add("edit")
                if (n.scrollable) flags.add("scroll")
                if (n.checked) flags.add("checked")
                if (flags.isNotEmpty()) sb.append(" ").append(flags.joinToString(","))
                sb.append("\n")
            }
            return sb.toString().trim()
        }

        internal fun snapshotToJson(snap: CachedSnapshot, fromCache: Boolean): JSONObject {
            val arr = JSONArray()
            snap.nodes.forEach { arr.put(it.toJson()) }
            val settled = snap.layoutStable && snap.nodes.isNotEmpty() && snap.activePackage.isNotBlank()
            val likelyBlank = snap.nodes.isEmpty() || snap.activePackage.isBlank()
            val pkgSafe = snap.activePackage.ifBlank { "unknown" }
            val layoutSig = "${snap.screenW}x${snap.screenH}_${pkgSafe}_${snap.treeHash}_n${snap.nodes.size}"
            val realDpi = runCatching {
                val dm = instance?.getSystemService(android.hardware.display.DisplayManager::class.java)
                val disp = dm?.getDisplay(snap.displayId) ?: dm?.getDisplay(android.view.Display.DEFAULT_DISPLAY)
                val metrics = android.util.DisplayMetrics()
                @Suppress("DEPRECATION")
                disp?.getRealMetrics(metrics)
                metrics.densityDpi.takeIf { it in 120..800 }
            }.getOrNull() ?: ((snap.screenW * 160) / 360).coerceIn(240, 640)
            val realSwDp = ((snap.screenW * 160f) / realDpi).toInt().coerceAtLeast(320)
            return JSONObject()
                .put("displayId", snap.displayId)
                .put("package", pkgSafe)
                .put("treeHash", snap.treeHash)
                .put("tree_hash", snap.treeHash)
                .put("cached", fromCache)
                .put("nodeCount", snap.nodes.size)
                .put("settled", settled)
                .put("layoutStable", settled)
                .put("settle_ms", if (fromCache) 0L else snap.settleMs)
                .put("dpi", realDpi)
                .put("swDp", realSwDp)
                .put("densityDpi", realDpi)
                .put("screenWidthDp", realSwDp)
                .put("fontScale", 1.0)
                .put("likely_blank", likelyBlank)
                .put("layout_signature", layoutSig)
                .put("summary", snap.summaryText)
                .put("nodes", arr)
        }
    }
}
