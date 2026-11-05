package dev.bubuki.proactive;

import android.content.*;
import android.os.*;
import java.io.*;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.util.UUID;
import org.json.JSONObject;

/** Private pipe child created by the Root parent. Android's clipboard verifies
 * the package/uid pair even for uid 0; use the actual shell package owner for
 * clipboard reads, never misattribute uid 0. Binder access references Mika,
 * e9daf53, Apache-2.0. No socket, Activity, exported service or input tool here. */
public final class RootClipboardBroker {
    public static void main(String[] args)throws Exception {
        if(android.os.Process.myUid()!=0)throw new SecurityException("Clipboard broker must be started by Root");
        android.system.Os.setgid(2000);android.system.Os.setuid(2000);
        Looper.prepareMainLooper();Context base=DisplayRuntime.systemContext();
        Context context=new ContextWrapper(base){
            @Override public String getPackageName(){return "com.android.shell";}
            @Override public String getOpPackageName(){return "com.android.shell";}
            @Override public Context getApplicationContext(){return this;}
            @Override public android.content.AttributionSource getAttributionSource(){return new android.content.AttributionSource.Builder(2000).setPackageName("com.android.shell").build();}
        };
        java.lang.reflect.Constructor<ClipboardManager> constructor=ClipboardManager.class.getDeclaredConstructor(Context.class,Handler.class);constructor.setAccessible(true);
        ClipboardManager manager=constructor.newInstance(context,new Handler(Looper.getMainLooper()));
        Object binder=Class.forName("android.os.ServiceManager").getMethod("getService",String.class).invoke(null,"clipboard");
        Object service=Class.forName("android.content.IClipboard$Stub").getMethod("asInterface",IBinder.class).invoke(null,binder);
        Class<?> type=Class.forName("android.content.IClipboard");Method getter;
        try{getter=type.getMethod("getPrimaryClip",String.class,String.class,int.class,int.class);}catch(NoSuchMethodException error){getter=type.getMethod("getPrimaryClip",String.class,String.class,int.class);}
        BufferedReader in=new BufferedReader(new InputStreamReader(System.in,StandardCharsets.UTF_8));PrintWriter out=new PrintWriter(new OutputStreamWriter(System.out,StandardCharsets.UTF_8),true);
        ClipData previous=read(getter,service);String label="pulse-input-"+UUID.randomUUID();boolean changed=false;
        try {
            String request=in.readLine();if(request==null)return;String text=new JSONObject(request).getString("text");
            ClipData pending=ClipData.newPlainText(label,text);PersistableBundle extras=new PersistableBundle();extras.putBoolean("android.content.extra.IS_SENSITIVE",true);pending.getDescription().setExtras(extras);
            manager.setPrimaryClip(pending);changed=true;out.println("{\"ready\":true}");
            in.readLine();
        }catch(Exception error){out.println(new JSONObject().put("error",error.getClass().getSimpleName()+": "+error.getMessage()));}
        finally{
            if(changed){ClipData current=read(getter,service);if(current!=null&&label.contentEquals(current.getDescription().getLabel())){if(previous==null)manager.clearPrimaryClip();else manager.setPrimaryClip(previous);}}
            out.println("{\"restored\":true}");
        }
        System.exit(0);
    }
    private static ClipData read(Method getter,Object service)throws Exception{return (ClipData)(getter.getParameterCount()==4?getter.invoke(service,"com.android.shell",null,0,0):getter.invoke(service,"com.android.shell",null,0));}
}
