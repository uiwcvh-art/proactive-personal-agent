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
    private final Object pipeLock = new Object();
    private final java.util.concurrent.atomic.AtomicLong reqSeq = new java.util.concurrent.atomic.AtomicLong(1L);
    private final ConcurrentHashMap<String, CompletableFuture<String>> pendingRpcs = new ConcurrentHashMap<>();
    private volatile CompletableFuture<String> handshakeFuture;
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
            lastDiag = "ok";
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

    JSONObject execute(JSONObject command) throws Exception {
        if (!rootGranted) {
            probe();
            if (!rootGranted) throw new Exception("先在此手机页面连接并授权 Root；请在 Magisk / KernelSU / APatch 中允许此应用");
        }
        String op = command.optString("op", "");
        long timeoutMs = "shell".equals(op) ? 30000L : 20000L;
        Exception lastErr = null;
        for (int attempt = 0; attempt < 2; attempt++) {
            String reqId = "r" + reqSeq.getAndIncrement();
            CompletableFuture<String> future = new CompletableFuture<>();
            pendingRpcs.put(reqId, future);
            try {
                JSONObject wireCmd = new JSONObject(command.toString());
                wireCmd.put("_req_id", reqId);
                synchronized (pipeLock) {
                    ensureRootLocked();
                    rootOut.write(wireCmd.toString());
                    rootOut.newLine();
                    rootOut.flush();
                }
                String line = future.get(timeoutMs, TimeUnit.MILLISECONDS);
                if (line == null) throw new IOException("Root 工具会话已断开" + (lastDiag.isEmpty() ? "" : " (" + lastDiag + ")"));
                JSONObject result = new JSONObject(line);
                result.remove("_req_id");
                if (result.has("error")) throw new Exception(result.getString("error"));
                return result;
            } catch (TimeoutException te) {
                // Single request timeout: do NOT close the shared pipe or interrupt other active requests!
                throw new Exception("等待 Root 工具响应超时 (" + op + ")");
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw e;
            } catch (ExecutionException ee) {
                Throwable cause = ee.getCause();
                if (cause instanceof IOException) {
                    synchronized (pipeLock) { closeRootLocked(); }
                    lastErr = (IOException) cause;
                } else {
                    throw (cause instanceof Exception) ? (Exception) cause : new Exception(cause);
                }
            } catch (IOException e) {
                synchronized (pipeLock) { closeRootLocked(); }
                lastErr = e;
            } finally {
                pendingRpcs.remove(reqId);
            }
        }
        synchronized (pipeLock) {
            if (rootProcess == null) {
                rootGranted = false;
                persistHint(false, suMode);
            }
        }
        throw lastErr != null ? lastErr : new Exception("Root 工具调用失败");
    }

    private void ensureRootLocked() throws Exception {
        if (rootProcess != null && rootProcess.isAlive() && rootIn != null && rootOut != null) return;
        closeRootLocked();
        lastDiag = "";
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
        final java.lang.Process proc = startSu(cmd, suMode);
        rootProcess = proc;
        final InputStream errStream = proc.getErrorStream();
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
        final BufferedReader reader = new BufferedReader(new InputStreamReader(proc.getInputStream(), StandardCharsets.UTF_8));
        rootIn = reader;
        rootOut = new BufferedWriter(new OutputStreamWriter(proc.getOutputStream(), StandardCharsets.UTF_8));
        final CompletableFuture<String> hs = new CompletableFuture<>();
        handshakeFuture = hs;
        ioPool.submit(() -> {
            try {
                String line;
                while ((line = reader.readLine()) != null) {
                    int idx = line.indexOf(RPC_PREFIX);
                    if (idx < 0) continue;
                    String payload = line.substring(idx + RPC_PREFIX.length()).trim();
                    if (payload.startsWith("READY:")) {
                        CompletableFuture<String> h = handshakeFuture;
                        if (h != null && !h.isDone()) h.complete(payload);
                        continue;
                    }
                    String targetReqId = "";
                    try {
                        JSONObject obj = new JSONObject(payload);
                        targetReqId = obj.optString("_req_id", "");
                    } catch (Throwable ignored) {}
                    if (!targetReqId.isEmpty()) {
                        CompletableFuture<String> f = pendingRpcs.remove(targetReqId);
                        if (f != null) f.complete(payload);
                    } else {
                        for (java.util.Map.Entry<String, CompletableFuture<String>> entry : pendingRpcs.entrySet()) {
                            if (pendingRpcs.remove(entry.getKey(), entry.getValue())) {
                                entry.getValue().complete(payload);
                                break;
                            }
                        }
                    }
                }
            } catch (Throwable ignored) {
            } finally {
                CompletableFuture<String> h = handshakeFuture;
                if (h != null && !h.isDone()) h.completeExceptionally(new IOException("Root 管道已关闭"));
                for (java.util.Map.Entry<String, CompletableFuture<String>> entry : pendingRpcs.entrySet()) {
                    if (pendingRpcs.remove(entry.getKey(), entry.getValue())) {
                        entry.getValue().completeExceptionally(new IOException("Root 管道已断开"));
                    }
                }
            }
        });
        String handshake;
        try {
            handshake = hs.get(12000, TimeUnit.MILLISECONDS);
        } catch (Exception e) {
            String diag = lastDiag;
            if ("ok".equalsIgnoreCase(diag)) diag = "";
            String exitInfo = "";
            try {
                if (!proc.isAlive()) exitInfo = "exit=" + proc.exitValue();
            } catch (Throwable ignored) {}
            closeRootLocked();
            String detail = !diag.isEmpty() ? diag : (!exitInfo.isEmpty() ? exitInfo : "daemon_handshake_timeout");
            throw new IOException("Root 工具进程握手未完成 (" + detail + ")");
        }
        if (!("READY:" + token).equals(handshake)) {
            String diag = lastDiag;
            if ("ok".equalsIgnoreCase(diag)) diag = "";
            closeRootLocked();
            throw new IOException("Root 工具进程握手未完成" + (diag.isEmpty() ? " (invalid_token)" : " (" + diag + ")"));
        }
    }

    private void closeRootLocked() {
        try { if (rootIn != null) rootIn.close(); } catch (Exception ignored) {}
        try { if (rootOut != null) rootOut.close(); } catch (Exception ignored) {}
        rootIn = null;
        rootOut = null;
        if (rootProcess != null) {
            try { rootProcess.destroyForcibly(); } catch (Exception ignored) {}
            rootProcess = null;
        }
    }

    void close() {
        synchronized (pipeLock) {
            try {
                if (rootOut != null) {
                    rootOut.write("{\"op\":\"exit\"}\n");
                    rootOut.flush();
                }
            } catch (IOException ignored) {}
            closeRootLocked();
        }
    }
}
