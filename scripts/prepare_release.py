from pathlib import Path
import base64,hashlib,json,secrets,subprocess,sys,os
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'server'))
from security import CryptoBox
private=ROOT/'private'
private.mkdir(exist_ok=True)
crypto=CryptoBox(private)
token_path=private/'device-token'
if not token_path.exists(): token_path.write_text(secrets.token_urlsafe(36),encoding='utf-8')
asset=ROOT/'android/app/src/main/assets'
asset.mkdir(parents=True,exist_ok=True)
(asset/'transport.json').write_text(json.dumps({**crypto.public(),'fingerprint':hashlib.sha256(crypto.public_der).hexdigest()},indent=2),encoding='utf-8')
password_path=private/'keystore-password'
if not password_path.exists(): password_path.write_text(secrets.token_urlsafe(30),encoding='utf-8')
password=password_path.read_text('utf-8').strip()
keystore=private/'release.jks'
if not keystore.exists():
    env={**os.environ,'AGENT_STOREPASS':password}
    subprocess.run([r'C:\Program Files\Java\jdk-22\bin\keytool.exe','-genkeypair','-keystore',str(keystore),'-alias','proactive','-storepass:env','AGENT_STOREPASS','-keypass:env','AGENT_STOREPASS','-keyalg','RSA','-keysize','3072','-validity','10000','-dname','CN=Proactive Personal Agent, O=Bubuki, C=JP'],env=env,check=True)
(ROOT/'android/local.properties').write_text('sdk.dir=C:/Users/uiwcv/AppData/Local/Android/Sdk\n',encoding='utf-8')
print('Release signing and pinned public transport key prepared. No secrets embedded.')
