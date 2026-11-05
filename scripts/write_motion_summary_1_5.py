"""Compare cold/warm frame evidence without turning callback counts into FPS claims."""
from pathlib import Path
import hashlib,json

ROOT=Path(__file__).resolve().parents[1]
apk=ROOT/'android/app/build/outputs/apk/release/app-release.apk'
digest=hashlib.sha256(apk.read_bytes()).hexdigest()
path=ROOT/'work/render-profiles'/digest[:12]/'baseline/files/bot-management-audit.json'
current=json.loads(path.read_text('utf-8'))
assert current['completed'] and current['profile_only']
study=json.loads((ROOT/'work/rendering-study-1.4.json').read_text('utf-8'))
phases={}
for phase in ['first-push','warm-push']:
    frames=[f for f in current['android_window_frames'] if f['phase']==phase]
    route=next(r for r in current['routes'] if r['phase']==phase)
    phases[phase]={'max_total_ms':round(max(f['total_ms'] for f in frames),2),'window_callbacks':len(frames),'over33ms':sum(f['total_ms']>33 for f in frames),'js_long_tasks':len(route['long_tasks']),'route_animations':route['animations']}
result={'client_version':'1.5.0','apk_sha256':digest,'baseline_apk_sha256':study['production_apk_sha256'],'baseline':study['variants']['baseline']['phases'],'current':phases,'raw_current':str(path),'full_grok_parity':False,'stable_60fps_proven':False,'scope':'Same explicitly owned two-core Android emulator, Windows Quiet mode; one no-recording profile per variant. Frame callbacks are not display FPS.','promoted_changes':['Replace full-viewport blurred shadow with a 1px foreground edge.','Start page motion at zero velocity; keep 380/330ms durations.'],'rejected_experiments':['Persistent layer','Gradient edge','View Transitions','Previous visual-state prepaint fence']}
(ROOT/'work/motion-summary-1.5.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
