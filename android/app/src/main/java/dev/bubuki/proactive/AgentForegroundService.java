package dev.bubuki.proactive;

import android.app.*;
import android.content.*;
import android.content.pm.ServiceInfo;
import android.os.*;
import org.json.*;
import java.util.*;
import java.util.concurrent.*;
import okhttp3.*;

public class AgentForegroundService extends Service {
    static volatile boolean connected=false;
    static volatile boolean running=false;
    private final Handler handler=new Handler(Looper.getMainLooper());
    private final ExecutorService io=Executors.newSingleThreadExecutor();
    private final ExecutorService cmdPool=Executors.newCachedThreadPool();
    private WebSocket socket;
    private byte[] sessionKey;
    private boolean stopped=false;
    private int retrySeconds=2;
    private OverlayController overlay;
    private JSONObject pendingOverlay;
    private BroadcastReceiver screen;
    private final Set<String> notified=new HashSet<>();
    private final Set<String> nonces=new HashSet<>();
    private long snoozeUntil=0;
    @Override public void onCreate(){
        super.onCreate();running=true;overlay=new OverlayController(this);
        NotificationManager nm=getSystemService(NotificationManager.class);
        nm.createNotificationChannel(new NotificationChannel("pulse_connection","云端连接",NotificationManager.IMPORTANCE_LOW));
        nm.createNotificationChannel(new NotificationChannel("pulse_nudges","好奇心与催单",NotificationManager.IMPORTANCE_HIGH));
        Intent open=new Intent(this,NativePulseActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK|Intent.FLAG_ACTIVITY_SINGLE_TOP);
        Notification notification=new Notification.Builder(this,"pulse_connection").setSmallIcon(R.drawable.ic_launcher)
            .setContentTitle("共生 · 轻声等候").setContentText("加密连接日本节点 · 平时没有悬浮球")
            .setContentIntent(PendingIntent.getActivity(this,0,open,PendingIntent.FLAG_IMMUTABLE|PendingIntent.FLAG_UPDATE_CURRENT))
            .setOngoing(true).setShowWhen(false).build();
        if(Build.VERSION.SDK_INT>=34)startForeground(10,notification,ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);else startForeground(10,notification);
        screen=new BroadcastReceiver(){@Override public void onReceive(Context c,Intent i){
            if(Intent.ACTION_SCREEN_OFF.equals(i.getAction()))overlay.hide();
            else if(Intent.ACTION_USER_PRESENT.equals(i.getAction())||Intent.ACTION_SCREEN_ON.equals(i.getAction()))tryShowOverlay();
        }};
        IntentFilter filter=new IntentFilter();filter.addAction(Intent.ACTION_SCREEN_ON);filter.addAction(Intent.ACTION_SCREEN_OFF);filter.addAction(Intent.ACTION_USER_PRESENT);
        if(Build.VERSION.SDK_INT>=33)registerReceiver(screen,filter,Context.RECEIVER_NOT_EXPORTED);else registerReceiver(screen,filter);
        connect();
    }
    @Override public int onStartCommand(Intent intent,int flags,int startId){return START_STICKY;}
    private void connect(){
        if(stopped)return;
        io.execute(()->{try{
            JSONObject settings=SecurePrefs.read(this);
            if(!settings.optBoolean("background",true)){handler.post(this::stopSelf);return;}
            SecureTransport.Session s=new SecureTransport(this).session(new JSONObject());
            sessionKey=s.key;nonces.clear();
            OkHttpClient client=SecureTransport.HTTP.newBuilder().pingInterval(30,TimeUnit.SECONDS).readTimeout(0,TimeUnit.SECONDS).build();
            String url=s.base.replaceFirst("^http","ws")+"/ws/client";
            socket=client.newWebSocket(new Request.Builder().url(url).build(),new WebSocketListener(){
                @Override public void onOpen(WebSocket ws,Response response){ws.send(s.packet.toString());}
                @Override public void onMessage(WebSocket ws,String text){try{
                    JSONObject packet=new JSONObject(text);String nonce=packet.getString("nonce");
                    synchronized(nonces){if(nonces.contains(nonce))return;if(nonces.size()>1000)nonces.clear();nonces.add(nonce);}
                    JSONObject event=SecureTransport.decrypt(s.key,packet);
                    handler.post(()->handle(event));
                }catch(Exception e){ws.close(1003,"invalid encrypted frame");}}
                @Override public void onFailure(WebSocket ws,Throwable t,Response r){handler.post(()->retry());}
                @Override public void onClosed(WebSocket ws,int code,String reason){handler.post(()->retry());}
                @Override public void onClosing(WebSocket ws,int code,String reason){ws.close(code,reason);}
            });
        }catch(Exception e){handler.post(()->retry());}});
    }
    private void retry(){
        connected=false;broadcast();if(stopped)return;
        handler.removeCallbacks(reconnect);handler.postDelayed(reconnect,retrySeconds*1000L);retrySeconds=Math.min(60,retrySeconds*2);
    }
    private final Runnable reconnect=this::connect;
    private void handle(JSONObject event){
        try{NativePulseModel.dispatchRealtimeEvent(event);}catch(Exception ignored){}
        String type=event.optString("type");
        if(type.equals("connected")){connected=true;retrySeconds=2;snoozeUntil=event.optLong("snooze_until")*1000;}
        if(type.equals("snooze")){snoozeUntil=event.optLong("until")*1000;pendingOverlay=null;overlay.hide();for(String id:notified)getSystemService(NotificationManager.class).cancel(id.hashCode());}
        if(type.equals("ack")){
            JSONArray ids=event.optJSONArray("ids");
            if(ids!=null)for(int i=0;i<ids.length();i++){
                String id=ids.optString(i);getSystemService(NotificationManager.class).cancel(id.hashCode());
                if(pendingOverlay!=null&&id.equals(pendingOverlay.optString("id"))){pendingOverlay=null;overlay.hide();}
            }
        }
        if(type.equals("phone_workspace_command")){
            String reqId=event.optString("request_id","");
            String op=event.optString("op","status");
            String botId=event.optString("bot_id","companion");
            JSONObject args=event.optJSONObject("args");
            if(args==null)args=new JSONObject();
            final JSONObject cmdArgs=args;
            cmdPool.execute(()->{
                JSONObject res=new JSONObject();
                try{
                    if(op.equals("run")||op.equals("task")){
                        String goal=cmdArgs.optString("goal",cmdArgs.optString("text","")).trim();
                        res=RootPhoneHarness.submitFromBridge(this,botId,goal);
                    }else{
                        JSONObject input=new JSONObject(cmdArgs.toString());
                        input.put("op",op);
                        if(Arrays.asList("capture","ui_tree","dump","tap","tap_node","swipe","text","type_node","key","launch").contains(op)){
                            JSONObject st=RootPhoneHarness.status(this);
                            if(!st.optBoolean("root")){
                                RootPhoneHarness.command(this,new JSONObject().put("op","connect"));
                                st=RootPhoneHarness.status(this);
                            }
                            JSONObject disp=st.optJSONObject("display");
                            if(disp==null||disp.optInt("displayId",-1)<=0){
                                RootPhoneHarness.command(this,new JSONObject().put("op","create"));
                            }
                        }
                        res=RootPhoneHarness.command(this,input);
                        if(Arrays.asList("ui_tree","dump","tree","nodes","read_screen").contains(op)){
                            int nc=res.optInt("nodeCount",0);
                            String pkg=res.optString("package","");
                            if(nc<3||"com.tencent.mm".equals(pkg)){
                                try{
                                    JSONObject snap=RootPhoneHarness.command(this,new JSONObject().put("op","preview").put("since",-1));
                                    if(snap.has("image"))res.put("image",snap.getString("image")).put("width",snap.optInt("width",1080)).put("height",snap.optInt("height",2400));
                                }catch(Exception ignored){}
                            }
                        }
                        if(Arrays.asList("launch","tap","tap_node","swipe","text","type_node","key").contains(op)){
                            Thread.sleep(op.equals("launch") ? 750 : 420);
                            try{
                                JSONObject tree=RootPhoneHarness.command(this,new JSONObject().put("op","ui_tree").put("refresh",true));
                                if(tree.has("summary"))res.put("ui_tree",tree.getString("summary")).put("node_count",tree.optInt("nodeCount",0)).put("package",tree.optString("package",""));
                            }catch(Exception ignored){}
                            try{
                                JSONObject snap=RootPhoneHarness.command(this,new JSONObject().put("op","preview").put("since",-1));
                                if(snap.has("image"))res.put("image",snap.getString("image")).put("width",snap.optInt("width",1080)).put("height",snap.optInt("height",2400));
                            }catch(Exception ignored){}
                        }
                    }
                }catch(Exception e){
                    try{res.put("error",MainActivity.safeError(e));}catch(Exception ignored){}
                }
                try{
                    if(socket!=null&&sessionKey!=null){
                        JSONObject reply=new JSONObject().put("type","phone_workspace_result").put("request_id",reqId).put("result",res);
                        socket.send(SecureTransport.encrypt(sessionKey,reply).toString());
                    }
                }catch(Exception ignored){}
            });
            return;
        }
        if(type.equals("user_notification")&&System.currentTimeMillis()>=snoozeUntil){
            String id=event.optString("id",UUID.randomUUID().toString());
            if(!notified.contains(id)){
                String title=event.optString("title","助理提醒");
                String body=event.optString("body","");
                String botId=event.optString("bot_id","companion");
                notifyCustom(id,title,body,botId);
                notified.add(id);
            }
        }
        if(type.equals("escalation")&&System.currentTimeMillis()>=snoozeUntil){
            int stage=event.optInt("stage");String id=event.optString("id");
            String text=event.optString("text","");JSONObject m=event.optJSONObject("message");if(m!=null)text=m.optString("text",text);
            if(stage==1&&!notified.contains(id)){
                String botName=m!=null?m.optString("from_bot_name",event.optString("bot_name","白kawa")):event.optString("bot_name","白kawa");
                notifyCustom(id,botName,text,m==null?event.optString("bot_id","companion"):m.optString("bot_id","companion"));notified.add(id);if(notified.size()>600)notified.clear();
            }
            if(stage==3){pendingOverlay=event;tryShowOverlay();}
        }
        broadcast();
    }
    private void tryShowOverlay(){
        if(pendingOverlay==null||System.currentTimeMillis()<snoozeUntil)return;
        PowerManager pm=getSystemService(PowerManager.class);KeyguardManager km=getSystemService(KeyguardManager.class);
        if(pm.isInteractive()&&!km.isKeyguardLocked())overlay.show(pendingOverlay.optString("id"),pendingOverlay.optString("text","这份成果等你拍板。"));
    }
    private void notifyCustom(String id,String title,String text,String botId){
        Intent open=new Intent(this,NativePulseActivity.class).setFlags(Intent.FLAG_ACTIVITY_NEW_TASK|Intent.FLAG_ACTIVITY_SINGLE_TOP).putExtra("openBot",botId);
        PendingIntent tap=PendingIntent.getActivity(this,id.hashCode(),open,PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
        Notification n=new Notification.Builder(this,"pulse_nudges").setSmallIcon(R.drawable.ic_launcher).setContentTitle(title)
            .setContentText(text).setStyle(new Notification.BigTextStyle().bigText(text)).setContentIntent(tap).setAutoCancel(true)
            .setCategory(Notification.CATEGORY_REMINDER)
            .addAction(new Notification.Action.Builder(null,"我看完了",AgentActionReceiver.pending(this,"ack",id)).build())
            .addAction(new Notification.Action.Builder(null,"别吵 1 小时",AgentActionReceiver.pending(this,"snooze",id)).build()).build();
        try{getSystemService(NotificationManager.class).notify(id.hashCode(),n);}catch(SecurityException ignored){}
    }
    private void broadcast(){sendBroadcast(new Intent(getPackageName()+".UPDATE").setPackage(getPackageName()));}
    @Override public void onDestroy(){stopped=true;running=false;connected=false;handler.removeCallbacksAndMessages(null);if(socket!=null)socket.close(1000,"stopped by user");overlay.hide();if(screen!=null)unregisterReceiver(screen);io.shutdownNow();cmdPool.shutdownNow();super.onDestroy();}
    @Override public IBinder onBind(Intent i){return null;}
}
