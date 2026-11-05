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
    private static final int FLAG_TRUSTED = 1 << 10;
    private static final int FLAG_OWN_DISPLAY_GROUP = 1 << 11;
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
    private static final long FRAME_INTERVAL_MS = 68L; // ~15fps coalesced cap (drops intermediate VSYNC frames in hardware)
    private static final int PREVIEW_SHORT_SIDE = 380;

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
    private RootClipboard clipboard;
    private volatile int width = 1080, height = 2400, densityDpi = 411;

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
        // Fast lock-free path for preview and status so frame polling NEVER queues behind tap/swipe/launch
        try {
            JSONObject fastCmd = new JSONObject(command);
            String fastOp = fastCmd.optString("op", "");
            if ("preview".equals(fastOp)) {
                return fastPreview(fastCmd).toString();
            }
            if ("status".equals(fastOp)) {
                return state().toString();
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
                float scale = Math.min(1f, (float) PREVIEW_SHORT_SIDE / Math.min(logicalW, logicalH));
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
                previewBuffer.compress(Bitmap.CompressFormat.JPEG, 80, out);
                String b64 = Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP);
                this.latestPreview = new PreviewSnapshot(seq, logicalW, logicalH, rot, b64);
            }
        } catch (Throwable ignored) {}
    }

    private static int displayFlags(int sdk, boolean trusted) {
        int flags = FLAG_PUBLIC | FLAG_OWN_CONTENT_ONLY | FLAG_SUPPORTS_TOUCH
            | FLAG_ROTATES_WITH_CONTENT | FLAG_DESTROY_CONTENT_ON_REMOVAL;
        if (sdk >= Build.VERSION_CODES.TIRAMISU) {
            flags |= FLAG_TOUCH_FEEDBACK_DISABLED;
            if (trusted) {
                // Deliberately omit FLAG_OWN_DISPLAY_GROUP so OEM ROMs (OriginOS / HyperOS / ColorOS)
                // do not create a separate display group that independently times out or locks with Keyguard.
                flags |= FLAG_TRUSTED | FLAG_ALWAYS_UNLOCKED;
            }
        }
        if (sdk >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE && trusted) {
            flags |= FLAG_OWN_FOCUS | FLAG_STEAL_TOP_FOCUS_DISABLED;
        }
        return flags;
    }

    @android.annotation.SuppressLint("WrongConstant")
    private JSONObject run(JSONObject cmd) throws Exception {
        String op = cmd.getString("op");
        if (op.equals("create")) {
            int myUid = android.os.Process.myUid();
            if (myUid != 0 && myUid != 2000) throw new Exception("虚拟屏需要 Root (uid 0) 或 Shell 权限");
            if (display != null && display.getDisplay() != null && display.getDisplay().getDisplayId() > 0) {
                pokeDisplayAwake(display.getDisplay().getDisplayId());
                return state();
            }
            release();
            Constructor<DisplayManager> ctor = DisplayManager.class.getDeclaredConstructor(Context.class);
            ctor.setAccessible(true);
            DisplayManager dm = ctor.newInstance(context);

            // Match main screen (Display.DEFAULT_DISPLAY) aspect ratio & pixel dimensions (like Mika's AgentDisplay)
            // so apps using Application context / Resources.getSystem() / AndroidAutoSize render 1:1 with normal font size!
            int defaultW = 1080, defaultH = 2400, defaultDpi = 411;
            try {
                Display mainDisp = dm.getDisplay(Display.DEFAULT_DISPLAY);
                if (mainDisp != null) {
                    android.util.DisplayMetrics realMetrics = new android.util.DisplayMetrics();
                    mainDisp.getRealMetrics(realMetrics);
                    int rw = realMetrics.widthPixels;
                    int rh = realMetrics.heightPixels;
                    if (rw >= 320 && rh >= 320) {
                        int shortSide = Math.min(rw, rh);
                        int longSide = Math.max(rw, rh);
                        // Cap buffer short side at 1080px for ultra-fast ImageReader DMA while keeping exact aspect ratio & dp
                        if (shortSide > 1080) {
                            float ratio = 1080f / shortSide;
                            defaultW = 1080;
                            defaultH = Math.round(longSide * ratio);
                            defaultDpi = Math.round(realMetrics.densityDpi * ratio);
                        } else {
                            defaultW = shortSide;
                            defaultH = longSide;
                            defaultDpi = realMetrics.densityDpi;
                        }
                        // Ensure logical smallestWidthDp is at least 420dp so fonts & UI inside virtual display never look oversized
                        int maxComfortDpi = Math.round(defaultW * 160f / 420f);
                        if (defaultDpi > maxComfortDpi) {
                            defaultDpi = maxComfortDpi;
                        }
                    }
                }
            } catch (Throwable ignored) {}

            width = cmd.optInt("width", defaultW);
            height = cmd.optInt("height", defaultH);
            densityDpi = cmd.optInt("dpi", defaultDpi);
            if (width < 128 || width > 4096 || height < 128 || height > 4096) throw new Exception("虚拟屏尺寸超出范围");
            if (densityDpi < 120 || densityDpi > 800) densityDpi = 411;

            reader = ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 2);
            Surface surface = reader.getSurface();
            try {
                trustedDisplay = true;
                display = dm.createVirtualDisplay("Pulse-Agent", width, height, densityDpi, surface, displayFlags(Build.VERSION.SDK_INT, true));
            } catch (Throwable firstErr) {
                // Retry without trusted display flags if ROM blocks ADD_TRUSTED_DISPLAY
                try {
                    trustedDisplay = false;
                    display = dm.createVirtualDisplay("Pulse-Agent", width, height, densityDpi, surface, displayFlags(Build.VERSION.SDK_INT, false));
                } catch (Exception secondErr) {
                    release();
                    throw secondErr;
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
        } else if (op.equals("release")) {
            release();
        } else if (op.equals("preview")) {
            return fastPreview(cmd);
        } else if (op.equals("shell")) {
            String rawCmd = cmd.optString("command", cmd.optString("cmd", "")).trim();
            if (rawCmd.isEmpty()) throw new Exception("shell 操作需要提供 command");
            String out = shell("sh", "-c", rawCmd);
            return state().put("output", out.length() > 24000 ? out.substring(0, 24000) + "\n...[truncated]" : out);
        } else if (op.equals("ui_tree_xml")) {
            int id = requireDisplay();
            String topPkg = topPackageOnDisplay(id);
            String xml = dumpTopViewHierarchyXml(topPkg);
            return state().put("xml", xml).put("topPackage", topPkg);
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
                ByteArrayOutputStream out = new ByteArrayOutputStream(65536);
                upright.compress(Bitmap.CompressFormat.JPEG, 86, out);
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
            if (!injectTap(id, x, y)) {
                shell("input", "-d", String.valueOf(id), "tap", String.valueOf(x), String.valueOf(y));
            }
            return state().put("clicked", true).put("x", x).put("y", y);
        } else if (op.equals("swipe")) {
            int id = requireDisplay();
            checkTarget(cmd, id);
            int x1 = cmd.has("x1") ? cmd.getInt("x1") : cmd.getInt("x");
            int y1 = cmd.has("y1") ? cmd.getInt("y1") : cmd.getInt("y");
            int x2 = cmd.getInt("x2"), y2 = cmd.getInt("y2");
            checkPoint(x1, y1);
            checkPoint(x2, y2);
            int duration = cmd.optInt("duration_ms", 320);
            if (duration < 50 || duration > 2500) throw new Exception("滑动时长须为 50–2500 ms");
            if (!injectSwipe(id, x1, y1, x2, y2, duration)) {
                shell("input", "-d", String.valueOf(id), "swipe", String.valueOf(x1), String.valueOf(y1), String.valueOf(x2), String.valueOf(y2), String.valueOf(duration));
            }
            return state().put("swiped", true).put("x1", x1).put("y1", y1).put("x2", x2).put("y2", y2);
        } else if (op.equals("text")) {
            int id = requireDisplay();
            checkTarget(cmd, id);
            String text = cmd.getString("text");
            if (text.isEmpty() || text.length() > 4000) throw new Exception("一次输入 1–4000 字");
            if (clipboard == null) clipboard = new RootClipboard(context);
            clipboard.paste(id, text);
            return state().put("typed", true).put("characters", text.length());
        } else if (op.equals("key")) {
            int id = requireDisplay();
            checkTarget(cmd, id);
            String key = cmd.getString("key").toLowerCase();
            int keyCode;
            switch (key) {
                case "home": keyCode = KeyEvent.KEYCODE_HOME; break;
                case "back": keyCode = KeyEvent.KEYCODE_BACK; break;
                case "enter": keyCode = KeyEvent.KEYCODE_ENTER; break;
                case "tab": keyCode = KeyEvent.KEYCODE_TAB; break;
                case "delete":
                case "backspace": keyCode = KeyEvent.KEYCODE_DEL; break;
                case "up": keyCode = KeyEvent.KEYCODE_DPAD_UP; break;
                case "down": keyCode = KeyEvent.KEYCODE_DPAD_DOWN; break;
                case "left": keyCode = KeyEvent.KEYCODE_DPAD_LEFT; break;
                case "right": keyCode = KeyEvent.KEYCODE_DPAD_RIGHT; break;
                default: throw new Exception("不支持此工作区按键");
            }
            if (!injectKey(id, keyCode)) {
                shell("input", "-d", String.valueOf(id), "keyevent", String.valueOf(keyCode));
            }
            return state().put("key", key);
        } else if (op.equals("launch")) {
            int id = requireDisplay();
            checkTarget(cmd, id);
            String component = cmd.getString("component");
            if (!component.matches("[A-Za-z0-9_.$]+/[A-Za-z0-9_.$]+")) throw new Exception("填写 package/.Activity 形式的组件名");
            String pkg = component.split("/")[0];
            if (!component.equals("dev.bubuki.proactive/.DisplayTestActivity") && isPackageVisibleOnMainDisplay(pkg)) {
                throw new Exception("该 App 当前在主屏前台，余拒绝打断主人的主屏操作；请先切走该应用或换个应用");
            }
            int userId = cmd.optInt("user_id", 0);
            String output = launchOnDisplay(id, component, userId);
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
            // WindowManager.DISPLAY_IME_POLICY_LOCAL = 0
            wm.getClass().getMethod("setDisplayImePolicy", int.class, int.class).invoke(wm, displayId, 0);
        } catch (Throwable ignored) {}
    }

    private void pokeDisplayAwake(int displayId) {
        try {
            if (sPowerManager != null && sUserActivityMethod != null) {
                long now = SystemClock.uptimeMillis();
                sUserActivityMethod.invoke(sPowerManager, displayId, now, 2 /* EVENT_TOUCH */, 0);
            }
        } catch (Throwable ignored) {}
    }

    private void ensureAwakeAndUnlocked(int displayId) {
        pokeDisplayAwake(displayId);
        try {
            if (sPowerManager != null) {
                boolean interactive = true;
                try {
                    interactive = (Boolean) sPowerManager.getClass().getMethod("isInteractive").invoke(sPowerManager);
                } catch (Throwable ignored) {}
                if (!interactive) {
                    injectKey(0, KeyEvent.KEYCODE_WAKEUP);
                }
            }
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

    /**
     * Root Binder fallback for hardened apps (like WeChat on a VirtualDisplay) where Accessibility
     * windows report 0 visible nodes: parses `dumpsys activity top` View Hierarchy for `targetPkg`
     * and converts visible clickable/identified views into uiautomator-compatible `<hierarchy>` XML.
     */
    private String dumpTopViewHierarchyXml(String targetPkg) {
        if (targetPkg == null || targetPkg.isEmpty()) return "";
        try {
            String dump = shell("dumpsys", "activity", "top");
            if (dump == null || dump.isEmpty()) return "";
            String[] lines = dump.split("\n");
            boolean inMatchingActivity = false;
            boolean inViewHierarchy = false;
            int[] indentStack = new int[96];
            int[] absLeftStack = new int[96];
            int[] absTopStack = new int[96];
            boolean[] visStack = new boolean[96];
            int stackDepth = 0;

            StringBuilder xml = new StringBuilder(8192);
            xml.append("<?xml version='1.0' encoding='UTF-8' standalone='yes' ?><hierarchy rotation=\"0\">");
            int nodeCount = 0;

            for (String rawLine : lines) {
                String trimmed = rawLine.trim();
                if (trimmed.startsWith("TASK ") || trimmed.startsWith("ACTIVITY ")) {
                    if (trimmed.startsWith("ACTIVITY ")) {
                        inMatchingActivity = trimmed.contains(targetPkg + "/");
                        inViewHierarchy = false;
                        stackDepth = 0;
                    }
                    continue;
                }
                if (!inMatchingActivity) continue;
                if (trimmed.equals("View Hierarchy:")) {
                    inViewHierarchy = true;
                    stackDepth = 0;
                    continue;
                }
                if (!inViewHierarchy) continue;
                if (trimmed.startsWith("Looper ") || trimmed.startsWith("Local FragmentActivity") || trimmed.startsWith("Added Fragments:")) {
                    inViewHierarchy = false;
                    continue;
                }
                int braceOpen = trimmed.indexOf('{');
                int braceClose = trimmed.lastIndexOf('}');
                if (braceOpen <= 0 || braceClose <= braceOpen) continue;

                int indent = rawLine.indexOf(trimmed);
                String clsName = trimmed.substring(0, braceOpen).trim();
                String inside = trimmed.substring(braceOpen + 1, braceClose).trim();
                String[] parts = inside.split("\\s+");
                if (parts.length < 4) continue;

                String flags = parts[1];
                String coords = parts[3];
                int comma1 = coords.indexOf(',');
                int dash = coords.indexOf('-', comma1 + 1);
                int comma2 = coords.indexOf(',', dash + 1);
                if (comma1 < 0 || dash < 0 || comma2 < 0) continue;

                int relL, relT, relR, relB;
                try {
                    relL = Integer.parseInt(coords.substring(0, comma1));
                    relT = Integer.parseInt(coords.substring(comma1 + 1, dash));
                    relR = Integer.parseInt(coords.substring(dash + 1, comma2));
                    relB = Integer.parseInt(coords.substring(comma2 + 1));
                } catch (NumberFormatException e) {
                    continue;
                }

                while (stackDepth > 0 && indentStack[stackDepth - 1] >= indent) {
                    stackDepth--;
                }
                boolean parentVis = stackDepth == 0 || visStack[stackDepth - 1];
                boolean selfVis = flags.length() > 0 && flags.charAt(0) == 'V' && parentVis;
                int parentL = stackDepth == 0 ? 0 : absLeftStack[stackDepth - 1];
                int parentT = stackDepth == 0 ? 0 : absTopStack[stackDepth - 1];
                int absL = parentL + relL;
                int absT = parentT + relT;
                int absR = parentL + relR;
                int absB = parentT + relB;

                if (stackDepth < indentStack.length) {
                    indentStack[stackDepth] = indent;
                    absLeftStack[stackDepth] = absL;
                    absTopStack[stackDepth] = absT;
                    visStack[stackDepth] = selfVis;
                    stackDepth++;
                }

                if (!selfVis) continue;
                int w = absR - absL;
                int h = absB - absT;
                if (w < 8 || h < 8 || absR <= 0 || absB <= 0 || absL >= width || absT >= height) continue;

                boolean clickable = (flags.length() > 5 && flags.charAt(5) == 'C') || (flags.length() > 6 && flags.charAt(6) == 'L');
                boolean editable = clsName.toLowerCase().contains("edittext");
                String resId = "";
                for (int i = 4; i < parts.length; i++) {
                    if (parts[i].contains(":id/")) {
                        resId = parts[i];
                        break;
                    }
                }
                if (clickable || editable || !resId.isEmpty()) {
                    xml.append("<node index=\"0\" text=\"\" resource-id=\"")
                        .append(resId.replace("\"", ""))
                        .append("\" class=\"")
                        .append(clsName.replace("\"", ""))
                        .append("\" package=\"")
                        .append(targetPkg.replace("\"", ""))
                        .append("\" content-desc=\"\" checkable=\"false\" checked=\"false\" clickable=\"")
                        .append(clickable)
                        .append("\" enabled=\"true\" focusable=\"true\" focused=\"false\" scrollable=\"false\" long-clickable=\"false\" password=\"false\" selected=\"false\" bounds=\"[")
                        .append(Math.max(0, absL)).append(",").append(Math.max(0, absT)).append("][")
                        .append(Math.min(width, absR)).append(",").append(Math.min(height, absB)).append("]\" />");
                    nodeCount++;
                    if (nodeCount >= 110) break;
                }
            }
            xml.append("</hierarchy>");
            return nodeCount > 0 ? xml.toString() : "";
        } catch (Throwable ignored) {
            return "";
        }
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

    private String launchOnDisplay(int displayId, String component, int userId) throws Exception {
        String[] parts = component.split("/");
        String pkg = parts[0];
        String cls = parts[1].startsWith(".") ? pkg + parts[1] : parts[1];
        Intent intent = new Intent(Intent.ACTION_MAIN)
            .addCategory(Intent.CATEGORY_LAUNCHER)
            .setComponent(new ComponentName(pkg, cls))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        Bundle options = ActivityOptions.makeBasic().setLaunchDisplayId(displayId).toBundle();
        try {
            Object atm = Class.forName("android.app.ActivityTaskManager").getMethod("getService").invoke(null);
            for (Method m : atm.getClass().getMethods()) {
                if (m.getName().equals("startActivityAsUser") && m.getParameterTypes().length == 12) {
                    int res = (Integer) m.invoke(
                        atm, null, SHELL_PACKAGE, null, intent, null, null, null,
                        0, 0, null, options, userId == 0 ? USER_CURRENT : userId
                    );
                    if (res >= 0) return "startActivityAsUser ok (" + res + ")";
                    break;
                }
            }
        } catch (Throwable ignored) {}
        String output = shell("am", "start", "--display", String.valueOf(displayId), "--user", String.valueOf(userId), "-f", "0x18008000", "-n", component);
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
        return new JSONObject()
            .put("created", vd != null)
            .put("displayId", (vd == null || vd.getDisplay() == null) ? -1 : vd.getDisplay().getDisplayId())
            .put("width", r % 2 == 0 ? width : height)
            .put("height", r % 2 == 0 ? height : width)
            .put("dpi", densityDpi)
            .put("rotation", r)
            .put("uid", android.os.Process.myUid())
            .put("isolatedFocus", vd != null && trustedDisplay);
    }

    private static String shell(String... args) throws Exception {
        return RootCommands.run(args);
    }

    private void release() {
        stopKeepAwake();
        inputHandler.removeCallbacksAndMessages(null);
        frameHandler.removeCallbacksAndMessages(null);
        framePending = false;
        latestPreview = null;
        if (display != null) {
            try { display.release(); } catch (Throwable ignored) {}
            display = null;
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
    }

    void close() {
        execute("{\"op\":\"release\"}");
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
}
