"""Exercise the signed APK with real touch events in an explicitly selected emulator."""
from pathlib import Path
import argparse,os,subprocess,time,shutil,tempfile,json,urllib.request,hashlib
ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('--serial',default='emulator-5560');parser.add_argument('--overlay-only',action='store_true');parser.add_argument('--motion',action='store_true');parser.add_argument('--local',action='store_true');parser.add_argument('--bots',action='store_true');parser.add_argument('--identity',action='store_true');parser.add_argument('--record',action='store_true');parser.add_argument('--native-surface',action='store_true');parser.add_argument('--native-parity',action='store_true');parser.add_argument('--startup-profile',action='store_true');parser.add_argument('--profile-video',action='store_true');parser.add_argument('--render-variant',choices=['baseline','transition-shadow-off','all-shadows-off','settled-baseline','persistent-layer','persistent-layer-gradient','view-transition','line-off','native-hardware','native-software','native-preraster','native-snapshot','native-bitmap','native-bitmap-sync','native-bitmap-swprep'],default='baseline');args=parser.parse_args()
if args.startup_profile:
    if args.record and not args.profile_video:parser.error('The independent startup profile must run without screenrecord')
    if args.profile_video:args.record=True
    args.bots=True
if args.native_surface:
    # FrameTimeline records presented frames, beyond Window callback timings.
    trace_config=ROOT/'work/native-presented-trace.pbtxt'
    trace_config.write_text('''buffers { size_kb: 32768 fill_policy: RING_BUFFER }
duration_ms: 180000
data_sources { config { name: "linux.process_stats" process_stats_config { scan_all_processes_on_start: true proc_stats_poll_ms: 1000 } } }
data_sources { config { name: "android.surfaceflinger.frametimeline" } }
''')
if not args.serial.startswith('emulator-'): raise SystemExit('Acceptance is restricted to an emulator; real phones are excluded.')
ADB=[r'C:\Users\uiwcv\AppData\Local\Android\Sdk\platform-tools\adb.exe','-s',args.serial]
apk=ROOT/'android/app/build/outputs/apk/release/app-release.apk'
profile_dir='work/render-profiles/'+hashlib.sha256(apk.read_bytes()).hexdigest()[:12]+'/'+args.render_variant+('-recorded' if args.profile_video else '')
out=ROOT/('work/native-launch-acceptance' if args.native_parity else 'work/native-surface-acceptance' if args.native_surface else profile_dir if args.startup_profile else 'work/identity-acceptance' if args.identity else 'work/bot-management-acceptance' if args.bots else 'work/root-acceptance' if args.local else 'work/motion-rebuild/evidence' if args.motion else 'work/android-v2-evidence');out.mkdir(exist_ok=True,parents=True)
data=Path(tempfile.mkdtemp(prefix='ui-fixture-',dir=ROOT/'work'))
shutil.copy2(ROOT/'private/transport-key.pem',data/'transport-key.pem')
env={**os.environ,'AGENT_DATA_DIR':str(data),'AGENT_TOKEN':(ROOT/'private/device-token').read_text().strip()}
flags=subprocess.CREATE_NO_WINDOW|subprocess.BELOW_NORMAL_PRIORITY_CLASS if os.name=='nt' else 0
log=(ROOT/'work/android-v2-fixture.log').open('w',encoding='utf-8')
server=subprocess.Popen([str(ROOT/'server/.venv/Scripts/python.exe'),'-X','utf8',str(ROOT/'scripts/start_ui_fixture.py')],env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=flags)
def adb(*values,**kwargs):return subprocess.run(ADB+list(values),check=True,**kwargs)
recording=None;record_pid=None;trace_pid=None
started=time.monotonic()
try:
    for _ in range(40):
        if server.poll() is not None: raise RuntimeError('Fixture server exited; inspect work/android-v2-fixture.log')
        try:
            with urllib.request.urlopen('http://127.0.0.1:18191/health',timeout=1) as response:
                if response.status==200:break
        except Exception: time.sleep(.25)
    else: raise RuntimeError('Fixture server not ready')
    adb('reverse','tcp:18191','tcp:18191')
    installed=subprocess.run(ADB+['shell','pm','path','dev.bubuki.proactive'],capture_output=True,text=True).stdout.strip()
    installed_path=installed.splitlines()[0].removeprefix('package:') if installed.startswith('package:/data/app/') else ''
    installed_hash=subprocess.run(ADB+['shell','sha256sum',installed_path],capture_output=True,text=True).stdout.split() if installed_path else []
    if not installed_hash or installed_hash[0]!=hashlib.sha256(apk.read_bytes()).hexdigest():
        adb('install','-r',str(apk))
    adb('install','-r',str(ROOT/'android/app/build/outputs/apk/androidTest/release/app-release-androidTest.apk'))
    adb('shell','am','force-stop','dev.bubuki.proactive')
    adb('shell','cmd','statusbar','collapse',capture_output=True)
    adb('push',str(ROOT/'private/device-token'),'/data/local/tmp/proactive-pairing')
    if args.record:
        existing=subprocess.run(ADB+['shell','pidof','screenrecord'],capture_output=True,text=True).stdout.strip()
        if existing:raise RuntimeError('An emulator recording is already running; leave it alone.')
        recording=subprocess.Popen(ADB+['shell','screenrecord','--size','540x1170','--bit-rate','1800000','--time-limit','180','/sdcard/pulse-motion-acceptance.mp4'],stdout=log,stderr=subprocess.STDOUT,creationflags=flags)
        for _ in range(15):
            found=subprocess.run(ADB+['shell','pidof','screenrecord'],capture_output=True,text=True).stdout.strip()
            if found:
                record_pid=int(found);break
            time.sleep(.1)
        if record_pid is None:raise RuntimeError('Emulator recording did not start')
    if args.native_surface:
        launched=adb('shell','perfetto','--background-wait','--txt','-c','-','-o','/data/misc/perfetto-traces/pulse-native-presented.perfetto-trace',input=trace_config.read_text(),capture_output=True,text=True)
        trace_pid=int(launched.stdout.strip())
    result_log=out/'instrumentation.txt' if args.bots or args.identity or args.native_surface or args.native_parity or args.local else ROOT/('work/motion-rebuild/instrumentation.txt' if args.motion else 'work/android-v2-instrumentation.txt')
    with result_log.open('w',encoding='utf-8') as output:
        adb('shell','am','instrument','-w','-r','-e','class','dev.bubuki.proactive.'+('NativeSurfaceAcceptanceTest#launcherAttachmentsOfflinePhoneAndNotifications' if args.native_parity else 'NativeSurfaceAcceptanceTest#nativeHomeChatAndSecondaryRoundTrip' if args.native_surface else 'IdentityAcceptanceTest' if args.identity else 'BotManagementAcceptanceTest#startupRenderingProfile' if args.startup_profile else 'BotManagementAcceptanceTest#placementAndHiddenConversations' if args.bots else 'RootEngineAcceptanceTest' if args.local else 'MotionAcceptanceTest' if args.motion else 'ReleaseAcceptanceTest'),'-e','uiServer','http://127.0.0.1:18191','-e','overlayOnly',str(args.overlay_only).lower(),'-e','renderVariant',args.render_variant,'dev.bubuki.proactive.test/androidx.test.runner.AndroidJUnitRunner',stdout=output,stderr=subprocess.STDOUT,timeout=420)
    result=result_log.read_text('utf-8');print(result)
    if 'OK (1 test)' not in result: raise RuntimeError('Signed APK UI acceptance failed')
    if (args.bots or args.identity or args.native_surface or args.native_parity or args.local) and not args.startup_profile:
        apk=ROOT/'android/app/build/outputs/apk/release/app-release.apk'
        (out/'receipt.json').write_text(json.dumps({'passed':True,'apk_sha256':hashlib.sha256(apk.read_bytes()).hexdigest(),'seconds':round(time.monotonic()-started,2),'log':str(result_log),'serial':args.serial},indent=2))
    scope='own Root uid 0 workspace, actual image/tool loop, Unicode paste, display-bound input, main focus, stop and local task history; emulator only' if args.local else 'public native launcher, offline Root page, document picker/upload, attachments, wiki editor, offline roster, reduced motion and native notification routing' if args.native_parity else 'native Compose Home/Chat with real keyboard, API reply, pin persistence, edge gestures, secondary Activity and shared theme/draft roundtrip' if args.native_surface else 'native photo picker/crop gestures, identity reload/cancel, primary routing and isolated avatar provider' if args.identity else 'cold/warm rendering profile '+('with' if args.record else 'without')+' screenrecord; variant='+args.render_variant if args.startup_profile else 'real long press, pin/hide/restore, draft preservation and activity restart' if args.bots else 'real touch, signed release, encrypted local API, native WebSocket and escalation; no external model or SMTP credentials'
    print(json.dumps({'passed':True,'overlay_only':args.overlay_only,'serial':args.serial,'data':str(data),'scope':scope},ensure_ascii=True))
finally:
    if trace_pid is not None:
        subprocess.run(ADB+['shell','kill','-TERM',str(trace_pid)],capture_output=True)
        for _ in range(30):
            alive=subprocess.run(ADB+['shell','kill','-0',str(trace_pid)],capture_output=True)
            if alive.returncode!=0:break
            time.sleep(.1)
        subprocess.run(ADB+['pull','/data/misc/perfetto-traces/pulse-native-presented.perfetto-trace',str(out/'presented.perfetto-trace')],capture_output=True,check=True)
        subprocess.run(ADB+['shell','rm','-f','/data/local/tmp/pulse-native-presented.pbtxt','/data/misc/perfetto-traces/pulse-native-presented.perfetto-trace'],capture_output=True)
    if recording is not None:
        if recording.poll() is None and record_pid is not None:
            subprocess.run(ADB+['shell','kill','-2',str(record_pid)],capture_output=True)
        try:recording.wait(timeout=15)
        except subprocess.TimeoutExpired:recording.terminate()
        subprocess.run(ADB+['pull','/sdcard/pulse-motion-acceptance.mp4',str(out/'motion-acceptance.mp4')],capture_output=True)
    subprocess.run(ADB+['shell','rm','-f','/data/local/tmp/proactive-pairing'],capture_output=True)
    subprocess.run(ADB+['pull','/sdcard/Android/data/dev.bubuki.proactive/files',str(out)],capture_output=True)
    if server.poll() is None:
        if os.name=='nt':subprocess.run(['taskkill','/PID',str(server.pid),'/T','/F'],capture_output=True)
        else:server.terminate()
        server.wait(timeout=10)
    log.close()
