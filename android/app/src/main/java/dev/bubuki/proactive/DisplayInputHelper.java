package dev.bubuki.proactive;

import android.app.ActivityOptions;
import android.app.Application;
import android.app.Instrumentation;
import android.content.*;
import android.content.pm.ApplicationInfo;
import android.graphics.*;
import android.hardware.display.DisplayManager;
import android.os.*;
import android.view.Display;
import android.view.InputDevice;
import android.view.InputEvent;
import android.view.KeyEvent;
import android.view.MotionEvent;
import java.lang.reflect.*;
import java.util.List;

/**
 * [Module] DisplayInputHelper
 * [Responsibility] Low-level InputEvent construction & reflection injection, ActivityTaskManager
 *                  display task routing/containment, adaptive display geometry calculation,
 *                  AI grid overlay rendering, and ShellContext bootstrap.
 * [Public API]
 *   - displayFlags(sdk, trusted, ownDisplayGroup)
 *   - computeAdaptiveDisplaySpec(dm, tierInput)
 *   - setFullscreenWindowingMode(displayId) / setImePolicy(displayId) / ensureAwakeAndUnlocked(displayId)
 *   - injectEvent / injectTap / injectSwipe / injectKey / resolveKeyCode
 *   - topPackageOnDisplay / queryTasksOnDisplay / invokeMoveRootTaskToDisplay / invokeMoveTaskToFront
 *   - launchOnDisplay / openUriOnDisplay / guardAndContainLaunchOnDisplay
 *   - systemContext() / drawAiGridOverlay(bmp) / shell(args...)
 */
@android.annotation.SuppressLint({"PrivateApi", "DiscouragedPrivateApi", "BlockedPrivateApi", "SoonBlockedPrivateApi", "WrongConstant"})
final class DisplayInputHelper {
    static final String SHELL_PACKAGE = "com.android.shell";
    static final int SHELL_UID = 2000;

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

    static final int INJECT_MODE_ASYNC = 0;
    static final int INJECT_MODE_WAIT_FOR_RESULT = 1;
    static final int VIRTUAL_KEYBOARD = -1;
    static final int USER_CURRENT = -2;

    // Cached reflection handles for zero-overhead touch injection & keep-awake
    private static Object sInputManager;
    private static Method sInjectInputEventMethod;
    private static Method sSetDisplayIdMethod;
    static Object sPowerManager;
    static Method sUserActivityMethod;

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

    private DisplayInputHelper() {}

    static int displayFlags(int sdk, boolean trusted, boolean ownDisplayGroup) {
        // Strictly omit FLAG_ROTATES_WITH_CONTENT (1 << 7) so DisplayContent never computes
        // a square/landscape mOverrideDisplayInfo (largest app 1552x1552) that causes first-frame oversized fonts.
        int flags = FLAG_PUBLIC | FLAG_OWN_CONTENT_ONLY | FLAG_SUPPORTS_TOUCH
            | FLAG_DESTROY_CONTENT_ON_REMOVAL;
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

    static int roundTo16(float value, int minVal, int maxVal) {
        int clamped = Math.max(minVal, Math.min(maxVal, Math.round(value)));
        return ((clamped + 8) / 16) * 16;
    }

    static int[] computeAdaptiveDisplaySpec(DisplayManager dm, String tierInput) {
        String tier = (tierInput == null || tierInput.isEmpty()) ? "compact" : tierInput.trim().toLowerCase();
        int rw = 1080, rh = 2400, realDpi = 480;
        try {
            Display mainDisp = dm != null ? dm.getDisplay(Display.DEFAULT_DISPLAY) : null;
            if (mainDisp != null) {
                android.util.DisplayMetrics realMetrics = new android.util.DisplayMetrics();
                mainDisp.getRealMetrics(realMetrics);
                if (realMetrics.widthPixels >= 320 && realMetrics.heightPixels >= 320) {
                    rw = realMetrics.widthPixels;
                    rh = realMetrics.heightPixels;
                }
                if (realMetrics.densityDpi >= 120 && realMetrics.densityDpi <= 800) {
                    realDpi = realMetrics.densityDpi;
                }
            }
        } catch (Throwable ignored) {}

        int shortSide = Math.min(rw, rh);
        int longSide = Math.max(rw, rh);

        // Lock VirtualDisplay geometry to 720x1584 @ 320dpi (swDp = 360dp, XHDPI 2.0x density bucket)
        // matching Display 0's portrait aspect ratio and 360dp layout width while keeping ImageReader lightweight.
        int targetW = 720;
        int targetH = roundTo16(720f * ((float) longSide / (float) shortSide), 1280, 1920);
        int targetDpi = 320;

        int targetPreviewShort;
        if ("compact".equals(tier) || "small".equals(tier) || "mini".equals(tier)) {
            targetPreviewShort = 460;
        } else if ("large".equals(tier) || "hd".equals(tier) || "full".equals(tier)) {
            targetPreviewShort = 720;
        } else {
            targetPreviewShort = 560;
        }
        return new int[]{targetW, targetH, targetDpi, targetPreviewShort};
    }

    static void setFullscreenWindowingMode(int displayId) {
        setFullscreenWindowingMode(displayId, 0, 0, 0);
    }

    static void setFullscreenWindowingMode(int displayId, int width, int height, int densityDpi) {
        try {
            Object wm = Class.forName("android.view.WindowManagerGlobal").getMethod("getWindowManagerService").invoke(null);
            try {
                wm.getClass().getMethod("setWindowingMode", int.class, int.class).invoke(wm, displayId, 1);
            } catch (Throwable ignored) {}
            try {
                wm.getClass().getMethod("fixToUserRotation", int.class, int.class).invoke(wm, displayId, 2);
            } catch (Throwable ignored) {}
            try {
                wm.getClass().getMethod("freezeDisplayRotation", int.class, int.class).invoke(wm, displayId, 0);
            } catch (Throwable ignored) {}
        } catch (Throwable t) {
            try {
                shell("wm", "set-display-windowing-mode", "-d", String.valueOf(displayId), "1");
            } catch (Throwable ignored) {}
        }
        try {
            shell("wm", "user-rotation", "-d", String.valueOf(displayId), "lock", "0");
            shell("wm", "size", "reset", "-d", String.valueOf(displayId));
            shell("wm", "density", "reset", "-d", String.valueOf(displayId));
        } catch (Throwable ignored) {}
    }

    static void setImePolicy(int displayId) {
        try {
            Object wm = Class.forName("android.view.WindowManagerGlobal").getMethod("getWindowManagerService").invoke(null);
            wm.getClass().getMethod("setDisplayImePolicy", int.class, int.class).invoke(wm, displayId, 2);
        } catch (Throwable ignored) {}
    }

    static void pokeDisplayAwake(int displayId) {
        // Intentionally no-op: FLAG_ALWAYS_UNLOCKED on VirtualDisplay keeps rendering active without waking physical backlight.
    }

    static void ensureAwakeAndUnlocked(int displayId) {
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

    static boolean injectEvent(InputEvent event, int displayId, int mode) {
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

    static boolean injectTap(Handler inputHandler, Runnable onFrameSchedule, int displayId, int x, int y) {
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
            if (onFrameSchedule != null) onFrameSchedule.run();
        }, 32L);
        return true;
    }

    static boolean injectSwipe(Handler inputHandler, Runnable onFrameSchedule, int displayId, int x1, int y1, int x2, int y2, int durationMs) {
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
                    if (stepIdx % 2 == 0 && onFrameSchedule != null) onFrameSchedule.run();
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
                    if (onFrameSchedule != null) onFrameSchedule.run();
                }
            }, delayMs);
        }
        return true;
    }

    static boolean injectKey(int displayId, int keyCode) {
        long now = SystemClock.uptimeMillis();
        KeyEvent down = new KeyEvent(now, now, KeyEvent.ACTION_DOWN, keyCode, 0, 0, VIRTUAL_KEYBOARD, 0, 0, InputDevice.SOURCE_KEYBOARD);
        KeyEvent up = KeyEvent.changeAction(down, KeyEvent.ACTION_UP);
        if (!injectEvent(down, displayId, INJECT_MODE_WAIT_FOR_RESULT)) return false;
        return injectEvent(up, displayId, INJECT_MODE_WAIT_FOR_RESULT) || injectEvent(up, displayId, INJECT_MODE_WAIT_FOR_RESULT);
    }

    static int resolveKeyCode(String key, int explicitCode) throws Exception {
        int keyCode;
        if (explicitCode > 0 && key.isEmpty()) {
            keyCode = explicitCode;
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
        if (keyCode == KeyEvent.KEYCODE_HOME || keyCode == KeyEvent.KEYCODE_APP_SWITCH) {
            keyCode = KeyEvent.KEYCODE_BACK;
        }
        return keyCode;
    }

    static String topPackageOnDisplay(int displayId) {
        try {
            Object atm = Class.forName("android.app.ActivityTaskManager").getMethod("getService").invoke(null);
            Class<?> taskInfoCls = Class.forName("android.app.TaskInfo");
            Field displayField = taskInfoCls.getField("displayId");
            Field topField = taskInfoCls.getField("topActivity");
            for (Object t : queryTasksOnDisplay(atm, displayId)) {
                if (t == null) continue;
                if (displayField.getInt(t) == displayId) {
                    ComponentName top = (ComponentName) topField.get(t);
                    if (top != null) return top.getPackageName();
                }
            }
        } catch (Throwable ignored) {}
        return "";
    }

    static List<?> queryTasksOnDisplay(Object atm, int targetDisplayId) {
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

    static void invokeMoveRootTaskToDisplay(Object atm, int taskId, int targetDisplayId, boolean onTop) {
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

    static void invokeMoveTaskToFront(Object atm, int taskId) {
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

    static void guardAndContainLaunchOnDisplay(Object atm, int displayId, String targetPkg, int mainBeforeTaskId, String mainBeforePkg) {
        if (atm == null || displayId <= 0 || "dev.bubuki.proactive".equals(targetPkg)) return;
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

    static String launchOnDisplay(int displayId, String component, int userId) throws Exception {
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
            for (Object t : queryTasksOnDisplay(atm, Display.DEFAULT_DISPLAY)) {
                if (t == null) continue;
                if (displayField.getInt(t) != Display.DEFAULT_DISPLAY) continue;
                int tid = taskIdField.getInt(t);
                ComponentName top = (ComponentName) topField.get(t);
                ComponentName base = (ComponentName) baseField.get(t);
                String topPkg = top != null ? top.getPackageName() : "";
                String basePkg = base != null ? base.getPackageName() : "";
                if (!"dev.bubuki.proactive".equals(pkg) && (pkg.equals(topPkg) || pkg.equals(basePkg))) {
                    invokeMoveRootTaskToDisplay(atm, tid, displayId, true);
                } else if (mainBeforeTaskId < 0) {
                    mainBeforeTaskId = tid;
                    mainBeforePkg = topPkg;
                }
            }
        } catch (Throwable ignored) {}

        setFullscreenWindowingMode(displayId);
        Intent intent = new Intent(Intent.ACTION_MAIN)
            .setComponent(new ComponentName(pkg, cls))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        if ("dev.bubuki.proactive".equals(pkg)) {
            intent.addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP);
        } else {
            intent.addCategory(Intent.CATEGORY_LAUNCHER);
        }
        ActivityOptions actOpts = ActivityOptions.makeBasic().setLaunchDisplayId(displayId);
        try {
            actOpts.getClass().getMethod("setLaunchWindowingMode", int.class).invoke(actOpts, 1);
        } catch (Throwable ignored) {}
        Bundle options = actOpts.toBundle();
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

    static String openUriOnDisplay(int displayId, String uri, int userId) throws Exception {
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

    static String shell(String... args) throws Exception {
        return RootCommands.run(args);
    }

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

    static void drawAiGridOverlay(Bitmap bmp) {
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
