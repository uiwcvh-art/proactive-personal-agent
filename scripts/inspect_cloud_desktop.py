"""Read-only inventory scoped to prerequisites for this project's own desktop."""
import json,subprocess
from pathlib import Path
from deploy_vps import SSH

command=r'''python3 - <<'PY'
import os,shutil,subprocess,json
def read(args):
    p=subprocess.run(args,text=True,capture_output=True)
    return {'code':p.returncode,'stdout':p.stdout.strip(),'stderr':p.stderr.strip()[:300]}
result={
 'os':read(['uname','-a']),
 'memory':read(['free','-m']),
 'disk':read(['df','-h','/opt/proactive-agent']),
 'tools':{name:shutil.which(name) for name in ['Xvfb','openbox','xterm','xdotool','xclip','dbus-run-session','ffmpeg','xsetroot']},
 'app_user':read(['id','proactive-agent']),
 'desktop_user_exists':read(['id','proactive-desktop'])['code']==0,
 'reserved_ports':read(['ss','-ltnH','sport >= :18190 and sport <= :18199']),
 'display_91_exists':os.path.exists('/tmp/.X91-lock') or os.path.exists('/tmp/.X11-unix/X91'),
 'python':read(['/opt/proactive-agent/venv/bin/python','--version']),
}
print(json.dumps(result))
PY'''
result=json.loads(subprocess.check_output(SSH+[command],text=True,timeout=60))
target=Path(__file__).resolve().parents[1]/'work/cloud-desktop-inventory.json'
target.write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
