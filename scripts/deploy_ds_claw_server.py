"""Safely deploy DeepSeek Claw server updates to vps-jp while preserving state.json (Baike persona) and all protected services."""
from pathlib import Path
import hashlib, json, subprocess, tarfile, time
from deploy_vps import SSH, SCP

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work"
WORK.mkdir(exist_ok=True)


def remote(command: str) -> str:
    return subprocess.check_output(SSH + [command], text=True, encoding="utf-8")


PROTECTED = r"""python3 - <<'PY'
import subprocess, json
lines = subprocess.check_output(['systemctl','list-units','--all','--type=service','--no-legend','--plain'], text=True)
units = [line.split()[0] for line in lines.splitlines() if any(t in line.lower() for t in ('bb-jp-','claude','browser','docker','sing-box'))]
result = {u: subprocess.check_output(['systemctl','show',u,'-p','ActiveState','-p','SubState','-p','MainPID'], text=True) for u in units}
print(json.dumps(result, sort_keys=True))
PY"""


def main():
    archive = WORK / "ds-claw-server.tar.gz"
    files = sorted(
        {
            *(ROOT / "server").glob("*.py"),
            *(ROOT / "server/core").rglob("*.py"),
            *(ROOT / "server/transport").rglob("*.py"),
            *(ROOT / "server/plugins").rglob("*.py"),
            *(ROOT / "server/cloud_os").rglob("*.py"),
            *(ROOT / "server/tests").rglob("*.py"),
            *(ROOT / "server/memory_vault_seed").rglob("*.md"),
            *(ROOT / "desktop").rglob("*.py"),
            ROOT / "ARCHITECTURE_AND_OPERATIONS.md",
            ROOT / "server/requirements.txt",
            ROOT / "server/computer_requirements.txt",
            ROOT / "server/computer-openbox.xml",
        }
    )
    with tarfile.open(archive, "w:gz") as out:
        for p in files:
            out.add(p, arcname=p.relative_to(ROOT).as_posix())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())

    before = json.loads(remote(PROTECTED))
    subprocess.run(
        SCP + [str(archive), "vps-jp:/opt/proactive-agent/.deploy/ds-claw-server.tar.gz"],
        check=True,
    )

    script = f"""set -eu
umask 077
cd /opt/proactive-agent
test "$(sha256sum .deploy/ds-claw-server.tar.gz | cut -d' ' -f1)" = {digest}
tar -czf .deploy/server-before-ds-claw-{stamp}.tgz server
cp -p data/state.json .deploy/state-before-ds-claw-{stamp}.json
tar -xzf .deploy/ds-claw-server.tar.gz -C /opt/proactive-agent
chmod -R a+rX server
venv/bin/python - <<'PY'
from pathlib import Path
for p in Path('server').rglob('*.py'):
    compile(p.read_text('utf-8'), str(p), 'exec')
PY
systemctl restart proactive-agent.service
for attempt in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS http://127.0.0.1:18190/health > .deploy/ds-claw-health.json; then
    cat .deploy/ds-claw-health.json
    exit 0
  fi
  sleep 1
done
tar -xzf .deploy/server-before-ds-claw-{stamp}.tgz -C /opt/proactive-agent
systemctl restart proactive-agent.service
exit 1
"""
    sh_path = WORK / "deploy-ds-claw.sh"
    sh_path.write_text(script, encoding="utf-8", newline="\n")
    subprocess.run(
        SCP + [str(sh_path), "vps-jp:/opt/proactive-agent/.deploy/deploy-ds-claw.sh"],
        check=True,
    )
    health_raw = remote("bash /opt/proactive-agent/.deploy/deploy-ds-claw.sh")
    after = json.loads(remote(PROTECTED))
    assert before == after, "Protected services changed!"
    print("DEPLOY_OK:", health_raw.strip())


if __name__ == "__main__":
    main()
