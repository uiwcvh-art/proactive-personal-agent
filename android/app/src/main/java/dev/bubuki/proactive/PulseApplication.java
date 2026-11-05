package dev.bubuki.proactive;

/** Our own application process; phone tools share the same product state. */
public final class PulseApplication extends android.app.Application {
    @Override public void onCreate(){
        String legacy="{}";try{legacy=SecurePrefs.read(this).toString();}catch(Exception ignored){}
        UiPreferences.initialize(this,legacy);
        super.onCreate();
    }
}
