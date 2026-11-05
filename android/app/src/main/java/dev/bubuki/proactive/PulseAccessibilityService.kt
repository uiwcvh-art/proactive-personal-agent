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
                info.eventTypes = AccessibilityEvent.TYPES_ALL_MASK
                info.feedbackType = info.feedbackType or
                    AccessibilityServiceInfo.FEEDBACK_SPOKEN or
                    AccessibilityServiceInfo.FEEDBACK_GENERIC
                info.flags = info.flags or
                    AccessibilityServiceInfo.FLAG_INCLUDE_NOT_IMPORTANT_VIEWS or
                    AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS or
                    AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS or
                    AccessibilityServiceInfo.FLAG_REQUEST_ENHANCED_WEB_ACCESSIBILITY
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
            .put("id", resourceId)
            .put("bounds", JSONArray().put(bounds.left).put(bounds.top).put(bounds.right).put(bounds.bottom))
            .put("center_x", centerX)
            .put("center_y", centerY)
            .put("clickable", clickable)
            .put("editable", editable)
            .put("scrollable", scrollable)
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
        fun updateSnapshotFromExternal(displayId: Int, nodes: List<CachedNode>, activePkg: String): CachedSnapshot {
            val hash = computeTreeHash(nodes, activePkg)
            val summary = formatCompactSummary(displayId, activePkg, hash, nodes)
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
            waitMs: Long = 1500L
        ): JSONObject? {
            if (displayId <= 0) return null
            if (!forceRefresh) {
                val cached = snapshotCache[displayId]
                if (cached != null && System.currentTimeMillis() - cached.timestampMs < 180L && cached.nodes.isNotEmpty()) {
                    return snapshotToJson(cached, fromCache = true)
                }
            }
            val deadline = SystemClock.uptimeMillis() + waitMs.coerceAtLeast(0L)
            val wSafe = screenW.coerceAtLeast(1)
            val hSafe = screenH.coerceAtLeast(1)
            val screenRect = Rect(0, 0, wSafe, hSafe)
            val maxContainerArea = (wSafe.toLong() * hSafe.toLong() * 80L) / 100L

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
                        val collected = ArrayList<CachedNode>(64)
                        var activePkg = ""
                        var nextIdx = 1

                        fun collectFromWindows(strictVisible: Boolean) {
                            for (win in contentWindows) {
                                val root = try { win.root } catch (t: Throwable) { null } ?: continue
                                val winPkg = root.packageName?.toString().orEmpty()
                                // Never read our own host app UI on the virtual display
                                if (winPkg == "dev.bubuki.proactive") continue
                                if (activePkg.isEmpty() && winPkg.isNotBlank() && win.type == AccessibilityWindowInfo.TYPE_APPLICATION) {
                                    activePkg = winPkg
                                } else if (activePkg.isEmpty() && winPkg.isNotBlank()) {
                                    activePkg = winPkg
                                }

                                fun traverse(node: AccessibilityNodeInfo?, depth: Int) {
                                    if (node == null || depth > 52 || collected.size >= 140) return
                                    val visible = try { node.isVisibleToUser } catch (t: Throwable) { true }

                                    val rect = Rect()
                                    try { node.getBoundsInScreen(rect) } catch (t: Throwable) {}
                                    val intersects = rect.intersect(screenRect) && rect.width() >= 4 && rect.height() >= 4

                                    // NOTE: On a VirtualDisplay (displayId > 0), hardened apps like WeChat (com.tencent.mm)
                                    // often report `isVisibleToUser = false` on outer containers while children have valid bounds!
                                    // Therefore we NEVER return before traversing children.
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
                                            !editable && !scrollable && !checkable &&
                                            ownText.isEmpty() && ownDesc.isEmpty() && hint.isEmpty()

                                        val effectiveText = when {
                                            ownText.isNotEmpty() -> ownText
                                            ownDesc.isEmpty() && hint.isEmpty() && clickable && !isFullscreenBlankContainer -> descendantLabel(node, strictVisible)
                                            else -> ""
                                        }

                                        val meaningful = intersects && !isFullscreenBlankContainer && (
                                            clickable || editable || scrollable || checkable ||
                                                effectiveText.isNotEmpty() || ownDesc.isNotEmpty() || hint.isNotEmpty()
                                            )
                                        if (meaningful) {
                                            val cx = rect.centerX().coerceIn(0, (wSafe - 1).coerceAtLeast(0))
                                            val cy = rect.centerY().coerceIn(0, (hSafe - 1).coerceAtLeast(0))
                                            val duplicate = collected.any { prev ->
                                                !clickable && !editable &&
                                                    prev.text == effectiveText && prev.desc == ownDesc &&
                                                    kotlin.math.abs(prev.centerX - cx) <= 6 && kotlin.math.abs(prev.centerY - cy) <= 6
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
                        if (collected.isNotEmpty()) {
                            val snap = updateSnapshotFromExternal(displayId, collected, activePkg)
                            return snapshotToJson(snap, fromCache = false)
                        }
                    }
                }
                if (SystemClock.uptimeMillis() >= deadline) break
                try { Thread.sleep(85L) } catch (ignored: InterruptedException) { break }
            }
            return null
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
                if (root.packageName?.toString() == "dev.bubuki.proactive") continue
                val rootRect = Rect()
                try { root.getBoundsInScreen(rootRect) } catch (t: Throwable) {}
                val maxParentArea = (rootRect.width().coerceAtLeast(1080).toLong() * rootRect.height().coerceAtLeast(1920).toLong() * 70L) / 100L
                val found = findLiveNode(root, target, 0)
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

        private fun findLiveNode(node: AccessibilityNodeInfo?, target: CachedNode, depth: Int): AccessibilityNodeInfo? {
            if (node == null || depth > 48) return null
            val r = Rect()
            try { node.getBoundsInScreen(r) } catch (t: Throwable) {}
            if (kotlin.math.abs(r.centerX() - target.centerX) <= 10 && kotlin.math.abs(r.centerY() - target.centerY) <= 10) {
                val txt = node.text?.toString()?.trim().orEmpty()
                val desc = node.contentDescription?.toString()?.trim().orEmpty()
                val resId = node.viewIdResourceName?.substringAfterLast('/')?.trim().orEmpty()
                if (
                    (target.resourceId.isNotEmpty() && resId == target.resourceId) ||
                    (txt.isNotEmpty() && txt == target.text) ||
                    (desc.isNotEmpty() && desc == target.desc) ||
                    (node.isClickable == target.clickable && kotlin.math.abs(r.width() - target.bounds.width()) <= 16)
                ) {
                    return node
                }
            }
            val count = try { node.childCount } catch (t: Throwable) { 0 }
            for (i in 0 until count) {
                val child = try { node.getChild(i) } catch (t: Throwable) { null }
                val match = findLiveNode(child, target, depth + 1)
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
                feed(n.role); feed(n.text); feed(n.desc); feed("${n.centerX},${n.centerY}")
            }
            return Integer.toHexString(h)
        }

        internal fun formatCompactSummary(displayId: Int, activePkg: String, hash: String, nodes: List<CachedNode>): String {
            val sb = StringBuilder()
            sb.append("displayId=").append(displayId)
                .append(" package=").append(activePkg.ifBlank { "unknown" })
                .append(" treeHash=").append(hash)
                .append(" count=").append(nodes.size).append("\n")
            for (n in nodes) {
                sb.append(n.ref).append(" [").append(n.role).append("]")
                val label = listOf(n.text, n.desc, n.hint).firstOrNull { it.isNotBlank() }.orEmpty()
                if (label.isNotEmpty()) sb.append(" \"").append(label.replace("\n", " ")).append("\"")
                if (n.resourceId.isNotEmpty()) sb.append(" id=").append(n.resourceId)
                sb.append(" center=(").append(n.centerX).append(",").append(n.centerY).append(")")
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
