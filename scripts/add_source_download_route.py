"""Add one exact public artifact route inside this project's owned nginx snippet."""
from pathlib import Path
import hashlib,json,subprocess
from deploy_vps import SSH
from deploy_identity_1_6 import PROTECTED
ROOT=Path(__file__).resolve().parents[1];WORK=ROOT/'work'
def remote(command):return subprocess.check_output(SSH+[command],text=True)
before=json.loads(remote(PROTECTED))
code=r"""python3 - <<'PY'
from pathlib import Path
import hashlib,json,subprocess
path=Path('/etc/nginx/snippets/proactive-agent.conf')
old=path.read_bytes()
assert old.startswith(b'# Owned only by Proactive Agent.')
marker=b'location = /proactive-agent/download/proactive-personal-agent-source.zip'
addition=b'''
# Exact public source artifact; all existing proxy rules above remain byte-for-byte.
location = /proactive-agent/download/proactive-personal-agent-source.zip {
    alias /opt/proactive-agent/dist/proactive-personal-agent-source.zip;
    default_type application/zip;
    add_header Content-Disposition 'attachment; filename="proactive-personal-agent-source.zip"';
    access_log off;
}
'''
changed=marker not in old
if changed:
    Path('/opt/proactive-agent/.deploy/nginx-before-source-1.7.conf').write_bytes(old)
    new=old+addition
    assert new[:len(old)]==old
    path.write_bytes(new)
    test=subprocess.run(['nginx','-t'],capture_output=True,text=True)
    if test.returncode:
        path.write_bytes(old)
        raise SystemExit('Owned snippet validation failed and was restored: '+test.stderr)
    reload=subprocess.run(['systemctl','reload','nginx'],capture_output=True,text=True)
    if reload.returncode:
        path.write_bytes(old)
        raise SystemExit('nginx reload failed; owned snippet restored: '+reload.stderr)
else:
    subprocess.run(['nginx','-t'],check=True,capture_output=True)
print(json.dumps({'path':str(path),'before_sha256':hashlib.sha256(old).hexdigest(),'after_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'changed':changed,'nginx_config_valid':True,'nginx_graceful_reload':changed,'existing_proxy_rules_preserved':True}))
PY"""
result=json.loads(remote(code))
after=json.loads(remote(PROTECTED));assert before==after,'Protected service state changed'
result['protected_services_unchanged']=True;result['protected_services_checked']=len(before)
(WORK/'source-download-route-1.7.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
