"""Publish 1.6 after one immutable APK passes its six relevant acceptance flows."""
from pathlib import Path
import datetime,hashlib,json,subprocess,sys,shutil
import httpx
from deploy_vps import SSH,SCP

ROOT=Path(__file__).resolve().parents[1]
APK=ROOT/'android/app/build/outputs/apk/release/app-release.apk'
gate=json.loads((ROOT/'work/final-release-gate.json').read_text())
digest=hashlib.sha256(APK.read_bytes()).hexdigest()
assert gate['version']=='1.6.0' and gate['apk_sha256']==digest
assert {c['check'] for c in gate['checks']}=={'identity','bots','ui','motion','mika','public'} and all(c['passed'] for c in gate['checks'])
assert '34 passed' in (ROOT/'work/server-1.6-tests.txt').read_text('utf-8')
server=json.loads((ROOT/'work/deployment-1.6.json').read_text())
assert server['version']=='1.6.0' and server['protected_services_unchanged'] and server['health']['status']=='ok'
assert all(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==sha for name,sha in server['server_hashes'].items())
subprocess.run([sys.executable,str(ROOT/'scripts/publish_apk.py')],cwd=ROOT,check=True)
shutil.copy2(ROOT/'dist/proactive-agent.apk',ROOT/'dist/proactive-agent-1.6.0.apk')
base='http://216.23.82.196/proactive-agent'
source=ROOT/'dist/mika-corresponding-source.tar.gz'
with source.open('rb') as file:source_digest=hashlib.file_digest(file,'sha256').hexdigest()
metadata={'tag_name':'v1.6.0','name':'Pulse Bot 1.6.0','published_at':datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(),'html_url':base+'/',
    'body':'新增主助手更换与实际主动联系路由、独立 Title 标签、形状/生成/上传头像、拖动缩放裁剪和未保存修改提示。保留 Mika 本机助理、云电脑、文件和透明记忆。',
    'assets':[{'name':'proactive-agent.apk','size':APK.stat().st_size,'digest':'sha256:'+digest,'browser_download_url':base+'/download/proactive-agent.apk'},
              {'name':source.name,'size':source.stat().st_size,'digest':'sha256:'+source_digest,'browser_download_url':base+'/download/'+source.name}]}
path=ROOT/'dist/release.json';path.write_text(json.dumps(metadata,ensure_ascii=False,indent=2),'utf-8')
subprocess.run(SCP+[str(path),'vps-jp:/opt/proactive-agent/.deploy/release.json'],check=True)
subprocess.run(SSH+['chmod 644 /opt/proactive-agent/.deploy/release.json && mv /opt/proactive-agent/.deploy/release.json /opt/proactive-agent/dist/release.json'],check=True)
headers=subprocess.run(['curl.exe','--silent','--show-error','--head','--proxy','http://127.0.0.1:7890',base+'/download/proactive-agent.apk'],capture_output=True,text=True,check=True)
(ROOT/'work/apk-public-headers.txt').write_text(headers.stdout,'utf-8');assert '200 OK' in headers.stdout
with httpx.Client(proxy='http://127.0.0.1:7890',timeout=40,trust_env=False) as client:
    response=client.get(base+'/download/release.json');response.raise_for_status();assert response.json()==metadata
    gpl=client.head(base+'/download/'+source.name);gpl.raise_for_status();assert int(gpl.headers['content-length'])==source.stat().st_size
receipt={'version':'1.6.0','apk_sha256':digest,'apk_bytes':APK.stat().st_size,'curl_head_200':True,'release_metadata_status':response.status_code,'gpl_source_head_status':gpl.status_code,'gpl_source_bytes':source.stat().st_size,'gpl_source_sha256':source_digest}
(ROOT/'work/release-1.6-publication.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt,indent=2))
