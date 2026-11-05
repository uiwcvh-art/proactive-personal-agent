package dev.bubuki.proactive;

import android.Manifest;
import android.app.Activity;
import android.content.*;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.*;
import android.provider.Settings;
import android.view.*;
import android.webkit.*;
import android.widget.FrameLayout;
import org.json.JSONObject;
import java.util.concurrent.*;

public class MainActivity extends Activity {
    private static final ExecutorService WEB_STARTUP=Executors.newSingleThreadExecutor(r->new Thread(r,"pulse-web-startup"));
    private WebView web;
    private FrameLayout frame;
    private final ExecutorService io=Executors.newFixedThreadPool(3);
    private boolean resumed=false;
    private BroadcastReceiver updates;
    private boolean imeAnimating=false;
    private android.window.OnBackInvokedCallback backCallback;
    private String uploadRequestId,uploadSource;
    private String avatarRequestId;
    private boolean nativeParent=false;
    private void applyInsets(WindowInsets insets){
        android.graphics.Insets bars=insets.getInsets(WindowInsets.Type.systemBars()|WindowInsets.Type.displayCutout());
        android.graphics.Insets ime=insets.getInsets(WindowInsets.Type.ime());
        frame.setPadding(bars.left,bars.top,bars.right,Math.max(bars.bottom,ime.bottom));
    }
    @Override public void onCreate(Bundle state){
        super.onCreate(state);
        JSONObject saved;try{saved=dev.bubuki.proactive.UiPreferences.decorate(this,SecurePrefs.read(this));}catch(Exception ignored){saved=new JSONObject();}
        String appearance=saved.optString("appearance","system");
        final boolean firstDark="dark".equals(appearance)||"system".equals(appearance)&&(getResources().getConfiguration().uiMode&android.content.res.Configuration.UI_MODE_NIGHT_MASK)==android.content.res.Configuration.UI_MODE_NIGHT_YES;
        final JSONObject bootstrap=new JSONObject();try{bootstrap.put("appearance",appearance).put("motion",saved.optBoolean("motion",true)).put("haptics",saved.optBoolean("haptics",true)).put("systemDark",(getResources().getConfiguration().uiMode&android.content.res.Configuration.UI_MODE_NIGHT_MASK)==android.content.res.Configuration.UI_MODE_NIGHT_YES);}catch(Exception ignored){}
        final JSONObject entry=new JSONObject();
        nativeParent=getIntent().getBooleanExtra("nativeParent",false)&&getPackageName().equals(getCallingPackage());
        if(nativeParent)try{
            entry.put("view",getIntent().getStringExtra("entryRoute")).put("botId",getIntent().getStringExtra("entryBot"))
                .put("action",getIntent().getStringExtra("entryAction")).put("mode",getIntent().getStringExtra("entryMode")).put("path",getIntent().getStringExtra("entryPath"));
        }catch(Exception ignored){}
        final int firstColor=firstDark?Color.rgb(11,13,18):Color.rgb(250,250,250);
        getWindow().setStatusBarColor(firstColor);getWindow().setNavigationBarColor(firstColor);
        getWindow().setBackgroundDrawable(new android.graphics.drawable.ColorDrawable(firstColor));
        getWindow().getDecorView().setSystemUiVisibility(firstDark?0:View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR|View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR);
        frame=new FrameLayout(this);frame.setBackgroundColor(firstColor);
        frame.setOnApplyWindowInsetsListener((v,insets)->{
            if(Build.VERSION.SDK_INT>=30){if(!imeAnimating)applyInsets(insets);}
            else v.setPadding(insets.getSystemWindowInsetLeft(),insets.getSystemWindowInsetTop(),insets.getSystemWindowInsetRight(),insets.getSystemWindowInsetBottom());
            return insets;
        });
        frame.setWindowInsetsAnimationCallback(new WindowInsetsAnimation.Callback(WindowInsetsAnimation.Callback.DISPATCH_MODE_CONTINUE_ON_SUBTREE){
            @Override public void onPrepare(WindowInsetsAnimation animation){if((animation.getTypeMask()&WindowInsets.Type.ime())!=0)imeAnimating=true;}
            @Override public WindowInsets onProgress(WindowInsets insets,java.util.List<WindowInsetsAnimation> running){if(imeAnimating)applyInsets(insets);return insets;}
            @Override public void onEnd(WindowInsetsAnimation animation){if((animation.getTypeMask()&WindowInsets.Type.ime())!=0){imeAnimating=false;WindowInsets insets=frame.getRootWindowInsets();if(insets!=null)applyInsets(insets);}}
        });
        android.widget.TextView loading=new android.widget.TextView(this);loading.setText("正在打开…");loading.setTextSize(14);loading.setTextColor(firstDark?Color.rgb(149,152,163):Color.rgb(121,121,129));loading.setGravity(Gravity.CENTER);
        frame.addView(loading,new FrameLayout.LayoutParams(-1,-1));setContentView(frame);
        if(Build.VERSION.SDK_INT>=34)registerPredictiveBack();
        else if(Build.VERSION.SDK_INT>=33){backCallback=this::handleBack;getOnBackInvokedDispatcher().registerOnBackInvokedCallback(android.window.OnBackInvokedDispatcher.PRIORITY_DEFAULT,backCallback);}
        RootPhoneHarness.initialize(this);
        updates=new BroadcastReceiver(){@Override public void onReceive(Context c,Intent i){emit("refresh",new JSONObject());}};
        if(Build.VERSION.SDK_INT>=33)registerReceiver(updates,new IntentFilter(getPackageName()+".UPDATE"),Context.RECEIVER_NOT_EXPORTED);
        else registerReceiver(updates,new IntentFilter(getPackageName()+".UPDATE"));
        // A native parent must remain able to acknowledge focus loss while the
        // WebView provider loads its classes and native library for the first time.
        androidx.webkit.WebViewCompat.startUpWebView(getApplicationContext(),new androidx.webkit.WebViewStartUpConfig.Builder(WEB_STARTUP).build(),
            new androidx.webkit.WebViewOutcomeReceiver<androidx.webkit.WebViewStartUpResult,androidx.webkit.WebViewStartupException>(){
                @Override public void onResult(androidx.webkit.WebViewStartUpResult result){if(!isFinishing()&&!isDestroyed())initializeWeb(firstColor,firstDark,bootstrap,entry,loading);}
                @Override public void onError(androidx.webkit.WebViewStartupException error){if(!isFinishing()&&!isDestroyed())loading.setText("页面暂时无法打开\n请返回后重试");}
            });
    }
    private void initializeWeb(int firstColor,boolean firstDark,JSONObject bootstrap,JSONObject entry,View loading){
        web=new WebView(this);web.setBackgroundColor(firstColor);web.setVisibility(View.INVISIBLE);
        WebSettings settings=web.getSettings();settings.setJavaScriptEnabled(true);settings.setDomStorageEnabled(false);
        settings.setAllowFileAccess(false);settings.setAllowContentAccess(false);settings.setAllowUniversalAccessFromFileURLs(false);settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setTextZoom(100);settings.setDefaultTextEncodingName("UTF-8");
        web.setWebViewClient(new WebViewClient(){
            @Override public boolean shouldOverrideUrlLoading(WebView view,WebResourceRequest request){return true;}
            @Override public void onPageFinished(WebView view,String url){
                view.postVisualStateCallback(0,new WebView.VisualStateCallback(){
                    @Override public void onComplete(long requestId){if(web==view&&!isDestroyed()){view.setVisibility(View.VISIBLE);frame.removeView(loading);emit("visibility",visibility(resumed));}}
                });
            }
        });
        web.addJavascriptInterface(new Bridge(),"Native");
        frame.addView(web,new FrameLayout.LayoutParams(-1,-1));
        // android_asset requests bypass shouldInterceptRequest. Build the trusted
        // document before loading it so CSS and the first script see the same theme.
        try(java.io.InputStream input=getAssets().open("index.html")){
            String html=new String(Io.read(input,200000),java.nio.charset.StandardCharsets.UTF_8)
                .replace("data-theme=\"dark\"","data-theme=\""+(firstDark?"dark":"light")+"\"")
                .replace("<head>","<head><script>window.bootstrapPreferences="+bootstrap+";window.nativeEntry="+entry+";</script>");
            web.loadDataWithBaseURL("file:///android_asset/",html,"text/html","UTF-8",null);
        }catch(java.io.IOException error){throw new IllegalStateException("无法读取应用首页",error);}
    }
    private void emit(String type,JSONObject payload){if(web!=null)runOnUiThread(()->web.evaluateJavascript("window.onNativeEvent&&window.onNativeEvent("+JSONObject.quote(type)+","+payload+")",null));}
    private void reply(String id,JSONObject data,String error){
        try{JSONObject packet=new JSONObject().put("id",id).put("ok",error==null).put("data",data==null?new JSONObject():data).put("error",error==null?"":error);
            runOnUiThread(()->{if(web!=null)web.evaluateJavascript("window.onNativeReply("+packet+")",null);});
        }catch(Exception ignored){}
    }
    @Override protected void onResume(){super.onResume();resumed=true;emit("preferences",dev.bubuki.proactive.UiPreferences.decorate(this,new JSONObject()));emit("visibility",visibility(true));emit("refresh",new JSONObject());}
    @Override protected void onPause(){resumed=false;emit("visibility",visibility(false));super.onPause();}
    @Override protected void onActivityResult(int request,int result,Intent data){
        super.onActivityResult(request,result,data);
        if((request==731||request==732)&&avatarRequestId!=null){
            String id=avatarRequestId;avatarRequestId=null;
            if(result!=RESULT_OK||(request==731&&(data==null||data.getData()==null))){
                if(request==732)AvatarImages.cameraFile(this).delete();
                reply(id,new JSONObject(),null);return;
            }
            Uri image=request==732?Uri.fromFile(AvatarImages.cameraFile(this)):data.getData();
            io.execute(()->{try{reply(id,AvatarImages.read(this,image),null);}catch(Exception error){reply(id,null,safeError(error));}finally{if(request==732)AvatarImages.cameraFile(this).delete();}});
            return;
        }
        if(request==730&&uploadRequestId!=null){String id=uploadRequestId,source=uploadSource;uploadRequestId=null;
            if(result!=RESULT_OK||data==null||data.getData()==null){reply(id,null,"已取消选择文件");return;}
            Uri uri=data.getData();io.execute(()->{try{reply(id,CloudFiles.upload(this,uri,source),null);}catch(Exception error){reply(id,null,safeError(error));}});
        }
    }
    private static JSONObject visibility(boolean visible){JSONObject o=new JSONObject();try{o.put("foreground",visible);}catch(Exception ignored){}return o;}
    @Override protected void onDestroy(){if(Build.VERSION.SDK_INT>=33&&backCallback!=null)getOnBackInvokedDispatcher().unregisterOnBackInvokedCallback(backCallback);if(updates!=null)unregisterReceiver(updates);io.shutdownNow();if(web!=null){web.destroy();web=null;}super.onDestroy();}
    private void handleBack(){if(web!=null)web.evaluateJavascript("window.onSystemBackInvoked?window.onSystemBackInvoked():window.onAppBack?window.onAppBack():false",value->{if("false".equals(value))finish();});else finish();}
    @Override public void onBackPressed(){handleBack();}
    @android.annotation.TargetApi(34) private void registerPredictiveBack(){
        backCallback=new android.window.OnBackAnimationCallback(){
            @Override public void onBackStarted(android.window.BackEvent event){emit("backStart",new JSONObject());}
            @Override public void onBackProgressed(android.window.BackEvent event){JSONObject payload=new JSONObject();try{payload.put("progress",event.getProgress());}catch(Exception ignored){}emit("backProgress",payload);}
            @Override public void onBackCancelled(){emit("backCancel",new JSONObject());}
            @Override public void onBackInvoked(){handleBack();}
        };
        getOnBackInvokedDispatcher().registerOnBackInvokedCallback(android.window.OnBackInvokedDispatcher.PRIORITY_DEFAULT,backCallback);
    }
    final class Bridge {
        @JavascriptInterface public void finishEntry(){if(nativeParent)runOnUiThread(()->finish());}
        @JavascriptInterface public void createdBot(String id){if(nativeParent)runOnUiThread(()->{setResult(RESULT_OK,new Intent().putExtra("createdBot",id));finish();});}
        @JavascriptInterface public void request(String id,String method,String path,String body,String query){
            io.execute(()->{try{reply(id,new SecureTransport(MainActivity.this).request(method,path,body.isEmpty()?null:new JSONObject(body),query.isEmpty()?null:new JSONObject(query)),null);}catch(Exception e){reply(id,null,safeError(e));}});
        }
        @JavascriptInterface public void settings(String id,String json){
            io.execute(()->{try{if(!json.isEmpty())synchronized(SecurePrefs.class){JSONObject incoming=new JSONObject(json),fresh=SecurePrefs.read(MainActivity.this);for(String owned:new String[]{"phoneHistory","phoneTask"})if(fresh.has(owned))incoming.put(owned,fresh.get(owned));SecurePrefs.save(MainActivity.this,incoming);}reply(id,UiPreferences.decorate(MainActivity.this,SecurePrefs.read(MainActivity.this)),null);}catch(Exception e){reply(id,null,safeError(e));}});
        }
        @JavascriptInterface public void action(String id,String action,String args){
            io.execute(()->{try{
                JSONObject data=args.isEmpty()?new JSONObject():new JSONObject(args);
                JSONObject result=new JSONObject();
                switch(action){
                    case "chooseAvatar":
                        boolean camera=data.optBoolean("camera",false);
                        runOnUiThread(()->{
                            if(avatarRequestId!=null||uploadRequestId!=null){reply(id,null,"请先完成或取消当前的图片选择");return;}
                            avatarRequestId=id;
                            try{startActivityForResult(camera?AvatarImages.cameraIntent(MainActivity.this):new Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("image/*"),camera?732:731);}
                            catch(Exception error){avatarRequestId=null;reply(id,null,safeError(error));}
                        });
                        return;
                    case "localStatus":result=RootPhoneHarness.status(MainActivity.this);break;
                    case "downloadFile":result=CloudFiles.download(MainActivity.this,data.getString("source"),data.getString("path"));break;
                    case "uploadFile":
                        String source=data.optString("source","workspace");
                        runOnUiThread(()->{if(uploadRequestId!=null){reply(id,null,"已有文件选择窗口，请先完成或取消");return;}uploadRequestId=id;uploadSource=source;try{startActivityForResult(new Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("*/*"),730);}catch(Exception error){uploadRequestId=null;reply(id,null,safeError(error));}});
                        return;
                    case "phoneHistory":result=RootPhoneHarness.history(MainActivity.this);break;
                    case "phoneRun":result=RootPhoneHarness.submitFromBridge(MainActivity.this,data.optString("botId","companion"),data.getString("goal"));break;
                    case "openLocal":
                        Intent local=new Intent(MainActivity.this,NativePhoneActivity.class);
                        runOnUiThread(()->{try{startActivity(local);reply(id,new JSONObject(),null);}catch(Exception error){reply(id,null,safeError(error));}});
                        return;
                    case "haptic":
                        runOnUiThread(()->web.performHapticFeedback("confirm".equals(data.optString("kind"))?HapticFeedbackConstants.CONFIRM:HapticFeedbackConstants.CLOCK_TICK));
                        break;
                    case "appearance":
                        dev.bubuki.proactive.UiPreferences.update(MainActivity.this,data);
                        boolean dark=data.optBoolean("dark",true);
                        runOnUiThread(()->{
                            int color=dark?Color.rgb(11,13,18):Color.rgb(250,250,250);
                            getWindow().setStatusBarColor(color);getWindow().setNavigationBarColor(color);
                            frame.setBackgroundColor(color);web.setBackgroundColor(color);
                            getWindow().getDecorView().setSystemUiVisibility(dark?0:View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR|View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR);
                        });
                        break;
                    case "copy":
                        String copyText=data.optString("text","");
                        runOnUiThread(()->getSystemService(android.content.ClipboardManager.class).setPrimaryClip(ClipData.newPlainText("Pulse Bot",copyText)));
                        break;
                    case "openUrl":
                        Uri target=Uri.parse(data.getString("url"));
                        if(!("https".equals(target.getScheme())||"http".equals(target.getScheme()))||target.getHost()==null)throw new Exception("只支持 HTTP(S) 链接");
                        runOnUiThread(()->{try{startActivity(new Intent(Intent.ACTION_VIEW,target));}catch(Exception e){emit("error",errorJson("没有可用的浏览器"));}});
                        break;
                    case "status":result=RootPhoneHarness.status(MainActivity.this).put("websocket",AgentForegroundService.connected);break;
                    case "probe":result=RootPhoneHarness.command(MainActivity.this,new JSONObject().put("op","connect"));break;
                    case "overlay":runOnUiThread(()->startActivity(new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,Uri.parse("package:"+getPackageName()))));break;
                    case "notifications":runOnUiThread(()->{if(Build.VERSION.SDK_INT>=33&&checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED)requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS},15);else startActivity(new Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE,getPackageName()));});break;
                    case "listener":runOnUiThread(()->startActivity(new Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)));break;
                    case "background":
                        JSONObject p=SecurePrefs.read(MainActivity.this);p.put("background",data.optBoolean("enabled",true));SecurePrefs.save(MainActivity.this,p);
                        if(data.optBoolean("enabled",true))runOnUiThread(()->{try{startForegroundService(new Intent(MainActivity.this,AgentForegroundService.class));}catch(Exception e){emit("error",errorJson(safeError(e)));}});
                        else stopService(new Intent(MainActivity.this,AgentForegroundService.class));
                        break;
                    case "reconnect":runOnUiThread(()->{stopService(new Intent(MainActivity.this,AgentForegroundService.class));try{startForegroundService(new Intent(MainActivity.this,AgentForegroundService.class));}catch(Exception e){emit("error",errorJson(safeError(e)));}});break;
                    case "displayCreate":result=RootPhoneHarness.command(MainActivity.this,new JSONObject().put("op","create").put("manual",true));break;
                    case "displayRelease":result=RootPhoneHarness.command(MainActivity.this,new JSONObject().put("op","release").put("manual",true));break;
                    case "displayCapture":result=RootPhoneHarness.command(MainActivity.this,new JSONObject().put("op","capture").put("manual",true));break;
                    case "displayLaunch":result=RootPhoneHarness.command(MainActivity.this,new JSONObject().put("op","launch").put("component",data.optString("component",getPackageName()+"/.DisplayTestActivity")).put("manual",true));break;
                    case "displayTap":result=RootPhoneHarness.command(MainActivity.this,new JSONObject(data.toString()).put("op","tap").put("manual",true));break;
                    default:throw new Exception("未知操作");
                }
                reply(id,result,null);
            }catch(Exception e){reply(id,null,safeError(e));}});
        }
    }
    static JSONObject errorJson(String e){JSONObject o=new JSONObject();try{o.put("message",e);}catch(Exception ignored){}return o;}
    static String safeError(Exception e){String message=e.getMessage();return message==null?e.getClass().getSimpleName():message.substring(0,Math.min(message.length(),300));}
}
