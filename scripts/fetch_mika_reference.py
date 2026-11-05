"""Download an immutable upstream source archive and its official release APK."""
from pathlib import Path, PurePosixPath
import hashlib, json, zipfile
import requests

ROOT=Path(__file__).resolve().parents[1]/'work/references'
ROOT.mkdir(parents=True,exist_ok=True)
session=requests.Session()
session.trust_env=False
session.proxies={'http':'http://127.0.0.1:7890','https':'http://127.0.0.1:7890'}
session.headers['User-Agent']='PulseBot-reference-audit'

def download(url, target, expected=None):
    if target.exists() and (expected is None or hashlib.file_digest(target.open('rb'),'sha256').hexdigest()==expected):
        return hashlib.file_digest(target.open('rb'),'sha256').hexdigest()
    temporary=target.with_suffix(target.suffix+'.partial')
    offset=temporary.stat().st_size if temporary.exists() else 0
    headers={'Accept-Encoding':'identity'}
    if offset:headers['Range']=f'bytes={offset}-'
    with session.get(url,headers=headers,stream=True,timeout=(15,60)) as response:
        response.raise_for_status()
        with temporary.open('ab' if offset and response.status_code==206 else 'wb') as output:
            for data in response.iter_content(1024*512):output.write(data)
    digest=hashlib.file_digest(temporary.open('rb'),'sha256').hexdigest()
    if expected and digest!=expected:raise RuntimeError('Upstream SHA-256 mismatch')
    temporary.replace(target)
    print(target.name,target.stat().st_size,digest,flush=True)
    return digest

commit='e9daf53d6834b236e713c6a62c3a095338eb7f95'
source=ROOT/'Mika'
source_digest=None
if not (source/'app/src/main/AndroidManifest.xml').exists():
    archive=ROOT/f'Mika-{commit[:7]}.zip'
    source_digest=download(f'https://codeload.github.com/wangzan101/Mika/zip/{commit}',archive)
    source=ROOT/'Mika-source'
    with zipfile.ZipFile(archive) as bundle:
        for item in bundle.infolist():
            parts=PurePosixPath(item.filename).parts
            if '..' in parts or PurePosixPath(item.filename).is_absolute():raise RuntimeError('Invalid ZIP path')
            if len(parts)<2 or item.is_dir():continue
            target=source.joinpath(*parts[1:])
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(bundle.read(item))
print('Source ready',source,flush=True)

release=session.get('https://api.github.com/repos/wangzan101/Mika/releases/latest',timeout=(15,45))
release.raise_for_status();release=release.json()
asset=next(item for item in release['assets'] if item['name'].endswith('.apk') and 'arm64' not in item['name'])
expected=asset.get('digest','').removeprefix('sha256:') or None
apk=ROOT/asset['name'];digest=download(asset['browser_download_url'],apk,expected)
gpl=next((item for item in release['assets'] if 'gpl' in item['name'] and item['name'].endswith('.tar.gz')),None)
gpl_record=None
if gpl:
    gpl_path=ROOT/gpl['name'];gpl_expected=gpl.get('digest','').removeprefix('sha256:') or None
    gpl_record={'file':str(gpl_path),'sha256':download(gpl['browser_download_url'],gpl_path,gpl_expected),'url':gpl['browser_download_url']}
receipt={'source_commit':commit,'source_sha256':source_digest,'source_directory':str(source),'release':release['tag_name'],'release_url':release['html_url'],'apk':str(apk),'apk_bytes':apk.stat().st_size,'apk_sha256':digest,'upstream_sha256':expected}
receipt['gpl_source']=gpl_record
(ROOT/'mika-reference.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
print(json.dumps(receipt,indent=2),flush=True)
