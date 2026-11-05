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
import java.util.HashSet;
import java.util.concurrent.*;
import static org.junit.Assert.*;

/** Real touch/drag regressions. Records compositor positions, not merely CSS declarations. */
@RunWith(AndroidJUnit4.class)
public class MotionAcceptanceTest {
    private final Instrumentation ins=InstrumentationRegistry.getInstrumentation();
    private MainActivity activity; private WebView web;
    private final JSONArray audit=new JSONArray();
    private String js(String code)throws Exception{
        CountDownLatch done=new CountDownLatch(1);String[] answer={null};
        ins.runOnMainSync(()->web.evaluateJavascript(code,value->{answer[0]=value;done.countDown();}));
        assertTrue(done.await(12,TimeUnit.SECONDS));return answer[0];
    }
    private void waitFor(String code,int seconds)throws Exception{
        long end=SystemClock.uptimeMillis()+seconds*1000L;
        while(SystemClock.uptimeMillis()<end){if("true".equals(js("Boolean("+code+")")))return;Thread.sleep(100);}
        fail("Timed out: "+code+"; "+js("document.body.innerText.slice(-800)"));
    }
    private String shell(String command)throws Exception{
        try(ParcelFileDescriptor fd=ins.getUiAutomation().executeShellCommand(command);InputStream in=new FileInputStream(fd.getFileDescriptor())){
            return new String(Io.read(in,50000),StandardCharsets.UTF_8);
        }
    }
    private void setup()throws Exception{
        Context context=ins.getTargetContext();String token=shell("cat /data/local/tmp/proactive-pairing").trim();
        String key;try(InputStream in=context.getAssets().open("transport.json")){key=new JSONObject(new String(Io.read(in,20000),StandardCharsets.UTF_8)).getString("public_key");}
        SecurePrefs.save(context,new JSONObject().put("serverUrl",InstrumentationRegistry.getArguments().getString("uiServer","http://127.0.0.1:18191"))
            .put("publicKey",key).put("deviceToken",token).put("background",false).put("appearance","light").put("motion",true).put("haptics",false));
        UiPreferences.update(context,SecurePrefs.read(context));
        activity=(MainActivity)ins.startActivitySync(new Intent(context,MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK|Intent.FLAG_ACTIVITY_CLEAR_TASK));
        web=WebViewTestReady.await(ins,activity);
        waitFor("typeof state==='object'&&state.online",35);
    }
    private float[] point(String selector)throws Exception{
        String q=JSONObject.quote(selector);
        waitFor("document.querySelector("+q+")&&!document.querySelector("+q+").disabled",10);
        js("document.querySelector("+q+").scrollIntoView({block:'center',behavior:'instant'})");
        JSONArray r=new JSONArray(js("(()=>{const r=document.querySelector("+q+").getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2,innerWidth]})()"));
        int[] origin=new int[2];ins.runOnMainSync(()->web.getLocationOnScreen(origin));
        float scale=(float)(web.getWidth()/r.getDouble(2));return new float[]{origin[0]+(float)r.getDouble(0)*scale,origin[1]+(float)r.getDouble(1)*scale,scale};
    }
    private void event(long start,int action,float x,float y){
        MotionEvent event=MotionEvent.obtain(start,SystemClock.uptimeMillis(),action,x,y,0);event.setSource(InputDevice.SOURCE_TOUCHSCREEN);
        assertTrue(ins.getUiAutomation().injectInputEvent(event,true));event.recycle();
    }
    private void tap(String selector)throws Exception{
        float[] p=point(selector);long t=SystemClock.uptimeMillis();event(t,MotionEvent.ACTION_DOWN,p[0],p[1]);Thread.sleep(45);event(t,MotionEvent.ACTION_UP,p[0],p[1]);Thread.sleep(100);
    }
    private void settled()throws Exception{waitFor("!PulseMotion.navigating&&!PulseMotion.sheetClosing",6);Thread.sleep(60);}
    private void traceStart()throws Exception{
        js("window.__motionTrace=[];window.__traceUntil=performance.now()+1800;requestAnimationFrame(function sample(t){const root=document.querySelector('.app-shell:not(.route-ghost)'),sheet=document.getElementById('sheet');window.__motionTrace.push({t,x:root.getBoundingClientRect().x,y:sheet.open?sheet.getBoundingClientRect().y:null,height:innerHeight,composer:document.getElementById('chatForm').getBoundingClientRect().bottom,ghosts:document.querySelectorAll('.route-ghost').length});if(t<window.__traceUntil)requestAnimationFrame(sample);})");
    }
    private void traceEnd(String label,String axis)throws Exception{
        Thread.sleep(420);JSONArray samples=new JSONArray(js("window.__motionTrace"));
        HashSet<Integer> positions=new HashSet<>();
        for(int i=0;i<samples.length();i++){JSONObject sample=samples.getJSONObject(i);if(!sample.isNull(axis))positions.add((int)Math.round(sample.getDouble(axis)));}
        assertTrue(label+" has no visible intermediate positions: "+samples,positions.size()>=3);
        audit.put(new JSONObject().put("name",label).put("distinct_positions",positions.size()).put("samples",samples));
        js("window.__traceUntil=0");
    }
    private void drag(float x,float y,float endX,float endY,int duration)throws Exception{
        long start=SystemClock.uptimeMillis();event(start,MotionEvent.ACTION_DOWN,x,y);int steps=22;
        for(int i=1;i<=steps;i++){Thread.sleep(duration/steps);event(start,MotionEvent.ACTION_MOVE,x+(endX-x)*i/steps,y+(endY-y)*i/steps);}
        Thread.sleep(70);event(start,MotionEvent.ACTION_UP,endX,endY);Thread.sleep(350);
    }
    private void edgeDrag(float fraction)throws Exception{
        int[] origin=new int[2];ins.runOnMainSync(()->web.getLocationOnScreen(origin));
        float scale=web.getWidth()/Float.parseFloat(js("innerWidth"));
        drag(origin[0]+22*scale,origin[1]+web.getHeight()*.47f,origin[0]+web.getWidth()*fraction,origin[1]+web.getHeight()*.47f,650);
    }
    private void cancelEdgeDrag()throws Exception{
        int[] origin=new int[2];ins.runOnMainSync(()->web.getLocationOnScreen(origin));
        float scale=web.getWidth()/Float.parseFloat(js("innerWidth")),x=origin[0]+22*scale,y=origin[1]+web.getHeight()*.47f,end=origin[0]+web.getWidth()*.3f;
        long start=SystemClock.uptimeMillis();event(start,MotionEvent.ACTION_DOWN,x,y);
        for(int i=1;i<=16;i++){Thread.sleep(20);event(start,MotionEvent.ACTION_MOVE,x+(end-x)*i/16,y);}
        for(int i=1;i<=16;i++){Thread.sleep(20);event(start,MotionEvent.ACTION_MOVE,end-(end-x)*i/16,y);}
        event(start,MotionEvent.ACTION_UP,x,y);Thread.sleep(400);
    }
    private void text(String value)throws Exception{js("document.getElementById('chatInput').value="+JSONObject.quote(value)+";document.getElementById('chatInput').dispatchEvent(new Event('input',{bubbles:true}))");}
    private void picture(String name)throws Exception{
        Bitmap image=ins.getUiAutomation().takeScreenshot();assertNotNull(image);
        try(FileOutputStream out=new FileOutputStream(new File(ins.getTargetContext().getExternalFilesDir(null),name+".png"))){image.compress(Bitmap.CompressFormat.PNG,100,out);}image.recycle();
    }
    @Test public void transitionsGesturesMessagesAndIme()throws Exception{
        setup();
        try{
            traceStart();tap("[data-bot=companion]");settled();traceEnd("page-push","x");
            traceStart();tap("#chatBack");settled();traceEnd("page-return","x");
            tap("[data-bot=companion]");ins.sendKeyDownUpSync(KeyEvent.KEYCODE_BACK);
            waitFor("state.view==='home'&&!PulseMotion.navigating",6);
            assertEquals("0",js("document.querySelectorAll('.route-ghost').length"));
            assertEquals("1",js("document.querySelectorAll('.page.active').length"));
            tap("[data-bot=companion]");settled();
            int depth=Integer.parseInt(js("state.stack.length"));
            traceStart();cancelEdgeDrag();settled();traceEnd("edge-return-cancel","x");
            assertEquals("\"chat\"",js("state.view"));assertEquals(depth,Integer.parseInt(js("state.stack.length")));
            traceStart();edgeDrag(.72f);settled();traceEnd("edge-return-commit","x");
            assertEquals("\"home\"",js("state.view"));
            traceStart();tap("#newBot");waitFor("document.getElementById('sheet').open",5);Thread.sleep(440);traceEnd("sheet-open","y");
            float[] handle=point(".sheet-handle");
            traceStart();drag(handle[0],handle[1],handle[0],handle[1]+230*handle[2],520);waitFor("!document.getElementById('sheet').open",5);traceEnd("sheet-drag-dismiss","y");
            tap("#newBot");Thread.sleep(440);tap("#closeSheet");
            // A modal keeps the underlying page blocked until dismissal completes.
            waitFor("!document.getElementById('sheet').open",5);tap("#newBot");Thread.sleep(440);assertEquals("true",js("document.getElementById('sheet').open"));tap("#closeSheet");waitFor("!document.getElementById('sheet').open",5);
            tap("[data-bot=companion]");settled();
            text("一份保留的草稿");tap("#chatBack");settled();tap("[data-bot=research]");settled();
            assertEquals("\"\"",js("document.getElementById('chatInput').value"));tap("#chatBack");settled();tap("[data-bot=companion]");settled();
            assertEquals("\"一份保留的草稿\"",js("document.getElementById('chatInput').value"));
            text("[动态验收一] 今天有件事想理清楚。");tap("#sendButton");waitFor("!state.busy&&visibleMessages().length>=2",25);Thread.sleep(450);
            js("window.__retainedMessage=document.querySelector('#messages .message');window.__retainedDetails=[...document.querySelectorAll('#messages details')]");
            text("[动态验收二] 先帮我明确第一步。");tap("#sendButton");waitFor("!state.busy&&visibleMessages().some(m=>m.text.includes('[动态验收二]'))",25);
            assertEquals("true",js("window.__retainedMessage.isConnected&&window.__retainedMessage===document.querySelector('#messages .message')"));
            assertEquals("0",js("window.__retainedMessage.getAnimations().length"));
            traceStart();tap("#chatInput");Thread.sleep(650);picture("motion-keyboard");
            assertEquals("true",js("document.querySelector('.composer').getBoundingClientRect().bottom<=innerHeight+2"));
            JSONArray keyboard=new JSONArray(js("window.__motionTrace"));audit.put(new JSONObject().put("name","keyboard-show").put("samples",keyboard));js("window.__traceUntil=0");
            ins.runOnMainSync(()->activity.getSystemService(android.view.inputmethod.InputMethodManager.class).hideSoftInputFromWindow(web.getWindowToken(),0));Thread.sleep(500);
            long imeEnd=SystemClock.uptimeMillis()+10000;boolean[] hidden={false};Field ime=MainActivity.class.getDeclaredField("imeAnimating");ime.setAccessible(true);
            while(SystemClock.uptimeMillis()<imeEnd){ins.runOnMainSync(()->{try{hidden[0]=!activity.getWindow().getDecorView().getRootWindowInsets().isVisible(WindowInsets.Type.ime())&&!ime.getBoolean(activity);}catch(Exception error){throw new RuntimeException(error);}});if(hidden[0])break;Thread.sleep(100);}assertTrue("IME dismissal did not settle",hidden[0]);
            tap("#chatBack");settled();waitFor("state.view==='home'",6);tap("#profileButton");settled();waitFor("state.view==='settings'",6);tap("[data-route=appearance]");settled();tap("[data-preference=motion]");
            waitFor("state.settings.motion===false",5);picture("motion-preferences");
            tap("#subBack");settled();assertEquals("0",js("document.querySelectorAll('.route-ghost').length"));
            assertEquals("true",js("PulseMotion.reduced()"));
            tap("[data-route=appearance]");tap("[data-preference=motion]");waitFor("state.settings.motion===true",5);
            audit.put(new JSONObject().put("name","regressions").put("rapid_back",true).put("gesture_cancel",true).put("gesture_commit",true).put("sheet_reopen",true).put("draft_isolation",true).put("old_message_retained",true).put("reduced_motion",true));
        }finally{
            try(FileOutputStream out=new FileOutputStream(new File(ins.getTargetContext().getExternalFilesDir(null),"motion-audit.json"))){out.write(audit.toString(2).getBytes(StandardCharsets.UTF_8));}
            ins.runOnMainSync(()->activity.finish());
        }
    }
}
