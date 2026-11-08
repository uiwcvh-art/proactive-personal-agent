"""Update only this application's Python files and restart its existing service."""
from pathlib import Path
import hashlib,json,subprocess,tarfile,time
from deploy_vps import SSH,SCP
ROOT=Path(__file__).resolve().parents[1]
work=ROOT/'work'
archive=work/'redesign-server.tar.gz'
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
with tarfile.open(archive,'w:gz') as out:
    for file in files:out.add(file,arcname=file.relative_to(ROOT).as_posix())
digest=hashlib.sha256(archive.read_bytes()).hexdigest()
stamp=time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())
snapshot=r'''python3 - <<'PY'
import subprocess,json
names=subprocess.check_output(['systemctl','list-units','--all','--type=service','--no-legend','--plain'],text=True)
units=[line.split()[0] for line in names.splitlines() if any(term in line.lower() for term in ('bb-jp-','claude','browser','docker','sing-box'))]
result={}
for unit in units:
    result[unit]=subprocess.check_output(['systemctl','show',unit,'-p','ActiveState','-p','SubState','-p','MainPID','-p','ActiveEnterTimestampMonotonic'],text=True)
print(json.dumps(result,sort_keys=True))
PY'''
def remote(command):return subprocess.check_output(SSH+[command],text=True)
before=json.loads(remote(snapshot));(work/'redesign-protected-before.json').write_text(json.dumps(before,indent=2))
subprocess.run(SCP+[str(archive),'vps-jp:/opt/proactive-agent/.deploy/redesign-server.tar.gz'],check=True)
script=f'''set -eu
umask 077
cd /opt/proactive-agent
test -x venv/bin/python
test "$(sha256sum .deploy/redesign-server.tar.gz | cut -d' ' -f1)" = {digest}
tar -czf .deploy/server-before-{stamp}.tgz server
if [ -f data/state.json ]; then cp -p data/state.json .deploy/state-before-{stamp}.json; fi
tar -xzf .deploy/redesign-server.tar.gz -C /opt/proactive-agent
chmod -R a+rX server
venv/bin/python - <<'PY'
from pathlib import Path
for path in Path('server').rglob('*.py'): compile(path.read_text('utf-8'),str(path),'exec')
PY
systemctl restart proactive-agent.service
for attempt in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS http://127.0.0.1:18190/health > .deploy/redesign-health.json; then
    cat .deploy/redesign-health.json
    exit 0
  fi
  sleep 1
done
tar -xzf .deploy/server-before-{stamp}.tgz -C /opt/proactive-agent
systemctl restart proactive-agent.service
echo 'Application health failed; previous application code restored' >&2
exit 1
'''
script_path=work/'deploy-redesign.sh';script_path.write_text(script,encoding='utf-8',newline='\n')
subprocess.run(SCP+[str(script_path),'vps-jp:/opt/proactive-agent/.deploy/deploy-redesign.sh'],check=True)
health=remote('bash /opt/proactive-agent/.deploy/deploy-redesign.sh');print(health)
after=json.loads(remote(snapshot));(work/'redesign-protected-after.json').write_text(json.dumps(after,indent=2))
result={'health':json.loads(health),'protected_services_unchanged':before==after,'protected_services_checked':len(before),'backup':'/opt/proactive-agent/.deploy/server-before-'+stamp+'.tgz','nginx_changes':False,'unit_changes':False,'dependency_changes':False}
(work/'redesign-deployment.json').write_text(json.dumps(result,indent=2))
if before!=after:raise RuntimeError('Protected service snapshot changed; inspect before/after evidence')
print(json.dumps(result,indent=2))
