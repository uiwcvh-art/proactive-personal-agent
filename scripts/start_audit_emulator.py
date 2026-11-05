from pathlib import Path
import os,subprocess,json
ROOT=Path(__file__).resolve().parents[1]
sdk=Path(r'C:\Users\uiwcv\AppData\Local\Android\Sdk')
work=ROOT/'work'
env={**os.environ,'JAVA_HOME':r'C:\Program Files\Java\jdk-22','ANDROID_USER_HOME':str(work/'android-user'),'ANDROID_AVD_HOME':str(work/'avds')}
Path(env['ANDROID_USER_HOME']).mkdir(exist_ok=True,parents=True)
Path(env['ANDROID_AVD_HOME']).mkdir(exist_ok=True,parents=True)
avd=work/'avds/Proactive_Audit.avd'
if not (avd/'config.ini').exists():
    subprocess.run([str(sdk/'cmdline-tools/latest/bin/avdmanager.bat'),'create','avd','-n','Proactive_Audit','-k','system-images;android-35;default;x86_64','-p',str(avd),'-d','pixel_5'],env=env,input='no\n',text=True,check=True)
log=(work/'emulator.log').open('w')
proc=subprocess.Popen([str(sdk/'emulator/emulator.exe'),'-avd','Proactive_Audit','-port','5558','-no-window','-no-audio','-no-boot-anim','-no-snapshot','-cores','1','-memory','1536','-gpu','swiftshader','-camera-back','none','-camera-front','none'],env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW|subprocess.BELOW_NORMAL_PRIORITY_CLASS)
(work/'emulator-process.json').write_text(json.dumps({'pid':proc.pid,'serial':'emulator-5558'}))
print('Started isolated headless audit emulator',proc.pid,'emulator-5558; real attached phones will not be used.')
