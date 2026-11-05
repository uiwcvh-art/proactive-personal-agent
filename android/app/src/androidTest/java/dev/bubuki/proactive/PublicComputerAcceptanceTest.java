package dev.bubuki.proactive;
import android.app.*;
import android.content.*;
import android.graphics.Bitmap;
import android.net.Uri;
import android.os.*;
import android.view.*;
import android.webkit.WebView;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import androidx.test.runner.lifecycle.ActivityLifecycleMonitorRegistry;
import androidx.test.runner.lifecycle.Stage;
import org.json.*;
import org.junit.Test;
import org.junit.runner.RunWith;
import java.io.*;
import java.lang.reflect.Field;
import java.security.MessageDigest;
import java.util.concurrent.*;
import static org.junit.Assert.*;

/** Live encrypted public API, real native download, no model call or production config write. */
@RunWith(AndroidJUnit4.class)
public class PublicComputerAcceptanceTest {
 final Instrumentation ins=InstrumentationRegistry.getInstrumentation();WebView web;Context c;
 String js(String code)throws Exception{CountDownLatch latch=new CountDownLatch(1);String[] result={null};ins.runOnMainSync(()->web.evaluateJavascript(code,r->{result[0]=r;latch.countDown();}));assertTrue(latch.await(10,TimeUnit.SECONDS));return result[0];}
 void waitFor(String code,int seconds)throws Exception{long end=System.currentTimeMillis()+seconds*1000L;while(System.currentTimeMillis()<end){if("true".equals(js("Boolean("+code+")")))return;Thread.sleep(200);}fail("Public UI condition failed: "+code);}
 String shell(String command)throws Exception{try(ParcelFileDescriptor p=ins.getUiAutomation().executeShellCommand(command);InputStream in=new FileInputStream(p.getFileDescriptor())){return new String(Io.read(in,1000000),java.nio.charset.StandardCharsets.UTF_8);}}
 void tap(String selector)throws Exception{waitFor("!PulseMotion.navigating&&!PulseMotion.sheetClosing",10);String q=JSONObject.quote(selector);js("document.querySelector("+q+").scrollIntoView({block:'center',behavior:'instant'})");Thread.sleep(200);JSONArray r=new JSONArray(js("(()=>{const r=document.querySelector("+q+").getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2,innerWidth]})()"));int[] xy=new int[2];ins.runOnMainSync(()->web.getLocationOnScreen(xy));float scale=(float)(web.getWidth()/r.getDouble(2));long t=SystemClock.uptimeMillis();float x=xy[0]+(float)r.getDouble(0)*scale,y=xy[1]+(float)r.getDouble(1)*scale;MotionEvent a=MotionEvent.obtain(t,t,0,x,y,0),b=MotionEvent.obtain(t,t+60,1,x,y,0);a.setSource(InputDevice.SOURCE_TOUCHSCREEN);b.setSource(InputDevice.SOURCE_TOUCHSCREEN);assertTrue(ins.getUiAutomation().injectInputEvent(a,true));Thread.sleep(60);assertTrue(ins.getUiAutomation().injectInputEvent(b,true));a.recycle();b.recycle();}
 void shot(String name)throws Exception{Thread.sleep(350);Bitmap b=ins.getUiAutomation().takeScreenshot();if(name.contains("-mika-"))assertTrue("Native Mika must follow the portal's selected dark appearance",android.graphics.Color.red(b.getPixel(15,450))<100);try(OutputStream out=new FileOutputStream(new File(c.getExternalFilesDir(null),name+".png"))){b.compress(Bitmap.CompressFormat.PNG,100,out);}b.recycle();}
 @Test public void realPublicComputerAndNativeDownload()throws Exception{
  c=ins.getTargetContext();String token=shell("cat /data/local/tmp/proactive-pairing").trim();assertTrue(token.length()>30);
  SecurePrefs.save(c,new JSONObject().put("serverUrl",SecureTransport.DEFAULT_SERVER).put("deviceToken",token).put("appearance","dark").put("background",false));UiPreferences.update(c,SecurePrefs.read(c));
  SecureTransport transport=new SecureTransport(c);JSONObject initial=transport.request("GET","/api/computer/status",null,null);assertTrue(initial.getBoolean("available"));
  assertFalse("Refuse to interfere with an active user task",initial.optJSONObject("job")!=null&&java.util.Arrays.asList("running","paused","waiting_user").contains(initial.getJSONObject("job").optString("state")));
  MainActivity a=(MainActivity)ins.startActivitySync(new Intent(c,MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK|Intent.FLAG_ACTIVITY_CLEAR_TASK));web=WebViewTestReady.await(ins,a);
  try{
   waitFor("typeof state==='object'&&state.online",40);tap("#profileButton");tap(".page.active [data-route=appearance]");tap("[data-appearance=dark]");waitFor("document.documentElement.dataset.theme==='dark'",10);tap("#subBack");tap("#subBack");shot("public-01-home");tap("[data-route=computer]");waitFor("document.getElementById('computerFrame').complete&&!document.getElementById('computerFrame').hidden&&cloudSnapshot.available",40);shot("public-02-cloud-computer");
   if(!"true".equals(js("cloudSnapshot.control_ready")))tap("#computerTakeover");waitFor("cloudSnapshot.control_ready",15);
   tap("[data-computer-tab=control]");shot("public-03-takeover");tap("[data-computer-tab=files]");waitFor("document.querySelector('[data-download-path]')",20);shot("public-04-cloud-files");
   JSONArray files=transport.request("GET","/api/computer/files",null,null).getJSONArray("files");String proof=null;for(int i=0;i<files.length();i++){JSONObject file=files.getJSONObject(i);if(file.getString("path").contains("cloud-computer-proof-")&&file.getLong("bytes")==768034){proof=file.getString("path");break;}}assertNotNull(proof);
   js("(()=>{const receive=window.onNativeReply;window.onNativeReply=p=>{if(p.ok&&p.data.uri)window.fileReceipt=p.data;receive(p)}})()");tap("[data-download-path=\""+proof+"\"]");waitFor("window.fileReceipt&&fileReceipt.bytes===768034",60);JSONObject receipt=new JSONObject(js("fileReceipt"));byte[] downloaded;try(InputStream in=c.getContentResolver().openInputStream(Uri.parse(receipt.getString("uri")))){downloaded=Io.read(in,1000000);}StringBuilder digest=new StringBuilder();for(byte value:MessageDigest.getInstance("SHA-256").digest(downloaded))digest.append(String.format(java.util.Locale.ROOT,"%02x",value&255));assertEquals("645180c20dd53683b6e7340e1585817f67a74a1d86802b2d801e4dc727f6a144",digest.toString());c.getContentResolver().delete(Uri.parse(receipt.getString("uri")),null,null);
   tap("#subBack");waitFor("state.view==='home'",10);tap("#profileButton");tap(".page.active [data-route=library]");waitFor("document.querySelector('#libraryFiles [data-download-path]')",30);shot("public-05-library");
   JSONObject report=new JSONObject().put("public_encrypted_api",true).put("public_real_frame",true).put("public_takeover",true).put("native_media_store_download_bytes",downloaded.length).put("sha256",digest.toString()).put("production_config_writes",0).put("paid_model_calls",0);
   report.put("own_client_cloud_flow",true);
   try(OutputStream out=new FileOutputStream(new File(c.getExternalFilesDir(null),"public-computer-acceptance.json"))){out.write(report.toString(2).getBytes(java.nio.charset.StandardCharsets.UTF_8));}
  }finally{transport.request("POST","/api/computer/handback",new JSONObject(),null);}
 }
}
