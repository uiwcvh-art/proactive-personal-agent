package dev.bubuki.proactive;
import android.service.notification.*;
import android.content.*;
public class AgentNotificationListener extends NotificationListenerService {
    // Do not upload third-party notification contents. This listener only reports local permission/connection state.
    @Override public void onListenerConnected(){sendBroadcast(new Intent(getPackageName()+".UPDATE").setPackage(getPackageName()));}
    @Override public void onNotificationPosted(StatusBarNotification n){if(n.getPackageName().equals(getPackageName()))sendBroadcast(new Intent(getPackageName()+".UPDATE").setPackage(getPackageName()));}
}
