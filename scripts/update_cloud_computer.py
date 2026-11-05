"""Update only our private desktop, refusing to interrupt a running agent task."""
from pathlib import Path
import hashlib,json,subprocess,tarfile
from deploy_cloud_computer import BASE,ROOT,WORK,SNAPSHOT,remote
from deploy_vps import SCP,SSH

before=json.loads(remote(SNAPSHOT))
archive=WORK/'update.tar.gz'
with tarfile.open(archive,'w:gz') as out:
    for p in [*sorted((ROOT/'server').glob('computer*.py')),ROOT/'server/sandbox.py',ROOT/'server/computer-openbox.xml']:
        out.add(p,arcname='server/'+p.name)
digest=hashlib.sha256(archive.read_bytes()).hexdigest()
subprocess.run(SCP+[str(archive),'vps-jp:/opt/proactive-agent/.deploy/computer-update.tar.gz'],check=True)
command=r'''set -eu
python3 - <<'PY'
from pathlib import Path
import urllib.request,json
token=Path('/opt/proactive-agent/cloud-computer/control.env').read_text().strip().split('=',1)[1]
try:
    req=urllib.request.Request('http://127.0.0.1:18193/status',headers={'Authorization':'Bearer '+token})
    with urllib.request.urlopen(req,timeout=2) as r:status=json.load(r)
except Exception:status={}
if (status.get('job') or {}).get('state') in ('running','paused','waiting_user'):
    raise SystemExit('Our cloud computer has an active task; refusing restart')
PY
test "$(sha256sum /opt/proactive-agent/.deploy/computer-update.tar.gz | cut -d' ' -f1)" = __HASH__
systemctl stop proactive-desktop.service
tar -xzf /opt/proactive-agent/.deploy/computer-update.tar.gz -C /opt/proactive-agent/cloud-computer
chmod 644 /opt/proactive-agent/cloud-computer/server/*
systemctl start proactive-desktop.service
python3 - <<'PY'
from pathlib import Path
import urllib.request,json,time
token=Path('/opt/proactive-agent/cloud-computer/control.env').read_text().strip().split('=',1)[1]
for _ in range(30):
    try:
        req=urllib.request.Request('http://127.0.0.1:18193/status',headers={'Authorization':'Bearer '+token})
        with urllib.request.urlopen(req,timeout=1) as r:status=json.load(r)
        if status.get('available'):
            print(json.dumps(status));break
    except Exception:pass
    time.sleep(1)
else:raise SystemExit('Desktop did not become ready; inspect this service journal')
PY
systemctl show proactive-desktop.service -p ActiveState -p SubState -p MainPID
journalctl -u proactive-desktop.service --since '-10 seconds' --no-pager -o cat
'''.replace('__HASH__',digest)
try:
    p=subprocess.run(SSH+[command],capture_output=True,encoding='utf-8',errors='replace')
    (WORK/'update-result.txt').write_text(p.stdout+'\n'+p.stderr,encoding='utf-8')
    print(p.stdout.encode('ascii','backslashreplace').decode())
    if p.returncode:
        print(p.stderr.encode('ascii','backslashreplace').decode())
        log=remote('journalctl -u proactive-desktop.service -n 40 --no-pager -o cat')
        print(log.encode('ascii','backslashreplace').decode())
    p.check_returncode()
finally:
    after=json.loads(remote(SNAPSHOT));receipt={'protected_services_unchanged':before==after,'protected_services_checked':len(before),'archive_sha256':digest}
    (WORK/'update-receipt.json').write_text(json.dumps(receipt,indent=2))
    if before!=after:raise RuntimeError('Protected service snapshot changed')
