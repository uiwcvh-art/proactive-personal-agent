package dev.bubuki.proactive;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;

/** Public icon entry. Private navigation commands are never forwarded from callers. */
public final class LauncherActivity extends Activity {
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        Intent src = getIntent();
        String act = src != null ? src.getAction() : null;
        boolean isShare = Intent.ACTION_SEND.equals(act)
            || Intent.ACTION_SEND_MULTIPLE.equals(act)
            || Intent.ACTION_PROCESS_TEXT.equals(act);
        if (!isTaskRoot() && !isShare) {
            finish();
            return;
        }
        Intent next = new Intent(this, NativePulseActivity.class)
            .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        if (isShare && src != null) {
            next.setAction(act);
            next.setType(src.getType());
            if ((src.getFlags() & Intent.FLAG_GRANT_READ_URI_PERMISSION) != 0) {
                next.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
            }
            if (src.getClipData() != null) {
                next.setClipData(src.getClipData());
                next.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
            } else if (Intent.ACTION_SEND.equals(act)) {
                android.net.Uri streamUri = src.getParcelableExtra(Intent.EXTRA_STREAM);
                if (streamUri != null) {
                    next.setClipData(android.content.ClipData.newRawUri("share", streamUri));
                    next.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                }
            }
            if (src.getExtras() != null) {
                next.putExtras(src.getExtras());
            }
        }
        startActivity(next);
        finish();
    }
}
