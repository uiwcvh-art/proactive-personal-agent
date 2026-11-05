package dev.bubuki.proactive;

import android.app.Instrumentation;
import android.os.SystemClock;
import android.webkit.WebView;
import java.lang.reflect.Field;
import static org.junit.Assert.assertNotNull;

/** Wait for the asynchronous provider before inspecting the real secondary UI. */
final class WebViewTestReady {
    static WebView await(Instrumentation ins,MainActivity activity)throws Exception {
        Field field=MainActivity.class.getDeclaredField("web");field.setAccessible(true);WebView[] result={null};
        long deadline=SystemClock.uptimeMillis()+30000;
        while(result[0]==null&&SystemClock.uptimeMillis()<deadline){
            ins.runOnMainSync(()->{try{result[0]=(WebView)field.get(activity);}catch(IllegalAccessException e){throw new AssertionError(e);}});
            if(result[0]==null)Thread.sleep(100);
        }
        assertNotNull("Asynchronous secondary WebView did not become ready",result[0]);return result[0];
    }
}
