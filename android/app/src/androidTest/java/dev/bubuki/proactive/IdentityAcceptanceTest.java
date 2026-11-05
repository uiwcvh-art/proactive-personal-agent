package dev.bubuki.proactive;

import android.app.*;
import android.content.*;
import android.graphics.*;
import android.net.Uri;
import android.os.*;
import android.provider.MediaStore;
import android.view.*;
import android.view.accessibility.AccessibilityNodeInfo;
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

/** Real document picker, crop gestures, identity sync/reload, primary selection and generation. */
@RunWith(AndroidJUnit4.class)
public class IdentityAcceptanceTest {
    private final Instrumentation ins=InstrumentationRegistry.getInstrumentation();
    private MainActivity activity; private WebView web; private SecureTransport transport;
    private final JSONObject audit=new JSONObject();
    private String js(String code)throws Exception {
        CountDownLatch done=new CountDownLatch(1);String[] result={null};
        ins.runOnMainSync(()->web.evaluateJavascript(code,value->{result[0]=value;done.countDown();}));
        assertTrue(done.await(12,TimeUnit.SECONDS));return result[0];
    }
    private void waitFor(String code,int seconds)throws Exception {
        long end=SystemClock.uptimeMillis()+seconds*1000L;
        while(SystemClock.uptimeMillis()<end){if("true".equals(js("Boolean("+code+")")))return;Thread.sleep(100);}
        fail("Timed out: "+code+"; "+js("document.body.innerText.slice(-900)")+"; clicks="+js("JSON.stringify(window.__identityClicks?.slice(-12))"));
    }
    private String shell(String command)throws Exception {
        try(ParcelFileDescriptor fd=ins.getUiAutomation().executeShellCommand(command);InputStream in=new FileInputStream(fd.getFileDescriptor())){return new String(Io.read(in,60000),StandardCharsets.UTF_8);}
    }
    private void launch()throws Exception {
        activity=(MainActivity)ins.startActivitySync(new Intent(ins.getTargetContext(),MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK|Intent.FLAG_ACTIVITY_CLEAR_TASK));
        web=WebViewTestReady.await(ins,activity);
        waitFor("typeof state==='object'&&state.online&&!refreshing",40);
        js("window.__identityClicks=[];document.addEventListener('click',e=>__identityClicks.push({view:state.view,id:e.target.closest('button')?.id,at:performance.now()}),true)");
    }
    private void input(String id,String text)throws Exception {js("document.getElementById("+JSONObject.quote(id)+").value="+JSONObject.quote(text));}
    private void touch(float x,float y,int duration)throws Exception {
        long now=SystemClock.uptimeMillis();
        MotionEvent down=MotionEvent.obtain(now,now,MotionEvent.ACTION_DOWN,x,y,0),up=MotionEvent.obtain(now,now+duration,MotionEvent.ACTION_UP,x,y,0);
        down.setSource(InputDevice.SOURCE_TOUCHSCREEN);up.setSource(InputDevice.SOURCE_TOUCHSCREEN);
        assertTrue(ins.getUiAutomation().injectInputEvent(down,true));Thread.sleep(duration);assertTrue(ins.getUiAutomation().injectInputEvent(up,true));down.recycle();up.recycle();
    }
    private float[] center(String selector)throws Exception {
        String q=JSONObject.quote(selector);js("document.querySelector("+q+").scrollIntoView({block:'center',behavior:'instant'})");Thread.sleep(120);
        JSONArray r=new JSONArray(js("(()=>{const r=document.querySelector("+q+").getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2,innerWidth]})()"));int[] location=new int[2];ins.runOnMainSync(()->web.getLocationOnScreen(location));float scale=web.getWidth()/(float)r.getDouble(2);
        return new float[]{location[0]+(float)r.getDouble(0)*scale,location[1]+(float)r.getDouble(1)*scale,scale};
    }
    private void tap(String selector)throws Exception {
        ins.waitForIdleSync();Thread.sleep(120);
        String q=JSONObject.quote(selector);
        waitFor("!PulseMotion.navigating&&!PulseMotion.sheetClosing",12);
        waitFor("document.querySelector("+q+")&&!document.querySelector("+q+").disabled&&document.querySelector("+q+").getBoundingClientRect().height>10",12);
        float[] p=center(selector);
        waitFor("(()=>{const e=document.querySelector("+q+"),r=e.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return e===hit||e.contains(hit)})()",6);
        p=center(selector);touch(p[0],p[1],65);ins.waitForIdleSync();Thread.sleep(230);
    }
    private void panPhoto()throws Exception {
        float[] p=center("#avatarCropViewport");long now=SystemClock.uptimeMillis();
        for(int i=0;i<=16;i++){int action=i==0?MotionEvent.ACTION_DOWN:i==16?MotionEvent.ACTION_UP:MotionEvent.ACTION_MOVE;MotionEvent event=MotionEvent.obtain(now,now+i*20,action,p[0]+i*5*p[2],p[1]+i*2*p[2],0);event.setSource(InputDevice.SOURCE_TOUCHSCREEN);assertTrue(ins.getUiAutomation().injectInputEvent(event,true));event.recycle();Thread.sleep(20);}
    }
    private void shot(String name)throws Exception {
        waitFor("!PulseMotion.navigating&&!PulseMotion.sheetClosing&&!document.getAnimations().some(a=>a.playState==='running'&&a.effect?.getTiming().iterations!==Infinity)",10);
        ins.waitForIdleSync();Thread.sleep(180);
        Bitmap bitmap=ins.getUiAutomation().takeScreenshot();assertNotNull(bitmap);try(FileOutputStream out=new FileOutputStream(new File(ins.getTargetContext().getExternalFilesDir(null),name+".png"))){bitmap.compress(Bitmap.CompressFormat.PNG,100,out);}bitmap.recycle();
    }
    private AccessibilityNodeInfo findPhoto(AccessibilityNodeInfo node){
        if(node==null)return null;
        if(String.valueOf(node.getText()).contains("pulse-avatar-identity")||String.valueOf(node.getContentDescription()).contains("pulse-avatar-identity"))return node;
        for(int i=0;i<node.getChildCount();i++){AccessibilityNodeInfo found=findPhoto(node.getChild(i));if(found!=null)return found;}return null;
    }
    private AccessibilityNodeInfo nativeLabel(AccessibilityNodeInfo node,String label,boolean partial){
        if(node==null)return null;String text=String.valueOf(node.getText()),description=String.valueOf(node.getContentDescription());
        Rect bounds=new Rect();node.getBoundsInScreen(bounds);
        AccessibilityNodeInfo best=node.isVisibleToUser()&&bounds.width()>5&&bounds.height()>5&&(partial?text.contains(label)||description.contains(label):text.equals(label)||description.equals(label))?node:null;
        for(int i=0;i<node.getChildCount();i++){AccessibilityNodeInfo found=nativeLabel(node.getChild(i),label,partial);if(found!=null){Rect candidate=new Rect();found.getBoundsInScreen(candidate);Rect previous=new Rect();if(best!=null)best.getBoundsInScreen(previous);if(best==null||candidate.top>previous.top)best=found;}}return best;
    }
    private void nativeTap(String label,boolean partial)throws Exception {
        AccessibilityNodeInfo found=null;long until=SystemClock.uptimeMillis()+14000,nextScroll=SystemClock.uptimeMillis()+2000;int scrolls=0;
        while(SystemClock.uptimeMillis()<until){
            if(Build.VERSION.SDK_INT>=34)ins.getUiAutomation().clearCache();
            AccessibilityNodeInfo current=ins.getUiAutomation().getRootInActiveWindow();found=nativeLabel(current,label,partial);if(found!=null)break;
            // The emulator's storage root contains enough folders to put Pictures
            // below the fold. Traverse the real picker, rather than assuming it fits.
            if(current!=null&&"com.google.android.documentsui".contentEquals(current.getPackageName())&&SystemClock.uptimeMillis()>=nextScroll&&scrolls++<4){
                Bitmap screen=ins.getUiAutomation().takeScreenshot();int w=screen.getWidth(),h=screen.getHeight();screen.recycle();
                shell("input swipe "+w/2+" "+(h*85/100)+" "+w/2+" "+(h*42/100)+" 360");nextScroll=SystemClock.uptimeMillis()+1800;
            }
            Thread.sleep(150);
        }
        assertNotNull("Native picker label: "+label,found);ins.getUiAutomation().waitForIdle(250,5000);
        found=nativeLabel(ins.getUiAutomation().getRootInActiveWindow(),label,partial);assertNotNull(found);
        Rect bounds=new Rect();found.getBoundsInScreen(bounds);shell("input tap "+bounds.centerX()+" "+bounds.centerY());Thread.sleep(300);
    }
    private Uri insertPhoto()throws Exception {
        Context context=ins.getTargetContext();ContentValues values=new ContentValues();values.put(MediaStore.Images.Media.DISPLAY_NAME,"pulse-avatar-identity.png");values.put(MediaStore.Images.Media.MIME_TYPE,"image/png");values.put(MediaStore.Images.Media.RELATIVE_PATH,"Pictures/PulseAcceptance");values.put(MediaStore.Images.Media.IS_PENDING,1);values.put(MediaStore.Images.Media.DATE_TAKEN,System.currentTimeMillis());values.put(MediaStore.Images.Media.WIDTH,800);values.put(MediaStore.Images.Media.HEIGHT,500);
        Uri uri=context.getContentResolver().insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI,values);assertNotNull(uri);
        Bitmap image=Bitmap.createBitmap(800,500,Bitmap.Config.ARGB_8888);Canvas canvas=new Canvas(image);canvas.drawColor(Color.rgb(12,17,30));Paint paint=new Paint(Paint.ANTI_ALIAS_FLAG);paint.setShader(new LinearGradient(230,100,570,430,new int[]{0xFFAD7DFF,0xFF7C5CFF,0xFF303D99},null,Shader.TileMode.CLAMP));canvas.drawCircle(400,250,175,paint);paint.setShader(null);paint.setColor(0xFFFFDCAB);paint.setStrokeWidth(12);canvas.drawLine(170,350,650,140,paint);
        try(OutputStream out=context.getContentResolver().openOutputStream(uri)){assertTrue(image.compress(Bitmap.CompressFormat.PNG,100,out));}image.recycle();values.clear();values.put(MediaStore.Images.Media.IS_PENDING,0);context.getContentResolver().update(uri,values,null,null);
        try(android.database.Cursor cursor=context.getContentResolver().query(uri,new String[]{MediaStore.Images.Media.SIZE,MediaStore.Images.Media.IS_PENDING},null,null,null)){assertNotNull(cursor);assertTrue(cursor.moveToFirst());assertTrue(cursor.getLong(0)>0);assertEquals(0,cursor.getInt(1));}
        return uri;
    }
    private JSONObject research()throws Exception {JSONArray bots=transport.request("GET","/api/bots",null,null).getJSONArray("bots");for(int i=0;i<bots.length();i++)if(bots.getJSONObject(i).getString("id").equals("research"))return bots.getJSONObject(i);throw new AssertionError("research Bot missing");}
    @Test public void identityPhotosGenerationAndPrimary()throws Exception {
        Context context=ins.getTargetContext();String token=shell("cat /data/local/tmp/proactive-pairing").trim(),key;
        try(InputStream in=context.getAssets().open("transport.json")){key=new JSONObject(new String(Io.read(in,20000),StandardCharsets.UTF_8)).getString("public_key");}
        String server=InstrumentationRegistry.getArguments().getString("uiServer","http://127.0.0.1:18191");
        JSONObject settings=new JSONObject().put("serverUrl",server).put("publicKey",key).put("deviceToken",token).put("background",false).put("appearance","dark").put("motion",true).put("haptics",false);
        SecurePrefs.save(context,settings);UiPreferences.update(context,settings);transport=new SecureTransport(context);
        Uri photo=insertPhoto();
        try {
            assertTrue("Only the isolated emulator is in scope",Build.MODEL.contains("sdk")||Build.FINGERPRINT.contains("emulator"));
            shell("am force-stop com.google.android.documentsui");
            launch();tap("#botList [data-bot=research]");tap("#chatIdentity");tap("#editIdentity");waitFor("state.view==='identity'",8);
            input("identityName","旅行资料官");input("identityTitle","路线、机票与待办");shot("identity-01-edit");
            tap("#identityAvatar");shot("identity-02-shapes");tap("[data-avatar-tab=upload]");tap("[data-avatar-photo=library]");
            // Browse the real storage provider; Recents indexing is outside the app's contract.
            nativeTap("Show roots",false);nativeTap(Build.MODEL,false);nativeTap("Pictures",false);nativeTap("PulseAcceptance",false);
            if(nativeLabel(ins.getUiAutomation().getRootInActiveWindow(),"List view",false)!=null)nativeTap("List view",false);
            shot("identity-03-photo-picker");nativeTap("pulse-avatar-identity",true);
            waitFor("state.view==='avatarCrop'&&document.getElementById('avatarCropImage').naturalWidth>0",15);tap("#avatarZoom");waitFor("avatarCrop.zoom>1.3",4);panPhoto();assertTrue(Double.parseDouble(js("avatarCrop.x"))>10);shot("identity-04-crop");tap("#subAction");waitFor("state.view==='identity'",8);shot("identity-05-photo-preview");tap("#subAction");waitFor("state.view==='profile'&&bot().name==='旅行资料官'",20);
            JSONObject changed=research();String version=changed.getString("avatar_version");assertEquals(64,version.length());assertEquals("路线、机票与待办",changed.getString("title"));shot("identity-06-profile");
            ins.runOnMainSync(()->activity.finish());launch();tap("#botList [data-bot=research]");tap("#chatIdentity");waitFor("document.querySelector('#botProfile .photo-avatar img')?.naturalWidth===512",15);shot("identity-07-reloaded");audit.put("photo_reload",true).put("crop_pan_and_zoom",true);
            tap("#profilePrimary");waitFor("state.view==='primaryBot'",8);shot("identity-08-primary");tap("#subBack");assertEquals("companion",transport.request("GET","/api/bots",null,null).getString("primary_bot_id"));
            tap("#profilePrimary");tap("#confirmPrimary");waitFor("state.view==='profile'&&state.primaryBotId==='research'",15);assertEquals("research",transport.request("GET","/api/bots",null,null).getString("primary_bot_id"));
            tap("#subBack");tap("#chatActions");int index=Integer.parseInt(js("Array.from(document.querySelectorAll('#sheetBody [data-sheet-action]')).findIndex(b=>b.querySelector('strong')?.textContent==='触发好奇心追问')"));assertTrue(index>=0);tap("#sheetBody [data-sheet-action='"+index+"']");waitFor("!state.busy&&state.messages.some(m=>m.bot_id==='research'&&m.kind==='curiosity')",20);shot("identity-09-primary-checkin");audit.put("primary_routes_checkin",true);
            tap("#chatIdentity");tap("#editIdentity");input("identityTitle","这次不会保存");tap("#identityAvatar");tap("[data-identity-shape=diamond]");tap("[data-sheet-action=done]");tap("#subBack");waitFor("document.getElementById('sheet').open&&document.getElementById('sheetTitle').textContent.includes('放弃')",8);shot("identity-10-discard");tap("[data-sheet-action=discard]");waitFor("state.view==='profile'",8);assertEquals(version,research().getString("avatar_version"));assertEquals("路线、机票与待办",research().getString("title"));
            tap("#editIdentity");tap("#identityAvatar");tap("[data-avatar-tab=generate]");input("avatarPrompt","深空中的橙色小星球");input("avatarModel","avatar-fixture");tap("#generateAvatar");waitFor("document.getElementById('avatarGenerateFeedback').textContent.includes('API Key')",15);shot("identity-11-generation-config");
            transport.request("POST","/api/config",new JSONObject().put("big_brain",new JSONObject().put("api_key","fixture-avatar-key").put("base_url",server+"/v1").put("model","mika-fixture")),null);
            tap("#generateAvatar");waitFor("state.view==='avatarCrop'&&document.getElementById('avatarCropImage').naturalWidth===512",25);assertEquals(version,research().getString("avatar_version"));shot("identity-12-generated-crop");tap("#subAction");tap("#subAction");waitFor("state.view==='profile'&&!identitySaving",20);assertNotEquals(version,research().getString("avatar_version"));shot("identity-13-generated-avatar");audit.put("generated_avatar", "isolated HTTP provider fixture; no paid model");
            tap("#editIdentity");tap("#identityAvatar");tap("[data-identity-shape=flower]");tap("[data-sheet-action=done]");tap("#subAction");waitFor("state.view==='profile'&&!bot().avatar_version",20);assertEquals("",research().getString("avatar_version"));audit.put("photo_to_shape",true).put("unsaved_cancel_preserves_cloud",true).put("completed",true);
        } finally {
            if(!audit.optBoolean("completed")){try{shot("identity-failure");}catch(Exception ignored){}}
            context.getContentResolver().delete(photo,null,null);
            try(FileOutputStream out=new FileOutputStream(new File(context.getExternalFilesDir(null),"identity-acceptance.json"))){out.write(audit.toString(2).getBytes(StandardCharsets.UTF_8));}
            if(activity!=null)ins.runOnMainSync(()->activity.finish());
        }
    }
}
