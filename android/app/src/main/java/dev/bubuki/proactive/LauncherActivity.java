package dev.bubuki.proactive;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;

/** Public icon entry. Private navigation commands are never forwarded from callers. */
public final class LauncherActivity extends Activity {
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        if(!isTaskRoot()){finish();return;}
        startActivity(new Intent(this,NativePulseActivity.class).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP));
        finish();
    }
}
