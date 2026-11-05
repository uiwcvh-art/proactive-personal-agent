"""Publish the accepted immutable APK, then its update metadata and source links."""
from pathlib import Path
import datetime,hashlib,json,subprocess,sys,shutil
import httpx
from deploy_vps import SSH,SCP
ROOT=Path(__file__).resolve().parents[1];APK=ROOT/'android/app/build/outputs/apk/release/app-release.apk'
gate=json.loads((ROOT/'work/final-release-gate.json').read_text());digest=hashlib.sha256(APK.read_bytes()).hexdigest()
assert gate['apk_sha256']==digest and len(gate['checks'])==4 and all(x['passed'] for x in gate['checks'])
assert '29 passed' in (ROOT/'work/server-final-tests.txt').read_text('utf-8')
subprocess.run([sys.executable,str(ROOT/'scripts/publish_apk.py')],cwd=ROOT,check=True)
shutil.copy2(ROOT/'dist/proactive-agent.apk',ROOT/'dist/proactive-agent-1.3.0.apk')
base='http://216.23.82.196/proactive-agent'
source=ROOT/'dist/mika-corresponding-source.tar.gz'
with source.open('rb') as f:source_digest=hashlib.file_digest(f,'sha256').hexdigest()
metadata={'tag_name':'v1.3.0','name':'Pulse Bot 1.3.0','published_at':datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(),'html_url':base+'/',
    'body':'集成 Mika 本机助理；新增日本节点云电脑、接管、停止、文件资料库和加密分块传输。保留页面与面板动效、草稿、记忆与例行任务。需要在手机配置自己的执行模型后才能运行 AI 云电脑任务。',
    'assets':[{'name':'proactive-agent.apk','size':APK.stat().st_size,'digest':'sha256:'+digest,'browser_download_url':base+'/download/proactive-agent.apk'},
              {'name':source.name,'size':source.stat().st_size,'digest':'sha256:'+source_digest,'browser_download_url':base+'/download/'+source.name}]}
path=ROOT/'dist/release.json';path.write_text(json.dumps(metadata,ensure_ascii=False,indent=2),'utf-8')
subprocess.run(SCP+[str(path),'vps-jp:/opt/proactive-agent/.deploy/release.json'],check=True)
subprocess.run(SSH+['chmod 644 /opt/proactive-agent/.deploy/release.json && mv /opt/proactive-agent/.deploy/release.json /opt/proactive-agent/dist/release.json'],check=True)
curl=subprocess.run(['curl.exe','--silent','--show-error','--head','--proxy','http://127.0.0.1:7890',base+'/download/proactive-agent.apk'],capture_output=True,text=True,check=True)
(ROOT/'work/apk-public-headers.txt').write_text(curl.stdout,'utf-8');assert '200 OK' in curl.stdout
with httpx.Client(proxy='http://127.0.0.1:7890',timeout=40,trust_env=False) as client:
    response=client.get(base+'/download/release.json');response.raise_for_status();assert response.json()['assets'][0]['digest']=='sha256:'+digest
    gpl=client.head(base+'/download/'+source.name);gpl.raise_for_status();assert int(gpl.headers['content-length'])==source.stat().st_size
receipt={'apk_sha256':digest,'apk_bytes':APK.stat().st_size,'curl_head_200':True,'release_metadata_status':response.status_code,'gpl_source_head_status':gpl.status_code,'gpl_source_bytes':source.stat().st_size,'gpl_source_sha256':source_digest}
(ROOT/'work/release-publication.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt,indent=2))
