"""Launch only the project's existing headless reference device, at below-normal priority."""
from pathlib import Path
import json,os,socket,subprocess
ROOT=Path(__file__).resolve().parents[1]
SDK=Path(r'C:\Users\uiwcv\AppData\Local\Android\Sdk')
avds=ROOT/'work/reference-emulator/avds'
if not (avds/'Grok_Reference.avd/config.ini').exists():raise SystemExit('The owned reference AVD is missing')
probe=socket.socket()
try:
    if probe.connect_ex(('127.0.0.1',5560))==0:raise SystemExit('Port 5560 is in use; inspect the existing device instead of launching another')
finally:probe.close()
env={**os.environ,'ANDROID_AVD_HOME':str(avds),'ANDROID_USER_HOME':str(ROOT/'work/android-user')}
log=(ROOT/'work/identity-emulator-1.6.log').open('w')
process=subprocess.Popen([str(SDK/'emulator/emulator.exe'),'-avd','Grok_Reference','-port','5560',
    '-no-window','-no-audio','-no-boot-anim','-no-snapshot','-cores','2','-memory','2048','-gpu','host',
    '-camera-back','emulated','-camera-front','none','-http-proxy','http://127.0.0.1:7890'],env=env,stdout=log,stderr=subprocess.STDOUT,
    creationflags=subprocess.CREATE_NO_WINDOW|subprocess.BELOW_NORMAL_PRIORITY_CLASS)
receipt={'pid':process.pid,'serial':'emulator-5560','avd':'Grok_Reference','quiet_mode_changes':False}
(ROOT/'work/identity-emulator-process.json').write_text(json.dumps(receipt,indent=2))
print(json.dumps(receipt))
