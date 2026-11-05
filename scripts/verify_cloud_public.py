"""Read-only public encrypted API check. No model, SMTP or configuration mutations."""
from pathlib import Path
import os,sys,json,time,base64,argparse
import httpx
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'server'))
parser=argparse.ArgumentParser();parser.add_argument('--expect-version',default='1.4.0');parser.add_argument('--preflight',action='store_true');args=parser.parse_args()
from security import CryptoBox,b64
asset=json.loads((ROOT/'android/app/src/main/assets/transport.json').read_text())
public=serialization.load_der_public_key(base64.b64decode(asset['public_key']));token=(ROOT/'private/device-token').read_text().strip()
base='http://216.23.82.196/proactive-agent'
with httpx.Client(proxy='http://127.0.0.1:7890',timeout=40,trust_env=False) as client:
    health=client.get(base+'/health');health.raise_for_status();assert health.json()['version']==args.expect_version
    assert client.get(base+'/api/computer/status').status_code==401
    result={'health':health.json(),'unauthenticated_computer_status':401,'encrypted_endpoints':{},'paid_model_calls':0,'smtp_calls':0}
    paths=['/api/status','/api/computer/status'] if args.preflight else ['/api/status','/api/computer/status','/api/computer/frame','/api/computer/files','/api/files','/api/bots']
    for path in paths:
        key=os.urandom(32);nonce=os.urandom(12);raw=json.dumps({'timestamp':time.time(),'token':token,'method':'GET','path':path}).encode()
        packet={'key':b64(public.encrypt(key,padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None))),'nonce':b64(nonce),'data':b64(AESGCM(key).encrypt(nonce,raw,None))}
        response=client.post(base+'/api/secure',json=packet);response.raise_for_status();decoded=CryptoBox.frame(key,response.json());assert decoded['status']==200
        body=decoded['body'];entry={'status':200}
        if path=='/api/status':
            entry.update(online=body['online'],small_ready=body['small_ready'],big_ready=body['big_ready'],execution_active=bool(body.get('execution')))
            if args.preflight:assert not body.get('execution'),'Do not interrupt an active Harness task'
        if path=='/api/computer/status':
            assert body['available'];job_state=(body.get('job') or {}).get('state')
            entry.update(available=True,engine=body['engine'],model_ready=body['model_ready'],width=body['width'],height=body['height'],job_state=job_state)
            if args.preflight:assert job_state not in ('queued','running','paused','waiting_user'),'Do not interrupt an active cloud-computer job'
        if path=='/api/bots':
            assert body['bots'] and all(type(b['hidden']) is bool and type(b['pinned']) is bool for b in body['bots'])
            entry.update(count=len(body['bots']),placement_metadata_present=True)
        if path=='/api/computer/frame':
            data=base64.b64decode(body['image']);assert data.startswith(b'\xff\xd8') and len(data)>10000
            (ROOT/'work/cloud-computer/final-frame.jpg').write_bytes(data);entry.update(bytes=len(data),width=body['width'],height=body['height'])
        if path.endswith('/files'):entry['count']=len(body['files'])
        result['encrypted_endpoints'][path]=entry
target='preflight-1.4.json' if args.preflight else 'public-api-1.4.json'
(ROOT/'work/cloud-computer'/target).write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
