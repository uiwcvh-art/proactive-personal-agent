"""Import licensed upstream source and verify binary inputs against its Git tree."""
from pathlib import Path
import hashlib,json,shutil,subprocess,zipfile
ROOT=Path(__file__).resolve().parents[1]
source=ROOT/'work/references/Mika'
commit=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
assert commit=='e9daf53d6834b236e713c6a62c3a095338eb7f95'
receipt=json.loads((ROOT/'work/references/mika-reference.json').read_text())
apk=Path(receipt['apk'])
assert hashlib.file_digest(apk.open('rb'),'sha256').hexdigest()==receipt['upstream_sha256']
library=ROOT/'android/mika-local'
core=ROOT/'android/mika-core'
assert not library.exists() and not core.exists(),'Refuse to overwrite an existing integration'
shutil.copytree(source/'app/src/main',library/'src/main')
shutil.copytree(source/'core/src/main',core/'src/main')
shutil.copytree(source/'core/src/testFixtures',core/'src/testFixtures')
for target in [library,core]:
    for name in ['LICENSE','NOTICE']:shutil.copy2(source/name,target/name)
shutil.copytree(source/'licenses',library/'licenses')
shutil.copytree(source/'tools/sandbox',library/'upstream-sandbox-build')
shutil.copytree(source/'gradle',ROOT/'android/gradle',dirs_exist_ok=True)

tree=subprocess.check_output(['git','-C',str(source),'ls-tree','-r','HEAD'],text=True)
binary_inputs=[]
with zipfile.ZipFile(apk) as bundle:
    for line in tree.splitlines():
        meta,path=line.split('\t',1)
        if not path.startswith(('app/src/main/jniLibs/','app/src/main/assets/runtime/')):continue
        relative=path.removeprefix('app/src/main/')
        member=relative.replace('jniLibs/','lib/',1) if relative.startswith('jniLibs/') else relative
        data=bundle.read(member)
        expected=meta.split()[2]
        actual=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
        if actual!=expected:raise RuntimeError('Release binary differs from pinned source tree: '+path)
        target=library/'src/main'/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        binary_inputs.append({'path':relative,'git_blob':actual,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)})

provenance={'repository':'https://github.com/wangzan101/Mika','commit':commit,'license':'Apache-2.0; subprocess runtime licenses in mika-local/licenses','official_apk_sha256':receipt['apk_sha256'],'binary_inputs':binary_inputs}
(library/'UPSTREAM.json').write_text(json.dumps(provenance,indent=2),encoding='utf-8')
(core/'UPSTREAM.json').write_text(json.dumps({k:v for k,v in provenance.items() if k!='binary_inputs'},indent=2),encoding='utf-8')
print(json.dumps({'imported_source_files':sum(1 for base in [library,core] for path in base.rglob('*') if path.is_file()),'binary_inputs':binary_inputs},indent=2))
