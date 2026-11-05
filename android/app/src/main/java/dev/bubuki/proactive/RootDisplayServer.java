package dev.bubuki.proactive;

import android.os.Looper;
import java.io.*;
import java.nio.charset.StandardCharsets;

/**
 * Root helper entry point launched via app_process.
 * Communicates with PrivilegeManager over direct anonymous pipes (stdin/stdout)
 * using a tagged line protocol ("PULSE_RPC:") so SELinux unix_stream_socket rules
 * and framework stdout chatter can never interfere.
 */
public final class RootDisplayServer {
    private static final String RPC_PREFIX = "PULSE_RPC:";

    public static void main(String[] args) throws Exception {
        if (android.os.Process.myUid() != 0 && android.os.Process.myUid() != 2000) {
            throw new SecurityException("Root display tools require uid 0 or 2000");
        }
        final PrintStream rpcOut = new PrintStream(new FileOutputStream(FileDescriptor.out), true, "UTF-8");
        final BufferedReader rpcIn = new BufferedReader(new InputStreamReader(new FileInputStream(FileDescriptor.in), StandardCharsets.UTF_8));
        // Redirect System.out to System.err so any Android framework/vendor println never pollutes our RPC pipe
        System.setOut(System.err);

        if (Looper.myLooper() == null) Looper.prepareMainLooper();
        final DisplayRuntime runtime = new DisplayRuntime(DisplayRuntime.systemContext());

        if (args.length == 1 && args[0].equals("--verify")) {
            try {
                rpcOut.println(runtime.execute("{\"op\":\"create\"}"));
                org.json.JSONObject guard = new org.json.JSONObject(runtime.execute("{\"op\":\"launch\",\"component\":\"dev.bubuki.proactive/.MainActivity\"}"));
                boolean refused = guard.optString("error").contains("前台");
                rpcOut.println(new org.json.JSONObject().put("foreground_guard_refused", refused));
                if (!refused) throw new IllegalStateException("Foreground protection check failed; start MainActivity on display 0 before --verify");
                rpcOut.println(runtime.execute("{\"op\":\"launch\",\"component\":\"dev.bubuki.proactive/.DisplayTestActivity\"}"));
                Thread.sleep(2500);
                org.json.JSONObject shot = new org.json.JSONObject(runtime.execute("{\"op\":\"capture\"}"));
                if (shot.has("image")) {
                    try (FileOutputStream out = new FileOutputStream("/data/local/tmp/proactive-virtual-display.png")) {
                        out.write(android.util.Base64.decode(shot.getString("image"), android.util.Base64.DEFAULT));
                    }
                    shot.remove("image");
                    shot.put("screenshot_saved", true);
                }
                rpcOut.println(shot);
                rpcOut.println(runtime.execute("{\"op\":\"tap\",\"x\":270,\"y\":570}"));
                Thread.sleep(500);
                org.json.JSONObject after = new org.json.JSONObject(runtime.execute("{\"op\":\"capture\"}"));
                if (after.has("image")) {
                    try (FileOutputStream out = new FileOutputStream("/data/local/tmp/proactive-virtual-display-after.png")) {
                        out.write(android.util.Base64.decode(after.getString("image"), android.util.Base64.DEFAULT));
                    }
                }
            } finally {
                rpcOut.println(runtime.execute("{\"op\":\"release\"}"));
                runtime.close();
            }
            return;
        }

        final String token = args.length > 0 ? args[0] : "";
        new Thread(() -> {
            try {
                rpcOut.println(RPC_PREFIX + "READY:" + token);
                rpcOut.flush();
                String line;
                while ((line = rpcIn.readLine()) != null) {
                    line = line.trim();
                    if (line.isEmpty()) continue;
                    try {
                        org.json.JSONObject parsed = new org.json.JSONObject(line);
                        if ("exit".equals(parsed.optString("op"))) break;
                    } catch (Throwable ignored) {}
                    String response = runtime.execute(line);
                    rpcOut.println(RPC_PREFIX + response);
                    rpcOut.flush();
                }
            } catch (Throwable t) {
                t.printStackTrace(System.err);
            } finally {
                runtime.close();
                System.exit(0);
            }
        }, "pulse-root-rpc").start();

        Looper.loop();
    }
}
