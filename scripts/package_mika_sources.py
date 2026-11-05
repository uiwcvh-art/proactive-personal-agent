"""Accompany bundled PRoot/Alpine binaries with their exact corresponding source."""
from pathlib import Path
import hashlib,importlib.util,json,shutil,tarfile,time
import requests,urllib.error

ROOT=Path(__file__).resolve().parents[1]
UPSTREAM=ROOT/'android/mika-local/upstream-sandbox-build'
WORK=ROOT/'work/mika-corresponding-source';WORK.mkdir(parents=True,exist_ok=True)
CACHE=WORK/'cache';CACHE.mkdir(exist_ok=True)
OUT=WORK/'mika-corresponding-source';OUT.mkdir(exist_ok=True)
session=requests.Session();session.trust_env=False
session.proxies={'http':'http://127.0.0.1:7890','https':'http://127.0.0.1:7890'}
def get(url):
    file=CACHE/hashlib.sha256(url.encode()).hexdigest()
    if file.exists():return file.read_bytes()
    for attempt in range(3):
        try:
            with session.get(url,timeout=(15,55),stream=True) as r:
                if r.status_code==404:raise urllib.error.HTTPError(url,404,'Not found',{},None)
                r.raise_for_status()
                partial=file.with_suffix('.partial')
                with partial.open('wb') as output:
                    for chunk in r.iter_content(262144):output.write(chunk)
                partial.replace(file);return file.read_bytes()
        except requests.RequestException:
            if attempt==2:raise
            time.sleep(1)
def main():
    for line in (UPSTREAM/'sources.lock').read_text('utf-8').splitlines():
        if not line.strip() or line.startswith('#'):continue
        name,version,url,want=line.split();data=get(url)
        if hashlib.sha256(data).hexdigest()!=want:raise RuntimeError('Pinned native source checksum mismatch: '+name)
        (OUT/f'{name}-{version}.tar.gz').write_bytes(data);print('Verified native source:',name,flush=True)
    for name in ['patches','runtime','build-native.sh','build-runtime.py','runtime-sources.py','sources.lock','README.md']:
        source=UPSTREAM/name;target=OUT/name
        if source.is_dir():shutil.copytree(source,target,dirs_exist_ok=True)
        else:shutil.copy2(source,target)
    shutil.copytree(ROOT/'android/mika-local/licenses',OUT/'licenses',dirs_exist_ok=True)
    spec=importlib.util.spec_from_file_location('upstream_sources',UPSTREAM/'runtime-sources.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.MANIFEST=str(ROOT/'android/mika-local/src/main/assets/runtime/packages.json');module.get=get
    import sys
    old=sys.argv;sys.argv=['runtime-sources.py',str(OUT/'runtime')]
    try:module.main()
    finally:sys.argv=old
    shutil.copy2(module.MANIFEST,OUT/'runtime/packages.json')
    shutil.copy2(ROOT/'android/mika-local/UPSTREAM.json',OUT/'UPSTREAM.json')
    (OUT/'PULSE-NOTICE.txt').write_text('Bundled native binaries are unchanged from the verified Mika v0.1.33 release. Exact source, Alpine recipes, patches, licenses and build instructions accompany them here. Mika Android/core source and Pulse modifications are distributed in the separate proactive-personal-agent-source.zip.\n','utf-8')
    target=ROOT/'dist/mika-corresponding-source.tar.gz'
    with tarfile.open(target,'w:gz',compresslevel=4) as archive:archive.add(OUT,arcname=OUT.name)
    with target.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
    receipt={'file':str(target),'bytes':target.stat().st_size,'sha256':digest,'runtime_sources':str(OUT/'runtime/SOURCES.txt')}
    (WORK/'receipt.json').write_text(json.dumps(receipt,indent=2))
    print(json.dumps(receipt,indent=2),flush=True)
if __name__=='__main__':main()
