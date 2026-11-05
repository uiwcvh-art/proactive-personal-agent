"""Correlate real touch ranges with Window callbacks and FrameTimeline attribution."""
from pathlib import Path
import collections,csv,hashlib,io,json,math,subprocess
ROOT=Path(__file__).resolve().parents[1];WORK=ROOT/'work'
EVIDENCE=WORK/'native-surface-acceptance'

def percentile(values,fraction):return round(sorted(values)[min(len(values)-1,math.ceil(fraction*len(values))-1)],2) if values else None

def main():
    audit=json.loads((EVIDENCE/'files/native-surface-audit.json').read_text());assert audit['completed']
    query="""SELECT f.ts/1e6 AS start_ms,f.dur/1e6 AS duration_ms,p.name AS process,f.layer_name,f.jank_type,f.present_type,f.on_time_finish
FROM actual_frame_timeline_slice f JOIN process p USING(upid)
WHERE p.name LIKE 'dev.bubuki.pro%' AND f.dur>0 ORDER BY f.ts"""
    result=subprocess.run([str(WORK/'tools/perfetto/trace_processor_shell.exe'),'query','--format','csv','--quiet',str(EVIDENCE/'presented.perfetto-trace'),query],check=True,capture_output=True,text=True)
    (EVIDENCE/'frametimeline.csv').write_text(result.stdout,'utf-8');rows=list(csv.DictReader(io.StringIO(result.stdout)))
    assert rows,'FrameTimeline did not capture app frames'
    routes=[]
    for event in audit['routes']:
        if event['phase'] not in {'first-push','warm-push','restart-push'}:continue
        selected=[r for r in rows if event['start_ms']<=float(r['start_ms'])<=event['end_ms'] and 'NativePulseActivity' in r['layer_name']]
        assert selected,'No native FrameTimeline frames in touch interval: '+event['phase']
        window=[f['total_ms'] for f in audit['android_window_frames'] if event['start_ms']<=f['intended_vsync_ms']<=event['end_ms']]
        types=collections.Counter(r['jank_type'] for r in selected);presentation=collections.Counter(r['present_type'] for r in selected)
        routes.append({'phase':event['phase'],'range_ms':event['end_ms']-event['start_ms'],'window_samples':len(window),'window_p95_ms':percentile(window,.95),'window_max_ms':round(max(window),2) if window else None,
            'frametimeline_samples':len(selected),'jank_type':dict(types),'present_type':dict(presentation),'late_present_frames':sum(int('Late' in r['present_type']) for r in selected),'raw_frametimeline_duration_p95_ms':percentile([float(r['duration_ms']) for r in selected],.95)})
    apk=ROOT/'android/app/build/outputs/apk/release/app-release.apk';version=json.loads(apk.with_name('output-metadata.json').read_text())['elements'][0]['versionName']
    result={'version':version,'apk_sha256':hashlib.sha256(apk.read_bytes()).hexdigest(),'activity_to_online_ms':audit['activity_to_online_ms'],'home_committed_ms':audit['rendered_home_ms'],
        'routes':routes,'environment':'Owned Android 35 emulator, 2 cores / 2 GB, existing 540x1170 and 210-dpi override, BelowNormal process priority, Quiet Windows host, screenrecord and minimal FrameTimeline trace; no concurrent build.',
        'stable_60fps_proven':False,'observation':'FrameTimeline 按真实触摸区间列出 jank_type 与 present_type；该结果包含模拟器、录屏和跟踪开销，不能等同于真机显示帧率。原始 CSV、trace 与录屏保留，仍不宣称稳定 60 fps。'}
    target=WORK/('native-presented-summary-'+version.removesuffix('.0')+'.json');target.write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8');print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
