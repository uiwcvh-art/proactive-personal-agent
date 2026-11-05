"""Install only this project's independent cloud computer; no shared browser changes.

The main API is connected separately after the private desktop passes acceptance.
"""
from pathlib import Path
import hashlib,json,subprocess,tarfile,time
from deploy_vps import SSH,SCP

ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/'work/cloud-computer';WORK.mkdir(parents=True,exist_ok=True)
BASE='/opt/proactive-agent/cloud-computer'
SNAPSHOT=r'''python3 - <<'PY'
import subprocess,json
lines=subprocess.check_output(['systemctl','list-units','--all','--type=service','--no-legend','--plain'],text=True)
units=[line.split()[0] for line in lines.splitlines() if any(term in line.lower() for term in ('bb-jp-','claude','browser','docker','sing-box'))]
result={unit:subprocess.check_output(['systemctl','show',unit,'-p','ActiveState','-p','SubState','-p','MainPID','-p','ActiveEnterTimestampMonotonic'],text=True) for unit in units}
print(json.dumps(result,sort_keys=True))
PY'''

def remote(command):return subprocess.check_output(SSH+[command],text=True)
def main():
    before=json.loads(remote(SNAPSHOT));(WORK/'protected-before.json').write_text(json.dumps(before,indent=2))
    archive=WORK/'computer.tar.gz'
    files=[*sorted((ROOT/'server').glob('computer*.py')),ROOT/'server/sandbox.py',ROOT/'server/computer-openbox.xml',ROOT/'server/computer_requirements.txt']
    with tarfile.open(archive,'w:gz') as out:
        for file in files:out.add(file,arcname='server/'+file.name)
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    stamp=time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())
    # Only our application's existing staging directory is used.
    subprocess.run(SCP+[str(archive),'vps-jp:/opt/proactive-agent/.deploy/computer.tar.gz'],check=True)
    script=r'''set -eu
umask 077
BASE=/opt/proactive-agent/cloud-computer
test -d /opt/proactive-agent
test "$(sha256sum /opt/proactive-agent/.deploy/computer.tar.gz | cut -d' ' -f1)" = __HASH__
if systemctl is-active --quiet proactive-desktop.service; then
  echo 'Our desktop is already running; use the explicit update workflow so an active task is not interrupted.' >&2
  exit 1
fi
python3 - <<'PY'
import socket,pathlib
for port in (18193,18194,18195):
    with socket.socket() as s:
        try:s.bind(('127.0.0.1',port))
        except OSError:raise SystemExit('Reserved project port is occupied; refusing to affect another process')
PY
install -d -m 755 "$BASE" "$BASE/server" "$BASE/desktop-deps" "$BASE/browser-runtime"
install -d -m 700 "$BASE/bootstrap"
tar -xzf /opt/proactive-agent/.deploy/computer.tar.gz -C "$BASE"
chmod 644 "$BASE"/server/*
if ! id proactive-desktop >/dev/null 2>&1; then
  useradd --system --home-dir "$BASE/desktop-home" --shell /usr/sbin/nologin proactive-desktop
fi
install -d -m 700 -o proactive-desktop -g proactive-desktop "$BASE/desktop-home"
runuser -u proactive-desktop -- test -x /opt/proactive-agent
if [ ! -x "$BASE/desktop-venv/bin/python" ]; then python3 -m venv "$BASE/desktop-venv"; fi
nice -n 10 "$BASE/desktop-venv/bin/python" -m pip install --disable-pip-version-check -r "$BASE/server/computer_requirements.txt" > "$BASE/bootstrap/install.log" 2>&1
PLAYWRIGHT_BROWSERS_PATH="$BASE/browser-runtime" "$BASE/desktop-venv/bin/python" -m playwright install chromium > "$BASE/bootstrap/playwright.log" 2>&1
chmod -R a+rX "$BASE/desktop-venv" "$BASE/browser-runtime"
# Extract two small utilities privately. Do not install packages or run package triggers.
cd "$BASE/bootstrap"
apt-get download xclip xauth > utilities.log 2>&1
for file in ./*.deb; do dpkg-deb -x "$file" "$BASE/desktop-deps"; done
chmod -R a+rX "$BASE/desktop-deps"
python3 - <<'PY'
from pathlib import Path
import secrets,os,subprocess
base=Path('/opt/proactive-agent/cloud-computer')
browser=next(base.glob('browser-runtime/chromium-*/chrome-linux*/chrome'),None)
if browser is None:raise SystemExit('Chromium executable was not downloaded')
missing=subprocess.check_output(['ldd',str(browser)],text=True)
if 'not found' in missing:raise SystemExit('Private Chromium has missing libraries: '+missing)
control=base/'control.env'
if not control.exists():control.write_text('COMPUTER_CONTROL_TOKEN='+secrets.token_urlsafe(48)+'\n')
control.chmod(0o600)
(base/'desktop.env').write_text('HOME='+str(base/'desktop-home')+'\nCOMPUTER_BROWSER_EXECUTABLE='+str(browser)+'\nCOMPUTER_XCLIP_EXECUTABLE='+str(base/'desktop-deps/usr/bin/xclip')+'\nCOMPUTER_XAUTH_EXECUTABLE='+str(base/'desktop-deps/usr/bin/xauth')+'\nPLAYWRIGHT_BROWSERS_PATH='+str(base/'browser-runtime')+'\nANONYMIZED_TELEMETRY=false\nBROWSER_USE_CLOUD_API_KEY=\nBROWSER_USE_SETUP_LOGGING=false\n')
(base/'desktop.env').chmod(0o600)
PY
cat > /etc/systemd/system/proactive-desktop.service <<'UNIT'
[Unit]
Description=Proactive Agent private computer
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=proactive-desktop
Group=proactive-desktop
WorkingDirectory=/opt/proactive-agent/cloud-computer/server
EnvironmentFile=/opt/proactive-agent/cloud-computer/control.env
EnvironmentFile=/opt/proactive-agent/cloud-computer/desktop.env
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=PYTHONUNBUFFERED=1
Environment=XDG_RUNTIME_DIR=/run/proactive-desktop
RuntimeDirectory=proactive-desktop
RuntimeDirectoryMode=0700
ExecStart=/opt/proactive-agent/cloud-computer/desktop-venv/bin/python computer_runtime.py
Restart=on-failure
RestartSec=10
UMask=0077
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/proactive-agent/cloud-computer/desktop-home
PrivateTmp=true
PrivateDevices=true
PrivateIPC=true
ProtectProc=invisible
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK
CapabilityBoundingSet=
Nice=10
CPUQuota=150%
MemoryMax=1400M
TasksMax=256
TimeoutStopSec=20

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now proactive-desktop.service
for attempt in $(seq 1 60); do
  if python3 - <<'PY'
from pathlib import Path
import urllib.request,json
token=Path('/opt/proactive-agent/cloud-computer/control.env').read_text().strip().split('=',1)[1]
request=urllib.request.Request('http://127.0.0.1:18193/status',headers={'Authorization':'Bearer '+token})
try:
    with urllib.request.urlopen(request,timeout=2) as r:status=json.load(r)
    if not status.get('available'):raise ValueError('not ready')
except Exception:raise SystemExit(1)
Path('/opt/proactive-agent/cloud-computer/bootstrap/status.json').write_text(json.dumps(status))
PY
  then cat "$BASE/bootstrap/status.json"; exit 0; fi
  sleep 1
done
systemctl status proactive-desktop.service --no-pager --lines=10 >&2
journalctl -u proactive-desktop.service -n 65 --no-pager >&2
exit 1
'''.replace('__HASH__',digest)
    path=WORK/'deploy.sh';path.write_text(script,encoding='utf-8',newline='\n')
    subprocess.run(SCP+[str(path),'vps-jp:/opt/proactive-agent/.deploy/deploy-computer.sh'],check=True)
    try:
        output=remote('bash /opt/proactive-agent/.deploy/deploy-computer.sh');print(output)
    finally:
        after=json.loads(remote(SNAPSHOT));(WORK/'protected-after.json').write_text(json.dumps(after,indent=2))
        receipt={'timestamp':stamp,'protected_services_unchanged':before==after,'protected_services_checked':len(before),'nginx_changes':False,'global_package_changes':False,'existing_browser_changes':False,'files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
        (WORK/'deployment.json').write_text(json.dumps(receipt,indent=2))
        if before!=after:raise RuntimeError('Protected service snapshot changed; inspect recorded evidence')
    print('Independent desktop started; main API is not yet connected.')

if __name__=='__main__':main()
