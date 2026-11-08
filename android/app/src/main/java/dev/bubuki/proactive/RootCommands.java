package dev.bubuki.proactive;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;

/** Fixed-argument Root tools, with bounded output and no shell interpolation. */
final class RootCommands {
    private static final ScheduledExecutorService TIMER=Executors.newSingleThreadScheduledExecutor(r->{Thread t=new Thread(r,"root-command-timeout");t.setDaemon(true);return t;});
    static String run(String... args)throws Exception {
        java.lang.Process process=new ProcessBuilder(args).redirectErrorStream(true).start();AtomicBoolean timedOut=new AtomicBoolean();
        ScheduledFuture<?> deadline=TIMER.schedule(()->{timedOut.set(true);try{process.getInputStream().close();}catch(Throwable ignored){}process.destroyForcibly();},8,TimeUnit.SECONDS);
        try(InputStream input=process.getInputStream()){
            String output=new String(Io.read(input,1000000),StandardCharsets.UTF_8);process.waitFor();
            if(timedOut.get())throw new Exception("Root 系统命令超时");
            if(process.exitValue()!=0)throw new Exception(output.substring(0,Math.min(250,output.length())));return output;
        }finally{deadline.cancel(false);if(process.isAlive())process.destroyForcibly();}
    }
}
