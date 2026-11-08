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
                // Subscribe ONLY to low-frequency window state transitions instead of TYPES_ALL_MASK,
                // and avoid FEEDBACK_SPOKEN / FLAG_REQUEST_ENHANCED_WEB_ACCESSIBILITY so we never
                // trigger system-wide TalkBack/WebView accessibility overhead or high-frequency scroll/content IPC wakeups.
                info.eventTypes = AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED
                info.feedbackType = AccessibilityServiceInfo.FEEDBACK_GENERIC
                info.notificationTimeout = 500L
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
        val byRef: Map<String, CachedNode>
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
        fun updateSnapshotFromExternal(displayId: Int, nodes: List<CachedNode>, activePkg: String, screenW: Int = 720, screenH: Int = 1600): CachedSnapshot {
            val hash = computeTreeHash(nodes, activePkg)
            val summary = formatCompactSummary(displayId, activePkg, hash, nodes, screenW, screenH)
            val snap = CachedSnapshot(
                displayId = displayId,
                timestampMs = System.currentTimeMillis(),
                treeHash = hash,
                activePackage = activePkg,
                nodes = nodes,
                summaryText = summary,
                byRef = nodes.associateBy { it.ref }
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

        /**
         * Reads the Accessibility node tree strictly on [displayId] (`> 0`) using `windowsOnAllDisplays`.
         * Waits up to [waitMs] if the virtual display is in the middle of an Activity transition so it
         * never returns empty prematurely or leaks the user's main screen (Display 0).
         */
        @JvmStatic
        @JvmOverloads
        fun dumpDisplayTree(
            displayId: Int,
            screenW: Int,
            screenH: Int,
            forceRefresh: Boolean = false,
            waitMs: Long = 420L
        ): JSONObject? {
            if (displayId <= 0) return null
            if (!forceRefresh) {
                val cached = snapshotCache[displayId]
                if (cached != null && System.currentTimeMillis() - cached.timestampMs < 240L && cached.nodes.isNotEmpty()) {
                    return snapshotToJson(cached, fromCache = true)
                }
            }
            val deadline = SystemClock.uptimeMillis() + waitMs.coerceIn(0L, 750L)
            val wSafe = screenW.coerceAtLeast(1)
            val hSafe = screenH.coerceAtLeast(1)
            val screenRect = Rect(0, 0, wSafe, hSafe)
            val maxContainerArea = (wSafe.toLong() * hSafe.toLong() * 65L) / 100L
            val maxRowHeight = (hSafe * 22) / 100
            var lastEmptySnap: CachedSnapshot? = null

            while (true) {
                val svc = instance
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

                        fun collectFromWindows(strictVisible: Boolean) {
                            var visitedNodes = 0
                            for (win in contentWindows) {
                                val root = try { win.root } catch (t: Throwable) { null } ?: continue
                                val winPkg = root.packageName?.toString().orEmpty()
                                val winTitle = try { win.title?.toString().orEmpty() } catch (t: Throwable) { "" }
                                // Skip our own main chat UI if it ever lands on the virtual display, but allow DisplayTestActivity
                                if (winPkg == "dev.bubuki.proactive" && !winTitle.contains("测试", ignoreCase = true) && !winTitle.contains("Test", ignoreCase = true)) {
                                    val firstChildId = try { root.getChild(0)?.viewIdResourceName.orEmpty() } catch (t: Throwable) { "" }
                                    if (firstChildId.isEmpty() && contentWindows.size > 1) continue
                                }
                                if (activePkg.isEmpty() && winPkg.isNotBlank() && win.type == AccessibilityWindowInfo.TYPE_APPLICATION) {
                                    activePkg = winPkg
                                } else if (activePkg.isEmpty() && winPkg.isNotBlank()) {
                                    activePkg = winPkg
                                }

                                fun traverse(node: AccessibilityNodeInfo?, depth: Int) {
                                    if (node == null || depth > 42 || collected.size >= 110 || ++visitedNodes > 650) return
                                    val visible = try { node.isVisibleToUser } catch (t: Throwable) { true }

                                    val rawRect = Rect()
                                    try { node.getBoundsInScreen(rawRect) } catch (t: Throwable) {}
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
                        if (collected.size < 2 && activePkg != "com.tencent.mm") {
                            collected.clear()
                            nextIdx = 1
                            collectFromWindows(strictVisible = false)
                        }
                        val snap = updateSnapshotFromExternal(displayId, collected, activePkg, wSafe, hSafe)
                        if (collected.isNotEmpty() || SystemClock.uptimeMillis() >= deadline) {
                            return snapshotToJson(snap, fromCache = false)
                        }
                        lastEmptySnap = snap
                    }
                }
                if (SystemClock.uptimeMillis() >= deadline) break
                try { Thread.sleep(110L) } catch (ignored: InterruptedException) { break }
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
        fun resolveTargetNode(
            displayId: Int,
            screenW: Int,
            screenH: Int,
            ref: String?,
            text: String?,
            resourceId: String?
        ): CachedNode? {
            var snap = snapshotCache[displayId]
            if (snap == null || System.currentTimeMillis() - snap.timestampMs > 15000L) {
                dumpDisplayTree(displayId, screenW, screenH, forceRefresh = true)
                snap = snapshotCache[displayId]
            }
            if (snap == null) return null
            return matchNodeInSnapshot(snap, ref, text, resourceId)
        }

        internal fun matchNodeInSnapshot(
            snap: CachedSnapshot,
            ref: String?,
            text: String?,
            resourceId: String?
        ): CachedNode? {
            val cleanRef = ref?.trim().orEmpty().let { if (it.matches(Regex("e\\d+"))) "@$it" else it }
            if (cleanRef.startsWith("@e")) {
                snap.byRef[cleanRef]?.let { return it }
            }
            val qText = text?.trim().orEmpty()
            val qId = resourceId?.trim()?.substringAfterLast('/').orEmpty()
            if (qId.isNotEmpty()) {
                snap.nodes.firstOrNull { it.resourceId.equals(qId, ignoreCase = true) && it.clickable }?.let { return it }
                snap.nodes.firstOrNull { it.resourceId.contains(qId, ignoreCase = true) }?.let { return it }
            }
            if (qText.isNotEmpty()) {
                // 1. Exact match on clickable/editable node
                snap.nodes.firstOrNull {
                    (it.clickable || it.editable) &&
                        (it.text.equals(qText, ignoreCase = true) || it.desc.equals(qText, ignoreCase = true) || it.hint.equals(qText, ignoreCase = true))
                }?.let { return it }
                // 2. Exact match on any node
                snap.nodes.firstOrNull {
                    it.text.equals(qText, ignoreCase = true) || it.desc.equals(qText, ignoreCase = true) || it.hint.equals(qText, ignoreCase = true)
                }?.let { return it }
                // 3. Substring match on clickable/editable node
                snap.nodes.firstOrNull {
                    (it.clickable || it.editable) &&
                        (it.text.contains(qText, ignoreCase = true) || it.desc.contains(qText, ignoreCase = true) || it.hint.contains(qText, ignoreCase = true))
                }?.let { return it }
                // 4. Substring match on any node
                snap.nodes.firstOrNull {
                    it.text.contains(qText, ignoreCase = true) || it.desc.contains(qText, ignoreCase = true) || it.hint.contains(qText, ignoreCase = true)
                }?.let { return it }
            }
            return null
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
            for (win in contentWindows) {
                val root = try { win.root } catch (t: Throwable) { null } ?: continue
                val winPkg = root.packageName?.toString().orEmpty()
                val winTitle = try { win.title?.toString().orEmpty() } catch (t: Throwable) { "" }
                if (winPkg == "dev.bubuki.proactive" && !winTitle.contains("测试", ignoreCase = true) && !winTitle.contains("Test", ignoreCase = true) && contentWindows.size > 1) continue
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
                                found.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
                                val args = Bundle().apply {
                                    putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, inputText.orEmpty())
                                }
                                found.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
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
            if (kotlin.math.abs(r.centerX() - target.centerX) <= 14 && kotlin.math.abs(r.centerY() - target.centerY) <= 14) {
                val txt = node.text?.toString()?.trim().orEmpty()
                val desc = node.contentDescription?.toString()?.trim().orEmpty()
                val hint = node.hintText?.toString()?.trim().orEmpty()
                val resId = node.viewIdResourceName?.substringAfterLast('/')?.trim().orEmpty()
                val clickable = node.isClickable || node.isLongClickable || node.isContextClickable
                if (
                    (target.resourceId.isNotEmpty() && resId == target.resourceId) ||
                    (txt.isNotEmpty() && txt == target.text) ||
                    (desc.isNotEmpty() && desc == target.desc) ||
                    (hint.isNotEmpty() && hint == target.hint) ||
                    (node.isEditable && target.editable) ||
                    (clickable == target.clickable && kotlin.math.abs(r.width() - target.bounds.width()) <= 20)
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
            return JSONObject()
                .put("displayId", snap.displayId)
                .put("package", snap.activePackage)
                .put("treeHash", snap.treeHash)
                .put("cached", fromCache)
                .put("nodeCount", snap.nodes.size)
                .put("summary", snap.summaryText)
                .put("nodes", arr)
        }
    }
}
