from pathlib import Path
import datetime,hashlib,re,subprocess,sys,json,shutil
import httpx
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from deploy_vps import SSH,SCP
dist=ROOT/'dist';dist.mkdir(exist_ok=True)
apk=dist/'proactive-agent.apk'
shutil.copy2(ROOT/'android/app/build/outputs/apk/release/app-release.apk',apk)
digest=hashlib.sha256(apk.read_bytes()).hexdigest()
gradle_txt=(ROOT/'android/app/build.gradle').read_text('utf-8')
ver_name=re.search(r"versionName\s+'([^']+)'",gradle_txt).group(1)
ver_code=int(re.search(r"versionCode\s+(\d+)",gradle_txt).group(1))
notes=f"v{ver_name} (Build {ver_code}): 全面架构重构与虚拟屏体验修复——修复微信等自绘界面(node_count=0)点击被误判未响应及重复双击 Bug；全模型原生多模态图像直传(零二次OCR跳转)；修复微信小程序跳出虚拟屏到物理主屏、跳回桌面及主屏亮度升高问题；任务结束自动销毁回收虚拟屏释放内存"
release_meta={
    'version':ver_name,
    'version_name':ver_name,
    'versionName':ver_name,
    'tag_name':f'v{ver_name}',
    'version_code':ver_code,
    'versionCode':ver_code,
    'build':ver_code,
    'published_at':datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(),
    'apk_sha256':digest,
    'apk_size':apk.stat().st_size,
    'download_url':'/proactive-agent/download/proactive-agent.apk',
    'notes':notes,
    'body':notes,
}
rel_file=dist/'release.json'
rel_file.write_text(json.dumps(release_meta,ensure_ascii=False,indent=2),encoding='utf-8')
subprocess.run(SCP+[str(apk),'vps-jp:/opt/proactive-agent/.deploy/proactive-agent.apk'],check=True)
subprocess.run(SCP+[str(rel_file),'vps-jp:/opt/proactive-agent/.deploy/release.json'],check=True)
command=f"test $(sha256sum /opt/proactive-agent/.deploy/proactive-agent.apk | cut -d' ' -f1) = {digest} && chmod 644 /opt/proactive-agent/.deploy/proactive-agent.apk /opt/proactive-agent/.deploy/release.json && mv /opt/proactive-agent/.deploy/proactive-agent.apk /opt/proactive-agent/dist/proactive-agent.apk && mv /opt/proactive-agent/.deploy/release.json /opt/proactive-agent/dist/release.json"
subprocess.run(SSH+[command],check=True)
url='http://216.23.82.196/proactive-agent/download/proactive-agent.apk'
rel_url='http://216.23.82.196/proactive-agent/download/release.json'
with httpx.Client(proxy='http://127.0.0.1:7890',timeout=90,trust_env=False) as client:
    head=client.head(url);head.raise_for_status()
    r=client.get(url);r.raise_for_status()
    r_meta=client.get(rel_url);r_meta.raise_for_status()
    remote_rel=r_meta.json()
downloaded=ROOT/'work/download-readback.apk';downloaded.write_bytes(r.content)
result={'url':url,'version':ver_name,'versionCode':ver_code,'remote_release_versionName':remote_rel.get('versionName'),'head_status':head.status_code,'content_length':int(head.headers['content-length']),'local_bytes':apk.stat().st_size,
    'download_bytes':len(r.content),'sha256':digest,'download_sha256':hashlib.sha256(r.content).hexdigest()}
assert result['content_length']==result['local_bytes']==result['download_bytes']
assert result['sha256']==result['download_sha256']
assert remote_rel.get('versionName')==ver_name and remote_rel.get('versionCode')==ver_code
(dist/'SHA256SUMS.txt').write_text(digest+'  proactive-agent.apk\n')
(ROOT/'work/apk-download-verification.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2,ensure_ascii=False))
