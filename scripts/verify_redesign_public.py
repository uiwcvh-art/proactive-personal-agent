"""Read-only check of the real public encrypted API; no model or SMTP requests."""
from pathlib import Path
import os,sys,json,time,base64
import httpx
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'server'))
from security import CryptoBox,b64
asset=json.loads((ROOT/'android/app/src/main/assets/transport.json').read_text())
public=serialization.load_der_public_key(base64.b64decode(asset['public_key']))
token=(ROOT/'private/device-token').read_text().strip()
base='http://216.23.82.196/proactive-agent'
with httpx.Client(proxy='http://127.0.0.1:7890',timeout=40,trust_env=False) as client:
    health=client.get(base+'/health');health.raise_for_status();assert health.json()['version']=='1.1.0'
    result={'health':health.json(),'encrypted_endpoints':{},'external_model_calls':0,'smtp_calls':0}
    for path in ['/api/status','/api/bots','/api/tools','/api/routines']:
        key=os.urandom(32);nonce=os.urandom(12)
        raw=json.dumps({'timestamp':time.time(),'token':token,'method':'GET','path':path}).encode()
        packet={'key':b64(public.encrypt(key,padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None))),'nonce':b64(nonce),'data':b64(AESGCM(key).encrypt(nonce,raw,None))}
        response=client.post(base+'/api/secure',json=packet);response.raise_for_status()
        decoded=CryptoBox.frame(key,response.json());assert decoded['status']==200
        body=decoded['body'];entry={'status':decoded['status']}
        if path=='/api/status':entry.update(online=body['online'],small_ready=body['small_ready'],big_ready=body['big_ready'])
        if path=='/api/bots':entry['count']=len(body['bots']);assert entry['count']>=2
        if path=='/api/tools':entry['names']=[t['id'] for t in body['tools']];assert len(entry['names'])==6
        if path=='/api/routines':entry['count']=len(body['routines'])
        result['encrypted_endpoints'][path]=entry
(ROOT/'work/redesign-public-api.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
