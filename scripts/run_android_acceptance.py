from pathlib import Path
import subprocess,sys,json
ROOT=Path(__file__).resolve().parents[1]
ADB=[r'C:\Users\uiwcv\AppData\Local\Android\Sdk\platform-tools\adb.exe','-s','emulator-5558']
def adb(*args,**kwargs):return subprocess.run(ADB+list(args),check=True,**kwargs)
adb('install','-r',str(ROOT/'android/app/build/outputs/apk/release/app-release.apk'))
adb('install','-r',str(ROOT/'android/app/build/outputs/apk/androidTest/release/app-release-androidTest.apk'))
adb('push',str(ROOT/'private/device-token'),'/data/local/tmp/proactive-pairing')
log=ROOT/'work/android-instrumentation.txt'
try:
    with log.open('w',encoding='utf-8') as out:
        result=adb('shell','am','instrument','-w','-r','dev.bubuki.proactive.test/androidx.test.runner.AndroidJUnitRunner',stdout=out,stderr=subprocess.STDOUT,timeout=240)
finally:
    adb('shell','rm','-f','/data/local/tmp/proactive-pairing')
    adb('pull','/sdcard/Android/data/dev.bubuki.proactive/files',str(ROOT/'work/android-evidence'))
text=log.read_text('utf-8')
print(text)
if 'OK (1 test)' not in text:sys.exit(1)
