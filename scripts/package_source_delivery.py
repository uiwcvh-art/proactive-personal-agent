"""Reproducible source handoff with an allowlist and a private-secret exclusion gate."""
from pathlib import Path
import zipfile,hashlib,json,re
ROOT=Path(__file__).resolve().parents[1]
target=ROOT/'dist/proactive-personal-agent-source.zip'
version=re.search(r"versionName\s+'([^']+)'",(ROOT/'android/app/build.gradle').read_text('utf-8'))[1]
files=[]
for folder in ['android/app','android/gradle','scripts','desktop']:
    for p in (ROOT/folder).rglob('*'):
        rel=p.relative_to(ROOT)
        if not p.is_file() or any(part in {'build','.gradle','.kotlin','.cxx','__pycache__','.git'} for part in rel.parts):continue
        if p.suffix.lower() in {'.jks','.keystore','.apk','.pyc'} or p.name=='local.properties':continue
        files.append(p)
files.extend(p for p in (ROOT/'android').iterdir() if p.is_file() and p.name not in {'local.properties'})
for sub in ['server','server/core','server/transport','server/plugins','server/cloud_os','server/tests']:
    files.extend(p for p in (ROOT/sub).glob('*.py') if p.is_file())
for req in ['server/requirements.txt','server/computer_requirements.txt','server/computer-openbox.xml']:
    if (ROOT/req).exists():files.append(ROOT/req)
for name in ['README.md','THIRD_PARTY_NOTICES.md','CURRENT_PRODUCT_DIRECTION.md','MOTION_AND_PARITY_PLAN.md']:
    if (ROOT/name).exists():files.append(ROOT/name)
banned=[(ROOT/'private/device-token').read_bytes().strip(),(ROOT/'private/keystore-password').read_bytes().strip()]
with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED,compresslevel=5) as archive:
    for p in sorted(set(files)):
        data=p.read_bytes()
        if any(s and s in data for s in banned) or re.search(br'-----BEGIN [A-Z ]*PRIVATE KEY-----\r?\n[A-Za-z0-9+/=]',data):raise RuntimeError('Private material detected in source candidate: '+str(p.relative_to(ROOT)))
        archive.writestr(p.relative_to(ROOT).as_posix(),data)
    archive.writestr('SOURCE_README.txt',f'Pulse Bot {version} source. Own Android app and Root workspace; selected Mika Apache-2.0 source attribution is retained. Full Mika/native runtimes are not included in this APK. Build with JDK 22, Android SDK 35 and Gradle 8.13; supply your own signing keystore and AGENT_KEYSTORE_PASSWORD. The public transport key is intentionally bundled; pairing tokens, private keys, user data and signing keys are excluded. See CURRENT_PRODUCT_DIRECTION.md and THIRD_PARTY_NOTICES.md for scope and attribution.\n')
with target.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
receipt={'file':str(target),'bytes':target.stat().st_size,'sha256':digest,'files':len(set(files))+1,'private_secret_gate':'passed'}
(ROOT/'work/source-delivery.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt,indent=2))
