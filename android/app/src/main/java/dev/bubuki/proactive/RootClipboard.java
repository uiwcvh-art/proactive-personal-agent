package dev.bubuki.proactive;

import android.content.Context;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.*;
import org.json.JSONObject;

/** Root-authorized paste. Only clipboard access drops to its real package owner uid;
 * display input remains in the uid 0 parent. There is no non-Root authorization path. */
final class RootClipboard {
    RootClipboard(Context ignored){}
    private static String line(BufferedReader input)throws Exception {
        FutureTask<String> read=new FutureTask<>(input::readLine);Thread reader=new Thread(read,"pulse-clipboard-reply");reader.setDaemon(true);reader.start();
        String value=read.get(8,TimeUnit.SECONDS);if(value==null)throw new IOException("剪贴板会话已断开");
        JSONObject result=new JSONObject(value);if(result.has("error"))throw new IOException(result.getString("error"));return value;
    }
    void paste(int displayId,String text)throws Exception {
        if(android.os.Process.myUid()!=0||displayId<=0)throw new SecurityException("只允许 Root 向独立屏输入");
        java.lang.Process broker=new ProcessBuilder("app_process","/system/bin","dev.bubuki.proactive.RootClipboardBroker").redirectError(new File("/dev/null")).start();
        try(BufferedReader in=new BufferedReader(new InputStreamReader(broker.getInputStream(),StandardCharsets.UTF_8));BufferedWriter out=new BufferedWriter(new OutputStreamWriter(broker.getOutputStream(),StandardCharsets.UTF_8))){
            out.write(new JSONObject().put("text",text).toString()+"\n");out.flush();line(in);
            try{RootCommands.run("input","-d",String.valueOf(displayId),"keyevent","279");}
            finally{out.write("restore\n");out.flush();line(in);}
        }finally{if(broker.isAlive())broker.destroy();}
    }
}
