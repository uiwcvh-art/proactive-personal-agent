package dev.bubuki.proactive;
import android.content.*;
public class BootReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context c,Intent i){try{
        if(!Intent.ACTION_BOOT_COMPLETED.equals(i.getAction()))return;
        if(SecurePrefs.read(c).optBoolean("background",true))c.startForegroundService(new Intent(c,AgentForegroundService.class));
    }catch(Exception ignored){}}
}
