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
    private static final int FLAG_PUBLIC = 1 << 0, FLAG_SECURE = 1 << 2, FLAG_OWN_CONTENT_ONLY = 1 << 3;
    private static final int FLAG_SUPPORTS_TOUCH = 1 << 6, FLAG_ROTATES_WITH_CONTENT = 1 << 7, FLAG_DESTROY_CONTENT_ON_REMOVAL = 1 << 8;
    private static final int FLAG_TRUSTED = 1 << 10, FLAG_OWN_DISPLAY_GROUP = 1 << 11, FLAG_ALWAYS_UNLOCKED = 1 << 12;
    private static final int FLAG_TOUCH_FEEDBACK_DISABLED = 1 << 13, FLAG_OWN_FOCUS = 1 << 14, FLAG_STEAL_TOP_FOCUS_DISABLED = 1 << 16;
    static final int INJECT_MODE_ASYNC = 0, INJECT_MODE_WAIT_FOR_RESULT = 1, VIRTUAL_KEYBOARD = -1, USER_CURRENT = -2;
    // Cached reflection handles for zero-overhead touch injection & keep-awake
    private static Object sInputManager;
    private static Method sInjectInputEventMethod, sSetDisplayIdMethod;
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
        return displayFlags(sdk, trusted, ownDisplayGroup, true);
    }

    static int displayFlags(int sdk, boolean trusted, boolean ownDisplayGroup, boolean secure) {
        // Strictly omit FLAG_ROTATES_WITH_CONTENT (1 << 7) so DisplayContent never computes
        // a square/landscape mOverrideDisplayInfo (largest app 1552x1552) that causes first-frame oversized fonts.
        // Include FLAG_SECURE (1 << 2) by default so Alipay/WeChat payment/verification windows can render on VirtualDisplay.
        int flags = FLAG_PUBLIC | FLAG_OWN_CONTENT_ONLY | FLAG_SUPPORTS_TOUCH
            | FLAG_DESTROY_CONTENT_ON_REMOVAL;
        if (secure) {
            flags |= FLAG_SECURE;
        }
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
                try {
                    Display.Mode mode = mainDisp.getMode();
                    if (mode != null && mode.getPhysicalWidth() >= 720 && mode.getPhysicalHeight() >= 720) {
                        rw = mode.getPhysicalWidth();
                        rh = mode.getPhysicalHeight();
                    }
                } catch (Throwable ignored) {}
            }
        } catch (Throwable ignored) {}
        try {
            Object wm = Class.forName("android.view.WindowManagerGlobal").getMethod("getWindowManagerService").invoke(null);
            if (wm != null) {
                Point initialSize = new Point();
                wm.getClass().getMethod("getInitialDisplaySize", int.class, Point.class).invoke(wm, Display.DEFAULT_DISPLAY, initialSize);
                if (initialSize.x >= 720 && initialSize.y >= 720) {
                    rw = initialSize.x;
                    rh = initialSize.y;
                }
                int baseDpi = (Integer) wm.getClass().getMethod("getInitialDisplayDensity", int.class).invoke(wm, Display.DEFAULT_DISPLAY);
                if (baseDpi >= 120 && baseDpi <= 800) {
                    realDpi = baseDpi;
                }
            }
        } catch (Throwable ignored) {}

        int shortSide = Math.min(rw, rh);
        int longSide = Math.max(rw, rh);

        // Lock VirtualDisplay geometry & DPI 1:1 with Display 0's physical portrait dimensions and densityDpi!
        // In Android AOSP (ActivityRecord.ensureActivityConfiguration), moving a Task between Display 0 and
        // VirtualDisplay via moveRootTaskToDisplay triggers a full Activity Relaunch (restarting Alipay/WeChat)
        // if densityDpi, screenWidthDp, or smallestScreenWidthDp differs. Matching Display 0 1:1 guarantees
        // 100% lossless zero-relaunch task migration (promote / pull_back) AND native font scaling.
        int targetW = shortSide;
        int targetH = longSide;
        int targetDpi = realDpi;

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
        // Never inject KEYCODE_WAKEUP onto Display 0 or dismiss Display 0's keyguard;
        // VirtualDisplay uses FLAG_ALWAYS_UNLOCKED so waking only targets displayId (> 0).
        if (displayId <= 0) return;
        boolean interactive = true;
        try {
            if (sPowerManager != null) {
                interactive = (Boolean) sPowerManager.getClass().getMethod("isInteractive").invoke(sPowerManager);
            }
        } catch (Throwable ignored) {}
        if (!interactive) {
            try {
                injectKey(displayId, KeyEvent.KEYCODE_WAKEUP);
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

    private static List<?> unwrapTaskList(Object raw) {
        if (raw == null) return java.util.Collections.emptyList();
        if (raw instanceof List<?>) return (List<?>) raw;
        try {
            Object list = raw.getClass().getMethod("getList").invoke(raw);
            if (list instanceof List<?>) return (List<?>) list;
        } catch (Throwable ignored) {}
        return java.util.Collections.emptyList();
    }

    static List<?> queryTasksOnDisplay(Object atm, int targetDisplayId) {
        if (atm == null) return java.util.Collections.emptyList();
        Method getTasks4 = null, getTasks3 = null, getTasks1 = null;
        try {
            for (Method m : atm.getClass().getMethods()) {
                if (m.getName().equals("getTasks")) {
                    int n = m.getParameterTypes().length;
                    if (n == 4) getTasks4 = m;
                    else if (n == 3) getTasks3 = m;
                    else if (n == 1) getTasks1 = m;
                }
            }
        } catch (Throwable ignored) {}
        if (getTasks4 != null) {
            try {
                List<?> res = unwrapTaskList(getTasks4.invoke(atm, 50, false, false, targetDisplayId));
                if (!res.isEmpty() || getTasks3 == null) return res;
            } catch (Throwable ignored) {}
        }
        if (getTasks3 != null) {
            try {
                return unwrapTaskList(getTasks3.invoke(atm, 50, false, false));
            } catch (Throwable ignored) {}
        }
        if (getTasks1 != null) {
            try {
                return unwrapTaskList(getTasks1.invoke(atm, 50));
            } catch (Throwable ignored) {}
        }
        return java.util.Collections.emptyList();
    }

    static boolean isTaskOnDisplay(Object atm, int taskId, int targetDisplayId) {
        if (atm == null || taskId <= 0) return false;
        try {
            Class<?> taskInfoCls = Class.forName("android.app.TaskInfo");
            Field displayField = taskInfoCls.getField("displayId");
            Field taskIdField = taskInfoCls.getField("taskId");
            for (Object t : queryTasksOnDisplay(atm, targetDisplayId)) {
                if (t == null) continue;
                if (taskIdField.getInt(t) == taskId && displayField.getInt(t) == targetDisplayId) {
                    return true;
                }
            }
        } catch (Throwable ignored) {}
        return false;
    }

    private static Bundle buildDisplayActivityOptions(int targetDisplayId) {
        try {
            ActivityOptions opts = ActivityOptions.makeBasic().setLaunchDisplayId(targetDisplayId);
            if (targetDisplayId > 0) {
                try {
                    opts.getClass().getMethod("setLaunchWindowingMode", int.class).invoke(opts, 1);
                } catch (Throwable ignored) {}
            }
            return opts.toBundle();
        } catch (Throwable ignored) {
            return null;
        }
    }

    static boolean isHomeOrLauncherPackage(String pkg) {
        if (pkg == null) return true;
        String low = pkg.trim().toLowerCase();
        return low.isEmpty() || low.contains("launcher") || low.contains("home") || "com.android.systemui".equals(low) || "android".equals(low);
    }

    static int extractRootTaskId(Object taskInfo, Field taskIdField) {
        if (taskInfo == null) return -1;
        try {
            Field rootField = taskInfo.getClass().getField("rootTaskId");
            int rtid = rootField.getInt(taskInfo);
            if (rtid > 0) return rtid;
        } catch (Throwable ignored) {}
        try {
            return taskIdField.getInt(taskInfo);
        } catch (Throwable ignored) {
            return -1;
        }
    }

    static boolean invokeRemoveTask(Object atm, int taskId) {
        if (taskId <= 0) return false;
        if (atm != null) {
            try {
                for (Method m : atm.getClass().getMethods()) {
                    if (m.getName().equals("removeTask") && m.getParameterTypes().length == 1) {
                        Object r = m.invoke(atm, taskId);
                        if (!(r instanceof Boolean) || (Boolean) r) return true;
                    }
                }
            } catch (Throwable ignored) {}
        }
        try {
            shell("am", "stack", "remove", String.valueOf(taskId));
            return true;
        } catch (Throwable ignored) {
            return false;
        }
    }

    static void setDisplayRemoveModeDestroy(int displayId) {
        if (displayId <= 0) return;
        try {
            Object wm = Class.forName("android.view.WindowManagerGlobal").getMethod("getWindowManagerService").invoke(null);
            if (wm != null) {
                for (Method m : wm.getClass().getMethods()) {
                    if (m.getName().equals("setRemoveContentMode") && m.getParameterTypes().length == 2) {
                        // REMOVE_MODE_DESTROY_CONTENT = 2 prevents reparenting VirtualDisplay tasks onto Display 0 on release()
                        m.invoke(wm, displayId, 2);
                        break;
                    }
                }
            }
        } catch (Throwable ignored) {}
    }

    static boolean invokeMoveRootTaskToDisplay(Object atm, int taskId, int targetDisplayId, boolean onTop) {
        if (taskId <= 0) return false;
        if (atm != null) {
            Method moveRoot2 = null, moveRoot3 = null, moveStack2 = null, moveStack3 = null;
            try {
                for (Method m : atm.getClass().getMethods()) {
                    String n = m.getName();
                    int len = m.getParameterTypes().length;
                    if (n.equals("moveRootTaskToDisplay")) {
                        if (len == 2) moveRoot2 = m;
                        else if (len == 3) moveRoot3 = m;
                    } else if (n.equals("moveStackToDisplay")) {
                        if (len == 2) moveStack2 = m;
                        else if (len == 3) moveStack3 = m;
                    }
                }
            } catch (Throwable ignored) {}
            if (moveRoot2 != null) {
                try { moveRoot2.invoke(atm, taskId, targetDisplayId); } catch (Throwable ignored) {}
                if (isTaskOnDisplay(atm, taskId, targetDisplayId)) return true;
            }
            if (moveRoot3 != null) {
                try { moveRoot3.invoke(atm, taskId, targetDisplayId, onTop); } catch (Throwable ignored) {}
                if (isTaskOnDisplay(atm, taskId, targetDisplayId)) return true;
            }
            if (moveStack2 != null) {
                try { moveStack2.invoke(atm, taskId, targetDisplayId); } catch (Throwable ignored) {}
                if (isTaskOnDisplay(atm, taskId, targetDisplayId)) return true;
            }
            if (moveStack3 != null) {
                try { moveStack3.invoke(atm, taskId, targetDisplayId, onTop); } catch (Throwable ignored) {}
                if (isTaskOnDisplay(atm, taskId, targetDisplayId)) return true;
            }
        }
        try {
            shell("am", "display", "move-stack", String.valueOf(taskId), String.valueOf(targetDisplayId));
        } catch (Throwable ignored) {}
        return atm != null && isTaskOnDisplay(atm, taskId, targetDisplayId);
    }

    static void invokeMoveTaskToFront(Object atm, int taskId) {
        invokeMoveTaskToFront(atm, taskId, Display.DEFAULT_DISPLAY);
    }

    static boolean invokeMoveTaskToFront(Object atm, int taskId, int targetDisplayId) {
        if (atm == null || taskId <= 0) return false;
        // Never allow bringing a task to front for a VirtualDisplay if the task is still on Display 0!
        if (targetDisplayId > 0 && !isTaskOnDisplay(atm, taskId, targetDisplayId)) {
            return false;
        }
        if (targetDisplayId == Display.DEFAULT_DISPLAY) {
            // NEVER yank Launcher / Home / SystemUI to the front of Display 0!
            try {
                Class<?> taskInfoCls = Class.forName("android.app.TaskInfo");
                Field taskIdField = taskInfoCls.getField("taskId");
                Field topField = taskInfoCls.getField("topActivity");
                for (Object t : queryTasksOnDisplay(atm, Display.DEFAULT_DISPLAY)) {
                    if (t == null) continue;
                    if (taskIdField.getInt(t) == taskId) {
                        ComponentName top = (ComponentName) topField.get(t);
                        String topPkg = top != null ? top.getPackageName() : "";
                        if (isHomeOrLauncherPackage(topPkg)) {
                            return false;
                        }
                        break;
                    }
                }
            } catch (Throwable ignored) {}
        }
        Bundle opts = buildDisplayActivityOptions(targetDisplayId);
        try {
            for (Method m : atm.getClass().getMethods()) {
                if (m.getName().equals("startActivityFromRecents") && m.getParameterTypes().length == 2) {
                    m.invoke(atm, taskId, opts);
                    return true;
                }
            }
        } catch (Throwable ignored) {}
        try {
            for (Method m : atm.getClass().getMethods()) {
                if (m.getName().equals("moveTaskToFront") && m.getParameterTypes().length == 5) {
                    m.invoke(atm, null, SHELL_PACKAGE, taskId, 0, opts);
                    return true;
                }
            }
        } catch (Throwable ignored) {}
        return false;
    }

    static void guardAndContainLaunchOnDisplay(Object atm, int displayId, String targetPkg, int mainBeforeTaskId, String mainBeforePkg) {
        if (atm == null || displayId <= 0 || targetPkg == null || targetPkg.isEmpty() || "dev.bubuki.proactive".equals(targetPkg)) return;
        try {
            Class<?> taskInfoCls = Class.forName("android.app.TaskInfo");
            Field displayField = taskInfoCls.getField("displayId");
            Field taskIdField = taskInfoCls.getField("taskId");
            Field topField = taskInfoCls.getField("topActivity");
            Field baseField = taskInfoCls.getField("baseActivity");
            int[] waitsMs = new int[]{0, 60, 90, 120};
            for (int w : waitsMs) {
                if (w > 0) Thread.sleep(w);
                boolean movedAny = false;
                boolean escapedTopOnMain = false;
                int idx = 0;
                for (Object t : queryTasksOnDisplay(atm, Display.DEFAULT_DISPLAY)) {
                    if (t == null) continue;
                    if (displayField.getInt(t) != Display.DEFAULT_DISPLAY) continue;
                    int tid = taskIdField.getInt(t);
                    ComponentName top = (ComponentName) topField.get(t);
                    ComponentName base = (ComponentName) baseField.get(t);
                    String topPkg = top != null ? top.getPackageName() : "";
                    String basePkg = base != null ? base.getPackageName() : "";
                    boolean matchesTarget = targetPkg.equals(topPkg) || targetPkg.equals(basePkg);
                    if (matchesTarget && (tid != mainBeforeTaskId || !targetPkg.equals(mainBeforePkg))) {
                        if (idx == 0) escapedTopOnMain = true;
                        int rootTid = extractRootTaskId(t, taskIdField);
                        if (invokeMoveRootTaskToDisplay(atm, rootTid, displayId, true)) {
                            if (!targetPkg.equals(topPackageOnDisplay(displayId))) {
                                invokeMoveTaskToFront(atm, tid, displayId);
                            }
                            movedAny = true;
                        }
                    }
                    idx++;
                }
                if (movedAny && mainBeforeTaskId > 0 && !isHomeOrLauncherPackage(mainBeforePkg) && !targetPkg.equals(mainBeforePkg)) {
                    String nowMainTop = topPackageOnDisplay(Display.DEFAULT_DISPLAY);
                    if (isHomeOrLauncherPackage(nowMainTop)) {
                        invokeMoveTaskToFront(atm, mainBeforeTaskId, Display.DEFAULT_DISPLAY);
                    }
                }
                if (!escapedTopOnMain && targetPkg.equals(topPackageOnDisplay(displayId))) {
                    break;
                }
            }
        } catch (Throwable ignored) {}
    }

    static int[] promoteTopTaskToMainDisplay(int displayId) {
        if (displayId <= 0) return new int[]{-1, 0};
        try {
            Object atm = Class.forName("android.app.ActivityTaskManager").getMethod("getService").invoke(null);
            Class<?> taskInfoCls = Class.forName("android.app.TaskInfo");
            Field displayField = taskInfoCls.getField("displayId");
            Field taskIdField = taskInfoCls.getField("taskId");
            Field topField = taskInfoCls.getField("topActivity");
            for (Object t : queryTasksOnDisplay(atm, displayId)) {
                if (t == null) continue;
                if (displayField.getInt(t) != displayId) continue;
                int tid = taskIdField.getInt(t);
                ComponentName top = (ComponentName) topField.get(t);
                String topPkg = top != null ? top.getPackageName() : "";
                if ("dev.bubuki.proactive".equals(topPkg)) continue;
                boolean moved = invokeMoveRootTaskToDisplay(atm, tid, Display.DEFAULT_DISPLAY, true);
                if (moved) {
                    invokeMoveTaskToFront(atm, tid, Display.DEFAULT_DISPLAY);
                    return new int[]{tid, 1};
                }
                return new int[]{tid, 0};
            }
        } catch (Throwable ignored) {}
        return new int[]{-1, 0};
    }

    static int pullTaskFromMainToVirtualDisplay(int displayId, int preferredTaskId, String preferredPkg) {
        if (displayId <= 0) return -1;
        try {
            Object atm = Class.forName("android.app.ActivityTaskManager").getMethod("getService").invoke(null);
            Class<?> taskInfoCls = Class.forName("android.app.TaskInfo");
            Field displayField = taskInfoCls.getField("displayId");
            Field taskIdField = taskInfoCls.getField("taskId");
            Field topField = taskInfoCls.getField("topActivity");
            Field baseField = taskInfoCls.getField("baseActivity");
            boolean hasExplicitFilter = preferredTaskId > 0 || (preferredPkg != null && !preferredPkg.isEmpty());
            int fallbackTid = -1;
            for (Object t : queryTasksOnDisplay(atm, Display.DEFAULT_DISPLAY)) {
                if (t == null) continue;
                if (displayField.getInt(t) != Display.DEFAULT_DISPLAY) continue;
                int tid = taskIdField.getInt(t);
                ComponentName top = (ComponentName) topField.get(t);
                ComponentName base = (ComponentName) baseField.get(t);
                String topPkg = top != null ? top.getPackageName() : "";
                String basePkg = base != null ? base.getPackageName() : "";
                if ("dev.bubuki.proactive".equals(topPkg) || topPkg.contains("launcher") || topPkg.contains("home") || "com.android.systemui".equals(topPkg)) {
                    continue;
                }
                if (preferredTaskId > 0 && tid == preferredTaskId) {
                    if (invokeMoveRootTaskToDisplay(atm, tid, displayId, true)) {
                        invokeMoveTaskToFront(atm, tid, displayId);
                        return tid;
                    }
                    return -1;
                }
                if (preferredPkg != null && !preferredPkg.isEmpty() && (preferredPkg.equals(topPkg) || preferredPkg.equals(basePkg))) {
                    if (invokeMoveRootTaskToDisplay(atm, tid, displayId, true)) {
                        invokeMoveTaskToFront(atm, tid, displayId);
                        return tid;
                    }
                    return -1;
                }
                if (!hasExplicitFilter && fallbackTid < 0) {
                    fallbackTid = tid;
                }
            }
            if (!hasExplicitFilter && fallbackTid > 0) {
                if (invokeMoveRootTaskToDisplay(atm, fallbackTid, displayId, true)) {
                    invokeMoveTaskToFront(atm, fallbackTid, displayId);
                    return fallbackTid;
                }
            }
        } catch (Throwable ignored) {}
        return -1;
    }

    private static String resolveEnabledLauncherComponent(String pkg, int userId) {
        try {
            String resolved = shell(
                "cmd", "package", "resolve-activity", "--brief",
                "--user", String.valueOf(userId),
                "-a", "android.intent.action.MAIN",
                "-c", "android.intent.category.LAUNCHER",
                pkg
            );
            if (resolved != null) {
                for (String line : resolved.split("\\r?\\n")) {
                    String s = line.trim();
                    if (s.contains("/") && !s.startsWith("priority=") && !s.contains(" ")) {
                        return s;
                    }
                }
            }
        } catch (Throwable ignored) {}
        return "";
    }

    static String launchOnDisplay(int displayId, String component, int userId) throws Exception {
        if (displayId <= 0) {
            throw new Exception("拒绝在主屏 (displayId=" + displayId + ") 启动虚拟屏应用");
        }
        String[] parts = component.split("/");
        String pkg = parts[0];
        Object atm = null;
        int mainBeforeTaskId = -1;
        String mainBeforePkg = "";
        int existingMainTaskId = -1;
        int existingMainRootTaskId = -1;
        int existingVirtualTaskId = -1;

        setFullscreenWindowingMode(displayId);

        try {
            atm = Class.forName("android.app.ActivityTaskManager").getMethod("getService").invoke(null);
            Class<?> taskInfoCls = Class.forName("android.app.TaskInfo");
            Field displayField = taskInfoCls.getField("displayId");
            Field taskIdField = taskInfoCls.getField("taskId");
            Field topField = taskInfoCls.getField("topActivity");
            Field baseField = taskInfoCls.getField("baseActivity");

            // Record Display 0's true top task BEFORE touching any tasks so we can protect Display 0
            for (Object t : queryTasksOnDisplay(atm, Display.DEFAULT_DISPLAY)) {
                if (t == null) continue;
                if (displayField.getInt(t) != Display.DEFAULT_DISPLAY) continue;
                int tid = taskIdField.getInt(t);
                ComponentName top = (ComponentName) topField.get(t);
                ComponentName base = (ComponentName) baseField.get(t);
                String topPkg = top != null ? top.getPackageName() : "";
                String basePkg = base != null ? base.getPackageName() : "";
                if (mainBeforeTaskId < 0) {
                    mainBeforeTaskId = tid;
                    mainBeforePkg = topPkg;
                }
                if (!"dev.bubuki.proactive".equals(pkg) && existingMainTaskId < 0 && (pkg.equals(topPkg) || pkg.equals(basePkg))) {
                    existingMainTaskId = tid;
                    existingMainRootTaskId = extractRootTaskId(t, taskIdField);
                }
            }

            if (!"dev.bubuki.proactive".equals(pkg)) {
                for (Object vt : queryTasksOnDisplay(atm, displayId)) {
                    if (vt == null) continue;
                    if (displayField.getInt(vt) != displayId) continue;
                    int vtid = taskIdField.getInt(vt);
                    ComponentName vtop = (ComponentName) topField.get(vt);
                    ComponentName vbase = (ComponentName) baseField.get(vt);
                    String vtopPkg = vtop != null ? vtop.getPackageName() : "";
                    String vbasePkg = vbase != null ? vbase.getPackageName() : "";
                    if (pkg.equals(vtopPkg) || pkg.equals(vbasePkg)) {
                        existingVirtualTaskId = vtid;
                        break;
                    }
                }
                if (existingVirtualTaskId > 0) {
                    if (!pkg.equals(topPackageOnDisplay(displayId))) {
                        invokeMoveTaskToFront(atm, existingVirtualTaskId, displayId);
                        for (int i = 0; i < 4 && !pkg.equals(topPackageOnDisplay(displayId)); i++) {
                            Thread.sleep(45L);
                        }
                    }
                    if (pkg.equals(topPackageOnDisplay(displayId))) {
                        return "already_on_virtual_display_brought_to_front (" + existingVirtualTaskId + ")";
                    }
                }
                if (existingMainTaskId > 0) {
                    // Never hijack an app that the user is actively viewing on physical Display 0 right now!
                    if (existingMainTaskId == mainBeforeTaskId && pkg.equals(mainBeforePkg) && !isHomeOrLauncherPackage(mainBeforePkg)) {
                        boolean screenInteractive = true;
                        try {
                            android.os.PowerManager pm = systemContext().getSystemService(android.os.PowerManager.class);
                            if (pm != null) screenInteractive = pm.isInteractive();
                        } catch (Throwable ignored) {}
                        if (screenInteractive) {
                            throw new Exception("主人当前正在主屏前台使用该应用 (" + pkg + ")，为避免抢占前台或导致主屏返回桌面，已暂停拉入虚拟屏");
                        }
                    }
                    int moveTid = existingMainRootTaskId > 0 ? existingMainRootTaskId : existingMainTaskId;
                    boolean moved = invokeMoveRootTaskToDisplay(atm, moveTid, displayId, true);
                    if (moved || isTaskOnDisplay(atm, existingMainTaskId, displayId)) {
                        if (!pkg.equals(topPackageOnDisplay(displayId))) {
                            invokeMoveTaskToFront(atm, existingMainTaskId, displayId);
                        }
                        if (mainBeforeTaskId > 0 && mainBeforeTaskId != existingMainTaskId && !isHomeOrLauncherPackage(mainBeforePkg)) {
                            String nowMainTop = topPackageOnDisplay(Display.DEFAULT_DISPLAY);
                            if (isHomeOrLauncherPackage(nowMainTop)) {
                                invokeMoveTaskToFront(atm, mainBeforeTaskId, Display.DEFAULT_DISPLAY);
                            }
                        }
                        for (int i = 0; i < 4 && !pkg.equals(topPackageOnDisplay(displayId)); i++) {
                            Thread.sleep(45L);
                        }
                        if (pkg.equals(topPackageOnDisplay(displayId))) {
                            return "moved_existing_task_losslessly (" + existingMainTaskId + ")";
                        }
                    } else if (existingMainTaskId != mainBeforeTaskId && !pkg.equals(mainBeforePkg)) {
                        // Critical fix for singleTask apps (WeChat/Alipay): if a background task remains on Display 0,
                        // AOSP `am start --display <vdId>` will reuse that task on Display 0 and pop it to foreground!
                        // Remove the idle background task record first so `am start` creates a task strictly on `displayId`.
                        invokeRemoveTask(atm, moveTid);
                        Thread.sleep(40L);
                    }
                }
            }
        } catch (Exception e) {
            if (e.getMessage() != null && e.getMessage().contains("主人当前正在主屏前台使用")) {
                throw e;
            }
        } catch (Throwable ignored) {}

        String flagsHex = "dev.bubuki.proactive".equals(pkg) ? "0x14000000" : "0x10200000";
        String output = shell(
            "am", "start", "--display", String.valueOf(displayId), "--windowingMode", "1",
            "--user", String.valueOf(userId), "-f", flagsHex, "-n", component
        );
        if (output.contains("Error") || output.contains("Exception") || output.contains("does not exist") || output.contains("Permission Denial") || output.contains("Unknown option")) {
            if (output.contains("Unknown option")) {
                output = shell(
                    "am", "start", "--display", String.valueOf(displayId),
                    "--user", String.valueOf(userId), "-f", flagsHex, "-n", component
                );
            }
            if (output.contains("Error") || output.contains("Exception") || output.contains("does not exist") || output.contains("Permission Denial")) {
                String altComp = resolveEnabledLauncherComponent(pkg, userId);
                if (!altComp.isEmpty() && !altComp.equals(component)) {
                    output = shell(
                        "am", "start", "--display", String.valueOf(displayId),
                        "--user", String.valueOf(userId), "-f", flagsHex, "-n", altComp
                    );
                }
            }
        }
        guardAndContainLaunchOnDisplay(atm, displayId, pkg, mainBeforeTaskId, mainBeforePkg);
        if ((output.contains("Error") || output.contains("Exception")) && !pkg.equals(topPackageOnDisplay(displayId))) {
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
                Constructor<?> ctor = at.getDeclaredConstructor(); ctor.setAccessible(true);
                thread = ctor.newInstance();
                Field sCur = at.getDeclaredField("sCurrentActivityThread"); sCur.setAccessible(true); sCur.set(null, thread);
                Field mSys = at.getDeclaredField("mSystemThread"); mSys.setAccessible(true); mSys.setBoolean(thread, true);
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                    try {
                        Class<?> cc = Class.forName("android.app.ConfigurationController"), internal = Class.forName("android.app.ActivityThreadInternal");
                        Constructor<?> ccCtor = cc.getDeclaredConstructor(internal); ccCtor.setAccessible(true);
                        Field mCc = at.getDeclaredField("mConfigurationController"); mCc.setAccessible(true); mCc.set(thread, ccCtor.newInstance(thread));
                    } catch (Throwable ignored) {}
                }
                try {
                    Class<?> abd = Class.forName("android.app.ActivityThread$AppBindData");
                    Constructor<?> abdCtor = abd.getDeclaredConstructor(); abdCtor.setAccessible(true);
                    Object data = abdCtor.newInstance();
                    ApplicationInfo ai = new ApplicationInfo(); ai.packageName = SHELL_PACKAGE;
                    Field appInfoField = abd.getDeclaredField("appInfo"); appInfoField.setAccessible(true); appInfoField.set(data, ai);
                    Field boundField = at.getDeclaredField("mBoundApplication"); boundField.setAccessible(true); boundField.set(thread, data);
                } catch (Throwable ignored) {}
            } catch (Throwable fallback) { thread = at.getMethod("systemMain").invoke(null); }
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
        int w = bmp.getWidth(), h = bmp.getHeight(), cols = 6, rows = 10;
        Canvas c = new Canvas(bmp);
        Paint linePaint = new Paint(Paint.ANTI_ALIAS_FLAG); linePaint.setColor(0x5500E5FF); linePaint.setStrokeWidth(1f);
        Paint bgPaint = new Paint(Paint.ANTI_ALIAS_FLAG); bgPaint.setColor(0x77000000);
        Paint textPaint = new Paint(Paint.ANTI_ALIAS_FLAG); textPaint.setColor(0xDDFFFFFF); textPaint.setTextSize(Math.max(10f, w / 42f));
        for (int col = 1; col < cols; col++) { float x = w * col / (float) cols; c.drawLine(x, 0, x, h, linePaint); }
        for (int row = 1; row < rows; row++) { float y = h * row / (float) rows; c.drawLine(0, y, w, y, linePaint); }
        String[] letters = {"A", "B", "C", "D", "E", "F"};
        for (int r = 0; r < rows; r++) {
            for (int col = 0; col < cols; col++) {
                String label = letters[col] + (r + 1);
                float cx = w * (col + 0.08f) / cols, cy = h * (r + 0.22f) / rows;
                c.drawRect(cx - 2f, cy - textPaint.getTextSize(), cx + textPaint.measureText(label) + 2f, cy + 3f, bgPaint);
                c.drawText(label, cx, cy, textPaint);
            }
        }
    }
}
