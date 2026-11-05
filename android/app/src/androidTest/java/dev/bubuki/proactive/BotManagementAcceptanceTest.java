package dev.bubuki.proactive;

import android.app.*;
import android.content.*;
import android.graphics.Bitmap;
import android.os.*;
import android.view.*;
import android.webkit.WebView;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import org.json.*;
import org.junit.Test;
import org.junit.runner.RunWith;
import java.io.*;
import java.lang.reflect.Field;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.*;
import static org.junit.Assert.*;

/** Real long-press/menu/restore and draft regressions, plus a non-accepting startup profile. */
@RunWith(AndroidJUnit4.class)
public class BotManagementAcceptanceTest {
    private final Instrumentation ins=InstrumentationRegistry.getInstrumentation();
    private MainActivity activity; private WebView web;
    private final JSONObject audit=new JSONObject();
    private final JSONArray frames=new JSONArray(),routes=new JSONArray();
    private volatile String phase="startup";
    private HandlerThread metricsThread;
    private Window.OnFrameMetricsAvailableListener metrics;
    private NativeRouteProbe nativeProbe;
    private String js(String code)throws Exception {
        CountDownLatch done=new CountDownLatch(1);String[] value={null};
        ins.runOnMainSync(()->web.evaluateJavascript(code,r->{value[0]=r;done.countDown();}));
        assertTrue(done.await(12,TimeUnit.SECONDS));return value[0];
    }
    private void waitFor(String code,int seconds)throws Exception {
        long end=SystemClock.uptimeMillis()+seconds*1000L;
        while(SystemClock.uptimeMillis()<end){if("true".equals(js("Boolean("+code+")")))return;Thread.sleep(100);}
        fail("Timed out: "+code+"; "+js("document.body.innerText.slice(-800)"));
    }
    private String shell(String command)throws Exception {
        try(ParcelFileDescriptor fd=ins.getUiAutomation().executeShellCommand(command);InputStream in=new FileInputStream(fd.getFileDescriptor())) {
            return new String(Io.read(in,50000),StandardCharsets.UTF_8);
        }
    }
    private void launch()throws Exception {
        long start=SystemClock.uptimeMillis();
        activity=(MainActivity)ins.startActivitySync(new Intent(ins.getTargetContext(),MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK|Intent.FLAG_ACTIVITY_CLEAR_TASK));
        web=WebViewTestReady.await(ins,activity);
        metricsThread=new HandlerThread("acceptance-frames",android.os.Process.THREAD_PRIORITY_BACKGROUND);metricsThread.start();
        metrics=(window,f,dropped)->{try{synchronized(frames){frames.put(new JSONObject().put("phase",phase).put("at_ms",SystemClock.uptimeMillis()).put("vsync_ms",f.getMetric(FrameMetrics.VSYNC_TIMESTAMP)/1e6).put("intended_vsync_ms",f.getMetric(FrameMetrics.INTENDED_VSYNC_TIMESTAMP)/1e6).put("total_ms",f.getMetric(FrameMetrics.TOTAL_DURATION)/1e6).put("draw_ms",f.getMetric(FrameMetrics.DRAW_DURATION)/1e6).put("sync_ms",f.getMetric(FrameMetrics.SYNC_DURATION)/1e6).put("gpu_ms",f.getMetric(FrameMetrics.GPU_DURATION)/1e6).put("dropped_callbacks",dropped));}}catch(Exception ignored){}};
        activity.getWindow().addOnFrameMetricsAvailableListener(metrics,new Handler(metricsThread.getLooper()));
        waitFor("typeof state==='object'&&state.online&&!refreshing",35);
        assertEquals("\"light\"",js("window.bootstrapPreferences.appearance"));
        assertEquals("\"light\"",js("document.documentElement.dataset.theme"));
        audit.put("activity_to_online_ms",SystemClock.uptimeMillis()-start);
        observeRendering();
    }
    private void observeRendering()throws Exception {
        js("window.__longTasks=[];window.__routeAnimations=[];window.__longObserver=new PerformanceObserver(list=>list.getEntries().forEach(e=>__longTasks.push({start:e.startTime,duration:e.duration,name:e.name})));__longObserver.observe({type:'longtask',buffered:true});window.__animate=Element.prototype.animate;Element.prototype.animate=function(keyframes,options){if(this.matches('.app-shell:not(.route-ghost)'))__routeAnimations.push({start:performance.now(),duration:options.duration});return __animate.call(this,keyframes,options)};");
    }
    private void stopMetrics() {
        if(activity!=null&&metrics!=null)activity.getWindow().removeOnFrameMetricsAvailableListener(metrics);
        if(metricsThread!=null)metricsThread.quitSafely();
    }
    private float[] point(String selector)throws Exception {
        String q=JSONObject.quote(selector);waitFor("document.querySelector("+q+")&&!document.querySelector("+q+").disabled",10);
        js("document.querySelector("+q+").scrollIntoView({block:'center',behavior:'instant'})");
        JSONArray r=new JSONArray(js("(()=>{const r=document.querySelector("+q+").getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2,innerWidth]})()"));
        int[] origin=new int[2];ins.runOnMainSync(()->web.getLocationOnScreen(origin));float scale=(float)(web.getWidth()/r.getDouble(2));
        return new float[]{origin[0]+(float)r.getDouble(0)*scale,origin[1]+(float)r.getDouble(1)*scale};
    }
    private void touch(String selector,long duration)throws Exception {
        float[] p=point(selector);long t=SystemClock.uptimeMillis();
        for(int action:new int[]{MotionEvent.ACTION_DOWN,MotionEvent.ACTION_UP}) {
            MotionEvent e=MotionEvent.obtain(t,SystemClock.uptimeMillis(),action,p[0],p[1],0);e.setSource(InputDevice.SOURCE_TOUCHSCREEN);
            assertTrue(ins.getUiAutomation().injectInputEvent(e,true));e.recycle();if(action==MotionEvent.ACTION_DOWN)Thread.sleep(duration);
        }
        Thread.sleep(100);
    }
    private void tap(String selector)throws Exception {touch(selector,45);}
    private void settled()throws Exception {waitFor("!PulseMotion.navigating&&!PulseMotion.sheetClosing&&!window.__profileVTActive&&!window.__nativeRouteActive",6);Thread.sleep(80);}
    private void tapMenu(String title)throws Exception {
        waitFor("document.getElementById('sheet').open&&!PulseMotion.sheetClosing",5);Thread.sleep(420);
        int index=Integer.parseInt(js("[...document.querySelectorAll('.sheet-menu button')].findIndex(b=>b.querySelector('strong').textContent==="+JSONObject.quote(title)+")"));
        assertTrue("Missing menu item "+title,index>=0);tap("#sheetBody [data-sheet-action='"+index+"']");
    }
    private void picture(String name)throws Exception {
        Bitmap bitmap=ins.getUiAutomation().takeScreenshot();assertNotNull(bitmap);
        try(FileOutputStream out=new FileOutputStream(new File(ins.getTargetContext().getExternalFilesDir(null),name+".png"))){bitmap.compress(Bitmap.CompressFormat.PNG,100,out);}bitmap.recycle();
    }
    private void profile(String label)throws Exception {
        phase=label;long nativeBefore=SystemClock.uptimeMillis();double start=Double.parseDouble(js("performance.now()"));long nativeAfter=SystemClock.uptimeMillis();
        tap("#botList [data-bot=companion]");Thread.sleep(650);settled();
        assertEquals("\"chat\"",js("state.view"));
        assertEquals("true",js("!window.__nativeRouteError"));
        routes.put(new JSONObject().put("phase",label).put("start",start).put("native_start_ms",(nativeBefore+nativeAfter)/2.0).put("clock_query_ms",nativeAfter-nativeBefore).put("end",Double.parseDouble(js("performance.now()"))).put("animations",new JSONArray(js("__routeAnimations.filter(e=>e.start>="+start+")"))).put("long_tasks",new JSONArray(js("__longTasks.filter(e=>e.start>="+start+")"))));
        phase="other";tap("#chatBack");settled();Thread.sleep(120);assertEquals("\"home\"",js("state.view"));
    }
    private void seed()throws Exception {
        Context context=ins.getTargetContext();String token=shell("cat /data/local/tmp/proactive-pairing").trim();
        String key;try(InputStream in=context.getAssets().open("transport.json")){key=new JSONObject(new String(Io.read(in,20000),StandardCharsets.UTF_8)).getString("public_key");}
        JSONObject settings=new JSONObject().put("serverUrl",InstrumentationRegistry.getArguments().getString("uiServer","http://127.0.0.1:18191")).put("publicKey",key).put("deviceToken",token).put("background",false).put("appearance","light").put("motion",true).put("haptics",false);
        SecurePrefs.save(context,settings);UiPreferences.update(context,settings);
    }
    private void writeAudit()throws Exception {
        if(nativeProbe!=null){audit.put("native_routes",nativeProbe.records());ins.runOnMainSync(()->nativeProbe.close());}
        stopMetrics();audit.put("routes",routes);synchronized(frames){audit.put("android_window_frames",frames);}
        try(FileOutputStream out=new FileOutputStream(new File(ins.getTargetContext().getExternalFilesDir(null),"bot-management-audit.json"))){out.write(audit.toString(2).getBytes(StandardCharsets.UTF_8));}
        if(activity!=null)ins.runOnMainSync(()->activity.finish());
    }
    @Test public void startupRenderingProfile()throws Exception {
        seed();
        for(String name:new String[]{"native-probe-incoming.png","native-probe-live.png"})new File(ins.getTargetContext().getExternalFilesDir(null),name).delete();
        try {
            launch();String variant=InstrumentationRegistry.getArguments().getString("renderVariant","baseline");
            long nativePrepareStart=SystemClock.uptimeMillis();
            if(variant.startsWith("native-"))ins.runOnMainSync(()->{
                if(variant.equals("native-hardware")) {web.setLayerType(View.LAYER_TYPE_HARDWARE,null);web.buildLayer();}
                if(variant.equals("native-software")) web.setLayerType(View.LAYER_TYPE_SOFTWARE,null);
                if(variant.equals("native-preraster")) web.getSettings().setOffscreenPreRaster(true);
                if(variant.equals("native-snapshot")||variant.equals("native-bitmap")||variant.equals("native-bitmap-sync")||variant.equals("native-bitmap-swprep")){nativeProbe=new NativeRouteProbe(activity,web,variant.equals("native-bitmap")||variant.equals("native-bitmap-sync")||variant.equals("native-bitmap-swprep"),variant.equals("native-bitmap-swprep"));web.addJavascriptInterface(nativeProbe,"NativeRouteProbe");web.reload();}
            });
            if(variant.equals("native-snapshot")||variant.equals("native-bitmap")||variant.equals("native-bitmap-sync")||variant.equals("native-bitmap-swprep")){
                waitFor("typeof state==='object'&&state.online&&!refreshing&&typeof NativeRouteProbe!=='undefined'",35);
                observeRendering();
                audit.put("native_reload",true);
            }
            audit.put("native_configuration_ms",SystemClock.uptimeMillis()-nativePrepareStart);
            String css=switch(variant){
                case "baseline", "settled-baseline", "native-hardware", "native-software", "native-preraster", "native-snapshot", "native-bitmap", "native-bitmap-sync", "native-bitmap-swprep" -> "";
                case "transition-shadow-off" -> ".route-moving{box-shadow:none!important}";
                case "all-shadows-off" -> "*,*::before,*::after{box-shadow:none!important;filter:none!important;backdrop-filter:none!important}";
                case "line-off" -> ".route-moving::before{content:none!important}";
                case "persistent-layer" -> ".app-shell{will-change:transform;transform:translateZ(0)}";
                case "persistent-layer-gradient" -> ".app-shell{will-change:transform;transform:translateZ(0)}.route-moving{box-shadow:none!important}.route-moving::before{content:'';position:absolute;pointer-events:none;left:-18px;top:0;bottom:0;width:18px;background:linear-gradient(to right,transparent,#00000014)}";
                case "view-transition" -> ".app-shell:not(.route-ghost){view-transition-name:pulse-surface}::view-transition-old(root),::view-transition-new(root){animation:none;mix-blend-mode:normal}::view-transition-group(pulse-surface){animation:none}::view-transition-image-pair(pulse-surface){isolation:isolate}::view-transition-old(pulse-surface){animation:profile-old 380ms cubic-bezier(.22,.78,.18,1) both;mix-blend-mode:normal}::view-transition-new(pulse-surface){animation:profile-new 380ms cubic-bezier(.22,.78,.18,1) both;mix-blend-mode:normal}:root[data-profile-back=yes]::view-transition-old(pulse-surface){animation-name:profile-back-old;animation-duration:330ms}:root[data-profile-back=yes]::view-transition-new(pulse-surface){animation-name:profile-back-new;animation-duration:330ms}@keyframes profile-old{from{transform:translateX(0)}to{transform:translateX(-24%)}}@keyframes profile-new{from{transform:translateX(100%)}to{transform:translateX(0)}}@keyframes profile-back-old{from{transform:translateX(0)}to{transform:translateX(100%)}}@keyframes profile-back-new{from{transform:translateX(-24%)}to{transform:translateX(0)}}";
                default -> throw new IllegalArgumentException("Unknown rendering variant");
            };
            js("(()=>{const style=document.createElement('style');style.textContent="+JSONObject.quote(css)+";document.head.append(style);window.__profileStyleReady=false;requestAnimationFrame(()=>requestAnimationFrame(()=>window.__profileStyleReady=true));})()");
            waitFor("window.__profileStyleReady",3);
            boolean settled=variant.equals("settled-baseline")||variant.startsWith("persistent-layer")||variant.equals("view-transition")||variant.startsWith("native-");
            if(settled)Thread.sleep(350);
            if(variant.equals("native-bitmap-sync")||variant.equals("native-bitmap-swprep")){
                js("window.__nativeSync=true;window.__nativePrimeReady=false;window.__nativePrimeDone=okay=>window.__nativePrimeReady=okay;NativeRouteProbe.prime()");
                waitFor("window.__nativePrimeReady",5);audit.put("native_prime_ms",nativeProbe.primeMs());
            }
            if(variant.equals("native-snapshot")||variant.equals("native-bitmap")||variant.equals("native-bitmap-sync")||variant.equals("native-bitmap-swprep")){
                js("(()=>{const originalGo=go,originalPop=popRoute;let sequence=0,pending=new Map();window.__nativeCaptureDone=(id,ok)=>{const p=pending.get(id);if(!p)return;if(!ok){window.__nativeRouteError='capture';__nativeRouteActive=false;return;}const motion=state.settings.motion;state.settings.motion=false;try{p.update()}finally{state.settings.motion=motion}document.querySelector('.app-shell').getBoundingClientRect();if(window.__nativeSync)requestAnimationFrame(()=>NativeRouteProbe.prepare(id,p.forward));else requestAnimationFrame(()=>requestAnimationFrame(()=>NativeRouteProbe.prepare(id,p.forward)))};window.__nativeStarted=(id,ok)=>__routeAnimations.push({start:performance.now(),duration:pending.get(id)?.forward?380:330,kind:'native-snapshot'});window.__nativeFinished=(id,ok)=>{pending.delete(id);window.__nativeRouteActive=false;if(!ok)window.__nativeRouteError='animation'};function route(update,forward){window.__nativeRouteActive=true;const id=++sequence;pending.set(id,{update,forward});if(window.__nativeSync){const okay=NativeRouteProbe.captureSync(id)>0;__nativeCaptureDone(id,okay)}else NativeRouteProbe.capture(id)}go=(...args)=>route(()=>originalGo(...args),true);popRoute=(...args)=>route(()=>originalPop(...args),false)})()");
            }
            if(variant.equals("view-transition")){
                assertEquals("\"function\"",js("typeof document.startViewTransition"));
                js("(()=>{const originalGo=go,originalPop=popRoute;function transition(update,back){document.documentElement.dataset.profileBack=back?'yes':'no';window.__profileVTActive=true;const vt=document.startViewTransition(()=>{const motion=state.settings.motion;state.settings.motion=false;try{update()}finally{state.settings.motion=motion}});vt.ready.then(()=>__routeAnimations.push({start:performance.now(),duration:back?330:380,kind:'view-transition'}));vt.finished.finally(()=>window.__profileVTActive=false)}go=(...args)=>transition(()=>originalGo(...args),false);popRoute=(...args)=>transition(()=>originalPop(...args),true)})()");
            }
            profile("first-push");profile("warm-push");
            audit.put("render_variant",variant).put("style_settle_ms",settled?350:0).put("profile_only",true).put("completed",true);
        } finally {writeAudit();}
    }
    @Test public void placementAndHiddenConversations()throws Exception {
        seed();
        try {
            launch();profile("first-push");profile("warm-push");
            touch("#botList [data-bot=research]",680);
            assertEquals("\"home\"",js("state.view"));picture("bots-long-press-menu");
            tapMenu("置顶到首页");waitFor("state.bots.find(b=>b.id==='research').pinned&&!document.getElementById('sheet').open",6);
            assertEquals("\"research\"",js("document.querySelector('#botList [data-bot]').dataset.bot"));picture("bots-pinned-home");
            touch("#botList [data-bot=research]",680);tapMenu("隐藏助手");
            waitFor("state.bots.find(b=>b.id==='research').hidden&&!document.getElementById('sheet').open",6);
            assertEquals("0",js("document.querySelectorAll('#botList [data-bot=research]').length"));
            tap("#botList [data-bot=companion]");settled();
            js("document.getElementById('chatInput').value='隐藏以后仍保留的草稿';document.getElementById('chatInput').dispatchEvent(new Event('input',{bubbles:true}))");
            tap("#chatMenu");tapMenu("隐藏助手");waitFor("state.view==='home'&&state.bots.every(b=>b.hidden)",6);settled();
            assertEquals("0",js("document.querySelectorAll('#botList [data-bot]').length"));picture("bots-all-hidden");
            tap("#botList [data-route=hiddenBots]");settled();
            assertEquals("2",js("document.querySelectorAll('#hiddenBotList [data-bot]').length"));picture("bots-hidden-list");
            tap("[data-restore-bot=companion]");waitFor("!state.bots.find(b=>b.id==='companion').hidden",6);
            tap("#subBack");settled();tap("#botList [data-bot=companion]");settled();
            assertEquals("\"隐藏以后仍保留的草稿\"",js("document.getElementById('chatInput').value"));
            tap("#chatBack");settled();
            // Recreate the real Activity and reload metadata, without seeding its in-memory state.
            stopMetrics();ins.runOnMainSync(()->activity.finish());launch();
            assertEquals("true",js("state.bots.find(b=>b.id==='research').hidden&&state.bots.find(b=>b.id==='research').pinned"));
            tap("#profileButton");settled();tap(".page.active [data-route=hiddenBots]");settled();
            tap("[data-restore-bot=research]");waitFor("!state.bots.find(b=>b.id==='research').hidden",6);
            tap("#subBack");settled();tap("#subBack");settled();
            assertEquals("\"research\"",js("document.querySelector('#botList [data-bot]').dataset.bot"));picture("bots-restored-home");
            audit.put("passed",true).put("long_press_no_accidental_chat",true).put("pinned_order",true).put("all_hidden_empty_state",true).put("settings_hidden_route",true).put("draft_preserved",true).put("activity_restart",true);
        } finally {
            writeAudit();
        }
    }
}
