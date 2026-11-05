package dev.bubuki.proactive;

import android.app.NotificationManager;
import android.content.Context;
import android.os.Build;
import android.provider.Settings;
import org.json.JSONObject;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.UUID;
import java.util.concurrent.*;

/**
 * Root-authenticated transport using direct anonymous kernel pipes (stdin/stdout)
 * to the su -> app_process helper, avoiding SELinux abstract LocalSocket blocks on
 * Magisk / KernelSU / APatch (Android 13/14/15).
 */
final class PrivilegeManager {
    private static final String RPC_PREFIX = "PULSE_RPC:";
    private final Context context;
    private volatile boolean rootGranted;
    private volatile int suMode = 0;
    private volatile String lastDiag = "";
    private BufferedReader rootIn;
    private BufferedWriter rootOut;
    private java.lang.Process rootProcess;
    private final ExecutorService ioPool = Executors.newCachedThreadPool(r -> {
        Thread t = new Thread(r, "pulse-root-io");
        t.setDaemon(true);
        return t;
    });

    PrivilegeManager(Context context) {
        this.context = context.getApplicationContext();
        try {
            JSONObject saved = SecurePrefs.read(this.context);
            if (saved.optBoolean("rootGrantedHint", false)) {
                this.rootGranted = true;
                this.suMode = saved.optInt("suModeHint", 0);
            }
        } catch (Throwable ignored) {}
    }

    boolean isRootGrantedFast() {
        return rootGranted;
    }

    JSONObject status() throws Exception {
        String listener = Settings.Secure.getString(context.getContentResolver(), "enabled_notification_listeners");
        return new JSONObject()
            .put("root", rootGranted)
            .put("engine", "root")
            .put("sdk", Build.VERSION.SDK_INT)
            .put("isolatedFocusSupported", Build.VERSION.SDK_INT >= 34)
            .put("overlay", Settings.canDrawOverlays(context))
            .put("notifications", context.getSystemService(NotificationManager.class).areNotificationsEnabled())
            .put("listener", listener != null && listener.contains(context.getPackageName()))
            .put("diag", lastDiag);
    }

    synchronized JSONObject probe() throws Exception {
        boolean detected = false;
        int winningMode = 0;
        // Modes:
        // 0: su -c <cmd>
        // 1: su 0 sh -c <cmd>
        // 2: /system/bin/su -c <cmd>
        // 3: /system/xbin/su -c <cmd>
        // 4: interactive su (write to stdin)
        for (int mode = 0; mode <= 4; mode++) {
            java.lang.Process process = null;
            try {
                process = startSu("id -u", mode);
                if (!process.waitFor(25, TimeUnit.SECONDS)) {
                    process.destroyForcibly();
                    continue;
                }
                String out = new String(Io.read(process.getInputStream(), 2048), StandardCharsets.UTF_8).trim();
                String err = new String(Io.read(process.getErrorStream(), 2048), StandardCharsets.UTF_8).trim();
                if (containsUidZero(out) || containsUidZero(err)) {
                    detected = true;
                    winningMode = mode;
                    break;
                }
            } catch (Throwable ignored) {
            } finally {
                if (process != null && process.isAlive()) process.destroyForcibly();
            }
        }
        rootGranted = detected;
        suMode = winningMode;
        persistHint(detected, winningMode);
        if (detected) {
            autoGrantPermissionsViaRoot(winningMode);
            try {
                JSONObject identity = execute(new JSONObject().put("op", "status"));
                if (identity.optInt("uid", -1) != 0) {
                    lastDiag = "helper uid=" + identity.optInt("uid", -1);
                } else {
                    lastDiag = "ok";
                }
            } catch (Throwable e) {
                // Keep rootGranted = true because `su` (`id -u == 0`) genuinely succeeded!
                // Record the helper diagnostic so we can retry or report accurately without claiming "no Root".
                lastDiag = e.getMessage() != null ? e.getMessage() : e.getClass().getSimpleName();
            }
        } else {
            lastDiag = "su id -u did not return 0";
        }
        return status();
    }

    private void autoGrantPermissionsViaRoot(int mode) {
        ioPool.submit(() -> {
            java.lang.Process p = null;
            try {
                String pkg = context.getPackageName();
                String svc = pkg + "/" + pkg + ".PulseAccessibilityService";
                String grantCmd = "appops set " + pkg + " SYSTEM_ALERT_WINDOW allow >/dev/null 2>&1; "
                    + "pm grant " + pkg + " android.permission.POST_NOTIFICATIONS >/dev/null 2>&1; "
                    + "cur=$(settings get secure enabled_accessibility_services 2>/dev/null); "
                    + "case \"$cur\" in *\"" + svc + "\"*) ;; \"\"|\"null\") settings put secure enabled_accessibility_services \"" + svc + "\" ;; *) settings put secure enabled_accessibility_services \"$cur:" + svc + "\" ;; esac; "
                    + "settings put secure accessibility_enabled 1 >/dev/null 2>&1";
                p = startSu(grantCmd, mode);
                if (mode == 4) {
                    OutputStream os = p.getOutputStream();
                    os.write("exit\n".getBytes(StandardCharsets.UTF_8));
                    os.flush();
                }
                p.waitFor(6, TimeUnit.SECONDS);
            } catch (Throwable ignored) {
            } finally {
                if (p != null && p.isAlive()) p.destroyForcibly();
            }
        });
    }

    private static boolean containsUidZero(String text) {
        if (text == null || text.isEmpty()) return false;
        for (String line : text.split("\\r?\\n")) {
            String trimmed = line.trim();
            if ("0".equals(trimmed) || trimmed.startsWith("uid=0(")) return true;
        }
        return false;
    }

    private void persistHint(boolean granted, int mode) {
        try {
            synchronized (SecurePrefs.class) {
                JSONObject saved = SecurePrefs.read(context);
                saved.put("rootGrantedHint", granted);
                saved.put("suModeHint", mode);
                SecurePrefs.save(context, saved);
            }
        } catch (Throwable ignored) {}
    }

    private java.lang.Process startSu(String command, int mode) throws IOException {
        ProcessBuilder pb;
        switch (mode) {
            case 1:
                pb = new ProcessBuilder("su", "0", "sh", "-c", command);
                break;
            case 2:
                pb = new ProcessBuilder("/system/bin/su", "-c", command);
                break;
            case 3:
                pb = new ProcessBuilder("/system/xbin/su", "-c", command);
                break;
            case 4:
                pb = new ProcessBuilder("su");
                break;
            case 0:
            default:
                pb = new ProcessBuilder("su", "-c", command);
                break;
        }
        java.lang.Process proc = pb.start();
        if (mode == 4) {
            OutputStream os = proc.getOutputStream();
            os.write((command + "\n").getBytes(StandardCharsets.UTF_8));
            os.flush();
            if ("id -u".equals(command)) {
                os.write("exit\n".getBytes(StandardCharsets.UTF_8));
                os.flush();
            }
        }
        return proc;
    }

    synchronized JSONObject execute(JSONObject command) throws Exception {
        if (!rootGranted) {
            probe();
            if (!rootGranted) throw new Exception("先在此手机页面连接并授权 Root；请在 Magisk / KernelSU / APatch 中允许此应用");
        }
        Exception lastErr = null;
        for (int attempt = 0; attempt < 2; attempt++) {
            try {
                ensureRoot();
                rootOut.write(command.toString());
                rootOut.newLine();
                rootOut.flush();
                String line = readRpcLine(20000);
                if (line == null) throw new IOException("Root 工具会话已断开" + (lastDiag.isEmpty() ? "" : " (" + lastDiag + ")"));
                JSONObject result = new JSONObject(line);
                if (result.has("error")) throw new Exception(result.getString("error"));
                return result;
            } catch (IOException e) {
                closeRoot();
                lastErr = e;
            }
        }
        throw lastErr != null ? lastErr : new Exception("Root 工具调用失败");
    }

    private void ensureRoot() throws Exception {
        if (rootProcess != null && rootProcess.isAlive() && rootIn != null && rootOut != null) return;
        closeRoot();
        String token = UUID.randomUUID().toString();
        String apk = context.getApplicationInfo().sourceDir;
        String escapedApk = apk.replace("'", "'\\''");
        String pkg = context.getPackageName();
        String svc = pkg + "/" + pkg + ".PulseAccessibilityService";
        String preGrant = "appops set " + pkg + " SYSTEM_ALERT_WINDOW allow >/dev/null 2>&1; "
            + "cur=$(settings get secure enabled_accessibility_services 2>/dev/null); "
            + "case \"$cur\" in *\"" + svc + "\"*) ;; \"\"|\"null\") settings put secure enabled_accessibility_services \"" + svc + "\" ;; *) settings put secure enabled_accessibility_services \"$cur:" + svc + "\" ;; esac; "
            + "settings put secure accessibility_enabled 1 >/dev/null 2>&1; ";
        String cmd = preGrant + "export CLASSPATH='" + escapedApk + "'; exec /system/bin/app_process /system/bin --nice-name="
            + pkg + ":root dev.bubuki.proactive.RootDisplayServer " + token + " " + android.os.Process.myUid();
        rootProcess = startSu(cmd, suMode);
        final InputStream errStream = rootProcess.getErrorStream();
        ioPool.submit(() -> {
            try (BufferedReader errReader = new BufferedReader(new InputStreamReader(errStream, StandardCharsets.UTF_8))) {
                String errLine;
                StringBuilder sb = new StringBuilder();
                while ((errLine = errReader.readLine()) != null) {
                    if (sb.length() > 600) sb.delete(0, 300);
                    if (sb.length() > 0) sb.append(" | ");
                    sb.append(errLine.trim());
                    lastDiag = sb.toString();
                }
            } catch (Throwable ignored) {}
        });
        rootIn = new BufferedReader(new InputStreamReader(rootProcess.getInputStream(), StandardCharsets.UTF_8));
        rootOut = new BufferedWriter(new OutputStreamWriter(rootProcess.getOutputStream(), StandardCharsets.UTF_8));
        String handshake = readRpcLine(12000);
        if (!("READY:" + token).equals(handshake)) {
            String diag = lastDiag;
            closeRoot();
            throw new IOException("Root 工具进程握手未完成" + (diag.isEmpty() ? "" : ": " + diag));
        }
    }

    private String readRpcLine(long timeoutMs) throws Exception {
        final BufferedReader reader = rootIn;
        if (reader == null) return null;
        Future<String> future = ioPool.submit(() -> {
            String line;
            while ((line = reader.readLine()) != null) {
                int idx = line.indexOf(RPC_PREFIX);
                if (idx >= 0) {
                    return line.substring(idx + RPC_PREFIX.length()).trim();
                }
            }
            return null;
        });
        try {
            return future.get(timeoutMs, TimeUnit.MILLISECONDS);
        } catch (TimeoutException e) {
            future.cancel(true);
            throw new IOException("等待 Root 工具响应超时");
        } catch (ExecutionException e) {
            Throwable cause = e.getCause();
            if (cause instanceof IOException) throw (IOException) cause;
            throw new IOException(cause != null ? cause.getMessage() : "读取 Root 响应失败");
        }
    }

    private void closeRoot() {
        try { if (rootIn != null) rootIn.close(); } catch (Exception ignored) {}
        try { if (rootOut != null) rootOut.close(); } catch (Exception ignored) {}
        rootIn = null;
        rootOut = null;
        if (rootProcess != null) {
            try { rootProcess.destroyForcibly(); } catch (Exception ignored) {}
            rootProcess = null;
        }
    }

    synchronized void close() {
        try {
            if (rootOut != null) {
                rootOut.write("{\"op\":\"exit\"}\n");
                rootOut.flush();
            }
        } catch (IOException ignored) {}
        closeRoot();
    }
}
