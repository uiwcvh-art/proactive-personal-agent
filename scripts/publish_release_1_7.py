"""Publish only client artifacts after the native launcher and existing flows pass."""
from pathlib import Path
import datetime,hashlib,json,re,shutil,subprocess,sys,zipfile
import httpx
from deploy_vps import SSH,SCP
from deploy_identity_1_6 import PROTECTED

ROOT=Path(__file__).resolve().parents[1];WORK=ROOT/'work';DIST=ROOT/'dist'
APK=ROOT/'android/app/build/outputs/apk/release/app-release.apk'
BASE='http://216.23.82.196/proactive-agent'
REQUIRED={'native','native-parity','identity','bots','ui','motion','root','public'}

def digest(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def remote(command):return subprocess.check_output(SSH+[command],text=True)

def main():
    sha=digest(APK);gate=json.loads((WORK/'final-release-gate.json').read_text())
    assert gate['version']=='1.7.0' and gate['apk_sha256']==sha
    assert {c['check'] for c in gate['checks']}==REQUIRED and all(c['passed'] for c in gate['checks'])
    for name in ['native-surface-acceptance/files/native-surface-audit.json','native-launch-acceptance/files/native-launch-audit.json']:
        assert json.loads((WORK/name).read_text())['completed']
    server=json.loads((WORK/'deployment-1.6.json').read_text())
    assert all(digest(ROOT/name)==value for name,value in server['server_hashes'].items()),'Unexpected server source change'
    assert '34 passed' in (WORK/'server-1.6-tests.txt').read_text('utf-8')
    assert 'BUILD SUCCESSFUL' in (WORK/'root-fusion-build.txt').read_text('utf-8')
    assert 'BUILD SUCCESSFUL' in (WORK/'root-final-lint.txt').read_text('utf-8')
    assert '0 errors' in (ROOT/'android/app/build/reports/lint-results-release.txt').read_text('utf-8')
    root=json.loads((WORK/'root-acceptance/files/root-engine-proof.json').read_text())
    assert root['actual_root_uid']==0 and root['tool_loop'] and root['unicode_paste'] and root['main_focus_preserved'] and root['stop_released_display'] and root['needs_user_resume']
    certificate='879d8a5eb7e55e054925d1030f350b3441276e46bef549214001b2bfc4672329'
    signature=(WORK/'apk-1.7-signature.txt').read_text('utf-8')
    assert certificate in signature and 'Verified using v2 scheme (APK Signature Scheme v2): true' in signature
    assert (WORK/'quiet-mode-1.7.txt').read_text('utf-8-sig').strip()=='quiet'
    private=[(ROOT/'private/device-token').read_bytes().strip(),(ROOT/'private/keystore-password').read_bytes().strip()]
    with zipfile.ZipFile(APK) as archive:
        assert not any('mikarun' in name or '/libproot' in name or 'alpine' in name.lower() for name in archive.namelist())
        for entry in archive.infolist():
            blob=archive.read(entry)
            if entry.filename.endswith('.dex'):assert b'com/wangzan101/mika/' not in blob, 'Full Mika classes are still bundled'
            assert not any(secret and secret in blob for secret in private),'Private credential embedded in APK'
            assert not re.search(br'-----BEGIN [A-Z ]*PRIVATE KEY-----\r?\n[A-Za-z0-9+/=]',blob),'Private key embedded in APK'
    source=json.loads((WORK/'source-delivery.json').read_text());source_path=Path(source['file'])
    assert source['private_secret_gate']=='passed' and digest(source_path)==source['sha256']
    with zipfile.ZipFile(source_path) as archive:
        assert 'Pulse Bot 1.7.0 source.' in archive.read('SOURCE_README.txt').decode()
        assert not any(name.startswith(('android/mika-core/','android/mika-local/')) for name in archive.namelist())
        for name in ['android/app/src/main/java/dev/bubuki/proactive/NativePulseActivity.kt','android/app/src/main/java/dev/bubuki/proactive/NativePhoneActivity.kt','android/app/src/main/java/dev/bubuki/proactive/RootPhoneHarness.kt','android/app/src/main/java/dev/bubuki/proactive/markdown/MarkdownText.kt']:
            assert archive.read(name)==(ROOT/name).read_bytes()
    before=json.loads(remote(PROTECTED));(WORK/'client-1.7-protected-before.json').write_text(json.dumps(before,indent=2))
    # Source changes are published before the APK; no daemon, unit or nginx restart.
    assets=[source_path]
    for path in assets:
        expected=digest(path);name=path.name
        existing=remote(f'test ! -f /opt/proactive-agent/dist/{name} || sha256sum /opt/proactive-agent/dist/{name}').split()
        if not existing or existing[0]!=expected:
            subprocess.run(SCP+[str(path),'vps-jp:/opt/proactive-agent/.deploy/'+name],check=True)
            remote(f"test $(sha256sum /opt/proactive-agent/.deploy/{name} | cut -d' ' -f1) = {expected} && chmod 644 /opt/proactive-agent/.deploy/{name} && mv /opt/proactive-agent/.deploy/{name} /opt/proactive-agent/dist/{name}")
    receipt=WORK/'apk-download-verification.json'
    prior=json.loads(receipt.read_text()) if receipt.exists() else {}
    already=prior.get('sha256')==sha==prior.get('download_sha256') and prior.get('head_status')==200
    if already:
        assert digest(DIST/'proactive-agent.apk')==sha
        assert remote('sha256sum /opt/proactive-agent/dist/proactive-agent.apk').split()[0]==sha
    else:subprocess.run([sys.executable,str(ROOT/'scripts/publish_apk.py')],cwd=ROOT,check=True)
    shutil.copy2(DIST/'proactive-agent.apk',DIST/'proactive-agent-1.7.0.apk')
    all_assets=[DIST/'proactive-agent.apk',*assets]
    metadata={'tag_name':'v1.7.0','name':'Pulse Bot 1.7.0','published_at':datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(),'html_url':BASE+'/',
        'body':'自家原生首页、对话与 Root 独立手机工作区；共用执行模型、任务记录、停止与接管。借鉴 Mika 底层工具，不包含完整 Mika 应用或原生沙箱。保留云电脑、附件、透明记忆和二级设置。Root 独立焦点需要 Android 14+；模拟器验收与真机兼容性分别说明。',
        'assets':[{'name':p.name,'size':p.stat().st_size,'digest':'sha256:'+digest(p),'browser_download_url':BASE+'/download/'+p.name} for p in all_assets]}
    path=DIST/'release.json';path.write_text(json.dumps(metadata,ensure_ascii=False,indent=2),'utf-8')
    subprocess.run(SCP+[str(path),'vps-jp:/opt/proactive-agent/.deploy/release.json'],check=True)
    remote('chmod 644 /opt/proactive-agent/.deploy/release.json && mv /opt/proactive-agent/.deploy/release.json /opt/proactive-agent/dist/release.json')
    headers=subprocess.check_output(['curl.exe','--silent','--show-error','--head','--proxy','http://127.0.0.1:7890',BASE+'/download/proactive-agent.apk'],text=True)
    assert '200 OK' in headers;(WORK/'apk-public-headers.txt').write_text(headers,'utf-8')
    with httpx.Client(proxy='http://127.0.0.1:7890',timeout=90,trust_env=False) as client:
        response=client.get(BASE+'/download/release.json');response.raise_for_status();assert response.json()==metadata
        for asset in metadata['assets']:
            check=client.head(asset['browser_download_url']);check.raise_for_status();assert int(check.headers['content-length'])==asset['size']
            if asset['name']!='proactive-agent.apk':
                with client.stream('GET',asset['browser_download_url']) as stream:
                    stream.raise_for_status();hasher=hashlib.sha256();count=0
                    for chunk in stream.iter_bytes():hasher.update(chunk);count+=len(chunk)
                    assert count==asset['size'] and 'sha256:'+hasher.hexdigest()==asset['digest']
        health=client.get(BASE+'/health');health.raise_for_status();assert health.json()['status']=='ok'
    after=json.loads(remote(PROTECTED));(WORK/'client-1.7-protected-after.json').write_text(json.dumps(after,indent=2));assert before==after
    result={'version':'1.7.0','apk_sha256':sha,'apk_bytes':APK.stat().st_size,'curl_head_200':True,'sources_full_readback':True,
        'protected_services_unchanged':True,'protected_services_checked':len(before),'server_deployed':False,'nginx_changes':(WORK/'source-download-route-1.7.json').exists(),'unit_changes':False,'health':health.json(),'assets':metadata['assets']}
    (WORK/'release-1.7-publication.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))

if __name__=='__main__':main()
