"""One final pass on one immutable signed APK; the emulator is shared sequentially."""
from pathlib import Path
import argparse,subprocess,sys,hashlib,json,time,re,zipfile
parser=argparse.ArgumentParser()
parser.add_argument('--only',choices=['native','native-parity','identity','bots','ui','motion','root','public'])
args=parser.parse_args()
ROOT=Path(__file__).resolve().parents[1];APK=ROOT/'android/app/build/outputs/apk/release/app-release.apk'
version=json.loads(APK.with_name('output-metadata.json').read_text())['elements'][0]['versionName']
assert version==re.search(r"versionName\s+'([^']+)'",(ROOT/'android/app/build.gradle').read_text('utf-8'))[1], 'The packaged APK version is stale'
with zipfile.ZipFile(APK) as archive:
    for source in (ROOT/'android/app/src/main/assets').iterdir():
        if source.is_file():assert archive.read('assets/'+source.name)==source.read_bytes(), 'Stale packaged asset: '+source.name
digest=hashlib.sha256(APK.read_bytes()).hexdigest();results=[]
progress=ROOT/'work'/('release-'+version+'-progress.json')
if progress.exists():
    saved=json.loads(progress.read_text())
    if saved.get('apk_sha256')==digest:
        results=[{**c,'reused_same_apk_acceptance':True} for c in saved['checks'] if c.get('passed') and 'OK (1 test)' in Path(c['log']).read_text('utf-8')]
for name,directory in [('bots','bot-management-acceptance'),('identity','identity-acceptance'),('native','native-surface-acceptance'),('native-parity','native-launch-acceptance'),('root','root-acceptance')]:
    receipt=ROOT/'work'/directory/'receipt.json'
    if receipt.exists() and not any(c['check']==name for c in results):
        previous=json.loads(receipt.read_text())
        if previous.get('passed') and previous.get('apk_sha256')==digest and 'OK (1 test)' in Path(previous['log']).read_text('utf-8'):
            results.append({'check':name,'passed':True,'seconds':previous['seconds'],'log':previous['log'],'reused_same_apk_acceptance':True})
steps=[('root',['scripts/run_ui_acceptance.py','--local','--record']),('native',['scripts/run_ui_acceptance.py','--native-surface','--record']),('native-parity',['scripts/run_ui_acceptance.py','--native-parity','--record']),('identity',['scripts/run_ui_acceptance.py','--identity','--record']),('bots',['scripts/run_ui_acceptance.py','--bots','--record']),('ui',['scripts/run_ui_acceptance.py','--record']),('motion',['scripts/run_ui_acceptance.py','--motion','--record']),('public',['scripts/run_public_computer_acceptance.py'])]
required={s[0] for s in steps}
steps=[s for s in steps if (not args.only or s[0]==args.only) and not any(c['check']==s[0] for c in results)]
for name,command in steps:
    log=ROOT/'work'/('release-'+version+'-'+name+'.txt');start=time.monotonic()
    print('Checking',name,flush=True)
    with log.open('w',encoding='utf-8') as out:subprocess.run([sys.executable,'-X','utf8',*command],cwd=ROOT,stdout=out,stderr=subprocess.STDOUT,check=True)
    assert 'OK (1 test)' in log.read_text('utf-8')
    assert hashlib.sha256(APK.read_bytes()).hexdigest()==digest,'The release APK changed during acceptance'
    results.append({'check':name,'passed':True,'seconds':round(time.monotonic()-start,2),'log':str(log)})
    progress.write_text(json.dumps({'version':version,'apk_sha256':digest,'checks':results},indent=2))
    print(name,'passed',flush=True)
if required!={c['check'] for c in results}:
    print(json.dumps({'partial':True,'passed':[c['check'] for c in results],'remaining':sorted(required-{c['check'] for c in results})}))
    sys.exit(0)
receipt={'version':version,'apk_sha256':digest,'apk_bytes':APK.stat().st_size,'checks':results,'paid_model_calls':0,'smtp_calls':0}
old=ROOT/'work/final-release-gate.json'
if old.exists():
    prior=json.loads(old.read_text())
    backup=ROOT/'work'/('final-release-gate-'+prior.get('version','1.3.0')+'.json')
    if not backup.exists():backup.write_text(json.dumps(prior,indent=2))
(ROOT/'work/final-release-gate.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt,indent=2))
