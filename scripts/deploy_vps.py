"""Narrow host-only deployment. No Docker, network, proxy or firewall operations."""
from pathlib import Path
import argparse,json,os,subprocess,tarfile,tempfile
ROOT=Path(__file__).resolve().parents[1]
SSH_CONFIG=r'C:\Users\uiwcv\Documents\Codex\2026-10-22\d-bubuki-vps-server-info-md\outputs\private-access\ssh_config'
SSH=['ssh','-F',SSH_CONFIG,'-J','vps-us-198','-o','BatchMode=yes','-o','ConnectTimeout=30','-o','ConnectionAttempts=2','-o','ServerAliveInterval=15','vps-jp']
SCP=['scp','-F',SSH_CONFIG,'-J','vps-us-198','-o','BatchMode=yes','-o','ConnectTimeout=30','-o','ConnectionAttempts=2']

REMOTE=r'''set -eu
BASE=/opt/proactive-agent
install -d -m 755 "$BASE" "$BASE/server" "$BASE/dist"
install -d -m 700 "$BASE/bootstrap"
tar -xzf "$BASE/.deploy/incoming.tar.gz" -C "$BASE"
find "$BASE/server" -maxdepth 1 -type f -exec chmod 644 {} +
if [ -f "$BASE/dist/proactive-agent.apk" ]; then chmod 644 "$BASE/dist/proactive-agent.apk"; fi
if ! id proactive-agent >/dev/null 2>&1; then useradd --system --home-dir "$BASE/data" --shell /usr/sbin/nologin proactive-agent; fi
install -d -m 700 -o proactive-agent -g proactive-agent "$BASE/data"
for secret in transport-key.pem device-token; do
  if [ ! -f "$BASE/data/$secret" ]; then install -m 600 -o proactive-agent -g proactive-agent "$BASE/bootstrap/$secret" "$BASE/data/$secret"; fi
done
rm -f "$BASE/bootstrap/transport-key.pem" "$BASE/bootstrap/device-token"
if [ ! -x "$BASE/venv/bin/python" ]; then python3 -m venv "$BASE/venv"; fi
"$BASE/venv/bin/python" -m pip install --disable-pip-version-check -r "$BASE/server/requirements.txt" > "$BASE/install.log" 2>&1
cat > /etc/systemd/system/proactive-agent.service <<'UNIT'
[Unit]
Description=Proactive Personal Agent (isolated host venv)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=proactive-agent
Group=proactive-agent
WorkingDirectory=/opt/proactive-agent/server
Environment=AGENT_DATA_DIR=/opt/proactive-agent/data
Environment=AGENT_APK=/opt/proactive-agent/dist/proactive-agent.apk
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/proactive-agent/venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 18190 --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1 --root-path /proactive-agent --no-access-log
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/proactive-agent/data
PrivateTmp=true
PrivateDevices=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
CapabilityBoundingSet=
MemoryMax=768M
CPUQuota=100%
TasksMax=96

[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/nginx/snippets/proactive-agent.conf <<'NGINX'
# Owned only by Proactive Agent. Included inside the existing default :80 server.
location = /proactive-agent { return 301 /proactive-agent/; }
location ^~ /proactive-agent/ {
    proxy_pass http://127.0.0.1:18190/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 180s;
    proxy_send_timeout 180s;
    proxy_buffering off;
    client_max_body_size 6m;
    access_log off;
}
NGINX
python3 - <<'PY'
from pathlib import Path
import re,json
default=Path('/etc/nginx/sites-available/default')
candidates=[p for p in Path('/etc/nginx/conf.d').glob('*.conf')
            if re.search(r'(?m)^\s*listen\s+80\s*;',p.read_text())
            and re.search(r'(?m)^\s*server_name\s+216\.23\.82\.196\s*;',p.read_text())]
if len(candidates)>1: raise SystemExit('Ambiguous IP virtual host; refusing routing changes')
target=candidates[0] if candidates else default
text=target.read_text()
line='    include /etc/nginx/snippets/proactive-agent.conf;\n'
rollback={}
if 'include /etc/nginx/snippets/proactive-agent.conf;' not in text:
    matches=list(re.finditer(r'(?m)^\s*server\s*\{',text))
    if len(matches)!=1: raise SystemExit('Expected one unambiguous HTTP server block')
    backup=Path('/opt/proactive-agent/nginx-'+target.name+'.before')
    if not backup.exists(): backup.write_text(text)
    rollback[str(target)]=text
    point=matches[0].end()
    target.write_text(text[:point]+'\n'+line+text[point:])
    print('Added only independent proactive-agent location include to the IP HTTP virtual host')
Path('/opt/proactive-agent/.deploy/nginx-rollback.json').write_text(json.dumps(rollback))
PY
if ! nginx -t; then
  python3 -c 'import json; from pathlib import Path; [(Path(p).write_text(t)) for p,t in json.loads(Path("/opt/proactive-agent/.deploy/nginx-rollback.json").read_text()).items()]'
  echo 'nginx validation failed; this deployment changes restored' >&2
  exit 1
fi
systemctl daemon-reload
systemctl enable --now proactive-agent.service
systemctl restart proactive-agent.service
systemctl reload nginx.service
sleep 2
systemctl is-active proactive-agent.service
curl -fsS http://127.0.0.1:18190/health
'''

def run(args,**kw):return subprocess.run(args,check=True,**kw)
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--server-only',action='store_true');args=parser.parse_args()
    work=ROOT/'work';work.mkdir(exist_ok=True)
    archive=work/'deploy.tar.gz'
    with tarfile.open(archive,'w:gz') as tar:
        for file in sorted((ROOT/'server').glob('*.py')):tar.add(file,arcname='server/'+file.name)
        tar.add(ROOT/'server/requirements.txt',arcname='server/requirements.txt')
        tar.add(ROOT/'private/transport-key.pem',arcname='bootstrap/transport-key.pem')
        tar.add(ROOT/'private/device-token',arcname='bootstrap/device-token')
        if not args.server_only:tar.add(ROOT/'dist/proactive-agent.apk',arcname='dist/proactive-agent.apk')
    script=work/'deploy-remote.sh';script.write_text(REMOTE,encoding='utf-8',newline='\n')
    run(SSH+['install -d -m 755 /opt/proactive-agent; install -d -m 700 /opt/proactive-agent/.deploy; rm -f /opt/proactive-agent/incoming.tar.gz'])
    run(SCP+[str(archive),'vps-jp:/opt/proactive-agent/.deploy/incoming.tar.gz'])
    run(SCP+[str(script),'vps-jp:/opt/proactive-agent/deploy-remote.sh'])
    run(SSH+['bash /opt/proactive-agent/deploy-remote.sh'])
    # The archive contains bootstrap credentials; remove it after successful installation.
    run(SSH+['rm -f /opt/proactive-agent/.deploy/incoming.tar.gz'])
    print('Deployed host venv service and isolated nginx path. Protected services were not changed.')

if __name__=='__main__':main()
