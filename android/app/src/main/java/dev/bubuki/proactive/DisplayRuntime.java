package dev.bubuki.proactive;

import android.content.*;
import android.content.res.Resources;
import android.graphics.*;
import android.hardware.display.*;
import android.media.Image;
import android.media.ImageReader;
import android.os.*;
import android.util.Base64;
import android.view.Display;
import android.view.InputDevice;
import android.view.MotionEvent;
import android.view.Surface;
import org.json.*;
import java.io.*;
import java.lang.reflect.*;
import java.util.concurrent.*;

/** VirtualDisplay lifecycle, ImageReader frame pipeline, JSON RPC dispatcher & containment. */
@android.annotation.SuppressLint({"PrivateApi", "DiscouragedPrivateApi", "BlockedPrivateApi", "SoonBlockedPrivateApi", "WrongConstant"})
final class DisplayRuntime {
    private static final String SHELL_PACKAGE = DisplayInputHelper.SHELL_PACKAGE;
    private static final int SHELL_UID = DisplayInputHelper.SHELL_UID;

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
    private volatile long frameSequence;
    private boolean framePending = false;
    private long lastFrameProcessedAt = 0L;
    private static final long FRAME_INTERVAL_ACTIVE_MS = 45L; // ~22fps while user finger touches PiP
    private static final long FRAME_INTERVAL_IDLE_MS = 180L;  // ~5.5fps lightweight buffer copy when not touching
    private volatile int previewShortSide = 460;
    private volatile long liveTouchDownTime = 0L;
    private volatile long lastLiveTouchAt = 0L;

    private static final class PreviewSnapshot {
        final long sequence;
        final int width;
        final int height;
        final int rotation;
        final String base64;
        final String frameHash;
        PreviewSnapshot(long sequence, int width, int height, int rotation, String base64, String frameHash) {
            this.sequence = sequence;
            this.width = width;
            this.height = height;
            this.rotation = rotation;
            this.base64 = base64;
            this.frameHash = frameHash;
        }
    }
    private volatile PreviewSnapshot latestPreview = null;

    private volatile boolean trustedDisplay = true;
    private volatile boolean releasedToBackground = false;
    private RootClipboard clipboard;
    private volatile int width = 1472, height = 3232, densityDpi = 480;
    private volatile String activeTier = "compact";

    // Sandbox task containment & main-display anti-jump state
    private final java.util.Set<String> managedVirtualPackages = java.util.Collections.synchronizedSet(new java.util.HashSet<>());
    private volatile int baselineMainTaskId = -1;
    private volatile String baselineMainPkg = "";
    private volatile boolean baselineMainIsHome = true;
    private volatile long lastVirtualActionUptime = 0L;
    private volatile int lastPromotedTaskId = -1;
    private volatile String lastPromotedPkg = "";

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
            @Override public Resources getResources() {
                Resources sys = Resources.getSystem();
                try {
                    android.content.res.Configuration cfg = new android.content.res.Configuration(sys.getConfiguration());
                    cfg.densityDpi = densityDpi;
                    cfg.fontScale = 1.0f;
                    cfg.screenWidthDp = Math.round((Math.min(width, height) * 160f) / Math.max(120, densityDpi));
                    cfg.smallestScreenWidthDp = cfg.screenWidthDp;
                    android.util.DisplayMetrics dm = new android.util.DisplayMetrics();
                    dm.setTo(sys.getDisplayMetrics());
                    dm.densityDpi = densityDpi;
                    dm.density = densityDpi / 160f;
                    dm.scaledDensity = dm.density;
                    dm.widthPixels = width;
                    dm.heightPixels = height;
                    return new Resources(sys.getAssets(), dm, cfg);
                } catch (Throwable ignored) {
                    return sys;
                }
            }
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
        if (cmd.optBoolean("fresh", false)) processLatestFrame();
        long since = cmd.optLong("since", -1);
        long curSeq = this.frameSequence;
        PreviewSnapshot snap = this.latestPreview;
        if (since >= 0 && curSeq > 0 && curSeq == since && snap != null && snap.sequence == curSeq) {
            return state().put("hasFrame", true).put("unchanged", true).put("frameSequence", curSeq);
        }
        if (snap == null || snap.sequence != curSeq) {
            snap = encodeLatestPreviewOnDemand();
        }
        if (snap == null) {
            return state().put("hasFrame", false).put("frameSequence", 0);
        }
        if (since >= 0 && snap.sequence == since) {
            return state().put("hasFrame", true).put("unchanged", true).put("frameSequence", snap.sequence);
        }
        return state()
            .put("hasFrame", true)
            .put("width", snap.width)
            .put("height", snap.height)
            .put("rotation", snap.rotation)
            .put("frameSequence", snap.sequence)
            .put("frameHash", snap.frameHash)
            .put("image", snap.base64);
    }

    private static String computeLuminanceHash64(Bitmap bmp) {
        if (bmp == null || bmp.getWidth() < 8 || bmp.getHeight() < 8) return "";
        int w = bmp.getWidth(), h = bmp.getHeight();
        int[] luma = new int[64];
        int sum = 0, idx = 0;
        for (int gy = 0; gy < 8; gy++) {
            int py = Math.min(h - 1, (gy * h + h / 2) / 8);
            for (int gx = 0; gx < 8; gx++) {
                int px = Math.min(w - 1, (gx * w + w / 2) / 8);
                int c = bmp.getPixel(px, py);
                int y = (((c >> 16) & 0xFF) * 77 + ((c >> 8) & 0xFF) * 150 + (c & 0xFF) * 29) >> 8;
                luma[idx++] = y;
                sum += y;
            }
        }
        int avg = sum / 64;
        long bits = 0L;
        for (int i = 0; i < 64; i++) {
            if (luma[i] >= avg) bits |= (1L << i);
        }
        return String.format("%016x", bits);
    }

    private PreviewSnapshot encodeLatestPreviewOnDemand() {
        synchronized (frameLock) {
            if (latestFrame == null || display == null) return latestPreview;
            long seq = frameSequence;
            if (latestPreview != null && latestPreview.sequence == seq) return latestPreview;
            int rot = frameRotation;
            int bufW = this.width;
            int bufH = this.height;
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
            String fHash = computeLuminanceHash64(previewBuffer);
            ByteArrayOutputStream out = new ByteArrayOutputStream(20480);
            previewBuffer.compress(Bitmap.CompressFormat.JPEG, 72, out);
            String b64 = Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP);
            PreviewSnapshot snap = new PreviewSnapshot(seq, logicalW, logicalH, rot, b64, fHash);
            this.latestPreview = snap;
            return snap;
        }
    }

    private JSONObject fastTouch(JSONObject cmd) throws Exception {
        VirtualDisplay vd = this.display;
        if (vd == null || vd.getDisplay() == null || vd.getDisplay().getDisplayId() <= 0) {
            return state().put("ok", false);
        }
        releasedToBackground = false;
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
                lastLiveTouchAt = now;
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
                    DisplayInputHelper.injectEvent(ev, displayId, DisplayInputHelper.INJECT_MODE_ASYNC);
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

    private void onFrameAvailable(ImageReader source) {
        if (releasedToBackground || display == null) {
            try (Image img = source.acquireLatestImage()) {} catch (Throwable ignored) {}
            return;
        }
        scheduleFrameProcess();
    }

    private void scheduleFrameProcess() {
        if (framePending || display == null || releasedToBackground) return;
        framePending = true;
        long now = SystemClock.uptimeMillis();
        long interval = (now - lastLiveTouchAt < 1200L) ? FRAME_INTERVAL_ACTIVE_MS : FRAME_INTERVAL_IDLE_MS;
        long wait = lastFrameProcessedAt + interval - now;
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
            if (image == null || releasedToBackground) return;
            int rot = vd.getDisplay().getRotation();
            int bufH = this.height;
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
                frameSequence++;
            }
        } catch (Throwable ignored) {}
    }

    @android.annotation.SuppressLint("WrongConstant")
    private JSONObject run(JSONObject cmd) throws Exception {
        String op = cmd.getString("op");
        if (op.equals("create")) {
            int myUid = android.os.Process.myUid();
            if (myUid != 0 && myUid != 2000) throw new Exception("虚拟屏需要 Root (uid 0) 或 Shell 权限");
            String requestedTier = cmd.optString("tier", activeTier).trim().toLowerCase();
            if (requestedTier.isEmpty()) requestedTier = "compact";
            Constructor<DisplayManager> ctor = DisplayManager.class.getDeclaredConstructor(Context.class);
            ctor.setAccessible(true);
            DisplayManager dm = ctor.newInstance(context);
            int[] spec = DisplayInputHelper.computeAdaptiveDisplaySpec(dm, requestedTier);
            int wantW = cmd.optInt("width", spec[0]);
            int wantH = cmd.optInt("height", spec[1]);
            int wantDpi = cmd.optInt("dpi", spec[2]);
            if (wantDpi < 120 || wantDpi > 800) wantDpi = spec[2];

            if (display != null && display.getDisplay() != null && display.getDisplay().getDisplayId() > 0 && !cmd.optBoolean("recreate", false)) {
                releasedToBackground = false;
                activeTier = requestedTier;
                previewShortSide = cmd.optInt("preview_short_side", spec[3]);
                int existingId = display.getDisplay().getDisplayId();
                if (wantW != width || wantH != height || wantDpi != densityDpi) {
                    // Never resize an existing VirtualDisplay in-place when dimensions or DPI change:
                    // running activities keep stale WindowMetrics/BufferQueue sizes. Release & recreate cleanly.
                    release(true);
                } else {
                    DisplayInputHelper.ensureAwakeAndUnlocked(existingId);
                    startKeepAwake(existingId);
                    if (!cmd.optBoolean("skip_home", false)) {
                        String curTop = DisplayInputHelper.topPackageOnDisplay(existingId);
                        if (curTop == null || curTop.isEmpty() || "com.android.launcher".equals(curTop)) {
                            try {
                                DisplayInputHelper.launchOnDisplay(existingId, "dev.bubuki.proactive/.DisplayTestActivity", 0);
                            } catch (Throwable ignored) {}
                        }
                    }
                    if (reader != null) {
                        reader.setOnImageAvailableListener(this::onFrameAvailable, frameHandler);
                        frameHandler.post(this::scheduleFrameProcess);
                    }
                    return state().put("newly_created", false).put("reused_display", true);
                }
            }
            release(true);
            releasedToBackground = false;

            activeTier = requestedTier;
            width = wantW;
            height = wantH;
            densityDpi = wantDpi;
            previewShortSide = cmd.optInt("preview_short_side", spec[3]);
            if (width < 128 || width > 4096 || height < 128 || height > 4096) throw new Exception("虚拟屏尺寸超出范围");

            reader = ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 2);
            Surface surface = reader.getSurface();
            try {
                trustedDisplay = true;
                display = dm.createVirtualDisplay("Pulse-Agent", width, height, densityDpi, surface, DisplayInputHelper.displayFlags(Build.VERSION.SDK_INT, true, true));
            } catch (Throwable firstErr) {
                try {
                    trustedDisplay = true;
                    display = dm.createVirtualDisplay("Pulse-Agent", width, height, densityDpi, surface, DisplayInputHelper.displayFlags(Build.VERSION.SDK_INT, true, false));
                } catch (Throwable secondErr) {
                    try {
                        trustedDisplay = false;
                        display = dm.createVirtualDisplay("Pulse-Agent", width, height, densityDpi, surface, DisplayInputHelper.displayFlags(Build.VERSION.SDK_INT, false, false, true));
                    } catch (Throwable thirdErr) {
                        try {
                            trustedDisplay = false;
                            display = dm.createVirtualDisplay("Pulse-Agent", width, height, densityDpi, surface, DisplayInputHelper.displayFlags(Build.VERSION.SDK_INT, false, false, false));
                        } catch (Exception fourthErr) {
                            release();
                            throw fourthErr;
                        }
                    }
                }
            }
            if (display == null || display.getDisplay().getDisplayId() <= 0) {
                release();
                throw new Exception("系统没有返回独立虚拟屏");
            }
            int displayId = display.getDisplay().getDisplayId();
            DisplayInputHelper.setDisplayRemoveModeDestroy(displayId);
            DisplayInputHelper.setFullscreenWindowingMode(displayId, width, height, densityDpi);
            DisplayInputHelper.setImePolicy(displayId);
            DisplayInputHelper.ensureAwakeAndUnlocked(displayId);
            startKeepAwake(displayId);
            if (!cmd.optBoolean("skip_home", false)) {
                try {
                    DisplayInputHelper.launchOnDisplay(displayId, "dev.bubuki.proactive/.DisplayTestActivity", 0);
                } catch (Throwable ignored) {}
            }

            reader.setOnImageAvailableListener(this::onFrameAvailable, frameHandler);
            frameHandler.post(this::scheduleFrameProcess);
            return state().put("newly_created", true).put("reused_display", false);
        } else if (op.equals("resize")) {
            requireDisplay();
            Constructor<DisplayManager> ctor = DisplayManager.class.getDeclaredConstructor(Context.class);
            ctor.setAccessible(true);
            DisplayManager dm = ctor.newInstance(context);
            String requestedTier = cmd.optString("tier", activeTier).trim().toLowerCase();
            if (requestedTier.isEmpty()) requestedTier = "compact";
            int[] spec = DisplayInputHelper.computeAdaptiveDisplaySpec(dm, requestedTier);
            activeTier = requestedTier;
            previewShortSide = cmd.optInt("preview_short_side", spec[3]);
            latestPreview = null;
            if (cmd.optBoolean("force_display_resize", false)) {
                int newW = cmd.optInt("width", spec[0]);
                int newH = cmd.optInt("height", spec[1]);
                int newDpi = cmd.optInt("dpi", spec[2]);
                synchronized (frameLock) {
                    if (newW != width || newH != height) {
                        width = newW;
                        height = newH;
                        ImageReader oldReader = this.reader;
                        this.reader = ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 2);
                        this.reader.setOnImageAvailableListener(this::onFrameAvailable, frameHandler);
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
                DisplayInputHelper.setFullscreenWindowingMode(display.getDisplay().getDisplayId(), width, height, densityDpi);
            }
            scheduleFrameProcess();
            return state().put("resized", true).put("tier", activeTier);
        } else if (op.equals("promote") || op.equals("expand_to_foreground") || op.equals("to_foreground") || op.equals("fullscreen")) {
            if (display == null || display.getDisplay() == null || display.getDisplay().getDisplayId() <= 0) {
                return state().put("ok", false).put("error", "虚拟屏未启动");
            }
            int id = display.getDisplay().getDisplayId();
            String topPkg = DisplayInputHelper.topPackageOnDisplay(id);
            if (topPkg != null && !topPkg.isEmpty()) {
                managedVirtualPackages.remove(topPkg);
            }
            lastVirtualActionUptime = 0L;
            int[] res = DisplayInputHelper.promoteTopTaskToMainDisplay(id);
            if (res[1] == 1) {
                lastPromotedTaskId = res[0];
                lastPromotedPkg = topPkg != null ? topPkg : "";
            }
            releasedToBackground = false;
            return state()
                .put("ok", res[1] == 1)
                .put("promoted", res[1] == 1)
                .put("promoted_task_id", res[0])
                .put("promoted_package", lastPromotedPkg)
                .put("kept_alive", true);
        } else if (op.equals("pull_back") || op.equals("to_virtual")) {
            if (display == null || display.getDisplay() == null || display.getDisplay().getDisplayId() <= 0) {
                return state().put("ok", false).put("error", "虚拟屏未启动");
            }
            int id = display.getDisplay().getDisplayId();
            String wantPkg = cmd.optString("package", cmd.optString("pkg", lastPromotedPkg)).trim();
            int wantTid = cmd.optInt("task_id", lastPromotedTaskId);
            int pulledTid = DisplayInputHelper.pullTaskFromMainToVirtualDisplay(id, wantTid, wantPkg);
            if (pulledTid > 0) {
                String nowTop = DisplayInputHelper.topPackageOnDisplay(id);
                if (nowTop != null && !nowTop.isEmpty() && !"dev.bubuki.proactive".equals(nowTop)) {
                    managedVirtualPackages.add(nowTop);
                }
                lastPromotedTaskId = -1;
                lastPromotedPkg = "";
            }
            scheduleFrameProcess();
            return state()
                .put("ok", pulledTid > 0)
                .put("pulled_back", pulledTid > 0)
                .put("pulled_task_id", pulledTid);
        } else if (op.equals("release")) {
            if (display == null) {
                return state().put("ok", true).put("noop", true).put("released", true).put("destroyed", true).put("released_to_background", true);
            }
            boolean keepAlive = cmd.optBoolean("keep_alive", false);
            boolean destroy = !keepAlive || cmd.optBoolean("destroy", false) || cmd.optBoolean("user_close", false);
            if (!destroy) {
                releasedToBackground = false;
                return state().put("ok", true).put("released_to_background", false).put("kept_alive", true);
            }
            release(true);
            return state().put("ok", true).put("released", true).put("released_to_background", true).put("destroyed", true);
        } else if (op.equals("preview")) {
            return fastPreview(cmd);
        } else if (op.equals("shell")) {
            String rawCmd = cmd.optString("command", cmd.optString("cmd", "")).trim();
            if (rawCmd.isEmpty()) throw new Exception("shell 操作需要提供 command");
            int activeVdId = (display != null && display.getDisplay() != null) ? display.getDisplay().getDisplayId() : -1;
            boolean touchesUi = activeVdId > 0 && (rawCmd.contains("am ") || rawCmd.contains("input") || rawCmd.contains("cmd activity") || rawCmd.contains("monkey"));
            if (touchesUi) onBeforeVirtualInteraction(activeVdId);
            JSONObject shRes = RootCommands.runShell(rawCmd, cmd.optInt("timeout_sec", 18));
            if (touchesUi) {
                scheduleEscapeContainment(activeVdId);
                scheduleFrameProcess();
            }
            return shRes;
        } else if (op.equals("clipboard") || op.equals("clipboard_get") || op.equals("clipboard_set")) {
            boolean isSet = op.equals("clipboard_set")
                || "set".equalsIgnoreCase(cmd.optString("action", ""))
                || cmd.has("text") || cmd.has("value");
            if (isSet) {
                String text = cmd.optString("text", cmd.optString("value", ""));
                clipboard.setText(text);
                return new JSONObject()
                    .put("uid", android.os.Process.myUid())
                    .put("ok", true)
                    .put("op", "clipboard_set")
                    .put("length", text.length());
            } else {
                String text = clipboard.getText();
                return new JSONObject()
                    .put("uid", android.os.Process.myUid())
                    .put("ok", true)
                    .put("op", "clipboard_get")
                    .put("text", text);
            }
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
            int logicalW = upright.getWidth();
            int logicalH = upright.getHeight();
            Bitmap encodeBmp = upright;
            try {
                int minDim = Math.min(logicalW, logicalH);
                if (minDim > 720) {
                    float capScale = 720f / (float) minDim;
                    int encW = Math.max(1, Math.round(logicalW * capScale));
                    int encH = Math.max(1, Math.round(logicalH * capScale));
                    encodeBmp = Bitmap.createScaledBitmap(upright, encW, encH, true);
                }
                if (cmd.optBoolean("grid", false)) {
                    DisplayInputHelper.drawAiGridOverlay(encodeBmp);
                }
                ByteArrayOutputStream out = new ByteArrayOutputStream(65536);
                encodeBmp.compress(Bitmap.CompressFormat.JPEG, 84, out);
                return state().put("width", logicalW).put("height", logicalH)
                    .put("frameSequence", sequence)
                    .put("image", Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP));
            } finally {
                if (encodeBmp != null && encodeBmp != upright) {
                    encodeBmp.recycle();
                }
                upright.recycle();
            }
        } else if (op.equals("tap")) {
            int id = requireDisplay(), x = cmd.getInt("x"), y = cmd.getInt("y");
            checkTarget(cmd, id);
            checkPoint(x, y);
            onBeforeVirtualInteraction(id);
            if (!DisplayInputHelper.injectTap(inputHandler, this::scheduleFrameProcess, id, x, y)) {
                DisplayInputHelper.shell("input", "-d", String.valueOf(id), "tap", String.valueOf(x), String.valueOf(y));
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
            if (!DisplayInputHelper.injectSwipe(inputHandler, this::scheduleFrameProcess, id, x1, y1, x2, y2, duration)) {
                DisplayInputHelper.shell("input", "-d", String.valueOf(id), "swipe", String.valueOf(x1), String.valueOf(y1), String.valueOf(x2), String.valueOf(y2), String.valueOf(duration));
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
            if ("home".equals(key) || explicitCode == android.view.KeyEvent.KEYCODE_HOME) {
                onBeforeVirtualInteraction(id);
                DisplayInputHelper.launchOnDisplay(id, "dev.bubuki.proactive/.DisplayTestActivity", 0);
                scheduleFrameProcess();
                return state().put("key", "home").put("keycode", android.view.KeyEvent.KEYCODE_HOME).put("home_launched", true);
            }
            int keyCode = DisplayInputHelper.resolveKeyCode(key, explicitCode);
            if (key.isEmpty() && explicitCode > 0) key = String.valueOf(explicitCode);
            onBeforeVirtualInteraction(id);
            if (!DisplayInputHelper.injectKey(id, keyCode)) {
                DisplayInputHelper.shell("input", "-d", String.valueOf(id), "keyevent", String.valueOf(keyCode));
            }
            scheduleEscapeContainment(id);
            return state().put("key", key).put("keycode", keyCode);
        } else if (op.equals("open_uri") || op.equals("open_url")) {
            int id = requireDisplay();
            String uri = cmd.optString("uri", cmd.optString("url", cmd.optString("text", ""))).trim();
            if (uri.isEmpty()) throw new Exception("请提供要打开的 URI/URL");
            onBeforeVirtualInteraction(id);
            String out = DisplayInputHelper.openUriOnDisplay(id, uri, cmd.optInt("user_id", 0));
            scheduleEscapeContainment(id);
            return state().put("opened_uri", uri).put("output", out);
        } else if (op.equals("long_press")) {
            int id = requireDisplay(), x = cmd.getInt("x"), y = cmd.getInt("y");
            int duration = Math.max(450, Math.min(2000, cmd.optInt("duration_ms", 650)));
            checkTarget(cmd, id);
            checkPoint(x, y);
            onBeforeVirtualInteraction(id);
            if (!DisplayInputHelper.injectSwipe(inputHandler, this::scheduleFrameProcess, id, x, y, x, y, duration)) {
                DisplayInputHelper.shell("input", "-d", String.valueOf(id), "swipe", String.valueOf(x), String.valueOf(y), String.valueOf(x), String.valueOf(y), String.valueOf(duration));
            }
            scheduleEscapeContainment(id);
            return state().put("long_pressed", true).put("x", x).put("y", y);
        } else if (op.equals("launch")) {
            int id = requireDisplay();
            checkTarget(cmd, id);
            String component = cmd.getString("component");
            if (!component.matches("[A-Za-z0-9_.$]+/[A-Za-z0-9_.$]+")) throw new Exception("填写 package/.Activity 形式的组件名");
            if (component.startsWith("dev.bubuki.proactive/") && !component.endsWith(".DisplayTestActivity")) {
                throw new Exception("禁止将本客户端主界面移入虚拟屏");
            }
            String pkg = component.split("/")[0];
            onBeforeVirtualInteraction(id);
            if (!"dev.bubuki.proactive".equals(pkg)) {
                managedVirtualPackages.add(pkg);
            }
            int userId = cmd.optInt("user_id", 0);
            String output = DisplayInputHelper.launchOnDisplay(id, component, userId);
            scheduleEscapeContainment(id);
            return state().put("output", output);
        } else if (!op.equals("status")) {
            throw new Exception("未知虚拟屏操作");
        }
        return state();
    }

    private void onBeforeVirtualInteraction(int displayId) {
        if (displayId <= 0) return;
        lastVirtualActionUptime = SystemClock.uptimeMillis();
        try {
            String vdTop = DisplayInputHelper.topPackageOnDisplay(displayId);
            if (vdTop != null && !vdTop.isEmpty() && !"dev.bubuki.proactive".equals(vdTop)) {
                managedVirtualPackages.add(vdTop);
            }
            Object atm = Class.forName("android.app.ActivityTaskManager").getMethod("getService").invoke(null);
            Class<?> taskInfoCls = Class.forName("android.app.TaskInfo");
            Field displayField = taskInfoCls.getField("displayId");
            Field taskIdField = taskInfoCls.getField("taskId");
            Field topField = taskInfoCls.getField("topActivity");
            for (Object t : DisplayInputHelper.queryTasksOnDisplay(atm, Display.DEFAULT_DISPLAY)) {
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
        int[] delaysMs = new int[]{70, 200, 450};
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
        if (SystemClock.uptimeMillis() - triggerUptime > 850L) return;
        try {
            Object atm = Class.forName("android.app.ActivityTaskManager").getMethod("getService").invoke(null);
            Class<?> taskInfoCls = Class.forName("android.app.TaskInfo");
            Field displayField = taskInfoCls.getField("displayId");
            Field taskIdField = taskInfoCls.getField("taskId");
            Field topField = taskInfoCls.getField("topActivity");
            Field baseField = taskInfoCls.getField("baseActivity");
            boolean movedAny = false;
            for (Object t : DisplayInputHelper.queryTasksOnDisplay(atm, Display.DEFAULT_DISPLAY)) {
                if (t == null) continue;
                if (displayField.getInt(t) != Display.DEFAULT_DISPLAY) continue;
                int tid = taskIdField.getInt(t);
                ComponentName top = (ComponentName) topField.get(t);
                ComponentName base = (ComponentName) baseField.get(t);
                String topPkg = top != null ? top.getPackageName() : "";
                String basePkg = base != null ? base.getPackageName() : "";
                if (tid == baselineMainTaskId || tid == lastPromotedTaskId) break;
                if ("dev.bubuki.proactive".equals(topPkg) || topPkg.contains("launcher") || topPkg.contains("home") || "com.android.systemui".equals(topPkg)) {
                    break;
                }
                // Strictly contain ONLY packages managed inside VirtualDisplay so user app switches on Display 0 are NEVER hijacked!
                boolean isManagedEscape = !topPkg.isEmpty() && !topPkg.equals(baselineMainPkg)
                    && (managedVirtualPackages.contains(topPkg) || managedVirtualPackages.contains(basePkg));
                if (isManagedEscape) {
                    int rootTid = DisplayInputHelper.extractRootTaskId(t, taskIdField);
                    if (DisplayInputHelper.invokeMoveRootTaskToDisplay(atm, rootTid, displayId, true)) {
                        managedVirtualPackages.add(topPkg);
                        movedAny = true;
                    }
                }
                break;
            }
            if (movedAny && baselineMainTaskId > 0 && !DisplayInputHelper.isHomeOrLauncherPackage(baselineMainPkg)) {
                String nowTop = DisplayInputHelper.topPackageOnDisplay(Display.DEFAULT_DISPLAY);
                if (DisplayInputHelper.isHomeOrLauncherPackage(nowTop)) {
                    DisplayInputHelper.invokeMoveTaskToFront(atm, baselineMainTaskId, Display.DEFAULT_DISPLAY);
                }
            }
        } catch (Throwable ignored) {}
    }

    private void startKeepAwake(int displayId) {
        stopKeepAwake();
        awakeTask = awakeTimer.scheduleWithFixedDelay(() -> {
            try {
                DisplayInputHelper.pokeDisplayAwake(displayId);
            } catch (Throwable ignored) {}
        }, 0, 2500, TimeUnit.MILLISECONDS);
    }

    private void stopKeepAwake() {
        if (awakeTask != null) {
            awakeTask.cancel(false);
            awakeTask = null;
        }
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
        int swDp = Math.round((Math.min(lw, lh) * 160f) / Math.max(120, densityDpi));
        return new JSONObject()
            .put("created", activeCreated)
            .put("display_active", activeCreated)
            .put("newly_created", false)
            .put("allocated", vd != null && dispId > 0)
            .put("released", releasedToBackground)
            .put("displayId", dispId)
            .put("display_id", dispId)
            .put("width", lw)
            .put("height", lh)
            .put("dpi", densityDpi)
            .put("densityDpi", densityDpi)
            .put("tier", activeTier)
            .put("swDp", swDp)
            .put("screenWidthDp", swDp)
            .put("fontScale", 1.0f)
            .put("rotation", r)
            .put("uid", android.os.Process.myUid())
            .put("isolatedFocus", vd != null && trustedDisplay);
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
            releasedToBackground = true;
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
                DisplayInputHelper.setDisplayRemoveModeDestroy(dispId);
                try {
                    atm = Class.forName("android.app.ActivityTaskManager").getMethod("getService").invoke(null);
                    Class<?> taskInfoCls = Class.forName("android.app.TaskInfo");
                    Field displayField = taskInfoCls.getField("displayId");
                    Field taskIdField = taskInfoCls.getField("taskId");
                    Field topField = taskInfoCls.getField("topActivity");

                    for (Object t : DisplayInputHelper.queryTasksOnDisplay(atm, Display.DEFAULT_DISPLAY)) {
                        if (t == null) continue;
                        if (displayField.getInt(t) != Display.DEFAULT_DISPLAY) continue;
                        mainTopTaskId = taskIdField.getInt(t);
                        mainTopComp = (ComponentName) topField.get(t);
                        break;
                    }

                    java.util.List<Integer> vdRootTasks = new java.util.ArrayList<>();
                    for (Object t : DisplayInputHelper.queryTasksOnDisplay(atm, dispId)) {
                        if (t == null) continue;
                        if (displayField.getInt(t) != dispId) continue;
                        ComponentName top = (ComponentName) topField.get(t);
                        if (top != null && top.getPackageName() != null) {
                            vdPackages.add(top.getPackageName());
                        }
                        int rtid = DisplayInputHelper.extractRootTaskId(t, taskIdField);
                        if (rtid > 0 && !vdRootTasks.contains(rtid)) {
                            vdRootTasks.add(rtid);
                        }
                    }
                    // Remove all tasks inside VirtualDisplay BEFORE releasing it so OEM WindowManager
                    // NEVER reparents WeChat/Alipay/DisplayTestActivity onto physical Display 0!
                    for (int rtid : vdRootTasks) {
                        DisplayInputHelper.invokeRemoveTask(atm, rtid);
                    }
                } catch (Throwable ignored) {}
            }

            try { display.release(); } catch (Throwable ignored) {}
            display = null;

            if (dispId > 0 && atm != null) {
                int[] checkWaits = new int[]{40, 90, 140};
                String mainTopPkg = mainTopComp != null ? mainTopComp.getPackageName() : "";
                for (int w : checkWaits) {
                    try {
                        Thread.sleep(w);
                        String afterTop = DisplayInputHelper.topPackageOnDisplay(Display.DEFAULT_DISPLAY);
                        if (!afterTop.isEmpty() && vdPackages.contains(afterTop) && !afterTop.equals(mainTopPkg)) {
                            if (mainTopTaskId > 0 && !DisplayInputHelper.isHomeOrLauncherPackage(mainTopPkg)) {
                                DisplayInputHelper.invokeMoveTaskToFront(atm, mainTopTaskId, Display.DEFAULT_DISPLAY);
                            }
                        } else {
                            break;
                        }
                    } catch (Throwable ignored) {}
                }
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

    static Context systemContext() throws Exception {
        return DisplayInputHelper.systemContext();
    }
}
