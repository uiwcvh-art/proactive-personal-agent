package dev.bubuki.proactive;

import android.animation.*;
import android.app.Activity;
import android.graphics.*;
import android.os.*;
import android.view.*;
import android.view.animation.PathInterpolator;
import android.webkit.*;
import android.widget.*;
import org.json.*;
import java.util.concurrent.*;
import java.io.*;

/** Test-only real Window capture + native View transition. No fabricated images or UI state. */
final class NativeRouteProbe implements AutoCloseable {
    private final Activity activity;
    private final WebView web;
    private final FrameLayout parent;
    private final JSONArray records=new JSONArray();
    private ImageView old;
    private ImageView incoming;
    private final boolean bitmapMode;
    private final boolean softwarePrepare;
    private AnimatorSet animation;
    private long token,requestAt,capturedAt;
    private boolean closed;
    private volatile CountDownLatch captureLatch;
    private volatile long synchronousId=-1;
    private volatile boolean captureOkay;
    private long primeMs;
    NativeRouteProbe(Activity activity,WebView web,boolean bitmapMode,boolean softwarePrepare) {
        this.activity=activity;this.web=web;this.parent=(FrameLayout)web.getParent();
        this.bitmapMode=bitmapMode;
        this.softwarePrepare=softwarePrepare;
        if(!bitmapMode){web.setLayerType(View.LAYER_TYPE_HARDWARE,null);web.buildLayer();}
    }
    @JavascriptInterface public void capture(long id) {
        activity.runOnUiThread(()->{
            cancel();token=id;requestAt=SystemClock.uptimeMillis();
            int[] p=new int[2];web.getLocationInWindow(p);
            Bitmap bitmap=Bitmap.createBitmap(web.getWidth(),web.getHeight(),Bitmap.Config.ARGB_8888);
            PixelCopy.request(activity.getWindow(),new Rect(p[0],p[1],p[0]+web.getWidth(),p[1]+web.getHeight()),bitmap,result->{
                if(closed||id!=token){bitmap.recycle();return;}
                if(result!=PixelCopy.SUCCESS){bitmap.recycle();captured(id,false);return;}
                capturedAt=SystemClock.uptimeMillis();
                old=new ImageView(activity);old.setImageBitmap(bitmap);old.setScaleType(ImageView.ScaleType.FIT_XY);
                // Keep the incoming WebView drawing under the old surface while it prepares.
                old.setAlpha(.999f);old.setClickable(true);
                parent.addView(old,new FrameLayout.LayoutParams(web.getWidth(),web.getHeight()));
                if(softwarePrepare)web.setLayerType(View.LAYER_TYPE_SOFTWARE,null);
                captured(id,true);
            },new Handler(Looper.getMainLooper()));
        });
    }
    private void captured(long id,boolean okay){
        if(id==synchronousId){captureOkay=okay;CountDownLatch latch=captureLatch;if(latch!=null)latch.countDown();}
        else reply("__nativeCaptureDone",id,okay);
    }
    @JavascriptInterface public long captureSync(long id){
        synchronousId=id;captureOkay=false;captureLatch=new CountDownLatch(1);capture(id);
        try{if(captureLatch.await(350,TimeUnit.MILLISECONDS)&&captureOkay)return id;}
        catch(InterruptedException error){Thread.currentThread().interrupt();}
        finally{captureLatch=null;}
        activity.runOnUiThread(this::cancel);return 0;
    }
    @JavascriptInterface public void prime(){activity.runOnUiThread(()->{
        int[] p=new int[2];web.getLocationInWindow(p);long started=SystemClock.uptimeMillis();
        Bitmap image=Bitmap.createBitmap(web.getWidth(),web.getHeight(),Bitmap.Config.ARGB_8888);
        PixelCopy.request(activity.getWindow(),new Rect(p[0],p[1],p[0]+web.getWidth(),p[1]+web.getHeight()),image,status->{
            image.recycle();
            Bitmap warm=Bitmap.createBitmap(web.getWidth(),web.getHeight(),Bitmap.Config.ARGB_8888);web.draw(new Canvas(warm));warm.recycle();
            primeMs=SystemClock.uptimeMillis()-started;
            web.evaluateJavascript("window.__nativePrimeDone&&window.__nativePrimeDone("+(status==PixelCopy.SUCCESS)+")",null);
        },new Handler(Looper.getMainLooper()));
    });}
    long primeMs(){return primeMs;}
    @JavascriptInterface public void prepare(long id,boolean forward) {
        activity.runOnUiThread(()->{
            if(closed||token!=id||old==null){reply("__nativeFinished",id,false);return;}
            web.postVisualStateCallback(id,new WebView.VisualStateCallback(){
                @Override public void onComplete(long requestId){
                    if(closed||token!=id||old==null)return;
                    if(bitmapMode){
                        Bitmap image=Bitmap.createBitmap(web.getWidth(),web.getHeight(),Bitmap.Config.ARGB_8888);
                        web.draw(new Canvas(image));
                        if(id==1)saveProof("incoming",image);
                        incoming=new ImageView(activity);incoming.setImageBitmap(image);incoming.setScaleType(ImageView.ScaleType.FIT_XY);
                        incoming.setClickable(true);parent.addView(incoming,new FrameLayout.LayoutParams(web.getWidth(),web.getHeight()));
                        old.bringToFront();web.setVisibility(View.INVISIBLE);
                        if(softwarePrepare)web.setLayerType(View.LAYER_TYPE_NONE,null);
                    }
                    web.getViewTreeObserver().registerFrameCommitCallback(()->activity.runOnUiThread(()->start(id,forward)));
                    parent.invalidate();
                }
            });
        });
    }
    private void start(long id,boolean forward) {
        if(closed||id!=token||old==null)return;
        long started=SystemClock.uptimeMillis();float width=web.getWidth();
        View target=bitmapMode?incoming:web;
        target.setTranslationX(forward?width:-width*.24f);old.setTranslationX(0);
        if(forward)target.bringToFront();else old.bringToFront();
        AnimatorSet set=new AnimatorSet();animation=set;
        set.playTogether(ObjectAnimator.ofFloat(target,View.TRANSLATION_X,target.getTranslationX(),0),
            ObjectAnimator.ofFloat(old,View.TRANSLATION_X,0,forward?-width*.24f:width));
        set.setDuration(forward?380:330);set.setInterpolator(new PathInterpolator(.4f,0,.2f,1));
        set.addListener(new AnimatorListenerAdapter(){
            private boolean interrupted;
            @Override public void onAnimationCancel(Animator value){interrupted=true;}
            @Override public void onAnimationEnd(Animator value){
                try{synchronized(records){records.put(new JSONObject().put("id",id).put("forward",forward)
                    .put("request_ms",requestAt).put("captured_ms",capturedAt).put("animation_start_ms",started)
                    .put("animation_end_ms",SystemClock.uptimeMillis()).put("prepare_ms",started-requestAt).put("interrupted",interrupted));}}catch(JSONException ignored){}
                if(id!=token)return;
                animation=null;web.setTranslationX(0);
                if(bitmapMode&&!interrupted){
                    incoming.bringToFront();incoming.setAlpha(.999f);web.setVisibility(View.VISIBLE);
                    web.postVisualStateCallback(id,new WebView.VisualStateCallback(){
                        @Override public void onComplete(long request){
                            web.getViewTreeObserver().registerFrameCommitCallback(()->activity.runOnUiThread(()->{
                                if(id!=token)return;
                                removeOld();if(id==1)captureLiveProof(()->reply("__nativeFinished",id,true));else reply("__nativeFinished",id,true);
                            }));web.invalidate();
                        }
                    });web.invalidate();
                }else{removeOld();reply("__nativeFinished",id,!interrupted);}
            }
        });
        reply("__nativeStarted",id,true);set.start();
    }
    private void saveProof(String name,Bitmap image){
        try(FileOutputStream out=new FileOutputStream(new File(activity.getExternalFilesDir(null),"native-probe-"+name+".png"))){image.compress(Bitmap.CompressFormat.PNG,100,out);}catch(IOException error){throw new IllegalStateException(error);}
    }
    private void captureLiveProof(Runnable done){
        parent.postOnAnimation(()->{parent.getViewTreeObserver().registerFrameCommitCallback(()->activity.runOnUiThread(()->{
            int[] p=new int[2];web.getLocationInWindow(p);Bitmap image=Bitmap.createBitmap(web.getWidth(),web.getHeight(),Bitmap.Config.ARGB_8888);
            PixelCopy.request(activity.getWindow(),new Rect(p[0],p[1],p[0]+web.getWidth(),p[1]+web.getHeight()),image,status->{if(status==PixelCopy.SUCCESS)saveProof("live",image);done.run();},new Handler(Looper.getMainLooper()));
        }));parent.invalidate();});
    }
    private void reply(String callback,long id,boolean ok) {
        if(!closed)web.evaluateJavascript("window."+callback+"&&window."+callback+"("+id+","+ok+")",null);
    }
    private void removeOld(){if(old!=null){old.setImageDrawable(null);parent.removeView(old);old=null;}if(incoming!=null){incoming.setImageDrawable(null);parent.removeView(incoming);incoming=null;}}
    private void cancel(){token++;if(animation!=null){animation.cancel();animation=null;}removeOld();web.setTranslationX(0);web.setVisibility(View.VISIBLE);if(softwarePrepare)web.setLayerType(View.LAYER_TYPE_NONE,null);}
    JSONArray records()throws JSONException {synchronized(records){return new JSONArray(records.toString());}}
    @Override public void close(){closed=true;cancel();web.removeJavascriptInterface("NativeRouteProbe");web.setLayerType(View.LAYER_TYPE_NONE,null);}
}
