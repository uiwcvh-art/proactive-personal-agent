package dev.bubuki.proactive;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;
import org.json.JSONObject;

/** Fixed-argument Root tools, with bounded output and no shell interpolation. */
final class RootCommands {
    private static final ScheduledExecutorService TIMER=Executors.newSingleThreadScheduledExecutor(r->{Thread t=new Thread(r,"root-command-timeout");t.setDaemon(true);return t;});
    static String run(String... args)throws Exception {
        java.lang.Process process=new ProcessBuilder(args).redirectErrorStream(true).start();AtomicBoolean timedOut=new AtomicBoolean();
        ScheduledFuture<?> deadline=TIMER.schedule(()->{timedOut.set(true);try{process.getInputStream().close();}catch(Throwable ignored){}process.destroyForcibly();},12,TimeUnit.SECONDS);
        try(InputStream input=process.getInputStream()){
            String output=new String(Io.read(input,1000000),StandardCharsets.UTF_8);
            try { process.waitFor(2, TimeUnit.SECONDS); } catch (Throwable ignored) {}
            if(timedOut.get()) {
                if (!output.isEmpty()) return output;
                throw new Exception("Root 系统命令超时");
            }
            if(process.exitValue()!=0)throw new Exception(output.substring(0,Math.min(250,output.length())));return output;
        }finally{deadline.cancel(false);if(process.isAlive())process.destroyForcibly();}
    }

    static JSONObject runShell(String rawCmd, int timeoutSec) throws Exception {
        int waitSec = Math.max(3, Math.min(60, timeoutSec));
        java.lang.Process process = new ProcessBuilder("sh", "-c", rawCmd).redirectErrorStream(true).start();
        AtomicBoolean timedOut = new AtomicBoolean(false);
        ScheduledFuture<?> deadline = TIMER.schedule(() -> {
            timedOut.set(true);
            try { process.getInputStream().close(); } catch (Throwable ignored) {}
            process.destroyForcibly();
        }, waitSec, TimeUnit.SECONDS);
        try (InputStream input = process.getInputStream()) {
            String output = new String(Io.read(input, 1000000), StandardCharsets.UTF_8);
            try { process.waitFor(2, TimeUnit.SECONDS); } catch (Throwable ignored) {}
            int exitCode = timedOut.get() ? 124 : (process.isAlive() ? 124 : process.exitValue());
            String capped = output.length() > 24000 ? output.substring(0, 24000) + "\n...[truncated]" : output;
            JSONObject res = new JSONObject()
                .put("uid", android.os.Process.myUid())
                .put("ok", true)
                .put("exit_code", exitCode)
                .put("timeout", timedOut.get())
                .put("output", capped);
            if (timedOut.get()) {
                res.put("partial_output", capped);
            }
            return res;
        } finally {
            deadline.cancel(false);
            if (process.isAlive()) process.destroyForcibly();
        }
    }
}
