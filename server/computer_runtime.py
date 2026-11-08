"""Entry point for proactive-desktop.service, before loading the agent/browser SDK."""
import os,sys,subprocess
os.environ.setdefault('MALLOC_ARENA_MAX','2')
os.environ.setdefault('MALLOC_TRIM_THRESHOLD_','65536')
from pathlib import Path
from computer_security import restrict_desktop

def main():
    if sys.platform!='linux' or os.geteuid()==0:raise RuntimeError('Run as the dedicated unprivileged desktop account')
    home=Path(os.environ['HOME']).resolve()
    runtime=Path(os.environ['XDG_RUNTIME_DIR']).resolve()
    home.mkdir(parents=True,exist_ok=True);runtime.mkdir(parents=True,exist_ok=True)
    root=Path(__file__).resolve().parent.parent
    restrict_desktop([Path(__file__).parent,root/'desktop-venv',root/'browser-runtime',root/'desktop-deps'],[home,runtime,Path('/tmp'),Path('/dev/shm')])
    if os.getenv('COMPUTER_STARTUP_DIAGNOSTIC')=='1':
        probe=subprocess.run(['/usr/bin/unshare','--user','--map-root-user','true'],text=True,capture_output=True)
        print('Own user namespace probe:',probe.returncode,probe.stderr.strip(),file=sys.stderr,flush=True)
    import uvicorn
    uvicorn.run('computer_desktop:app',host='127.0.0.1',port=18193,log_level='warning',access_log=False)

if __name__=='__main__':main()
