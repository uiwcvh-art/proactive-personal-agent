package dev.bubuki.proactive;

import android.app.*;
import android.content.*;
import android.graphics.*;
import android.net.*;
import android.os.*;
import android.view.*;
import android.widget.*;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import org.json.*;
import org.junit.Test;
import org.junit.runner.RunWith;
import java.io.*;
import java.lang.reflect.Field;
import java.nio.charset.StandardCharsets;
import java.util.UUID;
import static org.junit.Assert.*;

/** SDK emulator only: a real uid 0 helper, not a mocked Root response or Magisk grant. */
@RunWith(AndroidJUnit4.class)
public class RootEngineAcceptanceTest {
    private final Instrumentation ins=InstrumentationRegistry.getInstrumentation();
    private final Context c=ins.getTargetContext();
    private NativePhoneActivity phone;
    private PrivilegeManager privilege;
    private String shell(String command)throws Exception {
        try(ParcelFileDescriptor fd=ins.getUiAutomation().executeShellCommand(command);InputStream in=new FileInputStream(fd.getFileDescriptor())){return new String(Io.read(in,1000000),StandardCharsets.UTF_8);}
    }
    private void waitPhase(String phase,int seconds)throws Exception {
        long end=SystemClock.uptimeMillis()+seconds*1000L;
        while(SystemClock.uptimeMillis()<end){if(phase.equals(RootPhoneHarness.status(c).optString("phase"))){Field job=RootPhoneHarness.class.getDeclaredField("job");job.setAccessible(true);kotlinx.coroutines.Job current=(kotlinx.coroutines.Job)job.get(null);if(phase.equals("running")||current==null||!current.isActive())return;}Thread.sleep(150);}
        fail("Expected phase "+phase+", actual "+RootPhoneHarness.status(c));
    }
    private JSONObject command(String op)throws Exception{return RootPhoneHarness.command(c,new JSONObject().put("op",op));}
    private JSONObject command(String op,String key,Object value)throws Exception{return RootPhoneHarness.command(c,new JSONObject().put("op",op).put(key,value));}
    private void refused(JSONObject input,String expected)throws Exception {
        try{RootPhoneHarness.command(c,input);fail("Input should have been refused: "+input);}catch(Exception error){assertTrue(error.toString(),error.toString().contains(expected));}
    }
    private void save(String name,JSONObject frame)throws Exception {
        byte[] png=android.util.Base64.decode(frame.getString("image"),android.util.Base64.DEFAULT);
        assertNotNull(BitmapFactory.decodeByteArray(png,0,png.length));
        try(FileOutputStream out=new FileOutputStream(new File(c.getExternalFilesDir(null),name+".png"))){out.write(png);}
    }
    private void screenshot(String name)throws Exception {
        Thread.sleep(400);Bitmap bitmap=ins.getUiAutomation().takeScreenshot();assertNotNull(bitmap);
        try(FileOutputStream out=new FileOutputStream(new File(c.getExternalFilesDir(null),name+".png"))){bitmap.compress(Bitmap.CompressFormat.PNG,100,out);}bitmap.recycle();
    }
    private <T extends View>T find(View view,Class<T> type){if(type.isInstance(view))return type.cast(view);if(view instanceof ViewGroup){ViewGroup group=(ViewGroup)view;for(int i=0;i<group.getChildCount();i++){T found=find(group.getChildAt(i),type);if(found!=null)return found;}}return null;}
    private JSONObject touch(View view,int display)throws Exception {
        int[] xy=new int[2];ins.runOnMainSync(()->{view.getLocationOnScreen(xy);xy[0]+=view.getWidth()/2;xy[1]+=view.getHeight()/2;});
        return RootPhoneHarness.command(c,new JSONObject().put("op","tap").put("display_id",display).put("x",xy[0]).put("y",xy[1]));
    }
    private void attachActualRootHelper()throws Exception {
        assertEquals("Root acceptance requires the owned userdebug emulator", "0",shell("su 0 id -u").trim());
        String socket="pulse-root-proof-"+UUID.randomUUID(),token=UUID.randomUUID().toString(),apk=c.getApplicationInfo().sourceDir;
        assertTrue(apk.matches("[A-Za-z0-9_./=+~:-]+"));
        File bootstrap=new File(c.getCacheDir(),"root-proof-bootstrap.sh");
        try(FileOutputStream file=new FileOutputStream(bootstrap)){file.write(("env CLASSPATH="+apk+" app_process /system/bin dev.bubuki.proactive.RootDisplayServer "+socket+" "+token+" "+c.getApplicationInfo().uid+" >/data/local/tmp/pulse-root-proof.log 2>&1 </dev/null &\n").getBytes(StandardCharsets.UTF_8));}
        shell("su 0 sh "+bootstrap.getAbsolutePath());bootstrap.delete();
        LocalSocket connection=null;
        for(int i=0;i<100;i++){LocalSocket candidate=new LocalSocket();try{candidate.connect(new LocalSocketAddress(socket));connection=candidate;break;}catch(IOException error){candidate.close();Thread.sleep(100);}}
        assertNotNull("Actual uid 0 helper did not start: "+shell("cat /data/local/tmp/pulse-root-proof.log"),connection);
        assertEquals(0,connection.getPeerCredentials().getUid());connection.setSoTimeout(20000);
        BufferedReader in=new BufferedReader(new InputStreamReader(connection.getInputStream(),StandardCharsets.UTF_8));
        BufferedWriter out=new BufferedWriter(new OutputStreamWriter(connection.getOutputStream(),StandardCharsets.UTF_8));out.write(token+"\n");out.flush();assertEquals("OK",in.readLine());
        Field manager=RootPhoneHarness.class.getDeclaredField("privilege");manager.setAccessible(true);privilege=(PrivilegeManager)manager.get(null);
        for(Object[] item:new Object[][]{{"rootSocket",connection},{"rootIn",in},{"rootOut",out},{"rootGranted",true}}){Field field=PrivilegeManager.class.getDeclaredField((String)item[0]);field.setAccessible(true);field.set(privilege,item[1]);}
        assertEquals(0,privilege.execute(new JSONObject().put("op","status")).getInt("uid"));
        RootPhoneHarness.status(c);
    }
    private JSONObject fixtureMetrics()throws Exception {
        try(InputStream in=new java.net.URL("http://127.0.0.1:18191/_fixture/phone_calls").openStream()){return new JSONObject(new String(Io.read(in,10000),StandardCharsets.UTF_8));}
    }
    @Test public void rootWorkspaceToolsAndOwnTaskLoop()throws Exception {
        JSONObject proof=new JSONObject().put("actual_root_uid",0).put("magisk_authorization_tested",false).put("physical_phone_tested",false);
        String token=shell("cat /data/local/tmp/proactive-pairing").trim(),publicKey;
        try(InputStream in=c.getAssets().open("transport.json")){publicKey=new JSONObject(new String(Io.read(in,20000),StandardCharsets.UTF_8)).getString("public_key");}
        JSONObject settings=new JSONObject().put("serverUrl","http://127.0.0.1:18191").put("publicKey",publicKey).put("deviceToken",token).put("appearance","dark").put("background",false)
            .put("config",new JSONObject().put("big_brain",new JSONObject().put("base_url","http://127.0.0.1:18191/v1").put("api_key","fixture-root-key").put("model","root-fixture")));
        SecurePrefs.save(c,settings);UiPreferences.update(c,settings);shell("pm grant dev.bubuki.proactive android.permission.POST_NOTIFICATIONS");
        phone=(NativePhoneActivity)ins.startActivitySync(new Intent(c,NativePhoneActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK|Intent.FLAG_ACTIVITY_CLEAR_TASK));
        try {
            attachActualRootHelper();JSONObject created=command("create");int display=created.getInt("displayId");assertTrue(display>0);assertTrue(created.getBoolean("isolatedFocus"));proof.put("display_id",display);
            refused(new JSONObject().put("op","tap").put("display_id",0).put("x",5).put("y",5),"displayId");
            refused(new JSONObject().put("op","tap").put("x",-1).put("y",5),"坐标");
            refused(new JSONObject().put("op","key").put("key","power"),"不支持");
            refused(new JSONObject().put("op","launch").put("component","dev.bubuki.proactive/.NativePhoneActivity"),"前台");
            Instrumentation.ActivityMonitor monitor=ins.addMonitor(DisplayTestActivity.class.getName(),null,false);
            command("launch","component","dev.bubuki.proactive/.DisplayTestActivity");
            DisplayTestActivity target=(DisplayTestActivity)ins.waitForMonitorWithTimeout(monitor,12000);ins.removeMonitor(monitor);assertNotNull(target);Thread.sleep(800);
            assertEquals(display,target.getDisplay().getDisplayId());assertTrue("Root launch stole main display focus",phone.hasWindowFocus());
            JSONObject before=command("capture");save("root-01-independent-before",before);assertEquals(540,before.getInt("width"));assertEquals(960,before.getInt("height"));
            int[] oldClick={0};ins.runOnMainSync(()->oldClick[0]=c.getSharedPreferences("display-test",0).getInt("clicked",0));
            Button button=find(target.getWindow().getDecorView(),Button.class);assertNotNull(button);touch(button,display);Thread.sleep(250);
            assertEquals(oldClick[0]+1,c.getSharedPreferences("display-test",0).getInt("clicked",0));assertEquals(display,c.getSharedPreferences("display-test",0).getInt("display_id",-1));
            RootPhoneHarness.command(c,new JSONObject().put("op","swipe").put("display_id",display).put("x1",100).put("y1",300).put("x2",100).put("y2",400));
            EditText editor=find(target.getWindow().getDecorView(),EditText.class);assertNotNull(editor);touch(editor,display);Thread.sleep(500);
            ClipboardManager clipboard=c.getSystemService(ClipboardManager.class);
            ins.runOnMainSync(()->clipboard.setPrimaryClip(ClipData.newPlainText("root-proof-preserved","用户原有剪贴板")));
            String text="独立屏中文 / Root ✓ exit";command("text","text",text);Thread.sleep(500);
            assertEquals("Unicode did not reach independent display",text,c.getSharedPreferences("display-test",0).getString("typed",""));
            String[] restored={null};ins.runOnMainSync(()->restored[0]=clipboard.getPrimaryClip().getItemAt(0).getText().toString());assertEquals("用户原有剪贴板",restored[0]);
            command("key","key","left");Thread.sleep(350);save("root-02-independent-after",command("capture"));
            assertTrue("Independent input stole main focus",phone.hasWindowFocus());
            refused(new JSONObject().put("op","tap").put("manual",true).put("x",20).put("y",20).put("expected_rotation",99),"方向已改变");
            command("capture");Thread.sleep(6500);screenshot("root-03-own-workspace");
            RootPhoneHarness.submitFromBridge(c,"companion","[root-proof] 在独立测试屏点击按钮，然后截图核对。");waitPhase("done",70);
            assertTrue(RootPhoneHarness.history(c).toString().contains("Root 独立屏真实工具回环验收通过"));
            assertTrue("The provider did not receive actual PNG screenshots",fixtureMetrics().getInt("images")>=2);
            JSONObject stored=SecurePrefs.read(c);assertTrue(stored.getJSONArray("phoneHistory").toString().contains("phone_capture"));
            assertFalse("Raw frame leaked into persistent trace",stored.getJSONArray("phoneHistory").toString().contains("data:image"));
            proof.put("tool_loop",true).put("unicode_paste",true).put("clipboard_restored",true).put("main_focus_preserved",phone.hasWindowFocus());
            RootPhoneHarness.submitFromBridge(c,"companion","[needs-user] 请在工作区核对后等我继续。");waitPhase("waiting_user",20);
            assertTrue(RootPhoneHarness.history(c).toString().contains("phone_question"));
            RootPhoneHarness.resume(c);waitPhase("done",70);
            RootPhoneHarness.submitFromBridge(c,"companion","[slow-stop] 等待停止验收。");waitPhase("running",5);Thread.sleep(350);
            RootPhoneHarness.stop(c);waitPhase("stopped",15);int calls=fixtureMetrics().getInt("calls");Thread.sleep(1800);assertEquals(calls,fixtureMetrics().getInt("calls"));
            assertFalse(privilege.execute(new JSONObject().put("op","status")).getBoolean("created"));
            proof.put("needs_user_resume",true).put("stop_released_display",true).put("fixture",fixtureMetrics());
            assertTrue(SecurePrefs.read(c).getJSONArray("phoneHistory").toString().contains("phone_stopped"));
            ins.runOnMainSync(()->phone.finish());
            NativePulseActivity chat=(NativePulseActivity)ins.startActivitySync(new Intent(c,NativePulseActivity.class).putExtra("openBot","companion").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
            Thread.sleep(1700);screenshot("root-04-own-conversation");ins.runOnMainSync(chat::finish);
            try(FileOutputStream out=new FileOutputStream(new File(c.getExternalFilesDir(null),"root-engine-proof.json"))){out.write(proof.toString(2).getBytes(StandardCharsets.UTF_8));}
        } finally {
            if(privilege!=null)privilege.close();
            if(phone!=null)ins.runOnMainSync(()->phone.finish());
        }
    }
}
