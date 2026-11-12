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
notes=f"v{ver_name} (Build {ver_code}): 1) 虚拟屏底层彻底重构：根治微信/支付宝抢占主屏前台与主屏莫名退回桌面 Bug，固定独立屏桌面根任务防止返回串栈，修复返回键连发 4 次问题；2) 主页横幅、对话顶栏与工具菜单全面新增一键「销毁虚拟屏」，AI 侧 close/release 真正彻底销毁虚拟屏且零残留；3) Agent Loop 与流式状态机大改：移除 DSH/Codex 后置总结抹除与重跑 Bug，修复未调工具提前停机；4) 引擎级持久目标模式 (/goal)：自动持续拉起 AI 直至显式调用 complete_goal 宣布目标完成；5) 升级虚拟屏 1080P 高清视觉截图并硬拦截修改系统深浅色/主屏设置"
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
(dist/'SHA256SUMS.txt').write_text(digest+'  proactive-agent.apk\n')
subprocess.run(SSH+['mkdir -p /opt/proactive-agent/.deploy /opt/proactive-agent/dist'],check=True)
subprocess.run(SCP+[str(apk),'vps-jp:/opt/proactive-agent/.deploy/proactive-agent.apk'],check=True)
subprocess.run(SCP+[str(rel_file),'vps-jp:/opt/proactive-agent/.deploy/release.json'],check=True)
subprocess.run(SCP+[str(dist/'SHA256SUMS.txt'),'vps-jp:/opt/proactive-agent/dist/SHA256SUMS.txt'],check=True)
command=f"test $(sha256sum /opt/proactive-agent/.deploy/proactive-agent.apk | cut -d' ' -f1) = {digest} && chmod 644 /opt/proactive-agent/.deploy/proactive-agent.apk /opt/proactive-agent/.deploy/release.json && chown proactive-agent:proactive-agent /opt/proactive-agent/.deploy/proactive-agent.apk /opt/proactive-agent/.deploy/release.json && mv /opt/proactive-agent/.deploy/proactive-agent.apk /opt/proactive-agent/dist/proactive-agent.apk && mv /opt/proactive-agent/.deploy/release.json /opt/proactive-agent/dist/release.json"
subprocess.run(SSH+[command],check=True)
remote_rel_raw=subprocess.check_output(SSH+['curl -fsSL http://127.0.0.1:18190/download/release.json'],text=True,encoding='utf-8')
remote_rel=json.loads(remote_rel_raw)
remote_sha=subprocess.check_output(SSH+["curl -fsSL http://127.0.0.1:18190/download/proactive-agent.apk | sha256sum | cut -d' ' -f1"],text=True,encoding='utf-8').strip()
url='http://43.167.195.151/proactive-agent/download/proactive-agent.apk'
result={'url':url,'version':ver_name,'versionCode':ver_code,'remote_release_versionName':remote_rel.get('versionName'),'local_bytes':apk.stat().st_size,'sha256':digest,'download_sha256':remote_sha}
assert result['sha256']==result['download_sha256']
assert remote_rel.get('versionName')==ver_name and remote_rel.get('versionCode')==ver_code
(ROOT/'work/apk-download-verification.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2,ensure_ascii=False))
