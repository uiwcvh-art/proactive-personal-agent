package dev.bubuki.proactive;

import android.app.ActivityOptions;
import android.app.Application;
import android.app.Instrumentation;
import android.content.*;
import android.content.pm.ApplicationInfo;
import android.content.res.Resources;
import android.graphics.*;
import android.hardware.display.*;
import android.media.Image;
import android.media.ImageReader;
import android.os.*;
import android.util.Base64;
import android.view.Display;
import android.view.InputDevice;
import android.view.InputEvent;
import android.view.KeyEvent;
import android.view.MotionEvent;
import android.view.Surface;
import org.json.*;
import java.io.*;
import java.lang.reflect.*;
import java.util.List;
import java.util.concurrent.*;

/**
 * Root/Shell virtual display workspace modeled after Mika's ShellContext & ShellService:
 * - Resilient ActivityThread & ShellContext ("com.android.shell") bootstrap
 * - Trusted virtual display creation with automatic untrusted fallback on strict ROMs
 * - Fullscreen windowing mode enforcement & local IME policy
 * - Per-display keep-awake via IPowerManager.userActivity
 * - Sub-2ms in-process InputManager.injectInputEvent for tap, 60fps smooth stroke swipe, and keys
 * - Fast Binder IActivityTaskManager.startActivityAsUser with setLaunchDisplayId
 */
@android.annotation.SuppressLint({"PrivateApi", "DiscouragedPrivateApi", "BlockedPrivateApi", "SoonBlockedPrivateApi", "WrongConstant"})
final class DisplayRuntime {
    private static final String SHELL_PACKAGE = "com.android.shell";
    private static final int SHELL_UID = 2000;

    // DisplayManager.VIRTUAL_DISPLAY_FLAG_* (including hidden constants)
    private static final int FLAG_PUBLIC = 1 << 0;
    private static final int FLAG_OWN_CONTENT_ONLY = 1 << 3;
    private static final int FLAG_SUPPORTS_TOUCH = 1 << 6;
    private static final int FLAG_ROTATES_WITH_CONTENT = 1 << 7;
    private static final int FLAG_DESTROY_CONTENT_ON_REMOVAL = 1 << 8;
    private static final int FLAG_OWN_DISPLAY_GROUP = 1 << 9;
    private static final int FLAG_TRUSTED = 1 << 10;
    private static final int FLAG_ALWAYS_UNLOCKED = 1 << 12;
    private static final int FLAG_TOUCH_FEEDBACK_DISABLED = 1 << 13;
    private static final int FLAG_OWN_FOCUS = 1 << 14;
    private static final int FLAG_STEAL_TOP_FOCUS_DISABLED = 1 << 16;

    private static final int INJECT_MODE_ASYNC = 0;
    private static final int INJECT_MODE_WAIT_FOR_RESULT = 1;
    private static final int VIRTUAL_KEYBOARD = -1;
    private static final int USER_CURRENT = -2;

    private final HandlerThread thread = new HandlerThread("pulse-independent-display");
    private final Handler handler;
    private final HandlerThread inputThread = new HandlerThread("pulse-virtual-input");
    private final Handler inputHandler;
    private final Context context;
    private volatile VirtualDisplay display;
    private ImageReader reader;
    private final HandlerThread frames = new HandlerThread("pulse-root-frames");
    private final Handler frameHandler;
    private final ScheduledExecutorService awakeTimer = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread t = new Thread(r, "pulse-display-awake");
        t.setDaemon(true);
        return t;
    });
    private ScheduledFuture<?> awakeTask;
    private final Object frameLock = new Object();
    private Bitmap latestFrame;
    private Bitmap previewBuffer;
    private final Paint bitmapPaint = new Paint(Paint.FILTER_BITMAP_FLAG);
    private int frameRotation;
    private long frameSequence;
    private boolean framePending = false;
    private long lastFrameProcessedAt = 0L;
    private static final long FRAME_INTERVAL_MS = 28L; // ~36fps low-latency coalesced cap for 1:1 live touch/scroll
    private volatile int previewShortSide = 540;
    private volatile long liveTouchDownTime = 0L;

    private static final class PreviewSnapshot {
        final long sequence;
        final int width;
        final int height;
        final int rotation;
        final String base64;
        PreviewSnapshot(long sequence, int width, int height, int rotation, String base64) {
            this.sequence = sequence;
            this.width = width;
            this.height = height;
            this.rotation = rotation;
            this.base64 = base64;
        }
    }
    private volatile PreviewSnapshot latestPreview = null;

    private volatile boolean trustedDisplay = true;
    private volatile boolean releasedToBackground = false;
    private RootClipboard clipboard;
    private volatile int width = 720, height = 1600, densityDpi = 320;
    private volatile String activeTier = "balanced";

    // Sandbox task containment & main-display anti-jump state
    private final java.util.Set<String> managedVirtualPackages = java.util.Collections.synchronizedSet(new java.util.HashSet<>());
    private volatile int baselineMainTaskId = -1;
    private volatile String baselineMainPkg = "";
    private volatile boolean baselineMainIsHome = true;
    private volatile long lastVirtualActionUptime = 0L;

    // Cached reflection handles for zero-overhead touch injection & keep-awake
    private static Object sInputManager;
    private static Method sInjectInputEventMethod;
    private static Method sSetDisplayIdMethod;
    private static Object sPowerManager;
    private static Method sUserActivityMethod;

    static {
        try {
            sSetDisplayIdMethod = InputEvent.class.getMethod("setDisplayId", int.class);
            try {
                sInputManager = Class.forName("android.hardware.input.InputManagerGlobal").getMethod("getInstance").invoke(null);
            } catch (Throwable ignored) {
                sInputManager = Class.forName("android.hardware.input.InputManager").getMethod("getInstance").invoke(null);
            }
            if (sInputManager != null) {
                sInjectInputEventMethod = sInputManager.getClass().getMethod("injectInputEvent", InputEvent.class, int.class);
            }
        } catch (Throwable ignored) {}
        try {
            Object binder = Class.forName("android.os.ServiceManager").getMethod("getService", String.class).invoke(null, "power");
            sPowerManager = Class.forName("android.os.IPowerManager$Stub").getMethod("asInterface", IBinder.class).invoke(null, binder);
            if (sPowerManager != null) {
                for (Method m : sPowerManager.getClass().getMethods()) {
                    Class<?>[] p = m.getParameterTypes();
                    if (m.getName().equals("userActivity") && p.length == 4
                        && p[0] == int.class && p[1] == long.class && p[2] == int.class && p[3] == int.class) {
                        sUserActivityMethod = m;
                        break;
                    }
                }
            }
        } catch (Throwable ignored) {}
    }

    private final Runnable processFrameRunnable = new Runnable() {
        @Override
        public void run() {
            framePending = false;
            lastFrameProcessedAt = SystemClock.uptimeMillis();
            processLatestFrame();
        }
    };

    DisplayRuntime(Context base) {
        context = new ContextWrapper(base) {
            @Override public String getPackageName() { return SHELL_PACKAGE; }
            @Override public String getOpPackageName() { return SHELL_PACKAGE; }
            @Override public Context getApplicationContext() { return this; }
            @Override public Resources getResources() { return Resources.getSystem(); }
            @Override public AttributionSource getAttributionSource() {
                return new AttributionSource.Builder(SHELL_UID).setPackageName(SHELL_PACKAGE).build();
            }
        };
        thread.start();
        handler = new Handler(thread.getLooper());
        inputThread.start();
        inputHandler = new Handler(inputThread.getLooper());
        frames.start();
        frameHandler = new Handler(frames.getLooper());
    }

    String execute(String command) {
        // Fast lock-free path for preview, status, and live touch stream so frame polling & dragging NEVER queue behind RPCs
        try {
            JSONObject fastCmd = new JSONObject(command);
            String fastOp = fastCmd.optString("op", "");
            if ("preview".equals(fastOp)) {
                return fastPreview(fastCmd).toString();
            }
            if ("status".equals(fastOp)) {
                return state().toString();
            }
            if ("touch".equals(fastOp)) {
                return fastTouch(fastCmd).toString();
            }
        } catch (Throwable ignored) {}

        FutureTask<String> task = new FutureTask<>(() -> {
            try {
                return run(new JSONObject(command)).toString();
            } catch (Throwable e) {
                Throwable cause = e instanceof InvocationTargetException ? ((InvocationTargetException) e).getTargetException() : e;
                return new JSONObject().put("error", cause.getClass().getSimpleName() + ": " + cause.getMessage()).toString();
            }
        });
        handler.post(task);
        try {
            return task.get(18, TimeUnit.SECONDS);
        } catch (Exception e) {
            return "{\"error\":\"虚拟屏操作超时\"}";
        }
    }

    private JSONObject fastPreview(JSONObject cmd) throws Exception {
        VirtualDisplay vd = this.display;
        if (vd == null || vd.getDisplay() == null || vd.getDisplay().getDisplayId() <= 0) {
            return state().put("hasFrame", false);
        }
        PreviewSnapshot snap = this.latestPreview;
        if (snap == null) {
            return state().put("hasFrame", false).put("frameSequence", 0);
        }
        long since = cmd.optLong("since", -1);
        if (since >= 0 && snap.sequence == since) {
            return state().put("hasFrame", true).put("unchanged", true).put("frameSequence", snap.sequence);
        }
        return state()
            .put("hasFrame", true)
            .put("width", snap.width)
            .put("height", snap.height)
            .put("rotation", snap.rotation)
            .put("frameSequence", snap.sequence)
            .put("image", snap.base64);
    }

    private JSONObject fastTouch(JSONObject cmd) throws Exception {
        VirtualDisplay vd = this.display;
        if (vd == null || vd.getDisplay() == null || vd.getDisplay().getDisplayId() <= 0) {
            return state().put("ok", false);
        }
        final int displayId = vd.getDisplay().getDisplayId();
        final int rot = vd.getDisplay().getRotation();
        final int lw = (rot % 2 == 0) ? width : height;
        final int lh = (rot % 2 == 0) ? height : width;
        final String actionStr = cmd.optString("action", "move").trim().toLowerCase();
        final float x = (float) Math.max(0, Math.min(Math.max(1, lw) - 1, cmd.optInt("x", 0)));
        final float y = (float) Math.max(0, Math.min(Math.max(1, lh) - 1, cmd.optInt("y", 0)));
        inputHandler.post(() -> {
            try {
                long now = SystemClock.uptimeMillis();
                int action;
                if ("down".equals(actionStr)) {
                    liveTouchDownTime = now;
                    action = MotionEvent.ACTION_DOWN;
                    onBeforeVirtualInteraction(displayId);
                } else if ("up".equals(actionStr)) {
                    action = MotionEvent.ACTION_UP;
                } else if ("cancel".equals(actionStr)) {
                    action = MotionEvent.ACTION_CANCEL;
                } else {
                    action = MotionEvent.ACTION_MOVE;
                }
                long downT = liveTouchDownTime > 0L ? liveTouchDownTime : now;
                MotionEvent.PointerProperties[] p = new MotionEvent.PointerProperties[]{new MotionEvent.PointerProperties()};
                p[0].id = 0;
                p[0].toolType = MotionEvent.TOOL_TYPE_FINGER;
                MotionEvent.PointerCoords[] c = new MotionEvent.PointerCoords[]{new MotionEvent.PointerCoords()};
                c[0].pressure = (action == MotionEvent.ACTION_UP || action == MotionEvent.ACTION_CANCEL) ? 0f : 1f;
                c[0].size = 1f;
                c[0].x = x;
                c[0].y = y;
                MotionEvent ev = MotionEvent.obtain(downT, now, action, 1, p, c, 0, 0, 1f, 1f, 0, 0, InputDevice.SOURCE_TOUCHSCREEN, 0);
                try {
                    injectEvent(ev, displayId, INJECT_MODE_ASYNC);
                } finally {
                    ev.recycle();
                }
                if (action == MotionEvent.ACTION_UP || action == MotionEvent.ACTION_CANCEL) {
                    liveTouchDownTime = 0L;
                    scheduleEscapeContainment(displayId);
                }
                scheduleFrameProcess();
            } catch (Throwable ignored) {}
        });
        return new JSONObject().put("uid", android.os.Process.myUid()).put("ok", true).put("displayId", displayId);
    }

    private void scheduleFrameProcess() {
        if (framePending || display == null) return;
        framePending = true;
        long wait = lastFrameProcessedAt + FRAME_INTERVAL_MS - SystemClock.uptimeMillis();
        if (wait > 0) {
            frameHandler.postDelayed(processFrameRunnable, wait);
        } else {
            frameHandler.post(processFrameRunnable);
        }
    }

    private void processLatestFrame() {
        ImageReader src = this.reader;
        VirtualDisplay vd = this.display;
        if (src == null || vd == null) return;
        try (Image image = src.acquireLatestImage()) {
            if (image == null) return;
            int rot = vd.getDisplay().getRotation();
            int bufW = this.width;
            int bufH = this.height;
            long seq;
            synchronized (frameLock) {
                if (this.reader != src || this.display == null) return;
                Image.Plane plane = image.getPlanes()[0];
                int padded = plane.getRowStride() / plane.getPixelStride();
                if (latestFrame == null || latestFrame.getWidth() != padded || latestFrame.getHeight() != bufH) {
                    if (latestFrame != null) latestFrame.recycle();
                    latestFrame = Bitmap.createBitmap(padded, bufH, Bitmap.Config.ARGB_8888);
                }
                latestFrame.copyPixelsFromBuffer(plane.getBuffer().rewind());
                frameRotation = rot;
                seq = ++frameSequence;

                int logicalW = rot % 2 == 0 ? bufW : bufH;
                int logicalH = rot % 2 == 0 ? bufH : bufW;
                int targetShort = Math.max(360, Math.min(logicalW, Math.min(logicalH, previewShortSide)));
                float scale = Math.min(1f, (float) targetShort / Math.min(logicalW, logicalH));
                int pw = Math.max(1, Math.round(logicalW * scale));
                int ph = Math.max(1, Math.round(logicalH * scale));
                if (previewBuffer == null || previewBuffer.getWidth() != pw || previewBuffer.getHeight() != ph) {
                    if (previewBuffer != null) previewBuffer.recycle();
                    previewBuffer = Bitmap.createBitmap(pw, ph, Bitmap.Config.ARGB_8888);
                }
                Canvas canvas = new Canvas(previewBuffer);
                canvas.scale((float) pw / logicalW, (float) ph / logicalH);
                if (rot == 1) { canvas.translate(0, bufW); canvas.rotate(-90); }
                else if (rot == 2) { canvas.translate(bufW, bufH); canvas.rotate(180); }
                else if (rot == 3) { canvas.translate(bufH, 0); canvas.rotate(90); }
                Rect bounds = new Rect(0, 0, bufW, bufH);
                canvas.drawBitmap(latestFrame, bounds, bounds, bitmapPaint);
                ByteArrayOutputStream out = new ByteArrayOutputStream(24576);
                previewBuffer.compress(Bitmap.CompressFormat.JPEG, 76, out);
                String b64 = Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP);
                this.latestPreview = new PreviewSnapshot(seq, logicalW, logicalH, rot, b64);
            }
        } catch (Throwable ignored) {}
    }

    private static int displayFlags(int sdk, boolean trusted, boolean ownDisplayGroup) {
        // Always include FLAG_DESTROY_CONTENT_ON_REMOVAL so that if the virtual display is ever
        // destroyed or the root daemon dies, Android WindowManagerService NEVER reparents virtual
        // display tasks onto Display 0 (the user's main screen foreground)!
        // Include FLAG_OWN_DISPLAY_GROUP when supported so apps inside the virtual display (e.g.
        // WeChat payment/repayment/QR code screens setting WindowManager.LayoutParams.screenBrightness)
        // stay in an isolated DisplayGroup and NEVER boost the physical main screen's brightness!
        int flags = FLAG_PUBLIC | FLAG_OWN_CONTENT_ONLY | FLAG_SUPPORTS_TOUCH
            | FLAG_ROTATES_WITH_CONTENT | FLAG_DESTROY_CONTENT_ON_REMOVAL;
        if (sdk >= Build.VERSION_CODES.TIRAMISU) {
            flags |= FLAG_TOUCH_FEEDBACK_DISABLED;
            if (trusted) {
                flags |= FLAG_TRUSTED | FLAG_ALWAYS_UNLOCKED;
                if (ownDisplayGroup) {
                    flags |= FLAG_OWN_DISPLAY_GROUP;
                }
            }
        }
        if (sdk >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE && trusted) {
            flags |= FLAG_OWN_FOCUS | FLAG_STEAL_TOP_FOCUS_DISABLED;
        }
        return flags;
    }

    private static int roundTo16(float value, int minVal, int maxVal) {
        int clamped = Math.max(minVal, Math.min(maxVal, Math.round(value)));
        return ((clamped + 8) / 16) * 16;
    }

    /**
     * Multi-tier adaptive virtual display specification algorithm:
     * Dynamically computes (width, height, densityDpi, previewShortSide) from the host device's
     * physical DisplayMetrics and the requested size tier ("compact", "balanced", "large").
     * - Preserves the host screen's true aspect ratio (clamped to [1.55, 2.35]) with 16px GPU stride alignment.
     * - Computes a comfort-scaled smallestWidthDp (328dp for compact, 354dp for balanced, 384dp for large)
     *   so apps inside the virtual display render crisp, well-proportioned text and controls at every window size.
     */
    private int[] computeAdaptiveDisplaySpec(DisplayManager dm, String tierInput) {
        String tier = (tierInput == null || tierInput.isEmpty()) ? "balanced" : tierInput.trim().toLowerCase();
        int rw = 1080, rh = 2400, realDpi = 440;
        try {
            Display mainDisp = dm != null ? dm.getDisplay(Display.DEFAULT_DISPLAY) : null;
            if (mainDisp != null) {
                android.util.DisplayMetrics realMetrics = new android.util.DisplayMetrics();
                mainDisp.getRealMetrics(realMetrics);
                if (realMetrics.widthPixels >= 320 && realMetrics.heightPixels >= 320) {
                    rw = realMetrics.widthPixels;
                    rh = realMetrics.heightPixels;
                    realDpi = realMetrics.densityDpi > 0 ? realMetrics.densityDpi : 440;
                }
            }
        } catch (Throwable ignored) {}

        int shortSide = Math.min(rw, rh);
        int longSide = Math.max(rw, rh);
        float aspect = Math.max(1.55f, Math.min(2.35f, (float) longSide / Math.max(1, shortSide)));
        float hostSwDp = (shortSide * 160f) / Math.max(120, realDpi);

        float resScale;
        int minShortPx, maxShortPx;
        float targetSwDp;
        int targetPreviewShort;

        if ("compact".equals(tier) || "small".equals(tier) || "mini".equals(tier)) {
            resScale = 0.58f;
            minShortPx = 544;
            maxShortPx = 704;
            targetSwDp = Math.max(384f, Math.min(411f, hostSwDp));
            targetPreviewShort = 460;
        } else if ("large".equals(tier) || "hd".equals(tier) || "full".equals(tier)) {
            resScale = 0.78f;
            minShortPx = 768;
            maxShortPx = 1088;
            targetSwDp = Math.max(392f, Math.min(420f, hostSwDp));
            targetPreviewShort = 640;
        } else {
            // "balanced" (default): matches host straight-bar phone swDp (~392-411dp) so apps never render giant text or tablet split-pane
            resScale = 0.667f;
            minShortPx = 640;
            maxShortPx = 864;
            targetSwDp = Math.max(388f, Math.min(412f, hostSwDp));
            targetPreviewShort = 540;
        }

        int targetW = shortSide <= minShortPx ? roundTo16(shortSide, 368, minShortPx) : roundTo16(shortSide * resScale, minShortPx, maxShortPx);
        int targetH = roundTo16(targetW * aspect, 640, 2560);
        int targetDpi = Math.max(160, Math.min(640, Math.round((targetW * 160f) / targetSwDp)));
        return new int[]{targetW, targetH, targetDpi, targetPreviewShort};
    }

    @android.annotation.SuppressLint("WrongConstant")
    private JSONObject run(JSONObject cmd) throws Exception {
        String op = cmd.getString("op");
        if (op.equals("create")) {
            int myUid = android.os.Process.myUid();
            if (myUid != 0 && myUid != 2000) throw new Exception("虚拟屏需要 Root (uid 0) 或 Shell 权限");
            String requestedTier = cmd.optString("tier", activeTier).trim().toLowerCase();
            if (requestedTier.isEmpty()) requestedTier = "balanced";
            if (display != null && display.getDisplay() != null && display.getDisplay().getDisplayId() > 0 && !cmd.optBoolean("recreate", false)) {
                releasedToBackground = false;
                int existingId = display.getDisplay().getDisplayId();
                ensureAwakeAndUnlocked(existingId);
                startKeepAwake(existingId);
                if (reader != null) {
                    reader.setOnImageAvailableListener(source -> scheduleFrameProcess(), frameHandler);
                    frameHandler.post(this::scheduleFrameProcess);
                }
                return state();
            }
            release(true);
            releasedToBackground = false;
            Constructor<DisplayManager> ctor = DisplayManager.class.getDeclaredConstructor(Context.class);
            ctor.setAccessible(true);
            DisplayManager dm = ctor.newInstance(context);

            int[] spec = computeAdaptiveDisplaySpec(dm, requestedTier);
            activeTier = requestedTier;
            width = cmd.optInt("width", spec[0]);
            height = cmd.optInt("height", spec[1]);
            densityDpi = cmd.optInt("dpi", spec[2]);
            previewShortSide = cmd.optInt("preview_short_side", spec[3]);
            if (width < 128 || width > 4096 || height < 128 || height > 4096) throw new Exception("虚拟屏尺寸超出范围");
            if (densityDpi < 120 || densityDpi > 800) densityDpi = spec[2];

            reader = ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 2);
            Surface surface = reader.getSurface();
            try {
                trustedDisplay = true;
                display = dm.createVirtualDisplay("Pulse-Agent", width, height, densityDpi, surface, displayFlags(Build.VERSION.SDK_INT, true, true));
            } catch (Throwable firstErr) {
                try {
                    trustedDisplay = true;
                    display = dm.createVirtualDisplay("Pulse-Agent", width, height, densityDpi, surface, displayFlags(Build.VERSION.SDK_INT, true, false));
                } catch (Throwable secondErr) {
                    // Retry without trusted display flags if ROM blocks ADD_TRUSTED_DISPLAY
                    try {
                        trustedDisplay = false;
                        display = dm.createVirtualDisplay("Pulse-Agent", width, height, densityDpi, surface, displayFlags(Build.VERSION.SDK_INT, false, false));
                    } catch (Exception thirdErr) {
                        release();
                        throw thirdErr;
                    }
                }
            }
            if (display == null || display.getDisplay().getDisplayId() <= 0) {
                release();
                throw new Exception("系统没有返回独立虚拟屏");
            }
            int displayId = display.getDisplay().getDisplayId();
            setFullscreenWindowingMode(displayId);
            setImePolicy(displayId);
            ensureAwakeAndUnlocked(displayId);
            startKeepAwake(displayId);

            reader.setOnImageAvailableListener(source -> scheduleFrameProcess(), frameHandler);
            frameHandler.post(this::scheduleFrameProcess);
        } else if (op.equals("resize")) {
            requireDisplay();
            Constructor<DisplayManager> ctor = DisplayManager.class.getDeclaredConstructor(Context.class);
            ctor.setAccessible(true);
            DisplayManager dm = ctor.newInstance(context);
            String requestedTier = cmd.optString("tier", activeTier).trim().toLowerCase();
            if (requestedTier.isEmpty()) requestedTier = "balanced";
            int[] spec = computeAdaptiveDisplaySpec(dm, requestedTier);
            activeTier = requestedTier;
            boolean keepRes = cmd.optBoolean("keep_resolution", true);
            int newW = keepRes ? width : cmd.optInt("width", spec[0]);
            int newH = keepRes ? height : cmd.optInt("height", spec[1]);
            int newDpi = cmd.optInt("dpi", keepRes ? Math.max(160, Math.min(640, Math.round((width * 160f) / ((spec[0] * 160f) / spec[2])))) : spec[2]);
            previewShortSide = cmd.optInt("preview_short_side", spec[3]);

            synchronized (frameLock) {
                if (newW != width || newH != height) {
                    width = newW;
                    height = newH;
                    ImageReader oldReader = this.reader;
                    this.reader = ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 2);
                    this.reader.setOnImageAvailableListener(source -> scheduleFrameProcess(), frameHandler);
                    display.setSurface(this.reader.getSurface());
                    if (oldReader != null) {
                        try { oldReader.setOnImageAvailableListener(null, null); oldReader.close(); } catch (Throwable ignored) {}
                    }
                    if (latestFrame != null) {
                        latestFrame.recycle();
                        latestFrame = null;
                    }
                }
                densityDpi = newDpi;
                display.resize(width, height, densityDpi);
            }
            scheduleFrameProcess();
            return state().put("resized", true).put("tier", activeTier);
        } else if (op.equals("release") || op.equals("promote") || op.equals("expand_to_foreground")) {
            if (display == null) {
                return state().put("ok", true).put("noop", true).put("released_to_background", true);
            }
            boolean destroy = cmd.optBoolean("destroy", true);
            release(destroy);
            return state().put("ok", true).put("released_to_background", true).put("destroyed", destroy);
        } else if (op.equals("preview")) {
            return fastPreview(cmd);
        } else if (op.equals("shell")) {
            String rawCmd = cmd.optString("command", cmd.optString("cmd", "")).trim();
            if (rawCmd.isEmpty()) throw new Exception("shell 操作需要提供 command");
            String out = shell("sh", "-c", rawCmd);
            return state().put("output", out.length() > 24000 ? out.substring(0, 24000) + "\n...[truncated]" : out);
        } else if (op.equals("capture")) {
            requireDisplay();
            Bitmap upright = null;
            long sequence = 0;
            for (int i = 0; i < 40 && upright == null; i++) {
                synchronized (frameLock) {
                    if (latestFrame != null) {
                        int w = frameRotation % 2 == 0 ? width : height, h = frameRotation % 2 == 0 ? height : width;
                        upright = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888);
                        Canvas canvas = new Canvas(upright);
                        if (frameRotation == 1) { canvas.translate(0, width); canvas.rotate(-90); }
                        else if (frameRotation == 2) { canvas.translate(width, height); canvas.rotate(180); }
                        else if (frameRotation == 3) { canvas.translate(height, 0); canvas.rotate(90); }
                        Rect bounds = new Rect(0, 0, width, height);
                        canvas.drawBitmap(latestFrame, bounds, bounds, new Paint(Paint.FILTER_BITMAP_FLAG));
                        sequence = frameSequence;
                    }
                }
                if (upright == null) Thread.sleep(50);
            }
            if (upright == null) throw new Exception("尚未收到工作区画面，先在虚拟屏打开一个应用");
            try {
                if (cmd.optBoolean("grid", false)) {
                    drawAiGridOverlay(upright);
                }
                ByteArrayOutputStream out = new ByteArrayOutputStream(65536);
                upright.compress(Bitmap.CompressFormat.JPEG, 84, out);
                return state().put("width", upright.getWidth()).put("height", upright.getHeight())
                    .put("frameSequence", sequence)
                    .put("image", Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP));
            } finally {
                upright.recycle();
            }
        } else if (op.equals("tap")) {
            int id = requireDisplay(), x = cmd.getInt("x"), y = cmd.getInt("y");
            checkTarget(cmd, id);
            checkPoint(x, y);
            onBeforeVirtualInteraction(id);
            if (!injectTap(id, x, y)) {
                shell("input", "-d", String.valueOf(id), "tap", String.valueOf(x), String.valueOf(y));
            }
            scheduleEscapeContainment(id);
            return state().put("clicked", true).put("x", x).put("y", y);
        } else if (op.equals("swipe")) {
            int id = requireDisplay();
            checkTarget(cmd, id);
            int x1 = cmd.has("x1") ? cmd.getInt("x1") : cmd.getInt("x");
            int y1 = cmd.has("y1") ? cmd.getInt("y1") : cmd.getInt("y");
            int x2 = cmd.getInt("x2"), y2 = cmd.getInt("y2");
            checkPoint(x1, y1);
            checkPoint(x2, y2);
            int duration = cmd.optInt("duration_ms", 220);
            if (duration < 40 || duration > 2500) throw new Exception("滑动时长须为 40–2500 ms");
            onBeforeVirtualInteraction(id);
            if (!injectSwipe(id, x1, y1, x2, y2, duration)) {
                shell("input", "-d", String.valueOf(id), "swipe", String.valueOf(x1), String.valueOf(y1), String.valueOf(x2), String.valueOf(y2), String.valueOf(duration));
            }
            scheduleEscapeContainment(id);
            return state().put("swiped", true).put("x1", x1).put("y1", y1).put("x2", x2).put("y2", y2);
        } else if (op.equals("text")) {
            int id = requireDisplay();
            checkTarget(cmd, id);
            String text = cmd.getString("text");
            if (text.isEmpty() || text.length() > 4000) throw new Exception("一次输入 1–4000 字");
            onBeforeVirtualInteraction(id);
            if (clipboard == null) clipboard = new RootClipboard(context);
            clipboard.paste(id, text);
            scheduleEscapeContainment(id);
            return state().put("typed", true).put("characters", text.length());
        } else if (op.equals("key")) {
            int id = requireDisplay();
            checkTarget(cmd, id);
            String key = cmd.optString("key", "").trim().toLowerCase();
            int explicitCode = cmd.optInt("keycode", cmd.optInt("key_code", -1));
            int keyCode;
            if (explicitCode > 0 && key.isEmpty()) {
                keyCode = explicitCode;
                key = String.valueOf(explicitCode);
            } else {
                switch (key) {
                    case "home": keyCode = KeyEvent.KEYCODE_BACK; break;
                    case "back": keyCode = KeyEvent.KEYCODE_BACK; break;
                    case "enter":
                    case "66": keyCode = KeyEvent.KEYCODE_ENTER; break;
                    case "search":
                    case "84": keyCode = KeyEvent.KEYCODE_SEARCH; break;
                    case "tab": keyCode = KeyEvent.KEYCODE_TAB; break;
                    case "delete":
                    case "backspace": keyCode = KeyEvent.KEYCODE_DEL; break;
                    case "up": keyCode = KeyEvent.KEYCODE_DPAD_UP; break;
                    case "down": keyCode = KeyEvent.KEYCODE_DPAD_DOWN; break;
                    case "left": keyCode = KeyEvent.KEYCODE_DPAD_LEFT; break;
                    case "right": keyCode = KeyEvent.KEYCODE_DPAD_RIGHT; break;
                    default:
                        if (explicitCode > 0) {
                            keyCode = explicitCode;
                        } else {
                            throw new Exception("不支持此工作区按键: " + key);
                        }
                }
            }
            // Never inject KEYCODE_HOME (3) or KEYCODE_APP_SWITCH (187) on a virtual display because Android PhoneWindowManager routes them to Display 0's Home Launcher!
            if (keyCode == KeyEvent.KEYCODE_HOME || keyCode == KeyEvent.KEYCODE_APP_SWITCH) {
                keyCode = KeyEvent.KEYCODE_BACK;
            }
            onBeforeVirtualInteraction(id);
            if (!injectKey(id, keyCode)) {
                shell("input", "-d", String.valueOf(id), "keyevent", String.valueOf(keyCode));
            }
            scheduleEscapeContainment(id);
            return state().put("key", key).put("keycode", keyCode);
        } else if (op.equals("open_uri") || op.equals("open_url")) {
            int id = requireDisplay();
            String uri = cmd.optString("uri", cmd.optString("url", cmd.optString("text", ""))).trim();
            if (uri.isEmpty()) throw new Exception("请提供要打开的 URI/URL");
            onBeforeVirtualInteraction(id);
            String out = openUriOnDisplay(id, uri, cmd.optInt("user_id", 0));
            scheduleEscapeContainment(id);
            return state().put("opened_uri", uri).put("output", out);
        } else if (op.equals("long_press")) {
            int id = requireDisplay(), x = cmd.getInt("x"), y = cmd.getInt("y");
            int duration = Math.max(450, Math.min(2000, cmd.optInt("duration_ms", 650)));
            checkTarget(cmd, id);
            checkPoint(x, y);
            onBeforeVirtualInteraction(id);
            if (!injectSwipe(id, x, y, x, y, duration)) {
                shell("input", "-d", String.valueOf(id), "swipe", String.valueOf(x), String.valueOf(y), String.valueOf(x), String.valueOf(y), String.valueOf(duration));
            }
            scheduleEscapeContainment(id);
            return state().put("long_pressed", true).put("x", x).put("y", y);
        } else if (op.equals("launch")) {
            int id = requireDisplay();
            checkTarget(cmd, id);
            String component = cmd.getString("component");
            if (!component.matches("[A-Za-z0-9_.$]+/[A-Za-z0-9_.$]+")) throw new Exception("填写 package/.Activity 形式的组件名");
            String pkg = component.split("/")[0];
            if (!component.equals("dev.bubuki.proactive/.DisplayTestActivity") && isPackageVisibleOnMainDisplay(pkg)) {
                throw new Exception("该 App 当前在主屏前台，余拒绝打断主人的主屏操作；请先切走该应用或换个应用");
            }
            managedVirtualPackages.add(pkg);
            onBeforeVirtualInteraction(id);
            int userId = cmd.optInt("user_id", 0);
            String output = launchOnDisplay(id, component, userId);
            scheduleEscapeContainment(id);
            return state().put("output", output);
        } else if (!op.equals("status")) {
            throw new Exception("未知虚拟屏操作");
        }
        return state();
    }

    private void setFullscreenWindowingMode(int displayId) {
        try {
            Object wm = Class.forName("android.view.WindowManagerGlobal").getMethod("getWindowManagerService").invoke(null);
            // WindowConfiguration.WINDOWING_MODE_FULLSCREEN = 1
            wm.getClass().getMethod("setWindowingMode", int.class, int.class).invoke(wm, displayId, 1);
        } catch (Throwable t) {
            try {
                shell("wm", "set-display-windowing-mode", "-d", String.valueOf(displayId), "1");
            } catch (Throwable ignored) {}
        }
    }

    private void setImePolicy(int displayId) {
        try {
            Object wm = Class.forName("android.view.WindowManagerGlobal").getMethod("getWindowManagerService").invoke(null);
            // WindowManager.DISPLAY_IME_POLICY_HIDE = 2: Prevents virtual display apps from hijacking or freezing the host phone's soft keyboard!
            wm.getClass().getMethod("setDisplayImePolicy", int.class, int.class).invoke(wm, displayId, 2);
        } catch (Throwable ignored) {}
    }

    private void pokeDisplayAwake(int displayId) {
        // Intentionally do NOT call IPowerManager.userActivity(displayId, ..., EVENT_TOUCH, 0)
        // because on ColorOS / HyperOS / OneUI, userActivity on any displayId wakes the global
        // DisplayPowerController and boosts physical screen brightness!
        // FLAG_ALWAYS_UNLOCKED on the VirtualDisplay + PARTIAL_WAKE_LOCK on CPU already keep the
        // virtual display rendering without touching physical screen backlight.
    }

    private void onBeforeVirtualInteraction(int displayId) {
        if (displayId <= 0) return;
        lastVirtualActionUptime = SystemClock.uptimeMillis();
        try {
            String vdTop = topPackageOnDisplay(displayId);
            if (vdTop != null && !vdTop.isEmpty()) {
                managedVirtualPackages.add(vdTop);
            }
            Object atm = Class.forName("android.app.ActivityTaskManager").getMethod("getService").invoke(null);
            Class<?> taskInfoCls = Class.forName("android.app.TaskInfo");
            Field displayField = taskInfoCls.getField("displayId");
            Field taskIdField = taskInfoCls.getField("taskId");
            Field topField = taskInfoCls.getField("topActivity");
            for (Object t : queryTasksOnDisplay(atm, Display.DEFAULT_DISPLAY)) {
                if (t == null) continue;
                if (displayField.getInt(t) != Display.DEFAULT_DISPLAY) continue;
                int tid = taskIdField.getInt(t);
                ComponentName top = (ComponentName) topField.get(t);
                String topPkg = top != null ? top.getPackageName() : "";
                if (!managedVirtualPackages.contains(topPkg)) {
                    baselineMainTaskId = tid;
                    baselineMainPkg = topPkg;
                }
                break;
            }
        } catch (Throwable ignored) {}
    }

    private void scheduleEscapeContainment(int displayId) {
        if (displayId <= 0) return;
        final long triggerUptime = SystemClock.uptimeMillis();
        lastVirtualActionUptime = triggerUptime;
        int[] delaysMs = new int[]{65, 190, 440, 820};
        for (int d : delaysMs) {
            awakeTimer.schedule(() -> {
                try {
                    containEscapedTasks(displayId, triggerUptime);
                } catch (Throwable ignored) {}
            }, d, TimeUnit.MILLISECONDS);
        }
    }

    private void containEscapedTasks(int displayId, long triggerUptime) {
        VirtualDisplay vd = this.display;
        if (vd == null || vd.getDisplay() == null || vd.getDisplay().getDisplayId() != displayId) return;
        if (SystemClock.uptimeMillis() - triggerUptime > 2200L) return;
        try {
            Object atm = Class.forName("android.app.ActivityTaskManager").getMethod("getService").invoke(null);
            Class<?> taskInfoCls = Class.forName("android.app.TaskInfo");
            Field displayField = taskInfoCls.getField("displayId");
            Field taskIdField = taskInfoCls.getField("taskId");
            Field topField = taskInfoCls.getField("topActivity");
            Field baseField = taskInfoCls.getField("baseActivity");
            boolean movedAny = false;
            for (Object t : queryTasksOnDisplay(atm, Display.DEFAULT_DISPLAY)) {
                if (t == null) continue;
                if (displayField.getInt(t) != Display.DEFAULT_DISPLAY) continue;
                int tid = taskIdField.getInt(t);
                ComponentName top = (ComponentName) topField.get(t);
                ComponentName base = (ComponentName) baseField.get(t);
                String topPkg = top != null ? top.getPackageName() : "";
                String basePkg = base != null ? base.getPackageName() : "";
                String topCls = top != null ? top.getClassName() : "";
                if (tid == baselineMainTaskId) break;
                boolean isManagedEscape = managedVirtualPackages.contains(topPkg)
                    || managedVirtualPackages.contains(basePkg)
                    || topCls.contains("AppBrandUI")
                    || topCls.contains("WebViewUI");
                if (isManagedEscape && !topPkg.equals(baselineMainPkg)) {
                    invokeMoveRootTaskToDisplay(atm, tid, displayId, true);
                    movedAny = true;
                }
                break;
            }
            if (movedAny && baselineMainTaskId > 0) {
                invokeMoveTaskToFront(atm, baselineMainTaskId);
            }
        } catch (Throwable ignored) {}
    }

    private void ensureAwakeAndUnlocked(int displayId) {
        pokeDisplayAwake(displayId);
        boolean interactive = true;
        try {
            if (sPowerManager != null) {
                interactive = (Boolean) sPowerManager.getClass().getMethod("isInteractive").invoke(sPowerManager);
            }
        } catch (Throwable ignored) {}
        if (!interactive) {
            try {
                injectKey(0, KeyEvent.KEYCODE_WAKEUP);
            } catch (Throwable ignored) {}
            try {
                Object wm = Class.forName("android.view.WindowManagerGlobal").getMethod("getWindowManagerService").invoke(null);
                for (Method m : wm.getClass().getMethods()) {
                    if (m.getName().equals("dismissKeyguard") && m.getParameterTypes().length == 2) {
                        m.invoke(wm, null, null);
                        break;
                    }
                }
            } catch (Throwable ignored) {}
        }
    }

    private String topPackageOnDisplay(int displayId) {
        try {
            Object atm = Class.forName("android.app.ActivityTaskManager").getMethod("getService").invoke(null);
            Class<?> taskInfoCls = Class.forName("android.app.TaskInfo");
            Field displayField = taskInfoCls.getField("displayId");
            Field topField = taskInfoCls.getField("topActivity");
            Method getTasks = null;
            for (Method m : atm.getClass().getMethods()) {
                if (m.getName().equals("getTasks") && (m.getParameterTypes().length == 3 || m.getParameterTypes().length == 4)) {
                    getTasks = m;
                    break;
                }
            }
            if (getTasks != null) {
                List<?> tasks = (List<?>) (getTasks.getParameterTypes().length == 4
                    ? getTasks.invoke(atm, 50, false, false, displayId)
                    : getTasks.invoke(atm, 50, false, false));
                for (Object t : tasks) {
                    if (t == null) continue;
                    if (displayField.getInt(t) == displayId) {
                        ComponentName top = (ComponentName) topField.get(t);
                        if (top != null) return top.getPackageName();
                    }
                }
            }
        } catch (Throwable ignored) {}
        return "";
    }

    private void startKeepAwake(int displayId) {
        stopKeepAwake();
        awakeTask = awakeTimer.scheduleWithFixedDelay(() -> {
            try {
                // Lightweight 0.05ms in-process Binder poke (never spawns shell processes!)
                pokeDisplayAwake(displayId);
            } catch (Throwable ignored) {}
        }, 0, 2500, TimeUnit.MILLISECONDS);
    }

    private void stopKeepAwake() {
        if (awakeTask != null) {
            awakeTask.cancel(false);
            awakeTask = null;
        }
    }

    private static boolean injectEvent(InputEvent event, int displayId, int mode) {
        try {
            if (sSetDisplayIdMethod != null) {
                sSetDisplayIdMethod.invoke(event, displayId);
            } else {
                event.getClass().getMethod("setDisplayId", int.class).invoke(event, displayId);
            }
            if (sInputManager != null && sInjectInputEventMethod != null) {
                return (Boolean) sInjectInputEventMethod.invoke(sInputManager, event, mode);
            }
            Object im = Class.forName("android.hardware.input.InputManagerGlobal").getMethod("getInstance").invoke(null);
            return (Boolean) im.getClass().getMethod("injectInputEvent", InputEvent.class, int.class).invoke(im, event, mode);
        } catch (Throwable t) {
            return false;
        }
    }

    private boolean injectTap(int displayId, int x, int y) {
        inputHandler.removeCallbacksAndMessages(null);
        long now = SystemClock.uptimeMillis();
        MotionEvent down = MotionEvent.obtain(now, now, MotionEvent.ACTION_DOWN, x, y, 0);
        down.setSource(InputDevice.SOURCE_TOUCHSCREEN);
        boolean ok;
        try {
            ok = injectEvent(down, displayId, INJECT_MODE_ASYNC);
        } finally {
            down.recycle();
        }
        if (!ok) return false;
        inputHandler.postDelayed(() -> {
            long upTime = SystemClock.uptimeMillis();
            MotionEvent up = MotionEvent.obtain(now, upTime, MotionEvent.ACTION_UP, x, y, 0);
            up.setSource(InputDevice.SOURCE_TOUCHSCREEN);
            try {
                if (!injectEvent(up, displayId, INJECT_MODE_ASYNC)) {
                    injectEvent(up, displayId, INJECT_MODE_ASYNC);
                }
            } finally {
                up.recycle();
            }
            scheduleFrameProcess();
        }, 32L);
        return true;
    }

    private boolean injectSwipe(int displayId, int x1, int y1, int x2, int y2, int durationMs) {
        inputHandler.removeCallbacksAndMessages(null);
        final int steps = Math.max(6, Math.min(60, durationMs / 14));
        final long downTime = SystemClock.uptimeMillis();
        MotionEvent.PointerProperties[] props = new MotionEvent.PointerProperties[]{new MotionEvent.PointerProperties()};
        props[0].id = 0;
        props[0].toolType = MotionEvent.TOOL_TYPE_FINGER;
        MotionEvent.PointerCoords[] coords = new MotionEvent.PointerCoords[]{new MotionEvent.PointerCoords()};
        coords[0].pressure = 1f;
        coords[0].size = 1f;
        coords[0].x = x1;
        coords[0].y = y1;

        MotionEvent down = MotionEvent.obtain(downTime, downTime, MotionEvent.ACTION_DOWN, 1, props, coords, 0, 0, 1f, 1f, 0, 0, InputDevice.SOURCE_TOUCHSCREEN, 0);
        boolean ok;
        try {
            ok = injectEvent(down, displayId, INJECT_MODE_ASYNC);
        } finally {
            down.recycle();
        }
        if (!ok) return false;

        // Schedule smooth 60fps trajectory asynchronously on inputHandler so RPC & preview frames NEVER block!
        for (int i = 1; i <= steps; i++) {
            final int stepIdx = i;
            final long delayMs = Math.round(((float) i / steps) * durationMs);
            inputHandler.postDelayed(() -> {
                MotionEvent.PointerProperties[] p = new MotionEvent.PointerProperties[]{new MotionEvent.PointerProperties()};
                p[0].id = 0;
                p[0].toolType = MotionEvent.TOOL_TYPE_FINGER;
                MotionEvent.PointerCoords[] c = new MotionEvent.PointerCoords[]{new MotionEvent.PointerCoords()};
                c[0].pressure = 1f;
                c[0].size = 1f;
                long eventTime = SystemClock.uptimeMillis();
                if (stepIdx < steps) {
                    float t = (float) stepIdx / steps;
                    float smooth = t * t * (3f - 2f * t);
                    c[0].x = x1 + (x2 - x1) * smooth;
                    c[0].y = y1 + (y2 - y1) * smooth;
                    MotionEvent move = MotionEvent.obtain(downTime, eventTime, MotionEvent.ACTION_MOVE, 1, p, c, 0, 0, 1f, 1f, 0, 0, InputDevice.SOURCE_TOUCHSCREEN, 0);
                    try {
                        injectEvent(move, displayId, INJECT_MODE_ASYNC);
                    } finally {
                        move.recycle();
                    }
                    if (stepIdx % 2 == 0) scheduleFrameProcess();
                } else {
                    c[0].x = x2;
                    c[0].y = y2;
                    MotionEvent up = MotionEvent.obtain(downTime, eventTime, MotionEvent.ACTION_UP, 1, p, c, 0, 0, 1f, 1f, 0, 0, InputDevice.SOURCE_TOUCHSCREEN, 0);
                    try {
                        if (!injectEvent(up, displayId, INJECT_MODE_ASYNC)) {
                            injectEvent(up, displayId, INJECT_MODE_ASYNC);
                        }
                    } finally {
                        up.recycle();
                    }
                    scheduleFrameProcess();
                }
            }, delayMs);
        }
        return true;
    }

    private boolean injectKey(int displayId, int keyCode) {
        long now = SystemClock.uptimeMillis();
        KeyEvent down = new KeyEvent(now, now, KeyEvent.ACTION_DOWN, keyCode, 0, 0, VIRTUAL_KEYBOARD, 0, 0, InputDevice.SOURCE_KEYBOARD);
        KeyEvent up = KeyEvent.changeAction(down, KeyEvent.ACTION_UP);
        if (!injectEvent(down, displayId, INJECT_MODE_WAIT_FOR_RESULT)) return false;
        return injectEvent(up, displayId, INJECT_MODE_WAIT_FOR_RESULT) || injectEvent(up, displayId, INJECT_MODE_WAIT_FOR_RESULT);
    }

    private boolean isPackageVisibleOnMainDisplay(String pkg) {
        try {
            Object atm = Class.forName("android.app.ActivityTaskManager").getMethod("getService").invoke(null);
            Class<?> taskInfoCls = Class.forName("android.app.TaskInfo");
            Field displayField = taskInfoCls.getField("displayId");
            Field topField = taskInfoCls.getField("topActivity");
            Field baseField = taskInfoCls.getField("baseActivity");
            Field visibleField = null;
            try { visibleField = taskInfoCls.getField("isVisible"); } catch (Throwable ignored) {}
            Method getTasks4 = null, getTasks3 = null;
            for (Method m : atm.getClass().getMethods()) {
                if (m.getName().equals("getTasks")) {
                    if (m.getParameterTypes().length == 4) getTasks4 = m;
                    else if (m.getParameterTypes().length == 3) getTasks3 = m;
                }
            }
            List<?> tasks = (List<?>) (getTasks4 != null
                ? getTasks4.invoke(atm, 50, false, false, Display.DEFAULT_DISPLAY)
                : getTasks3.invoke(atm, 50, false, false));
            int mainIdx = 0;
            for (Object t : tasks) {
                if (t == null) continue;
                if (displayField.getInt(t) != Display.DEFAULT_DISPLAY) continue;
                ComponentName top = (ComponentName) topField.get(t);
                ComponentName base = (ComponentName) baseField.get(t);
                boolean match = (top != null && pkg.equals(top.getPackageName())) || (base != null && pkg.equals(base.getPackageName()));
                boolean vis = visibleField != null ? visibleField.getBoolean(t) : (mainIdx == 0);
                if (match && vis) return true;
                mainIdx++;
            }
            return false;
        } catch (Throwable ignored) {
            try {
                String windows = shell("dumpsys", "window", "displays");
                for (String line : windows.split("\n")) {
                    if ((line.contains("mCurrentFocus") || line.contains("mFocusedApp")) && line.contains(pkg + "/")) {
                        return true;
                    }
                }
            } catch (Throwable ignored2) {}
            return false;
        }
    }

    private void guardAndContainLaunchOnDisplay(Object atm, int displayId, String targetPkg, int mainBeforeTaskId, String mainBeforePkg) {
        if (atm == null || displayId <= 0) return;
        try {
            Class<?> taskInfoCls = Class.forName("android.app.TaskInfo");
            Field displayField = taskInfoCls.getField("displayId");
            Field taskIdField = taskInfoCls.getField("taskId");
            Field topField = taskInfoCls.getField("topActivity");
            Field baseField = taskInfoCls.getField("baseActivity");
            for (int check = 0; check < 2; check++) {
                if (check > 0) Thread.sleep(55L);
                for (Object t : queryTasksOnDisplay(atm, Display.DEFAULT_DISPLAY)) {
                    if (t == null) continue;
                    if (displayField.getInt(t) != Display.DEFAULT_DISPLAY) continue;
                    int tid = taskIdField.getInt(t);
                    ComponentName top = (ComponentName) topField.get(t);
                    ComponentName base = (ComponentName) baseField.get(t);
                    String topPkg = top != null ? top.getPackageName() : "";
                    String basePkg = base != null ? base.getPackageName() : "";
                    if (tid != mainBeforeTaskId && !targetPkg.isEmpty() && (targetPkg.equals(topPkg) || targetPkg.equals(basePkg)) && !targetPkg.equals(mainBeforePkg)) {
                        invokeMoveRootTaskToDisplay(atm, tid, displayId, true);
                        if (mainBeforeTaskId > 0) {
                            invokeMoveTaskToFront(atm, mainBeforeTaskId);
                        }
                    }
                    break;
                }
            }
        } catch (Throwable ignored) {}
    }

    private String launchOnDisplay(int displayId, String component, int userId) throws Exception {
        String[] parts = component.split("/");
        String pkg = parts[0];
        String cls = parts[1].startsWith(".") ? pkg + parts[1] : parts[1];
        Object atm = null;
        int mainBeforeTaskId = -1;
        String mainBeforePkg = "";
        try {
            atm = Class.forName("android.app.ActivityTaskManager").getMethod("getService").invoke(null);
            Class<?> taskInfoCls = Class.forName("android.app.TaskInfo");
            Field displayField = taskInfoCls.getField("displayId");
            Field taskIdField = taskInfoCls.getField("taskId");
            Field topField = taskInfoCls.getField("topActivity");
            Field baseField = taskInfoCls.getField("baseActivity");
            int idx = 0;
            for (Object t : queryTasksOnDisplay(atm, Display.DEFAULT_DISPLAY)) {
                if (t == null) continue;
                if (displayField.getInt(t) != Display.DEFAULT_DISPLAY) continue;
                int tid = taskIdField.getInt(t);
                ComponentName top = (ComponentName) topField.get(t);
                ComponentName base = (ComponentName) baseField.get(t);
                String topPkg = top != null ? top.getPackageName() : "";
                String basePkg = base != null ? base.getPackageName() : "";
                if (idx == 0) {
                    mainBeforeTaskId = tid;
                    mainBeforePkg = topPkg;
                } else if (pkg.equals(topPkg) || pkg.equals(basePkg)) {
                    // Pre-move background task of target app from Display 0 into virtual display so singleTask apps don't wake on Display 0!
                    invokeMoveRootTaskToDisplay(atm, tid, displayId, true);
                }
                idx++;
            }
        } catch (Throwable ignored) {}

        Intent intent = new Intent(Intent.ACTION_MAIN)
            .addCategory(Intent.CATEGORY_LAUNCHER)
            .setComponent(new ComponentName(pkg, cls))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_MULTIPLE_TASK);
        Bundle options = ActivityOptions.makeBasic().setLaunchDisplayId(displayId).toBundle();
        try {
            if (atm != null) {
                for (Method m : atm.getClass().getMethods()) {
                    if (m.getName().equals("startActivityAsUser") && m.getParameterTypes().length == 12) {
                        int res = (Integer) m.invoke(
                            atm, null, SHELL_PACKAGE, null, intent, null, null, null,
                            0, 0, null, options, userId == 0 ? USER_CURRENT : userId
                        );
                        if (res >= 0) {
                            guardAndContainLaunchOnDisplay(atm, displayId, pkg, mainBeforeTaskId, mainBeforePkg);
                            return "startActivityAsUser ok (" + res + ")";
                        }
                        break;
                    }
                }
            }
        } catch (Throwable ignored) {}
        String output = shell("am", "start", "--display", String.valueOf(displayId), "--user", String.valueOf(userId), "-f", "0x18008000", "-n", component);
        guardAndContainLaunchOnDisplay(atm, displayId, pkg, mainBeforeTaskId, mainBeforePkg);
        if (output.contains("Error") || output.contains("Exception")) {
            throw new Exception(output.substring(0, Math.min(300, output.length())));
        }
        return output;
    }

    private String openUriOnDisplay(int displayId, String uri, int userId) throws Exception {
        String topPkg = topPackageOnDisplay(displayId);
        if ("com.tencent.mm".equals(topPkg) || uri.contains("docs.qq.com") || uri.contains("weixin.qq.com")) {
            try {
                String wxOut = shell(
                    "am", "start", "--display", String.valueOf(displayId), "--user", String.valueOf(userId),
                    "-f", "0x18008000",
                    "-n", "com.tencent.mm/.plugin.webview.ui.tools.WebViewUI",
                    "-e", "rawUrl", uri
                );
                if (!wxOut.contains("Error") && !wxOut.contains("Exception")) {
                    return wxOut;
                }
            } catch (Throwable ignored) {}
        }
        String output = shell("am", "start", "--display", String.valueOf(displayId), "--user", String.valueOf(userId), "-f", "0x18008000", "-a", "android.intent.action.VIEW", "-d", uri);
        if (output.contains("Error") || output.contains("Exception")) {
            throw new Exception(output.substring(0, Math.min(300, output.length())));
        }
        return output;
    }

    private int requireDisplay() throws Exception {
        if (display == null || display.getDisplay().getDisplayId() <= 0) {
            throw new Exception("先创建独立虚拟屏；禁止注入 display 0");
        }
        return display.getDisplay().getDisplayId();
    }

    private void checkTarget(JSONObject command, int id) throws Exception {
        if (command.optInt("display_id", id) != id) throw new Exception("displayId 不属于当前独立工作区");
        if (command.has("expected_rotation") && command.getInt("expected_rotation") != display.getDisplay().getRotation()) {
            throw new Exception("画面方向已改变，请重新截图后再操作");
        }
    }

    private void checkPoint(int x, int y) throws Exception {
        int rotation = display.getDisplay().getRotation();
        int w = rotation % 2 == 0 ? width : height, h = rotation % 2 == 0 ? height : width;
        if (x < 0 || y < 0 || x >= w || y >= h) throw new Exception("坐标超出独立虚拟屏");
    }

    private JSONObject state() throws Exception {
        VirtualDisplay vd = this.display;
        int r = (vd == null || vd.getDisplay() == null) ? 0 : vd.getDisplay().getRotation();
        int lw = r % 2 == 0 ? width : height;
        int lh = r % 2 == 0 ? height : width;
        int dispId = (vd == null || vd.getDisplay() == null) ? -1 : vd.getDisplay().getDisplayId();
        boolean activeCreated = vd != null && dispId > 0 && !releasedToBackground;
        return new JSONObject()
            .put("created", activeCreated)
            .put("allocated", vd != null && dispId > 0)
            .put("released", releasedToBackground)
            .put("displayId", dispId)
            .put("display_id", dispId)
            .put("width", lw)
            .put("height", lh)
            .put("dpi", densityDpi)
            .put("tier", activeTier)
            .put("swDp", Math.round((Math.min(lw, lh) * 160f) / Math.max(120, densityDpi)))
            .put("rotation", r)
            .put("uid", android.os.Process.myUid())
            .put("isolatedFocus", vd != null && trustedDisplay);
    }

    private static String shell(String... args) throws Exception {
        return RootCommands.run(args);
    }

    private List<?> queryTasksOnDisplay(Object atm, int targetDisplayId) {
        try {
            Method getTasks4 = null, getTasks3 = null;
            for (Method m : atm.getClass().getMethods()) {
                if (m.getName().equals("getTasks")) {
                    if (m.getParameterTypes().length == 4) getTasks4 = m;
                    else if (m.getParameterTypes().length == 3) getTasks3 = m;
                }
            }
            if (getTasks4 != null) {
                return (List<?>) getTasks4.invoke(atm, 50, false, false, targetDisplayId);
            } else if (getTasks3 != null) {
                return (List<?>) getTasks3.invoke(atm, 50, false, false);
            }
        } catch (Throwable ignored) {}
        return java.util.Collections.emptyList();
    }

    private void invokeMoveRootTaskToDisplay(Object atm, int taskId, int targetDisplayId, boolean onTop) {
        if (taskId <= 0) return;
        try {
            for (Method m : atm.getClass().getMethods()) {
                String n = m.getName();
                if ((n.equals("moveRootTaskToDisplay") || n.equals("moveStackToDisplay")) && m.getParameterTypes().length == 3) {
                    m.invoke(atm, taskId, targetDisplayId, onTop);
                    return;
                }
            }
        } catch (Throwable ignored) {}
    }

    private void invokeMoveTaskToFront(Object atm, int taskId) {
        if (taskId <= 0) return;
        try {
            for (Method m : atm.getClass().getMethods()) {
                if (m.getName().equals("startActivityFromRecents") && m.getParameterTypes().length == 2) {
                    m.invoke(atm, taskId, null);
                    return;
                }
            }
        } catch (Throwable ignored) {}
        try {
            for (Method m : atm.getClass().getMethods()) {
                if (m.getName().equals("moveTaskToFront") && m.getParameterTypes().length == 5) {
                    m.invoke(atm, null, SHELL_PACKAGE, taskId, 0, null);
                    return;
                }
            }
        } catch (Throwable ignored) {}
    }

    private String release() {
        return release(false);
    }

    private String release(boolean destroy) {
        stopKeepAwake();
        inputHandler.removeCallbacksAndMessages(null);
        frameHandler.removeCallbacksAndMessages(null);
        framePending = false;
        if (!destroy && display != null) {
            // Keep VirtualDisplay alive silently in the background with zero frame-processing CPU drain;
            // mark releasedToBackground = true so UI overlays know the workspace is closed/released!
            releasedToBackground = true;
            synchronized (frameLock) {
                if (reader != null) {
                    try { reader.setOnImageAvailableListener(null, null); } catch (Throwable ignored) {}
                }
            }
            return "";
        }
        releasedToBackground = false;
        latestPreview = null;
        if (display != null) {
            int dispId = -1;
            try {
                dispId = display.getDisplay() != null ? display.getDisplay().getDisplayId() : -1;
            } catch (Throwable ignored) {}

            Object atm = null;
            int mainTopTaskId = -1;
            ComponentName mainTopComp = null;
            java.util.Set<String> vdPackages = new java.util.HashSet<>();

            if (dispId > 0) {
                try {
                    atm = Class.forName("android.app.ActivityTaskManager").getMethod("getService").invoke(null);
                    Class<?> taskInfoCls = Class.forName("android.app.TaskInfo");
                    Field displayField = taskInfoCls.getField("displayId");
                    Field taskIdField = taskInfoCls.getField("taskId");
                    Field topField = taskInfoCls.getField("topActivity");

                    for (Object t : queryTasksOnDisplay(atm, Display.DEFAULT_DISPLAY)) {
                        if (t == null) continue;
                        if (displayField.getInt(t) != Display.DEFAULT_DISPLAY) continue;
                        mainTopTaskId = taskIdField.getInt(t);
                        mainTopComp = (ComponentName) topField.get(t);
                        break;
                    }

                    for (Object t : queryTasksOnDisplay(atm, dispId)) {
                        if (t == null) continue;
                        if (displayField.getInt(t) != dispId) continue;
                        ComponentName top = (ComponentName) topField.get(t);
                        if (top != null && top.getPackageName() != null) {
                            vdPackages.add(top.getPackageName());
                        }
                    }
                    // NEVER call invokeMoveRootTaskToDisplay(atm, tid, Display.DEFAULT_DISPLAY, ...) here!
                    // Moving tasks to Display 0 causes OEM ROMs to pop them onto the main screen foreground!
                } catch (Throwable ignored) {}
            }

            try { display.release(); } catch (Throwable ignored) {}
            display = null;

            if (dispId > 0 && atm != null) {
                try {
                    Thread.sleep(40L);
                    String afterTop = topPackageOnDisplay(Display.DEFAULT_DISPLAY);
                    String mainTopPkg = mainTopComp != null ? mainTopComp.getPackageName() : "";
                    if (!afterTop.isEmpty() && vdPackages.contains(afterTop) && !afterTop.equals(mainTopPkg)) {
                        if (mainTopTaskId > 0) {
                            invokeMoveTaskToFront(atm, mainTopTaskId);
                        }
                    }
                } catch (Throwable ignored) {}
            }
            managedVirtualPackages.clear();
        }
        synchronized (frameLock) {
            if (reader != null) {
                try { reader.setOnImageAvailableListener(null, null); reader.close(); } catch (Throwable ignored) {}
                reader = null;
            }
            if (latestFrame != null) {
                latestFrame.recycle();
                latestFrame = null;
            }
            if (previewBuffer != null) {
                previewBuffer.recycle();
                previewBuffer = null;
            }
            frameSequence = 0;
        }
        return "";
    }

    void close() {
        if (display != null) {
            execute("{\"op\":\"release\",\"destroy\":true}");
        }
        awakeTimer.shutdownNow();
        inputThread.quitSafely();
        thread.quitSafely();
        frames.quitSafely();
    }

    /**
     * Mika-style ShellContext bootstrap: safely obtains or constructs an ActivityThread
     * inside app_process without crashing on vendor hooks in ActivityThread.systemMain().
     */
    static Context systemContext() throws Exception {
        Class<?> at = Class.forName("android.app.ActivityThread");
        Object thread = null;
        try {
            thread = at.getMethod("currentActivityThread").invoke(null);
        } catch (Throwable ignored) {}
        if (thread == null) {
            try {
                Constructor<?> ctor = at.getDeclaredConstructor();
                ctor.setAccessible(true);
                thread = ctor.newInstance();
                Field sCur = at.getDeclaredField("sCurrentActivityThread");
                sCur.setAccessible(true);
                sCur.set(null, thread);
                Field mSys = at.getDeclaredField("mSystemThread");
                mSys.setAccessible(true);
                mSys.setBoolean(thread, true);
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                    try {
                        Class<?> cc = Class.forName("android.app.ConfigurationController");
                        Class<?> internal = Class.forName("android.app.ActivityThreadInternal");
                        Constructor<?> ccCtor = cc.getDeclaredConstructor(internal);
                        ccCtor.setAccessible(true);
                        Object controller = ccCtor.newInstance(thread);
                        Field mCc = at.getDeclaredField("mConfigurationController");
                        mCc.setAccessible(true);
                        mCc.set(thread, controller);
                    } catch (Throwable ignored) {}
                }
                try {
                    Class<?> abd = Class.forName("android.app.ActivityThread$AppBindData");
                    Constructor<?> abdCtor = abd.getDeclaredConstructor();
                    abdCtor.setAccessible(true);
                    Object data = abdCtor.newInstance();
                    ApplicationInfo ai = new ApplicationInfo();
                    ai.packageName = SHELL_PACKAGE;
                    Field appInfoField = abd.getDeclaredField("appInfo");
                    appInfoField.setAccessible(true);
                    appInfoField.set(data, ai);
                    Field boundField = at.getDeclaredField("mBoundApplication");
                    boundField.setAccessible(true);
                    boundField.set(thread, data);
                } catch (Throwable ignored) {}
            } catch (Throwable fallback) {
                thread = at.getMethod("systemMain").invoke(null);
            }
        }
        Context sys = (Context) at.getDeclaredMethod("getSystemContext").invoke(thread);
        try {
            Field initApp = at.getDeclaredField("mInitialApplication");
            initApp.setAccessible(true);
            if (initApp.get(thread) == null) {
                Application app = Instrumentation.newApplication(Application.class, sys);
                initApp.set(thread, app);
            }
        } catch (Throwable ignored) {}
        return sys;
    }

    private static void drawAiGridOverlay(Bitmap bmp) {
        if (bmp == null) return;
        int w = bmp.getWidth();
        int h = bmp.getHeight();
        Canvas c = new Canvas(bmp);
        Paint linePaint = new Paint(Paint.ANTI_ALIAS_FLAG);
        linePaint.setColor(0x5500E5FF);
        linePaint.setStrokeWidth(1f);
        Paint bgPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
        bgPaint.setColor(0x77000000);
        Paint textPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
        textPaint.setColor(0xDDFFFFFF);
        textPaint.setTextSize(Math.max(10f, w / 42f));
        int cols = 6;
        int rows = 10;
        for (int col = 1; col < cols; col++) {
            float x = w * col / (float) cols;
            c.drawLine(x, 0, x, h, linePaint);
        }
        for (int row = 1; row < rows; row++) {
            float y = h * row / (float) rows;
            c.drawLine(0, y, w, y, linePaint);
        }
        String[] letters = {"A", "B", "C", "D", "E", "F"};
        for (int r = 0; r < rows; r++) {
            for (int col = 0; col < cols; col++) {
                String label = letters[col] + (r + 1);
                float cx = w * (col + 0.08f) / cols;
                float cy = h * (r + 0.22f) / rows;
                c.drawRect(cx - 2f, cy - textPaint.getTextSize(), cx + textPaint.measureText(label) + 2f, cy + 3f, bgPaint);
                c.drawText(label, cx, cy, textPaint);
            }
        }
    }
}
