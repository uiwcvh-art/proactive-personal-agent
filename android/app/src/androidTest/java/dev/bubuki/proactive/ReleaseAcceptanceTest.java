package dev.bubuki.proactive;

import android.app.*;
import android.content.*;
import android.graphics.*;
import android.os.*;
import android.view.*;
import android.view.accessibility.AccessibilityNodeInfo;
import android.webkit.WebView;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.json.*;
import java.io.*;
import java.lang.reflect.Field;
import java.util.concurrent.*;
import static org.junit.Assert.*;

/** Signed release APK, actual touch hit testing, encrypted HTTP and native WebSocket/overlay. */
@RunWith(AndroidJUnit4.class)
public class ReleaseAcceptanceTest {
    private final Instrumentation ins=InstrumentationRegistry.getInstrumentation();
    private MainActivity activity; private WebView web; private SecureTransport transport;
    private String js(String code)throws Exception{
        CountDownLatch done=new CountDownLatch(1);String[] value={null};
        ins.runOnMainSync(()->web.evaluateJavascript(code,v->{value[0]=v;done.countDown();}));
        assertTrue("JS callback timed out",done.await(10,TimeUnit.SECONDS));return value[0];
    }
    private void waitFor(String condition,int seconds)throws Exception{
        long deadline=System.currentTimeMillis()+seconds*1000L;
        while(System.currentTimeMillis()<deadline){if("true".equals(js("Boolean("+condition+")")))return;Thread.sleep(180);}
        fail("UI condition timed out: "+condition+"; body="+js("document.body.innerText.slice(-1600)")+"; input="+js("JSON.stringify({busy:state.busy,mode:state.mode,online:state.online,draftLength:document.getElementById('chatInput').value.length,events:window.__inputAudit?.slice(-20),errors:window.__inputErrors})"));
    }
    private String shell(String command)throws Exception{
        try(ParcelFileDescriptor fd=ins.getUiAutomation().executeShellCommand(command);InputStream in=new FileInputStream(fd.getFileDescriptor())){return new String(Io.read(in,1000000),java.nio.charset.StandardCharsets.UTF_8);}
    }
    private void tap(String selector)throws Exception{
        String q=JSONObject.quote(selector);
        waitFor("!window.PulseMotion||!PulseMotion.navigating&&!PulseMotion.sheetClosing",8);
        waitFor("document.querySelector("+q+") && !document.querySelector("+q+").disabled",15);
        js("document.querySelector("+q+").scrollIntoView({block:'center',behavior:'instant'})");Thread.sleep(220);
        JSONArray rect=new JSONArray(js("(()=>{const r=document.querySelector("+q+").getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2,r.width,r.height,innerWidth]})()"));
        assertTrue("No touch target: "+selector,rect.getDouble(2)>10&&rect.getDouble(3)>10);
        int[] origin=new int[2];ins.runOnMainSync(()->web.getLocationOnScreen(origin));float scale=(float)(web.getWidth()/rect.getDouble(4));
        if(selector.equals("#sendButton"))System.out.println("SEND HIT "+js("(()=>{const r=document.getElementById('sendButton').getBoundingClientRect(),e=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return JSON.stringify({rect:[r.x,r.y,r.width,r.height],hit:e?.closest('button')?.id,active:document.activeElement?.id,inner:[innerWidth,innerHeight],viewport:[visualViewport.offsetTop,visualViewport.height]})})()")+" nativeOrigin="+origin[0]+","+origin[1]+" scale="+scale+" nativeScroll="+web.getScrollX()+","+web.getScrollY());
        touch(origin[0]+(float)rect.getDouble(0)*scale,origin[1]+(float)rect.getDouble(1)*scale);
        Thread.sleep(230);
    }
    private void touch(float x,float y){long t=SystemClock.uptimeMillis();MotionEvent down=MotionEvent.obtain(t,t,MotionEvent.ACTION_DOWN,x,y,0);down.setSource(InputDevice.SOURCE_TOUCHSCREEN);assertTrue(ins.getUiAutomation().injectInputEvent(down,true));SystemClock.sleep(60);MotionEvent up=MotionEvent.obtain(t,SystemClock.uptimeMillis(),MotionEvent.ACTION_UP,x,y,0);up.setSource(InputDevice.SOURCE_TOUCHSCREEN);assertTrue(ins.getUiAutomation().injectInputEvent(up,true));down.recycle();up.recycle();ins.waitForIdleSync();}
    private void text(String id,String value)throws Exception{js("document.getElementById("+JSONObject.quote(id)+").value="+JSONObject.quote(value)+";document.getElementById("+JSONObject.quote(id)+").dispatchEvent(new Event('input',{bubbles:true}))");}
    private void screenshot(String name)throws Exception{Thread.sleep(350);Bitmap bitmap=ins.getUiAutomation().takeScreenshot();assertNotNull(bitmap);File file=new File(ins.getTargetContext().getExternalFilesDir(null),name+".png");try(FileOutputStream out=new FileOutputStream(file)){bitmap.compress(Bitmap.CompressFormat.PNG,100,out);}bitmap.recycle();}
    private void waitForImeReady()throws Exception{
        Field field=MainActivity.class.getDeclaredField("imeAnimating");field.setAccessible(true);
        long end=SystemClock.uptimeMillis()+12000;int stable=0,lastHeight=-1;
        while(SystemClock.uptimeMillis()<end){
            boolean[] ready={false};int[] height={0};
            ins.runOnMainSync(()->{WindowInsets insets=activity.getWindow().getDecorView().getRootWindowInsets();height[0]=web.getHeight();try{ready[0]=insets!=null&&insets.isVisible(WindowInsets.Type.ime())&&!field.getBoolean(activity);}catch(IllegalAccessException error){throw new RuntimeException(error);}});
            stable=ready[0]&&height[0]==lastHeight?stable+1:0;lastHeight=height[0];
            if(stable>=2)return;Thread.sleep(60);
        }
        fail("Software keyboard did not become visible and settle before send hit testing");
    }
    private void home()throws Exception{for(int i=0;i<10&&!"true".equals(js("state.view==='home'"));i++){tap("true".equals(js("state.view==='chat'"))?"#chatBack":"#subBack");}waitFor("state.view==='home'",5);}

    @Test public void allSecondaryScreensAndNativeRoundTrip()throws Exception{
        Context c=ins.getTargetContext();String token=shell("cat /data/local/tmp/proactive-pairing").trim();assertTrue(token.length()>30);
        String publicKey;try(InputStream in=c.getAssets().open("transport.json")){publicKey=new JSONObject(new String(Io.read(in,20000),java.nio.charset.StandardCharsets.UTF_8)).getString("public_key");}
        String server=InstrumentationRegistry.getArguments().getString("uiServer","http://127.0.0.1:18191");
        SecurePrefs.save(c,new JSONObject().put("serverUrl",server).put("publicKey",publicKey).put("deviceToken",token).put("background",false).put("appearance","light"));
        UiPreferences.update(c,SecurePrefs.read(c));
        c.getSystemService(android.app.NotificationManager.class).cancelAll();shell("cmd statusbar collapse");
        assertEquals("http://216.23.82.196/proactive-agent",SecureTransport.DEFAULT_SERVER);
        transport=new SecureTransport(c);assertTrue(transport.request("GET","/api/status",null,null).getBoolean("online"));
        shell("pm grant dev.bubuki.proactive android.permission.POST_NOTIFICATIONS");shell("appops set dev.bubuki.proactive SYSTEM_ALERT_WINDOW allow");shell("settings put system screen_off_timeout 1800000");
        Intent intent=new Intent(c,MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK|Intent.FLAG_ACTIVITY_CLEAR_TASK);activity=(MainActivity)ins.startActivitySync(intent);
        web=WebViewTestReady.await(ins,activity);
        waitFor("typeof state==='object' && state.online && state.bots.length>=2",35);
        js("window.__inputAudit=[];window.__inputErrors=[];['pointerdown','pointerup','pointercancel','click','submit','invalid'].forEach(type=>document.addEventListener(type,e=>{const r={type,target:e.target.id||e.target.tagName,x:e.clientX,y:e.clientY};__inputAudit.push(r);queueMicrotask(()=>r.prevented=e.defaultPrevented)},true));addEventListener('error',e=>__inputErrors.push(e.message));addEventListener('unhandledrejection',e=>__inputErrors.push(String(e.reason)));");
        if(!"true".equals(InstrumentationRegistry.getArguments().getString("overlayOnly"))){
        screenshot("v2-01-bots-light");
        tap("[data-bot=companion]");waitFor("state.view==='chat'",5);screenshot("v2-02-chat-empty");
        tap("#chatInput");text("chatInput","[界面验收] 我想把今天的事情理清楚。");
        waitForImeReady();
        waitFor("document.querySelector('.composer').getBoundingClientRect().bottom<=innerHeight+1",5);screenshot("v2-03-keyboard");
        tap("#sendButton");waitFor("!state.busy && document.getElementById('messages').innerText.includes('本地引导')",25);
        ins.runOnMainSync(()->c.getSystemService(android.view.inputmethod.InputMethodManager.class).hideSoftInputFromWindow(web.getWindowToken(),0));Thread.sleep(300);screenshot("v2-04-chat");
        assertEquals("true",js("typeof window.__xss==='undefined'"));
        assertEquals("true",js("(()=>{const d=document.createElement('div');d.innerHTML=markdown('<img src=x onerror=window.__xss=1><script>window.__xss=1</script>[x](javascript:alert(1))');return !d.querySelector('script,img,[onerror],a[href^=javascript]')})()"));
        assertEquals("true",js("markdown(['| A | B |','|---|---|','| 1 | 2 |'].join(String.fromCharCode(10))).includes('<table>')"));
        tap("#chatIdentity");waitFor("state.view==='profile'",5);screenshot("v2-05-bot-profile");
        tap(".page.active [data-route=botSettings]");waitFor("state.view==='botSettings'",5);screenshot("v2-06-bot-settings");
        text("botInstructions","[界面验收] 亲切直接，每次只问一个问题。");tap("#saveBot");waitFor("state.view==='profile'",15);
        assertTrue(transport.request("GET","/api/bots",null,null).toString().contains("[界面验收]"));
        Instrumentation.ActivityMonitor phoneMonitor=ins.addMonitor(NativePhoneActivity.class.getName(),null,false);tap(".page.active [data-route=root]");Activity phone=ins.waitForMonitorWithTimeout(phoneMonitor,15000);ins.removeMonitor(phoneMonitor);assertNotNull("Own Root workspace did not open",phone);screenshot("v2-07-phone");ins.sendKeyDownUpSync(KeyEvent.KEYCODE_BACK);long phoneBack=System.currentTimeMillis()+10000;while(!activity.hasWindowFocus()&&System.currentTimeMillis()<phoneBack)Thread.sleep(100);assertTrue(activity.hasWindowFocus());
        tap(".page.active [data-route=vault]");waitFor("document.querySelectorAll('[data-file]').length>=7",15);screenshot("v2-08-memory-list");
        tap("[data-file=\"profile/user_traits.md\"]");waitFor("state.view==='editor'",10);screenshot("v2-09-memory-read");
        tap("#editTab");text("markdownEditor","# 用户画像\n\n- [界面验收] 在线编辑、保存与回读。\n");tap("#subAction");waitFor("document.getElementById('editorHint').innerText.includes('已同步')",15);screenshot("v2-10-memory-edit");
        assertTrue(transport.request("GET","/api/vault",null,new JSONObject().put("path","profile/user_traits.md")).getString("content").contains("在线编辑"));
        home();tap("#profileButton");waitFor("state.view==='settings'",5);screenshot("v2-11-settings");
        tap(".page.active [data-config=small]");waitFor("state.view==='config'",5);tap("[data-preset=deepseek]");screenshot("v2-12-small-model");tap("#saveConfig");waitFor("document.getElementById('configFeedback').innerText.includes('云端已保存')",25);tap("#subBack");
        tap(".page.active [data-config=big]");screenshot("v2-13-execution-model");tap("#subBack");
        tap(".page.active [data-config=mail]");screenshot("v2-14-email");tap("#subBack");
        tap(".page.active [data-route=tools]");waitFor("state.view==='tools'",5);screenshot("v2-15-tools");tap("[data-tool=read_file]");waitFor("state.view==='toolDetail'",5);screenshot("v2-16-tool-detail");
        tap("#useTool");waitFor("state.view==='chat'",5);tap("#sendButton");waitFor("!state.busy && document.querySelector('.result-controls .accept')",25);screenshot("v2-17-result");
        tap(".tool-step summary");screenshot("v2-18-tool-trace");
        // Stage 1 is visible in the preceding screenshot. Clear only this app's
        // disposable fixture notifications before touching the underlying card;
        // the final escalation below creates a new, independently verified alert.
        c.getSystemService(android.app.NotificationManager.class).cancelAll();ins.getUiAutomation().waitForIdle(500,6000);
        tap(".result-controls .accept");waitFor("document.getElementById('messages').innerText.includes('已加入例行任务')",15);
        home();tap("#openRoutines");waitFor("state.view==='routines' && document.querySelector('[data-routine]')",10);screenshot("v2-19-routines");tap("[data-routine]");waitFor("state.view==='routineDetail'",5);screenshot("v2-20-routine-detail");
        tap("[data-routine-toggle]");waitFor("document.querySelector('[data-routine-toggle] .toggle').classList.contains('on')===false",10);
        home();tap("#newBot");waitFor("document.getElementById('sheet').open",5);tap("[data-sheet-action='0']");text("createBotName","资料助手");tap("[data-shape=flower]");tap("[data-color=\"#FF742E\"]");screenshot("v2-21-create-bot");tap("#commitAvatar");
        waitFor("state.view==='chat' && !document.getElementById('sheet').open && bot().name==='资料助手'",15);assertEquals("0",js("visibleMessages().length"));
        home();tap("#profileButton");tap(".page.active [data-route=appearance]");tap("[data-appearance=dark]");home();screenshot("v2-22-bots-dark");tap("[data-bot=research]");screenshot("v2-23-chat-dark");
        waitFor("state.settings.background===true",10);
        }else{JSONObject prefs=SecurePrefs.read(c);prefs.put("background",true);SecurePrefs.save(c,prefs);ins.runOnMainSync(()->c.startForegroundService(new Intent(c,AgentForegroundService.class)));}
        long until=System.currentTimeMillis()+20000;while(!AgentForegroundService.connected&&System.currentTimeMillis()<until)Thread.sleep(250);assertTrue("WebSocket not connected",AgentForegroundService.connected);
        JSONObject demo=transport.request("POST","/api/test-escalation",new JSONObject(),null);ins.runOnMainSync(()->activity.moveTaskToBack(true));Thread.sleep(10000);screenshot("v2-24-stage3");
        assertTrue(shell("dumpsys window windows").contains("ty=APPLICATION_OVERLAY"));
        android.accessibilityservice.AccessibilityServiceInfo serviceInfo=ins.getUiAutomation().getServiceInfo();serviceInfo.flags|=android.accessibilityservice.AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS;ins.getUiAutomation().setServiceInfo(serviceInfo);
        AccessibilityNodeInfo root=null;
        for(android.view.accessibility.AccessibilityWindowInfo window:ins.getUiAutomation().getWindows()){
            AccessibilityNodeInfo candidate=window.getRoot();
            if(candidate!=null&&c.getPackageName().contentEquals(candidate.getPackageName())&&!candidate.findAccessibilityNodeInfosByText("需要你确认").isEmpty()){root=candidate;break;}
        }
        assertNotNull("Native overlay accessibility window not found",root);
        java.util.List<AccessibilityNodeInfo> nodes=root.findAccessibilityNodeInfosByText("确认已读");AccessibilityNodeInfo confirm=null;
        for(AccessibilityNodeInfo node:nodes)if("确认已读".contentEquals(node.getText())){confirm=node;break;}
        assertNotNull("Exact overlay confirmation button missing",confirm);Rect rect=new Rect();confirm.getBoundsInScreen(rect);System.out.println("Native overlay touch bounds: "+rect);touch(rect.centerX(),rect.centerY());
        boolean acknowledged=false;long acknowledgmentDeadline=System.currentTimeMillis()+10000;
        while(System.currentTimeMillis()<acknowledgmentDeadline){if(!transport.request("GET","/api/messages",null,null).getJSONObject("pending").has(demo.getString("id"))){acknowledged=true;break;}Thread.sleep(250);}
        if(!acknowledged)screenshot("v2-overlay-after-failed-touch");
        assertTrue("Overlay acknowledgment did not reach server",acknowledged);
        transport.request("POST","/api/snooze",new JSONObject().put("minutes",60),null);c.stopService(new Intent(c,AgentForegroundService.class));ins.runOnMainSync(()->activity.finish());
    }
}
