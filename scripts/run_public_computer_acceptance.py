"""Own emulator only: exercise the deployed cloud computer through the signed APK."""
from pathlib import Path
import subprocess
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'work/public-computer-acceptance';OUT.mkdir(exist_ok=True)
ADB=[r'C:\Users\uiwcv\AppData\Local\Android\Sdk\platform-tools\adb.exe','-s','emulator-5560']
def adb(*args,**kw):return subprocess.run(ADB+list(args),check=True,**kw)
try:
    adb('install','-r',str(ROOT/'android/app/build/outputs/apk/release/app-release.apk'))
    adb('install','-r',str(ROOT/'android/app/build/outputs/apk/androidTest/release/app-release-androidTest.apk'))
    adb('push',str(ROOT/'private/device-token'),'/data/local/tmp/proactive-pairing')
    with (OUT/'instrumentation.txt').open('w',encoding='utf-8') as log:
        adb('shell','am','instrument','-w','-r','-e','class','dev.bubuki.proactive.PublicComputerAcceptanceTest','dev.bubuki.proactive.test/androidx.test.runner.AndroidJUnitRunner',stdout=log,stderr=subprocess.STDOUT,timeout=300)
    text=(OUT/'instrumentation.txt').read_text('utf-8');print(text);assert 'OK (1 test)' in text
finally:
    subprocess.run(ADB+['shell','rm','-f','/data/local/tmp/proactive-pairing'],capture_output=True)
    subprocess.run(ADB+['pull','/sdcard/Android/data/dev.bubuki.proactive/files',str(OUT)],capture_output=True)
