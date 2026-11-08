package dev.bubuki.proactive;

import android.app.*;
import android.content.*;
import android.content.pm.ServiceInfo;
import android.media.AudioAttributes;
import android.media.Ringtone;
import android.media.RingtoneManager;
import android.net.Uri;
import android.os.*;
import org.json.*;
import java.util.*;
import java.util.concurrent.*;
import okhttp3.*;

public class AgentForegroundService extends Service {
    static volatile boolean connected=false;
    static volatile boolean running=false;
    static volatile long phoneAbortUntilMs=0L;
    static void abortPhoneCommands(Context ctx){
        phoneAbortUntilMs=System.currentTimeMillis()+5000L;
        try{
            if(RootPhoneHarness.isVirtualDisplayActive()){
                RootPhoneHarness.stop(ctx);
            }
        }catch(Exception ignored){}
    }
    private static volatile Ringtone activeAlarmRingtone=null;
    private static volatile Vibrator activeVibrator=null;
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
    private final Runnable autoStopAlarmRunnable=()->stopEmergencyAlarm(this);

    static synchronized void stopEmergencyAlarm(Context ctx){
        try{
            if(activeAlarmRingtone!=null){
                if(activeAlarmRingtone.isPlaying()) activeAlarmRingtone.stop();
                activeAlarmRingtone=null;
            }
        }catch(Exception ignored){}
        try{
            if(activeVibrator!=null){
                activeVibrator.cancel();
                activeVibrator=null;
            }
        }catch(Exception ignored){}
        if(ctx!=null){
            try{
                ctx.getSystemService(NotificationManager.class).cancel(99119);
            }catch(Exception ignored){}
        }
    }

    @Override public void onCreate(){
        super.onCreate();running=true;overlay=new OverlayController(this);
        NotificationManager nm=getSystemService(NotificationManager.class);
        nm.createNotificationChannel(new NotificationChannel("pulse_connection","云端连接",NotificationManager.IMPORTANCE_LOW));
        nm.createNotificationChannel(new NotificationChannel("pulse_nudges","好奇心与催单",NotificationManager.IMPORTANCE_HIGH));
        NotificationChannel alarmChan=new NotificationChannel("pulse_emergency_alarm","紧急闹钟提醒",NotificationManager.IMPORTANCE_HIGH);
        alarmChan.enableVibration(true);
        nm.createNotificationChannel(alarmChan);
        Intent open=new Intent(this,NativePulseActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK|Intent.FLAG_ACTIVITY_SINGLE_TOP);
        Notification notification=new Notification.Builder(this,"pulse_connection").setSmallIcon(R.drawable.ic_launcher)
            .setContentTitle("共生 · 轻声等候").setContentText("加密连接日本节点 · 平时没有悬浮球")
            .setContentIntent(PendingIntent.getActivity(this,0,open,PendingIntent.FLAG_IMMUTABLE|PendingIntent.FLAG_UPDATE_CURRENT))
            .setOngoing(true).setShowWhen(false).build();
        if(Build.VERSION.SDK_INT>=34)startForeground(10,notification,ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);else startForeground(10,notification);
        screen=new BroadcastReceiver(){@Override public void onReceive(Context c,Intent i){
            if(Intent.ACTION_SCREEN_OFF.equals(i.getAction())){
                overlay.hide();
                try{ RootPhoneHarness.scheduleAutoRelease(AgentForegroundService.this, 1500L); }catch(Throwable ignored){}
            }
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
            SecureTransport.Session s=new SecureTransport(this).session(new JSONObject(), true);
            sessionKey=s.key;nonces.clear();
            OkHttpClient client=SecureTransport.HTTP.newBuilder().pingInterval(28,TimeUnit.SECONDS).readTimeout(0,TimeUnit.SECONDS).build();
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
        String type=event.optString("type");
        String mySource=SecureTransport.getNodeSource(this);
        String evSource=event.optString("source","").trim();
        if(evSource.isEmpty()){
            JSONObject sub=event.optJSONObject("message");
            if(sub==null)sub=event.optJSONObject("stream");
            if(sub==null)sub=event.optJSONObject("execution");
            if(sub!=null)evSource=sub.optString("source","").trim();
        }
        if(!evSource.isEmpty()&&!evSource.equals(mySource)&&Arrays.asList("message","stream","execution","stopped","phone_workspace_command").contains(type)){
            return;
        }
        try{NativePulseModel.dispatchRealtimeEvent(event);}catch(Exception ignored){}
        if(type.equals("connected")){connected=true;retrySeconds=2;snoozeUntil=event.optLong("snooze_until")*1000;try{NativePulseModel.syncOnReconnect();}catch(Exception ignored){}}
        if(type.equals("heartbeat")){
            io.execute(()->{
                try{
                    if(socket!=null&&sessionKey!=null){
                        socket.send(SecureTransport.encrypt(sessionKey,new JSONObject().put("type","ping")).toString());
                    }
                }catch(Exception ignored){}
            });
            return;
        }
        if(type.equals("snooze")){
            snoozeUntil=event.optLong("until")*1000;pendingOverlay=null;overlay.hide();
            stopEmergencyAlarm(this);
            for(String id:notified)getSystemService(NotificationManager.class).cancel(id.hashCode());
        }
        if(type.equals("ack")){
            JSONArray ids=event.optJSONArray("ids");
            if(ids!=null)for(int i=0;i<ids.length();i++){
                String id=ids.optString(i);getSystemService(NotificationManager.class).cancel(id.hashCode());
                if(pendingOverlay!=null&&id.equals(pendingOverlay.optString("id"))){pendingOverlay=null;overlay.hide();}
            }
        }
        if(type.equals("emergency_alarm")){
            String action=event.optString("action","ring");
            if("stop".equals(action)){
                handler.removeCallbacks(autoStopAlarmRunnable);
                stopEmergencyAlarm(this);
            }else if(System.currentTimeMillis()>=snoozeUntil){
                String title=event.optString("title","🚨 紧急闹钟提醒");
                String body=event.optString("body","检测到十万火急事项，请立即查看！");
                String botId=event.optString("bot_id","companion");
                int durSec=Math.max(5,Math.min(30,event.optInt("duration_sec",15)));
                startEmergencyAlarm(title,body,botId,durSec);
            }
            return;
        }
        if(type.equals("phone_workspace_command")){
            String reqId=event.optString("request_id","");
            String op=event.optString("op","status");
            String botId=event.optString("bot_id","companion");
            JSONObject args=event.optJSONObject("args");
            if(args==null)args=new JSONObject();
            if("release".equals(op)&&args.optBoolean("force",false)){
                phoneAbortUntilMs=System.currentTimeMillis()+5000L;
            }
            final JSONObject cmdArgs=args;
            cmdPool.execute(()->{
                JSONObject res=new JSONObject();
                try{
                    if(!("release".equals(op)||"auto_release".equals(op))&&System.currentTimeMillis()<phoneAbortUntilMs){
                        throw new Exception("用户已停止执行");
                    }
                    if(("release".equals(op)||"auto_release".equals(op))&&!RootPhoneHarness.isVirtualDisplayActive()){
                        res=new JSONObject().put("ok",true).put("noop",true).put("created",false);
                    }else if(op.equals("run")||op.equals("task")){
                        String goal=cmdArgs.optString("goal",cmdArgs.optString("text","")).trim();
                        res=RootPhoneHarness.submitFromBridge(this,botId,goal);
                    }else{
                        JSONObject input=new JSONObject(cmdArgs.toString());
                        input.put("op",op);
                        int dispIdForSnap=-1;
                        String beforePkg="";
                        String beforeHash="";
                        int beforeNodes=-1;
                        long beforeFrameSeq=-1L;
                        String beforeImgSig="";
                        if(Arrays.asList("capture","ui_tree","dump","tap","tap_node","tap_and_type","long_press","batch","swipe","text","type_node","key","launch","open_uri","open_url").contains(op)){
                            JSONObject st=RootPhoneHarness.status(this);
                            if(!st.optBoolean("root")){
                                RootPhoneHarness.command(this,new JSONObject().put("op","connect"));
                                st=RootPhoneHarness.status(this);
                            }
                            JSONObject disp=st.optJSONObject("display");
                            if(disp!=null && disp.optBoolean("created",false)){
                                dispIdForSnap=disp.optInt("displayId",-1);
                            }
                            if(dispIdForSnap>0){
                                PulseAccessibilityService.CachedSnapshot prevSnap=PulseAccessibilityService.getCachedSnapshot(dispIdForSnap);
                                if(prevSnap!=null){
                                    beforePkg=prevSnap.getActivePackage();
                                    beforeHash=prevSnap.getTreeHash();
                                    beforeNodes=prevSnap.getNodes().size();
                                }
                                try{
                                    JSONObject prevFrame=RootPhoneHarness.command(this,new JSONObject().put("op","preview").put("since",-1));
                                    beforeFrameSeq=prevFrame.optLong("frameSequence",-1L);
                                    String bImg=prevFrame.optString("image","");
                                    if(!bImg.isEmpty()) beforeImgSig=bImg.length()+":"+bImg.hashCode();
                                }catch(Exception ignored){}
                            }
                        }
                        res=RootPhoneHarness.command(this,input);
                        if(Arrays.asList("ui_tree","dump","tree","nodes","read_screen").contains(op)){
                            try{
                                JSONObject snap=RootPhoneHarness.command(this,new JSONObject().put("op","preview").put("since",-1));
                                if(snap.has("image"))res.put("image",snap.getString("image")).put("width",snap.optInt("width",540)).put("height",snap.optInt("height",1200));
                            }catch(Exception ignored){}
                        }
                        if(Arrays.asList("launch","open_uri","open_url","tap","tap_node","tap_and_type","long_press","batch","swipe","text","type_node","key").contains(op)){
                            long settleMs=(op.equals("launch")||op.equals("open_uri")||op.equals("open_url")) ? 820L : (op.equals("swipe") ? 460L : 350L);
                            Thread.sleep(settleMs);
                            int ncAfter=0;
                            String afterPkg="";
                            String afterHash="";
                            try{
                                JSONObject tree=RootPhoneHarness.command(this,new JSONObject().put("op","ui_tree").put("refresh",true));
                                ncAfter=tree.optInt("nodeCount",0);
                                afterPkg=tree.optString("package","");
                                afterHash=tree.optString("treeHash","");
                                // If launch is still on splash screen (<=1 node), wait once more for home screen to settle
                                if((op.equals("launch")||op.equals("open_uri")||op.equals("open_url"))&&ncAfter<=1){
                                    Thread.sleep(480L);
                                    tree=RootPhoneHarness.command(this,new JSONObject().put("op","ui_tree").put("refresh",true));
                                    ncAfter=tree.optInt("nodeCount",0);
                                    afterPkg=tree.optString("package","");
                                    afterHash=tree.optString("treeHash","");
                                }
                                if(tree.has("summary"))res.put("ui_tree",tree.getString("summary")).put("node_count",ncAfter).put("package",afterPkg).put("tree_hash",afterHash);
                            }catch(Exception ignored){}
                            long afterFrameSeq=-1L;
                            String afterImgSig="";
                            try{
                                JSONObject snap=RootPhoneHarness.command(this,new JSONObject().put("op","preview").put("since",-1));
                                afterFrameSeq=snap.optLong("frameSequence",-1L);
                                if(snap.has("image")){
                                    String aImg=snap.getString("image");
                                    if(!aImg.isEmpty()) afterImgSig=aImg.length()+":"+aImg.hashCode();
                                    res.put("image",aImg).put("width",snap.optInt("width",540)).put("height",snap.optInt("height",1200));
                                }
                            }catch(Exception ignored){}
                            boolean visualChanged=(afterFrameSeq>0 && afterFrameSeq!=beforeFrameSeq) || (!afterImgSig.isEmpty() && !afterImgSig.equals(beforeImgSig));
                            boolean treeChanged=beforeHash.isEmpty() || !afterHash.equals(beforeHash) || !afterPkg.equals(beforePkg) || beforeNodes!=ncAfter;
                            boolean changed=treeChanged || visualChanged;
                            String changeSummary=(changed ? "✅ 界面已变化: " : "⚠️ 界面未变: ")
                                + "包名 " + (beforePkg.isEmpty() ? "none" : beforePkg) + " -> " + (afterPkg.isEmpty() ? "unknown" : afterPkg)
                                + ", 节点数 " + (beforeNodes<0 ? "?" : beforeNodes) + " -> " + ncAfter
                                + (visualChanged && !treeChanged ? " (自绘画面帧已更新)" : "");
                            res.put("ui_changed",changed).put("visual_changed",visualChanged).put("change_summary",changeSummary);
                        }
                    }
                }catch(Exception e){
                    try{res.put("error",MainActivity.safeError(e));}catch(Exception ignored){}
                }
                boolean sentViaWs=false;
                JSONObject reply=new JSONObject();
                try{
                    reply.put("type","phone_workspace_result").put("request_id",reqId).put("result",res);
                    if(socket!=null&&sessionKey!=null){
                        sentViaWs=socket.send(SecureTransport.encrypt(sessionKey,reply).toString());
                    }
                }catch(Exception ignored){}
                if(!sentViaWs){
                    try{
                        new SecureTransport(this).request("POST","/api/phone/result",reply,null);
                    }catch(Exception ignored){}
                }
            });
            return;
        }
        if(type.equals("message")&&System.currentTimeMillis()>=snoozeUntil&&!NativePulseModel.isForeground()){
            JSONObject m=event.optJSONObject("message");
            if(m!=null&&"assistant".equals(m.optString("role"))&&!m.optBoolean("is_sub_turn",false)){
                String id=m.optString("id","");
                String text=m.optString("text","").trim();
                String kind=m.optString("kind","chat");
                if(!id.isEmpty()&&!text.isEmpty()&&!notified.contains(id)&&!kind.equals("stopped")){
                    String botId=m.optString("bot_id","companion");
                    String botName=m.optString("from_bot_name","").trim();
                    if(botName.isEmpty()) botName="companion".equals(botId)?"白珂":botId;
                    String title="curiosity".equals(kind)?(botName+" · 主动搭话"):botName;
                    notifyCustom(id,title,text.length()>260?text.substring(0,260)+"…":text,botId);
                    notified.add(id);
                    if(notified.size()>600)notified.clear();
                }
            }
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
        if(!type.equals("stream")&&!type.equals("execution")&&!type.equals("cloud_desktop_frame")){
            broadcast();
        }
    }

    private void startEmergencyAlarm(String title,String body,String botId,int durSec){
        stopEmergencyAlarm(this);
        try{
            Uri alarmUri=RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM);
            if(alarmUri==null) alarmUri=RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE);
            if(alarmUri==null) alarmUri=RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION);
            if(alarmUri!=null){
                Ringtone r=RingtoneManager.getRingtone(getApplicationContext(),alarmUri);
                if(r!=null){
                    r.setAudioAttributes(new AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_ALARM)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                        .build());
                    if(Build.VERSION.SDK_INT>=28) r.setLooping(true);
                    r.play();
                    activeAlarmRingtone=r;
                }
            }
        }catch(Exception ignored){}
        try{
            Vibrator vib=getSystemService(Vibrator.class);
            if(vib!=null && vib.hasVibrator()){
                long[] pattern=new long[]{0,500,300,500,300,600};
                vib.vibrate(VibrationEffect.createWaveform(pattern,0));
                activeVibrator=vib;
            }
        }catch(Exception ignored){}
        handler.removeCallbacks(autoStopAlarmRunnable);
        handler.postDelayed(autoStopAlarmRunnable,durSec*1000L);

        Intent open=new Intent(this,NativePulseActivity.class).setFlags(Intent.FLAG_ACTIVITY_NEW_TASK|Intent.FLAG_ACTIVITY_SINGLE_TOP).putExtra("openBot",botId);
        PendingIntent tap=PendingIntent.getActivity(this,99119,open,PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
        Notification n=new Notification.Builder(this,"pulse_emergency_alarm")
            .setSmallIcon(R.drawable.ic_launcher)
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(new Notification.BigTextStyle().bigText(body))
            .setContentIntent(tap)
            .setCategory(Notification.CATEGORY_ALARM)
            .setOngoing(true)
            .addAction(new Notification.Action.Builder(null,"关闭闹钟",AgentActionReceiver.pending(this,"stop_alarm","alarm")).build())
            .addAction(new Notification.Action.Builder(null,"免打扰 1 小时",AgentActionReceiver.pending(this,"snooze","alarm")).build())
            .build();
        try{getSystemService(NotificationManager.class).notify(99119,n);}catch(SecurityException ignored){}
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
    @Override public void onDestroy(){stopped=true;running=false;connected=false;stopEmergencyAlarm(this);handler.removeCallbacksAndMessages(null);if(socket!=null)socket.close(1000,"stopped by user");overlay.hide();if(screen!=null)unregisterReceiver(screen);io.shutdownNow();cmdPool.shutdownNow();super.onDestroy();}
    @Override public IBinder onBind(Intent i){return null;}
}
