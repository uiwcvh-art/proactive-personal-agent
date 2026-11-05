"""Read back only this application's desktop proof files and final runtime metadata."""
from pathlib import Path
import json,subprocess,tarfile
from deploy_cloud_computer import remote,SNAPSHOT,ROOT,WORK
from deploy_vps import SCP
command=r'''/opt/proactive-agent/cloud-computer/desktop-venv/bin/python - <<'PY'
from pathlib import Path
import importlib.metadata,json,subprocess,tarfile
base=Path('/opt/proactive-agent/cloud-computer');out=base/'bootstrap'
group=subprocess.check_output(['systemctl','show','proactive-desktop.service','-p','ControlGroup','--value'],text=True).strip()
pids=(Path('/sys/fs/cgroup')/group.lstrip('/')/'cgroup.procs').read_text().split()
browser=None
for pid in pids:
    try:args=(Path('/proc')/pid/'cmdline').read_bytes().decode().split('\0')
    except FileNotFoundError:continue
    if '--user-data-dir='+str(base/'desktop-home/browser-profile') in args and not any(a.startswith('--type=') for a in args):browser={'pid':int(pid),'dedicated_profile':True,'chromium_sandbox_retained':'--no-sandbox' not in args,'debug_loopback':'--remote-debugging-address=127.0.0.1' in args};break
assert browser and browser['chromium_sandbox_retained'] and browser['debug_loopback']
runtime={'browser':browser,'versions':{p:importlib.metadata.version(p) for p in ['browser-use','playwright','fastapi','mss']},'services':{name:subprocess.check_output(['systemctl','show',name,'-p','ActiveState','-p','SubState','-p','MainPID','-p','MemoryCurrent'],text=True).strip() for name in ['proactive-agent.service','proactive-desktop.service']},'owned_listeners':subprocess.check_output(['ss','-ltnH','sport >= :18190 and sport <= :18194'],text=True).strip()}
(out/'runtime-proof.json').write_text(json.dumps(runtime,indent=2))
with tarfile.open(out/'proofs.tar.gz','w:gz') as t:
    for name in ['acceptance.json','workspace-acceptance.json','input-proof.jpg','browser-proof.jpg','runtime-proof.json']:t.add(out/name,arcname=name)
print(json.dumps(runtime))
PY'''
print(remote(command))
subprocess.run(SCP+['vps-jp:/opt/proactive-agent/cloud-computer/bootstrap/proofs.tar.gz',str(WORK/'proofs.tar.gz')],check=True)
with tarfile.open(WORK/'proofs.tar.gz') as t:
    for item in t:
        if not item.isfile() or item.name not in {'acceptance.json','workspace-acceptance.json','input-proof.jpg','browser-proof.jpg','runtime-proof.json'}:raise RuntimeError('Unexpected proof archive member')
        (WORK/item.name).write_bytes(t.extractfile(item).read())
protected=json.loads(remote(SNAPSHOT));(WORK/'protected-final.json').write_text(json.dumps(protected,indent=2))
initial=json.loads((WORK/'protected-before.json').read_text())
print(json.dumps({'protected_services_checked':len(protected),'initial_equals_final':initial==protected}))
