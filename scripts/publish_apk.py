from pathlib import Path
import hashlib,subprocess,sys,json,shutil
import httpx
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from deploy_vps import SSH,SCP
dist=ROOT/'dist';dist.mkdir(exist_ok=True)
apk=dist/'proactive-agent.apk'
shutil.copy2(ROOT/'android/app/build/outputs/apk/release/app-release.apk',apk)
digest=hashlib.sha256(apk.read_bytes()).hexdigest()
subprocess.run(SCP+[str(apk),'vps-jp:/opt/proactive-agent/.deploy/proactive-agent.apk'],check=True)
command=f"test $(sha256sum /opt/proactive-agent/.deploy/proactive-agent.apk | cut -d' ' -f1) = {digest} && chmod 644 /opt/proactive-agent/.deploy/proactive-agent.apk && mv /opt/proactive-agent/.deploy/proactive-agent.apk /opt/proactive-agent/dist/proactive-agent.apk"
subprocess.run(SSH+[command],check=True)
url='http://216.23.82.196/proactive-agent/download/proactive-agent.apk'
with httpx.Client(proxy='http://127.0.0.1:7890',timeout=90,trust_env=False) as client:
    head=client.head(url);head.raise_for_status()
    r=client.get(url);r.raise_for_status()
downloaded=ROOT/'work/download-readback.apk';downloaded.write_bytes(r.content)
result={'url':url,'head_status':head.status_code,'content_length':int(head.headers['content-length']),'local_bytes':apk.stat().st_size,
    'download_bytes':len(r.content),'sha256':digest,'download_sha256':hashlib.sha256(r.content).hexdigest()}
assert result['content_length']==result['local_bytes']==result['download_bytes']
assert result['sha256']==result['download_sha256']
(dist/'SHA256SUMS.txt').write_text(digest+'  proactive-agent.apk\n')
(ROOT/'work/apk-download-verification.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
