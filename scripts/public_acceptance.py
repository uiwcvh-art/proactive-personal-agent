from pathlib import Path
import base64,json,os,time,hashlib
import httpx
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
ROOT=Path(__file__).resolve().parents[1]
BASE='http://216.23.82.196/proactive-agent'
def b64(x):return base64.b64encode(x).decode()
public=json.loads((ROOT/'android/app/src/main/assets/transport.json').read_text())['public_key']
public=serialization.load_der_public_key(base64.b64decode(public))
client=httpx.Client(proxy='http://127.0.0.1:7890',timeout=90,trust_env=False)
def request(method,path,body=None,query=None):
    key=os.urandom(32);nonce=os.urandom(12)
    raw=json.dumps({'method':method,'path':path,'body':body,'query':query,'timestamp':time.time(),'token':(ROOT/'private/device-token').read_text().strip()}).encode()
    packet={'key':b64(public.encrypt(key,padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None))),'nonce':b64(nonce),'data':b64(AESGCM(key).encrypt(nonce,raw,None))}
    r=client.post(BASE+'/api/secure',json=packet);r.raise_for_status();data=r.json()
    result=json.loads(AESGCM(key).decrypt(base64.b64decode(data['nonce']),base64.b64decode(data['data']),None))
    return result['status'],result['body']

if __name__=='__main__':
    checks={}
    checks['public_health_200']=client.get(BASE+'/health').status_code==200
    checks['unauthenticated_config_rejected']=client.get(BASE+'/api/config').status_code==401
    status,body=request('GET','/api/status');checks['encrypted_api_200']=status==200 and body['online']
    status,config=request('GET','/api/config');checks['config_secrets_redacted']='api_key' not in config['small_brain'] and 'password' not in config['smtp']
    status,files=request('GET','/api/vault');checks['vault_complete']=len(files['files'])>=7
    note='# 交付验收记录\n\n这是自动验收写入、通过公网加密 API 回读的 Markdown 文件。\n\n- 测试时间：2026-11-02（北京时间）\n- 未配置真实模型或 SMTP 凭据；不把本次链路测试当作模型质量或邮件投递成功。\n'
    status,body=request('PUT','/api/vault',{'path':'notes/delivery-acceptance.md','content':note});checks['vault_write_200']=status==200
    status,body=request('GET','/api/vault',query={'path':'notes/delivery-acceptance.md'});checks['vault_readback_exact']=status==200 and body['content']==note
    status,body=request('PUT','/api/vault',{'path':'../escape.md','content':'should never be written'});checks['vault_escape_rejected']=status==400
    status,body=request('GET','/api/messages')
    demos=[mid for mid,p in body['pending'].items() if p.get('demo')]
    if demos:request('POST','/api/ack',{'ids':demos})
    result={'checks':checks,'all_passed':all(checks.values()),'network':'Windows existing local HTTP proxy 127.0.0.1:7890; direct local route to the Japan IP times out','llm_or_smtp_credentials_used':False}
    (ROOT/'work/public-acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if not result['all_passed']:raise SystemExit(1)
