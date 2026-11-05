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
import java.util.function.BooleanSupplier;
import static org.junit.Assert.*;

/** Native touch -> encrypted fixture API -> persisted native/secondary screen state. */
@RunWith(AndroidJUnit4.class)
public class NativeSurfaceAcceptanceTest {
    private final Instrumentation ins=InstrumentationRegistry.getInstrumentation();
    private NativePulseActivity activity; private NativePulseModel model; private MainActivity secondary;
    private final JSONArray frames=new JSONArray(); private final JSONObject audit=new JSONObject();
    private final JSONArray routes=new JSONArray();
    private volatile String phase="startup";
    private HandlerThread frameThread;
    private Window.OnFrameMetricsAvailableListener metricListener;

    private void waitFor(BooleanSupplier value,int seconds,String description)throws Exception {
        long deadline=SystemClock.uptimeMillis()+seconds*1000L;
        while(SystemClock.uptimeMillis()<deadline){if(value.getAsBoolean())return;Thread.sleep(100);}
        fail("Timed out: "+description);
    }
    private String shell(String command)throws Exception {
        try(ParcelFileDescriptor fd=ins.getUiAutomation().executeShellCommand(command);InputStream in=new FileInputStream(fd.getFileDescriptor())){
            return new String(Io.read(in,30000),StandardCharsets.UTF_8);
        }
    }
    private AccessibilityNodeInfo find(AccessibilityNodeInfo node,String label,boolean description){
        if(node==null)return null;
        CharSequence value=description?node.getContentDescription():node.getText();
        if(node.isVisibleToUser()&&value!=null&&label.contentEquals(value))return node;
        for(int i=node.getChildCount()-1;i>=0;i--){AccessibilityNodeInfo found=find(node.getChild(i),label,description);if(found!=null)return found;}
        return null;
    }
    private AccessibilityNodeInfo freshRoot(){
        if(Build.VERSION.SDK_INT>=34)ins.getUiAutomation().clearCache();
        AccessibilityNodeInfo root=ins.getUiAutomation().getRootInActiveWindow();
        if(root!=null&&Build.VERSION.SDK_INT<34)root.refresh();return root;
    }
    private Rect bounds(String label,boolean description)throws Exception {
        final Rect result=new Rect(),previous=new Rect();final int[] stable={0};
        waitFor(()->{AccessibilityNodeInfo found=find(freshRoot(),label,description);if(found==null){stable[0]=0;return false;}found.getBoundsInScreen(result);if(result.isEmpty()){stable[0]=0;return false;}stable[0]=result.equals(previous)?stable[0]+1:0;previous.set(result);return stable[0]>=2;},12,label+" stable touch bounds");
        return result;
    }
    private boolean visibleTextContains(AccessibilityNodeInfo node,String expected){
        if(node==null)return false;if(node.isVisibleToUser()&&node.getText()!=null&&node.getText().toString().contains(expected))return true;
        for(int i=0;i<node.getChildCount();i++)if(visibleTextContains(node.getChild(i),expected))return true;return false;
    }
    private void touch(float x,float y,long hold)throws Exception {
        long start=SystemClock.uptimeMillis();
        for(int action:new int[]{MotionEvent.ACTION_DOWN,MotionEvent.ACTION_UP}){
            MotionEvent.PointerProperties finger=new MotionEvent.PointerProperties();finger.id=0;finger.toolType=MotionEvent.TOOL_TYPE_FINGER;
            MotionEvent.PointerCoords point=new MotionEvent.PointerCoords();point.x=x;point.y=y;point.pressure=action==MotionEvent.ACTION_DOWN?1:0;point.size=1;
            MotionEvent e=MotionEvent.obtain(start,SystemClock.uptimeMillis(),action,1,new MotionEvent.PointerProperties[]{finger},new MotionEvent.PointerCoords[]{point},0,0,1,1,0,0,InputDevice.SOURCE_TOUCHSCREEN,0);assertTrue(ins.getUiAutomation().injectInputEvent(e,true));e.recycle();if(action==MotionEvent.ACTION_DOWN)Thread.sleep(hold);
        };Thread.sleep(100);
    }
    private void tap(String text,boolean description)throws Exception {Rect r=bounds(text,description);touch(r.exactCenterX(),r.exactCenterY(),45);}
    private void key(int code){ins.sendKeyDownUpSync(code);}
    private void screenshot(String name)throws Exception {
        ins.waitForIdleSync();
        if(activity!=null&&activity.hasWindowFocus())committedFrame();
        // The closing secondary Window and the IME can still be presenting an
        // older buffer after focus/data callbacks. Capture their settled pixels.
        Thread.sleep(450);
        Bitmap bitmap=ins.getUiAutomation().takeScreenshot();assertNotNull(bitmap);
        try(FileOutputStream out=new FileOutputStream(new File(ins.getTargetContext().getExternalFilesDir(null),name+".png"))){bitmap.compress(Bitmap.CompressFormat.PNG,100,out);}bitmap.recycle();
    }
    private void seed()throws Exception {
        Context c=ins.getTargetContext();String token=shell("cat /data/local/tmp/proactive-pairing").trim(),publicKey;
        try(InputStream in=c.getAssets().open("transport.json")){publicKey=new JSONObject(new String(Io.read(in,20000),StandardCharsets.UTF_8)).getString("public_key");}
        JSONObject settings=new JSONObject().put("serverUrl",InstrumentationRegistry.getArguments().getString("uiServer","http://127.0.0.1:18191"))
            .put("publicKey",publicKey).put("deviceToken",token).put("background",false).put("appearance","light").put("motion",true).put("haptics",false);
        SecurePrefs.save(c,settings);UiPreferences.update(c,settings);
    }
    private void launch()throws Exception {launch(true);}
    private void launch(boolean online)throws Exception {
        long start=SystemClock.uptimeMillis();
        Instrumentation.ActivityMonitor monitor=ins.addMonitor(NativePulseActivity.class.getName(),null,false);
        Intent launch=ins.getTargetContext().getPackageManager().getLaunchIntentForPackage(ins.getTargetContext().getPackageName());assertNotNull(launch);
        assertEquals(LauncherActivity.class.getName(),launch.getComponent().getClassName());
        launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK|Intent.FLAG_ACTIVITY_CLEAR_TASK).putExtra("openBot","research").putExtra("entryRoute","root");ins.getTargetContext().startActivity(launch);
        activity=(NativePulseActivity)ins.waitForMonitorWithTimeout(monitor,20000);ins.removeMonitor(monitor);assertNotNull("Public launcher did not open native UI",activity);
        Field f=NativePulseActivity.class.getDeclaredField("model");f.setAccessible(true);model=(NativePulseModel)f.get(activity);
        frameThread=new HandlerThread("native-surface-frames",android.os.Process.THREAD_PRIORITY_BACKGROUND);frameThread.start();
        metricListener=(window,m,dropped)->{try{synchronized(frames){frames.put(new JSONObject().put("phase",phase).put("at_ms",SystemClock.uptimeMillis()).put("intended_vsync_ms",m.getMetric(FrameMetrics.INTENDED_VSYNC_TIMESTAMP)/1e6).put("total_ms",m.getMetric(FrameMetrics.TOTAL_DURATION)/1e6).put("draw_ms",m.getMetric(FrameMetrics.DRAW_DURATION)/1e6).put("gpu_ms",m.getMetric(FrameMetrics.GPU_DURATION)/1e6).put("dropped_callbacks",dropped));}}catch(Exception ignored){}};
        activity.getWindow().addOnFrameMetricsAvailableListener(metricListener,new Handler(frameThread.getLooper()));
        if(online)waitFor(()->model.getUi().getOnline()&&!model.getUi().getBots().isEmpty(),35,"native encrypted API connected");
        audit.put(audit.has("activity_to_online_ms")?"restart_activity_to_online_ms":"activity_to_online_ms",SystemClock.uptimeMillis()-start);
        bounds("打开白kawa的对话",true);committedFrame();
        audit.put(audit.has("rendered_home_ms")?"restart_rendered_home_ms":"rendered_home_ms",SystemClock.uptimeMillis()-start);
    }
    private void committedFrame()throws Exception {
        CountDownLatch commit=new CountDownLatch(1);
        ins.runOnMainSync(()->{View view=activity.getWindow().getDecorView();view.getViewTreeObserver().registerFrameCommitCallback(commit::countDown);view.postInvalidateOnAnimation();});
        assertTrue("Native Window frame not committed",commit.await(10,TimeUnit.SECONDS));Thread.sleep(80);
    }
    private void imeReady()throws Exception {
        long end=SystemClock.uptimeMillis()+12000;int lastHeight=-1,stable=0;
        while(SystemClock.uptimeMillis()<end){
            int[] height={-1};boolean[] visible={false};
            ins.runOnMainSync(()->{WindowInsets insets=activity.getWindow().getDecorView().getRootWindowInsets();visible[0]=insets!=null&&insets.isVisible(WindowInsets.Type.ime());height[0]=insets==null?-1:insets.getInsets(WindowInsets.Type.ime()).bottom;});
            stable=visible[0]&&height[0]==lastHeight?stable+1:0;lastHeight=height[0];if(stable>=3){Thread.sleep(100);return;}Thread.sleep(80);
        };fail("Native IME did not settle before typing");
    }
    private void typeWord(String word)throws Exception {
        assertTrue(word.matches("[a-z]+"));tap("聊天消息",true);imeReady();shell("input text "+word);
        // The owned AVD uses a Pinyin IME. Enter commits its current Latin pre-edit;
        // punctuation would otherwise be localized and the final word left uncommitted.
        key(KeyEvent.KEYCODE_ENTER);
        waitFor(()->model.getUi().getDraft().trim().equals(word),10,"real keyboard composition commit");
    }
    private void settled(boolean chat)throws Exception {
        waitFor(()->model.getUi().getChatOpen()==chat&&!model.getNavigationRunning(),8,"native navigation settled");Thread.sleep(120);
    }
    private void open(String profile)throws Exception {
        Rect row=bounds("打开白kawa的对话",true);phase=profile;long start=SystemClock.uptimeMillis();
        touch(row.exactCenterX(),row.exactCenterY(),45);Thread.sleep(650);settled(true);
        routes.put(new JSONObject().put("phase",profile).put("start_ms",start).put("end_ms",SystemClock.uptimeMillis()));phase="other";
    }
    private String js(String script)throws Exception {
        WebView view=WebViewTestReady.await(ins,secondary);
        CountDownLatch done=new CountDownLatch(1);String[] value={null};
        ins.runOnMainSync(()->view.evaluateJavascript(script,r->{value[0]=r;done.countDown();}));
        assertTrue(done.await(10,TimeUnit.SECONDS));return value[0];
    }
    private void waitJs(String code)throws Exception {
        long until=SystemClock.uptimeMillis()+35000;
        while(SystemClock.uptimeMillis()<until){if("true".equals(js("Boolean("+code+")")))return;Thread.sleep(100);}
        fail("Secondary route did not settle: "+code);
    }
    private void webTap(String selector)throws Exception {
        String quoted=JSONObject.quote(selector);waitJs("document.querySelector("+quoted+")&&!PulseMotion.navigating");
        js("document.querySelector("+quoted+").scrollIntoView({block:'center',behavior:'instant'})");
        JSONArray rect=new JSONArray(js("(()=>{const r=document.querySelector("+quoted+").getBoundingClientRect();return[r.x+r.width/2,r.y+r.height/2,innerWidth]})()"));
        WebView view=WebViewTestReady.await(ins,secondary);
        int[] origin=new int[2];ins.runOnMainSync(()->view.getLocationOnScreen(origin));float scale=(float)(view.getWidth()/rect.getDouble(2));
        touch(origin[0]+(float)rect.getDouble(0)*scale,origin[1]+(float)rect.getDouble(1)*scale,45);
    }
    private void swipe(boolean complete)throws Exception {
        Rect r=new Rect();ins.getUiAutomation().getRootInActiveWindow().getBoundsInScreen(r);
        float y=r.height()*.47f,startX=r.left+8,endX=r.left+r.width()*(complete?.69f:.16f);long start=SystemClock.uptimeMillis();
        MotionEvent down=MotionEvent.obtain(start,start,MotionEvent.ACTION_DOWN,startX,y,0);down.setSource(InputDevice.SOURCE_TOUCHSCREEN);assertTrue(ins.getUiAutomation().injectInputEvent(down,true));down.recycle();
        for(int i=1;i<=22;i++){
            float t=i/22f;float fraction=complete?t:(t<.5f?t*2:(1-t)*2);
            MotionEvent move=MotionEvent.obtain(start,SystemClock.uptimeMillis(),MotionEvent.ACTION_MOVE,startX+(endX-startX)*fraction,y,0);move.setSource(InputDevice.SOURCE_TOUCHSCREEN);assertTrue(ins.getUiAutomation().injectInputEvent(move,true));move.recycle();Thread.sleep(25);
        }
        MotionEvent up=MotionEvent.obtain(start,SystemClock.uptimeMillis(),MotionEvent.ACTION_UP,complete?endX:startX,y,0);up.setSource(InputDevice.SOURCE_TOUCHSCREEN);assertTrue(ins.getUiAutomation().injectInputEvent(up,true));up.recycle();Thread.sleep(500);
    }
    private void scrollTo(String text,boolean description)throws Exception {
        for(int attempt=0;attempt<9;attempt++){
            AccessibilityNodeInfo found=find(freshRoot(),text,description);
            if(found!=null){Rect r=new Rect();found.getBoundsInScreen(r);if(r.height()>12)return;}
            Rect window=new Rect();ins.getUiAutomation().getRootInActiveWindow().getBoundsInScreen(window);
            float x=window.exactCenterX(),from=window.top+window.height()*.73f,to=window.top+window.height()*.32f;long start=SystemClock.uptimeMillis();
            MotionEvent down=MotionEvent.obtain(start,start,MotionEvent.ACTION_DOWN,x,from,0);down.setSource(InputDevice.SOURCE_TOUCHSCREEN);assertTrue(ins.getUiAutomation().injectInputEvent(down,true));down.recycle();
            for(int i=1;i<=18;i++){
                MotionEvent move=MotionEvent.obtain(start,SystemClock.uptimeMillis(),MotionEvent.ACTION_MOVE,x,from+(to-from)*i/18f,0);move.setSource(InputDevice.SOURCE_TOUCHSCREEN);assertTrue(ins.getUiAutomation().injectInputEvent(move,true));move.recycle();Thread.sleep(22);
            }
            MotionEvent up=MotionEvent.obtain(start,SystemClock.uptimeMillis(),MotionEvent.ACTION_UP,x,to,0);up.setSource(InputDevice.SOURCE_TOUCHSCREEN);assertTrue(ins.getUiAutomation().injectInputEvent(up,true));up.recycle();Thread.sleep(300);
        };fail("Native message action not reachable: "+text);
    }
    private void stopMetrics(){if(activity!=null&&metricListener!=null)activity.getWindow().removeOnFrameMetricsAvailableListener(metricListener);if(frameThread!=null)frameThread.quitSafely();}

    private JSONObject fixture(String path,JSONObject body)throws Exception {
        String base=InstrumentationRegistry.getArguments().getString("uiServer","http://127.0.0.1:18191");assertEquals("http://127.0.0.1:18191",base);
        java.net.HttpURLConnection connection=(java.net.HttpURLConnection)new java.net.URL(base+"/_fixture/"+path).openConnection();
        connection.setConnectTimeout(5000);connection.setReadTimeout(5000);
        if(body!=null){connection.setRequestMethod("POST");connection.setDoOutput(true);connection.setRequestProperty("Content-Type","application/json");try(OutputStream output=connection.getOutputStream()){output.write(body.toString().getBytes(StandardCharsets.UTF_8));}}
        try(InputStream input=connection.getInputStream()){return new JSONObject(new String(Io.read(input,200000),StandardCharsets.UTF_8));}finally{connection.disconnect();}
    }
    private void closeNative(){stopMetrics();if(activity!=null)ins.runOnMainSync(()->activity.finish());activity=null;}
    private void clearNativeInput()throws Exception {
        tap("聊天消息",true);imeReady();long t=SystemClock.uptimeMillis();
        for(int action:new int[]{KeyEvent.ACTION_DOWN,KeyEvent.ACTION_UP})assertTrue(ins.getUiAutomation().injectInputEvent(new KeyEvent(t,SystemClock.uptimeMillis(),action,KeyEvent.KEYCODE_A,0,KeyEvent.META_CTRL_ON),true));
        key(KeyEvent.KEYCODE_DEL);waitFor(()->model.getUi().getDraft().isEmpty(),6,"real select-all/delete");
    }

    @Test public void launcherAttachmentsOfflinePhoneAndNotifications()throws Exception {
        seed();Context c=ins.getTargetContext();Uri document=null;Activity local=null,phoneHub=null;
        SecureTransport transport=new SecureTransport(c);
        try {
            JSONObject prefs=SecurePrefs.read(c);prefs.put("config",new JSONObject().put("big_brain",new JSONObject().put("base_url","http://127.0.0.1:18191/v1").put("api_key","isolated-native-phone-key").put("model","root-fixture")));SecurePrefs.save(c,prefs);
            launch();assertFalse(model.getUi().getChatOpen());audit.put("public_launcher_native",true).put("public_launcher_ignores_navigation_extras",true);
            fixture("availability",new JSONObject().put("offline",true));
            Instrumentation.ActivityMonitor monitor=ins.addMonitor(NativePhoneActivity.class.getName(),null,false);tap("此手机",false);
            phoneHub=ins.waitForMonitorWithTimeout(monitor,15000);ins.removeMonitor(monitor);assertNotNull(phoneHub);
            bounds("连接 Root",false);screenshot("launch-01-own-root-offline");
            assertFalse(RootPhoneHarness.status(c).getBoolean("root"));
            tap("返回私人空间",true);phoneHub=null;
            fixture("availability",new JSONObject().put("offline",false));waitFor(()->activity.hasWindowFocus()&&model.getUi().getOnline(),15,"native node reconnected");
            audit.put("own_root_entry_without_cloud",true).put("no_third_party_activity",true);
            open("other");typeWord("nativerootdenieddraft");String denied=model.getUi().getDraft();tap("选择处理方式",true);tap("此手机",false);tap("发送消息",true);
            waitFor(()->model.getUi().getBusyBot()==null&&model.getUi().getDraft().equals(denied),12,"Root authorization denial retains draft");
            assertTrue(RootPhoneHarness.history(c).getJSONArray("messages").length()==0);assertEquals(0,fixture("phone_calls",null).getInt("calls"));audit.put("root_required_no_fallback_draft",true);
            clearNativeInput();tap("选择处理方式",true);tap("对话",false);
            // Use the system document picker and verify the encrypted upload's real bytes.
            byte[] content="native attachment\n测试附件\n".getBytes(StandardCharsets.UTF_8);String filename="native-launch-attachment.txt";
            ContentValues values=new ContentValues();values.put(MediaStore.Downloads.DISPLAY_NAME,filename);values.put(MediaStore.Downloads.MIME_TYPE,"text/plain");values.put(MediaStore.Downloads.RELATIVE_PATH,Environment.DIRECTORY_DOWNLOADS+"/");values.put(MediaStore.Downloads.IS_PENDING,1);
            document=c.getContentResolver().insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI,values);assertNotNull(document);try(OutputStream output=c.getContentResolver().openOutputStream(document)){assertNotNull(output);output.write(content);}values.clear();values.put(MediaStore.Downloads.IS_PENDING,0);c.getContentResolver().update(document,values,null,null);
            typeWord("nativeattachmentdraft");String draft=model.getUi().getDraft();tap("打开工具",true);tap("添加附件",false);
            bounds("Show roots",true);tap("Show roots",true);tap("Downloads",false);
            // Keep the disposable file in Downloads itself: DocumentsUI may also
            // expose the remembered folder in a transient breadcrumb with the
            // same text as its directory row. This test targets the actual file.
            ins.getUiAutomation().waitForIdle(450,6000);
            if(find(freshRoot(),"List view",true)!=null)tap("List view",true);
            tap(filename,false);waitFor(()->!model.getUi().getUploading()&&model.getUi().getAttachments().size()==1,20,"native file upload receipt");
            PulseAttachment file=model.getUi().getAttachments().get(0);assertEquals(content.length,file.getBytes());
            JSONObject data=transport.request("GET","/api/files/file",null,new JSONObject().put("path",file.getPath()).put("offset",0).put("size",262144));assertArrayEquals(content,android.util.Base64.decode(data.getString("data"),android.util.Base64.DEFAULT));
            tap("返回助手列表",true);settled(false);open("other");assertEquals(draft,model.getUi().getDraft());assertEquals(1,model.getUi().getAttachments().size());assertEquals("execute",model.getUi().getMode());screenshot("launch-05-attachment-draft");
            tap("打开工具",true);tap("添加附件",false);bounds("Show roots",true);key(KeyEvent.KEYCODE_BACK);waitFor(()->activity.hasWindowFocus(),10,"picker cancellation");assertEquals(1,model.getUi().getAttachments().size());assertEquals(draft,model.getUi().getDraft());
            fixture("availability",new JSONObject().put("offline",true));tap("发送消息",true);
            waitFor(()->model.getUi().getBusyBot()==null&&!model.getUi().getOnline()&&model.getUi().getDraft().equals(draft)&&model.getUi().getAttachments().size()==1,15,"failed offline send preserves draft and attachment");
            fixture("availability",new JSONObject().put("offline",false));waitFor(()->model.getUi().getOnline(),15,"node reconnect after failed send");audit.put("failed_send_preserves_attachment_draft",true);
            clearNativeInput();tap("发送消息",true);waitFor(()->model.getUi().getBusyBot()==null&&model.getUi().getAttachments().isEmpty()&&model.getUi().getMessages().stream().anyMatch(m->m.getRole().equals("user")&&m.getText().contains(file.getPath().replaceFirst("^workspace/",""))),25,"attachment-only message sent and cleared");
            key(KeyEvent.KEYCODE_BACK);Thread.sleep(300);audit.put("native_document_picker_and_upload",true).put("attachment_draft_cancel_and_send",true);
            // A real Harness reply carries an Obsidian link into the actual editor.
            transport.request("PUT","/api/vault",new JSONObject().put("path","tasks/sniffed_opportunities.md").put("content","# 记忆链接\n\n[[profile/user_traits|打开测试记忆]]\n"),null);
            tap("打开工具",true);tap("闲时信息搜集试探",false);waitFor(()->model.getUi().getBusyBot()==null&&model.getUi().getMessages().stream().anyMatch(m->m.getText().contains("打开测试记忆")),20,"Harness wiki-link reply");
            scrollTo("打开测试记忆",false);monitor=ins.addMonitor(MainActivity.class.getName(),null,false);tap("打开测试记忆",false);
            secondary=(MainActivity)ins.waitForMonitorWithTimeout(monitor,15000);ins.removeMonitor(monitor);assertNotNull(secondary);waitJs("state.view==='editor'&&state.note==='profile/user_traits.md'&&state.stack.length===0");screenshot("launch-06-wiki-editor");
            webTap("#editTab");webTap("#markdownEditor");shell("input text nativeunsavednote");
            // Commit the Chinese IME's composing candidate into the WebView.
            key(KeyEvent.KEYCODE_ENTER);waitJs("document.getElementById('markdownEditor').value.includes('native')&&document.getElementById('markdownEditor').value!==state.noteOriginal");
            key(KeyEvent.KEYCODE_BACK);Thread.sleep(250);key(KeyEvent.KEYCODE_BACK);waitJs("document.getElementById('sheet').open&&document.getElementById('sheetTitle').textContent.includes('保留')");webTap("[data-sheet-action=discard]");waitFor(()->activity.hasWindowFocus(),10,"discard returns directly to native chat");audit.put("wiki_link_editor_and_unsaved_gate",true);
            tap("返回助手列表",true);settled(false);
            Bitmap avatar=Bitmap.createBitmap(512,512,Bitmap.Config.ARGB_8888);avatar.eraseColor(0xFFFF8A3D);ByteArrayOutputStream encoded=new ByteArrayOutputStream();assertTrue(avatar.compress(Bitmap.CompressFormat.PNG,100,encoded));avatar.recycle();
            transport.request("PATCH","/api/bots/companion/identity",new JSONObject().put("image","data:image/png;base64,"+android.util.Base64.encodeToString(encoded.toByteArray(),android.util.Base64.NO_WRAP)),null);
            waitFor(()->model.getUi().getAvatars().containsKey("companion"),15,"native avatar loaded over the encrypted API");committedFrame();screenshot("launch-07-native-photo-avatar");
            Bitmap presented=ins.getUiAutomation().takeScreenshot();assertNotNull(presented);int orange=0;for(int y=0;y<presented.getHeight();y+=2)for(int x=0;x<presented.getWidth();x+=2){int pixel=presented.getPixel(x,y);if(Math.abs(Color.red(pixel)-255)<5&&Math.abs(Color.green(pixel)-138)<5&&Math.abs(Color.blue(pixel)-61)<5)orange++;}presented.recycle();assertTrue("Photo avatar must appear in the native rendered pixels",orange>400);audit.put("native_encrypted_photo_avatar_pixels",true);
            // Server-issued roster identity survives an offline Activity restart.
            transport.request("POST","/api/bots",new JSONObject().put("name","离线缓存助手").put("engine","big"),null);
            waitFor(()->model.getUi().getBots().stream().anyMatch(b->b.getName().equals("离线缓存助手")),10,"new server bot cached");model.persistDrafts().get(10,TimeUnit.SECONDS);
            fixture("availability",new JSONObject().put("offline",true));closeNative();launch(false);assertTrue(model.getUi().getBots().stream().anyMatch(b->b.getName().equals("离线缓存助手")));assertFalse(model.getUi().getOnline());screenshot("launch-07-offline-roster");
            fixture("availability",new JSONObject().put("offline",false));waitFor(()->model.getUi().getOnline(),15,"online restore after offline start");audit.put("encrypted_offline_roster",true);
            // App-level reduced motion changes no system animation setting.
            monitor=ins.addMonitor(MainActivity.class.getName(),null,false);tap("打开设置",true);secondary=(MainActivity)ins.waitForMonitorWithTimeout(monitor,15000);ins.removeMonitor(monitor);assertNotNull(secondary);waitJs("state.view==='settings'");webTap("[data-route=appearance]");waitJs("state.view==='appearance'&&!PulseMotion.navigating");webTap("[data-preference=motion]");waitJs("state.settings.motion===false");key(KeyEvent.KEYCODE_BACK);waitJs("state.view==='settings'&&!PulseMotion.navigating");key(KeyEvent.KEYCODE_BACK);waitFor(()->activity.hasWindowFocus()&&!model.getUi().getMotion(),10,"reduced motion shared to native");
            tap("新建",true);bounds("创建新助手",false);screenshot("launch-08-reduced-motion-menu");tap("关闭面板",true);waitFor(()->model.getUi().getMenu()==null,5,"static popup closes");audit.put("app_reduced_motion_menu",true);
            // Background service starts on the public native entry and its notification routes to its own bot.
            prefs=SecurePrefs.read(c).put("background",true);SecurePrefs.save(c,prefs);shell("pm grant dev.bubuki.proactive android.permission.POST_NOTIFICATIONS");
            closeNative();launch();waitFor(()->AgentForegroundService.connected,15,"native cold-start background WebSocket");
            key(KeyEvent.KEYCODE_HOME);Thread.sleep(400);JSONObject notice=fixture("notify",new JSONObject());
            waitFor(()->{for(android.service.notification.StatusBarNotification n:c.getSystemService(NotificationManager.class).getActiveNotifications())if(n.getId()==notice.optString("id").hashCode())return true;return false;},10,"encrypted WebSocket posted notification");
            assertTrue(ins.getUiAutomation().performGlobalAction(android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_NOTIFICATIONS));tap("原生通知回流验收",false);
            waitFor(()->activity.hasWindowFocus()&&model.getUi().getChatOpen()&&model.getUi().getBotId().equals("research")&&model.getUi().getMessages().stream().anyMatch(m->m.getId().equals(notice.optString("id"))),15,"notification opens the native research conversation");screenshot("launch-09-notification-bot");
            audit.put("native_background_and_notification_bot",true).put("completed",true).put("paid_provider_calls",false).put("physical_phone_used",false);
        } finally {
            try{fixture("availability",new JSONObject().put("offline",false));}catch(Exception ignored){}
            if(!audit.optBoolean("completed"))try{screenshot("launch-failure");}catch(Throwable ignored){}
            if(document!=null)c.getContentResolver().delete(document,null,null);
            if(local!=null){Activity a=local;ins.runOnMainSync(a::finish);}if(phoneHub!=null){Activity h=phoneHub;ins.runOnMainSync(h::finish);}
            if(secondary!=null&&!secondary.isFinishing())ins.runOnMainSync(()->secondary.finish());closeNative();
            JSONObject settings=SecurePrefs.read(c).put("background",false);SecurePrefs.save(c,settings);c.stopService(new Intent(c,AgentForegroundService.class));
            try(FileOutputStream output=new FileOutputStream(new File(c.getExternalFilesDir(null),"native-launch-audit.json"))){output.write(audit.toString(2).getBytes(StandardCharsets.UTF_8));}
        }
    }

    @Test public void nativeHomeChatAndSecondaryRoundTrip()throws Exception {
        seed();
        try {
            launch();assertFalse(model.getUi().getDark());screenshot("native-01-home-light");
            open("first-push");screenshot("native-02-chat-empty");
            tap("返回助手列表",true);settled(false);open("warm-push");
            typeWord("nativefixturemessage");
            waitFor(()->activity.getWindow().getDecorView().getRootWindowInsets().isVisible(WindowInsets.Type.ime()),8,"native IME visible");screenshot("native-03-keyboard");
            tap("发送消息",true);
            waitFor(()->model.getUi().getBusyBot()==null&&model.getUi().getMessages().stream().anyMatch(m->m.getRole().equals("user")&&m.getText().equals("nativefixturemessage"))&&model.getUi().getMessages().stream().anyMatch(m->!m.getRole().equals("user")),35,"encrypted chat response");
            key(KeyEvent.KEYCODE_BACK);Thread.sleep(450);screenshot("native-04-messages");
            tap("引用",false);waitFor(()->model.getUi().getReply()!=null,5,"reply context");tap("取消引用",true);assertNull(model.getUi().getReply());
            typeWord("nativesaveddraft");String exactDraft=model.getUi().getDraft();
            tap("返回助手列表",true);settled(false);open("other");assertEquals(exactDraft,model.getUi().getDraft());
            tap("选择处理方式",true);tap("执行任务",false);waitFor(()->"execute".equals(model.getUi().getMode()),5,"native mode selection");Thread.sleep(400);screenshot("native-05-mode");
            tap("打开工具",true);tap("闲时信息搜集试探",false);
            waitFor(()->model.getUi().getBusyBot()==null&&model.getUi().getMessages().stream().anyMatch(m->m.getKind().equals("result")&&m.getTrace().contains("write_file")),25,"real Harness file write/read result");
            scrollTo("展开write_file执行记录",true);Rect writeTarget=bounds("展开write_file执行记录",true);
            audit.put("write_target",new JSONArray().put(writeTarget.left).put(writeTarget.top).put(writeTarget.right).put(writeTarget.bottom));
            touch(writeTarget.exactCenterX(),writeTarget.exactCenterY(),45);
            PulseMessage resultMessage=model.getUi().getMessages().stream().filter(m->m.getKind().equals("result")).reduce((a,b)->b).get();
            JSONArray resultTrace=new JSONArray(resultMessage.getTrace());String writeOutput="";
            for(int i=0;i<resultTrace.length();i++)if("write_file".equals(resultTrace.getJSONObject(i).optString("tool")))writeOutput=resultTrace.getJSONObject(i).get("result").toString();
            assertFalse(writeOutput.isEmpty());final String expectedWriteOutput=writeOutput;
            waitFor(()->visibleTextContains(freshRoot(),expectedWriteOutput),6,"physically selected write_file output is visible");
            audit.put("physical_write_trace_verified",true);
            scrollTo("复制执行记录",false);screenshot("native-10-tool-record");
            scrollTo("正是我要的，以后接着干",false);tap("正是我要的，以后接着干",false);
            waitFor(()->model.getUi().getMessages().stream().anyMatch(m->m.getKind().equals("result")&&m.getDecision().equals("accepted")),15,"native result confirmation persisted");
            assertTrue(new SecureTransport(ins.getTargetContext()).request("GET","/api/routines",null,null).getJSONArray("routines").length()>0);
            assertEquals(exactDraft,model.getUi().getDraft());screenshot("native-11-accepted-result");audit.put("harness_result_confirmed",true);
            swipe(false);settled(true);swipe(true);settled(false);audit.put("edge_back_cancel_and_commit",true);
            Rect row=bounds("打开白kawa的对话",true);touch(row.exactCenterX(),row.exactCenterY(),650);tap("置顶到首页",false);
            waitFor(()->model.getUi().getBots().stream().anyMatch(b->b.getId().equals("companion")&&b.getPinned()),10,"pin persisted by server");screenshot("native-06-pinned");
            open("other");Instrumentation.ActivityMonitor monitor=ins.addMonitor(MainActivity.class.getName(),null,false);
            tap("助手详情",true);secondary=(MainActivity)ins.waitForMonitorWithTimeout(monitor,15000);ins.removeMonitor(monitor);assertNotNull(secondary);
            waitJs("state.view==='profile'&&state.botId==='companion'&&!PulseMotion.navigating");screenshot("native-07-secondary-profile");
            key(KeyEvent.KEYCODE_BACK);waitFor(()->activity.hasWindowFocus(),12,"secondary returns to native chat");assertEquals(exactDraft,model.getUi().getDraft());
            tap("返回助手列表",true);settled(false);monitor=ins.addMonitor(MainActivity.class.getName(),null,false);tap("打开设置",true);
            secondary=(MainActivity)ins.waitForMonitorWithTimeout(monitor,15000);ins.removeMonitor(monitor);assertNotNull(secondary);waitJs("state.view==='settings'&&!PulseMotion.navigating");
            webTap("[data-route=appearance]");waitJs("state.view==='appearance'&&!PulseMotion.navigating");webTap("[data-appearance=dark]");waitJs("document.documentElement.dataset.theme==='dark'");
            key(KeyEvent.KEYCODE_BACK);waitJs("state.view==='settings'&&!PulseMotion.navigating");key(KeyEvent.KEYCODE_BACK);waitFor(()->activity.hasWindowFocus()&&model.getUi().getDark(),12,"shared appearance roundtrip");screenshot("native-08-home-dark");
            stopMetrics();ins.runOnMainSync(()->activity.finish());activity=null;
            phase="restart-startup";launch();waitFor(()->model.getUi().getBots().stream().anyMatch(b->b.getId().equals("companion")&&b.getPinned()),12,"native restart pinned readback");
            open("restart-push");assertEquals(exactDraft,model.getUi().getDraft());assertTrue(model.getUi().getDark());screenshot("native-09-restart-chat-dark");
            secondary=(MainActivity)ins.startActivitySync(new Intent(ins.getTargetContext(),MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                .putExtra("nativeParent",true).putExtra("entryRoute","profile").putExtra("entryBot","companion"));
            waitJs("typeof state==='object'&&state.online&&state.view==='home'&&!refreshing");
            assertEquals("true",js("!window.nativeEntry.view"));
            assertEquals(LauncherActivity.class.getName(),ins.getTargetContext().getPackageManager().getLaunchIntentForPackage(ins.getTargetContext().getPackageName()).getComponent().getClassName());
            audit.put("untrusted_secondary_entry_rejected",true);
            audit.put("completed",true).put("paid_provider_calls",false).put("physical_phone_used",false).put("default_launcher_changed",true);
        } finally {
            stopMetrics();synchronized(frames){audit.put("android_window_frames",frames);}audit.put("routes",routes);
            if(!audit.optBoolean("completed")){audit.put("last_fixture_draft",model==null?"":model.getUi().getDraft());try{screenshot("native-failure");}catch(Throwable ignored){}}
            try(FileOutputStream out=new FileOutputStream(new File(ins.getTargetContext().getExternalFilesDir(null),"native-surface-audit.json"))){out.write(audit.toString(2).getBytes(StandardCharsets.UTF_8));}
            if(secondary!=null&&!secondary.isFinishing())ins.runOnMainSync(()->secondary.finish());if(activity!=null)ins.runOnMainSync(()->activity.finish());
        }
    }
}
