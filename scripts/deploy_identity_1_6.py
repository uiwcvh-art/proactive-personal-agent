"""Update only the existing application's venv and service after identity acceptance."""
from pathlib import Path
import hashlib,json,subprocess,tarfile,time
from deploy_vps import SSH,SCP
ROOT=Path(__file__).resolve().parents[1];WORK=ROOT/'work'
def remote(command):return subprocess.check_output(SSH+[command],text=True)
PROTECTED=r"""python3 - <<'PY'
import subprocess,json
lines=subprocess.check_output(['systemctl','list-units','--all','--type=service','--no-legend','--plain'],text=True)
units=[line.split()[0] for line in lines.splitlines() if any(t in line.lower() for t in ('bb-jp-','claude','browser','docker','sing-box'))]
result={u:subprocess.check_output(['systemctl','show',u,'-p','ActiveState','-p','SubState','-p','MainPID','-p','ActiveEnterTimestampMonotonic'],text=True) for u in units}
print(json.dumps(result,sort_keys=True))
PY"""
def main():
    assert '34 passed' in (WORK/'server-1.6-tests.txt').read_text('utf-8')
    progress=json.loads((WORK/'release-1.6.0-progress.json').read_text())
    assert any(c['check']=='identity' and c['passed'] for c in progress['checks'])
    apk=ROOT/'android/app/build/outputs/apk/release/app-release.apk'
    assert hashlib.sha256(apk.read_bytes()).hexdigest()==progress['apk_sha256']
    archive=WORK/'identity-server-1.6.tar.gz'
    files=sorted({
        *(ROOT/'server').glob('*.py'),
        *(ROOT/'server/core').rglob('*.py'),
        *(ROOT/'server/transport').rglob('*.py'),
        *(ROOT/'server/plugins').rglob('*.py'),
        *(ROOT/'server/cloud_os').rglob('*.py'),
        *(ROOT/'desktop').rglob('*.py'),
        ROOT/'server/requirements.txt',
        ROOT/'server/computer_requirements.txt',
        ROOT/'server/computer-openbox.xml',
    })
    hashes={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    with tarfile.open(archive,'w:gz') as out:
        for p in files:out.add(p,arcname=p.relative_to(ROOT).as_posix())
    digest=hashlib.sha256(archive.read_bytes()).hexdigest();stamp=time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())
    before=json.loads(remote(PROTECTED));(WORK/'identity-protected-before.json').write_text(json.dumps(before,indent=2))
    subprocess.run(SCP+[str(archive),'vps-jp:/opt/proactive-agent/.deploy/identity-server-1.6.tar.gz'],check=True)
    script=f"""set -eu
umask 077
cd /opt/proactive-agent
test "$(sha256sum .deploy/identity-server-1.6.tar.gz | cut -d' ' -f1)" = {digest}
venv/bin/python - <<'PY'
from pathlib import Path
import json,urllib.request
token=Path('data/device-token').read_text().strip()
request=urllib.request.Request('http://127.0.0.1:18190/api/status',headers={{'Authorization':'Bearer '+token}})
status=json.load(urllib.request.urlopen(request,timeout=15))
if status.get('execution') or status.get('activity') in ('thinking','curiosity','research'):
    raise SystemExit('Application is busy; wait for the current execution')
PY
tar -czf .deploy/server-before-identity-{stamp}.tgz server
cp -p data/state.json .deploy/state-before-identity-{stamp}.json
(umask 022; venv/bin/python -m pip install --disable-pip-version-check --no-deps --only-binary=:all: Pillow==13.0.0 > .deploy/pillow-1.6-install.log 2>&1)
tar -xzf .deploy/identity-server-1.6.tar.gz -C /opt/proactive-agent
chmod -R a+rX server
venv/bin/python - <<'PY'
from pathlib import Path
import PIL
assert PIL.__version__=='13.0.0'
for p in Path('server').rglob('*.py'):compile(p.read_text('utf-8'),str(p),'exec')
PY
systemctl restart proactive-agent.service
for attempt in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS http://127.0.0.1:18190/health > .deploy/identity-health.json; then
    cat .deploy/identity-health.json
    exit 0
  fi
  sleep 1
done
tar -xzf .deploy/server-before-identity-{stamp}.tgz -C /opt/proactive-agent
systemctl restart proactive-agent.service
echo 'Health failed; previous application code restored' >&2
exit 1
"""
    path=WORK/'deploy-identity-1.6.sh';path.write_text(script,encoding='utf-8',newline='\n')
    subprocess.run(SCP+[str(path),'vps-jp:/opt/proactive-agent/.deploy/deploy-identity-1.6.sh'],check=True)
    health=json.loads(remote('bash /opt/proactive-agent/.deploy/deploy-identity-1.6.sh'));assert health['version']=='1.6.0'
    check=r"""/opt/proactive-agent/venv/bin/python - <<'PY'
from pathlib import Path
import hashlib,json,urllib.request,urllib.error,PIL
base=Path('/opt/proactive-agent');token=(base/'data/device-token').read_text().strip()
def request(path,method='GET',body=None,auth=True):
    headers={'Content-Type':'application/json'}
    if auth:headers['Authorization']='Bearer '+token
    req=urllib.request.Request('http://127.0.0.1:18190'+path,method=method,headers=headers,data=json.dumps(body).encode() if body is not None else None)
    try:
        response=urllib.request.urlopen(req,timeout=15);return response.status,json.load(response)
    except urllib.error.HTTPError as error:return error.code,None
code,roster=request('/api/bots');assert code==200
assert roster['primary_bot_id'] in {b['id'] for b in roster['bots']}
assert all('title' in b and 'avatar_version' in b for b in roster['bots'])
checks=[request('/api/bots/primary','PUT',{'bot_id':'_release_missing_'},False)[0],request('/api/bots/primary','PUT',{'bot_id':'_release_missing_'})[0],request('/api/bots/_release_missing_/identity','PATCH',{'title':'check'})[0],request('/api/bots/_release_missing_/avatar')[0],request('/api/bots/_release_missing_/avatar/generate','POST',{'model':'fixture','prompt':'check'})[0]]
assert checks==[401,404,404,404,404],checks
files=sorted({
    *(base/'server').glob('*.py'),
    *(base/'server/core').rglob('*.py'),
    *(base/'server/transport').rglob('*.py'),
    *(base/'server/plugins').rglob('*.py'),
    *(base/'server/cloud_os').rglob('*.py'),
    *(base/'desktop').rglob('*.py'),
    base/'server/requirements.txt',
    base/'server/computer_requirements.txt',
    base/'server/computer-openbox.xml',
})
print(json.dumps({'checks':checks,'pillow':PIL.__version__,'hashes':{p.relative_to(base).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}))
PY"""
    live=json.loads(remote(check));assert all(live['hashes'].get(p)==sha for p,sha in hashes.items())
    after=json.loads(remote(PROTECTED));(WORK/'identity-protected-after.json').write_text(json.dumps(after,indent=2))
    result={'version':'1.6.0','health':health,'protected_services_unchanged':before==after,'protected_services_checked':len(before),
        'backup':'/opt/proactive-agent/.deploy/server-before-identity-'+stamp+'.tgz','nginx_changes':False,'unit_changes':False,
        'desktop_service_restarted':False,'dependency_addition':'Pillow==13.0.0 in the application venv',
        'server_hashes':hashes,'live_checks':live['checks'],'archive_sha256':digest}
    (WORK/'deployment-1.6.json').write_text(json.dumps(result,indent=2))
    if before!=after:raise RuntimeError('Protected service snapshot changed; inspect the saved before/after evidence')
    print(json.dumps({k:v for k,v in result.items() if k!='server_hashes'},indent=2))
if __name__=='__main__':main()
