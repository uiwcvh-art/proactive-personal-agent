"""Wire our two services together, preserving the existing nginx route and all other units."""
import json,subprocess
from deploy_cloud_computer import remote,SNAPSHOT,WORK
before=json.loads(remote(SNAPSHOT))
command=r'''set -eu
python3 - <<'PY'
from pathlib import Path
import urllib.request,json
base=Path('/opt/proactive-agent/cloud-computer')
token=(base/'control.env').read_text().strip().split('=',1)[1]
req=urllib.request.Request('http://127.0.0.1:18193/status',headers={'Authorization':'Bearer '+token})
with urllib.request.urlopen(req,timeout=3) as r:status=json.load(r)
job=status.get('job')
if job and job.get('state') in ('running','paused','waiting_user'):raise SystemExit('An active desktop task prevents deployment restart')
drop=Path('/etc/systemd/system/proactive-agent.service.d');drop.mkdir(mode=0o755,exist_ok=True)
(drop/'computer.conf').write_text('[Service]\nEnvironmentFile=/opt/proactive-agent/cloud-computer/control.env\n')
drop=Path('/etc/systemd/system/proactive-desktop.service.d');drop.mkdir(mode=0o755,exist_ok=True)
(drop/'runtime.conf').write_text('[Service]\nEnvironment=BROWSER_USE_LOGGING_LEVEL=warning\n')
(drop/'startup-diagnostic.conf').unlink(missing_ok=True)
PY
systemctl daemon-reload
systemctl restart proactive-desktop.service
python3 - <<'PY'
from pathlib import Path
import urllib.request,json,time,importlib.metadata
base=Path('/opt/proactive-agent/cloud-computer')
token=(base/'control.env').read_text().strip().split('=',1)[1]
for _ in range(40):
    try:
        req=urllib.request.Request('http://127.0.0.1:18193/status',headers={'Authorization':'Bearer '+token})
        with urllib.request.urlopen(req,timeout=1) as r:status=json.load(r)
        if status.get('available'):
            print(json.dumps(status));break
    except Exception:pass
    time.sleep(1)
else:raise SystemExit('Our final desktop startup did not become ready')
PY
'''
try:print(remote(command))
finally:
    after=json.loads(remote(SNAPSHOT));receipt={'protected_services_unchanged':before==after,'protected_services_checked':len(before),'main_service_dropin':'/etc/systemd/system/proactive-agent.service.d/computer.conf','nginx_changes':False,'existing_browser_changes':False,'desktop_restarted_only_after_terminal_job':True}
    (WORK/'connection.json').write_text(json.dumps(receipt,indent=2))
    if before!=after:raise RuntimeError('Protected service snapshot changed')
