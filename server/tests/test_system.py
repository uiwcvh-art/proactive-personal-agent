import asyncio, base64, json, os, sys, time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from main import create_app
from harness import Harness,public_url
from security import CryptoBox,b64,unb64

@pytest.fixture
def env(tmp_path,monkeypatch):
    monkeypatch.setenv('AGENT_TOKEN','test-private-token')
    app=create_app(tmp_path,scheduler=False)
    with TestClient(app) as client:
        client.headers['Authorization']='Bearer test-private-token'
        yield app,client

def sealed(app,payload):
    key=os.urandom(32);nonce=os.urandom(12)
    raw={'timestamp':time.time(),'token':'test-private-token',**payload}
    pub=serialization.load_der_public_key(app.state.crypto.public_der)
    encrypted=pub.encrypt(key,padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None))
    return key,{'key':b64(encrypted),'nonce':b64(nonce),'data':b64(AESGCM(key).encrypt(nonce,json.dumps(raw).encode(),None))}

def test_auth_config_and_secret_persistence(env,tmp_path):
    app,c=env
    assert c.get('/api/config',headers={'Authorization':''}).status_code==401
    r=c.post('/api/config',json={'small_brain':{'api_key':'a-secret','model':'small'},'big_brain':{'api_key':'big-secret'},'smtp':{'password':'mail-secret'}})
    assert r.status_code==200
    assert 'a-secret' not in r.text and 'big-secret' not in r.text and 'mail-secret' not in r.text
    assert c.post('/api/config',json={'small_brain':{'model':'updated'}}).status_code==200
    assert app.state.store.config.small_brain.api_key=='a-secret'
    restored=create_app(tmp_path,scheduler=False)
    assert restored.state.store.config.small_brain.model=='updated'
    assert restored.state.store.config.smtp.password=='mail-secret'
    assert c.post('/api/config',json={'smtp':{'port':0}}).status_code==422

def test_vault_edit_and_escape(env,tmp_path):
    app,c=env
    files=c.get('/api/vault').json()['files']
    assert {'INDEX.md','profile/user_traits.md','playbooks/routines.md'}<={f['path'] for f in files}
    assert c.put('/api/vault',json={'path':'notes/idea.md','content':'# 一个想法\n可以编辑'}).status_code==200
    assert '可以编辑' in c.get('/api/vault',params={'path':'notes/idea.md'}).json()['content']
    for path in ('../outside.md','/etc/passwd','profile/../../oops.md','C:\\secrets.md','anything.txt'):
        assert c.put('/api/vault',json={'path':path,'content':'bad'}).status_code==400
    assert c.get('/api/vault',params={'path':'missing.md'}).status_code==404

def test_chat_curiosity_snooze_and_real_result(env):
    app,c=env
    m=c.post('/api/trigger-curiosity').json()
    assert m['engine']=='local_prompt'
    assert m['id'] not in app.state.store.state['pending']
    r=c.post('/api/chat',json={'text':'我想整理本周任务，卡在不知道先做什么。'})
    assert r.status_code==200
    assert '我想整理本周任务' in '\n'.join(app.state.vault.read(f['path']) for f in app.state.vault.files() if f['path'].startswith('timeline/'))
    result=c.post('/api/research',json={}).json()
    assert result['kind'] in ('chat','result') and len(result['trace'])==2
    assert (app.state.brain.harness.root/'research/opportunities-snapshot.md').exists()
    assert c.post('/api/snooze',json={'minutes':60}).status_code==200
    assert app.state.store.state['snooze_until']>time.time()+3500

def test_escalation_states_ack_and_missing_smtp(env):
    app,c=env
    r=c.post('/api/test-escalation').json()
    p=app.state.store.state['pending'][r['id']]
    asyncio.run(app.state.escalation.tick(p['created_at']+5))
    assert p['stage']==2 and p['email_status']!='sent'
    asyncio.run(app.state.escalation.tick(p['created_at']+9))
    assert p['stage']==3
    assert c.post('/api/ack',json={'ids':[r['id']]}).status_code==200
    assert r['id'] not in app.state.store.state['pending']
    assert c.post('/api/test-email').json()['sent'] is False

def test_secure_rest_tamper_replay(env):
    app,c=env
    key,packet=sealed(app,{'method':'GET','path':'/api/status'})
    r=c.post('/api/secure',json=packet)
    data=CryptoBox.frame(key,r.json())
    assert data['status']==200 and data['body']['online']
    assert c.post('/api/secure',json=packet).status_code==400
    _,packet=sealed(app,{'method':'GET','path':'/api/config'})
    packet['data']=packet['data'][:-5]+'AAAAA'
    assert c.post('/api/secure',json=packet).status_code==400

def test_websocket_encrypted_push_and_ack(env):
    app,c=env
    key,packet=sealed(app,{})
    with c.websocket_connect('/ws/client') as ws:
        ws.send_json(packet)
        hello=CryptoBox.frame(key,ws.receive_json())
        assert hello['type']=='connected'
        m=c.post('/api/trigger-curiosity').json()
        push=CryptoBox.frame(key,ws.receive_json())
        assert push['message']['id']==m['id']
        ws.send_json(CryptoBox.seal(key,{'type':'ack','ids':[m['id']]}))
        ev1 = CryptoBox.frame(key,ws.receive_json())
        while ev1['type'] in ('user_notification', 'stream', 'execution', 'goal_status'):
            ev1 = CryptoBox.frame(key,ws.receive_json())
        assert ev1['type']=='ack'

def test_snoozed_reconnect_does_not_replay_overlay(env):
    app,c=env
    message=c.post('/api/test-escalation').json()
    app.state.store.state['pending'][message['id']]['stage']=3
    c.post('/api/snooze',json={'minutes':60})
    key,packet=sealed(app,{})
    with c.websocket_connect('/ws/client') as ws:
        ws.send_json(packet)
        hello=CryptoBox.frame(key,ws.receive_json())
        assert hello['snooze_until']>time.time()
        ws.send_json(CryptoBox.seal(key,{'type':'ping'}))
        assert CryptoBox.frame(key,ws.receive_json())['type']=='pong'

@pytest.mark.asyncio
async def test_read_during_smtp_does_not_escalate(env):
    app,c=env
    message=c.post('/api/test-escalation').json()
    p=app.state.store.state['pending'][message['id']]
    entered,release=asyncio.Event(),asyncio.Event()
    async def slow_mail(*args,**kwargs):
        entered.set();await release.wait();return {'sent':True}
    app.state.escalation.mail=slow_mail
    task=asyncio.create_task(app.state.escalation.tick(p['created_at']+9))
    await entered.wait()
    await app.state.escalation.ack([p['id']])
    release.set();await task
    assert p['stage']==1 and p['id'] not in app.state.store.state['pending']

def test_vision_coordinate_validation(env):
    app,c=env
    reply={'action':'tap','x':120,'y':240,'reason':'测试目标'}
    async def vision_model(*args,**kwargs):return {'role':'assistant','content':json.dumps(reply)}
    app.state.brain.complete=vision_model
    payload={'image':'test-fixture','goal':'找到按钮','width':540,'height':960,'display_id':3}
    response=c.post('/api/vision',json=payload)
    assert response.status_code==200 and response.json()['display_id']==3
    reply['x']=9999
    assert c.post('/api/vision',json=payload).status_code==502

def test_deadline_requires_timezone(env):
    app,c=env
    assert c.post('/api/deadlines',json={'title':'验收','due_at':'2026-12-01T10:00:00'}).status_code==422
    assert c.post('/api/deadlines',json={'title':'验收','due_at':'2026-12-01T10:00:00+08:00'}).status_code==200

@pytest.mark.asyncio
async def test_harness_exact_edit_and_network_boundary(tmp_path):
    h=Harness(tmp_path/'workspace')
    assert (await h.execute('write_file',{'path':'draft.md','content':'one two one'}))['bytes']==11
    assert 'error' in await h.execute('edit_file',{'path':'draft.md','old':'one','new':'three'})
    assert 'written' in await h.execute('edit_file',{'path':'draft.md','old':'two','new':'three'})
    assert 'three' in (await h.execute('read_file',{'path':'draft.md'}))['content']
    assert 'error' in await h.execute('read_file',{'path':'../secret'})
    with pytest.raises(ValueError): await public_url('http://127.0.0.1/private')
    with pytest.raises(ValueError): await public_url('http://169.254.169.254/latest/meta-data/')

@pytest.mark.asyncio
async def test_harness_model_tool_loop(tmp_path):
    h=Harness(tmp_path)
    calls=0
    async def model(messages,tools=None):
        nonlocal calls
        calls+=1
        if calls==1:
            return {'role':'assistant','content':None,'tool_calls':[{'id':'call1','type':'function','function':{'name':'write_file','arguments':json.dumps({'path':'done.md','content':'实际产物'})}}]}
        assert messages[-1]['role']=='tool' and 'written' in messages[-1]['content']
        return {'role':'assistant','content':'已写好 done.md，请验收。'}
    result=await h.run('生成第一步产物',model,'context')
    assert (tmp_path/'done.md').read_text('utf-8')=='实际产物'
    assert len(result['trace'])==1 and calls==2


def test_v2_9_9_features(env):
    app, c = env
    brain = app.state.brain
    # 1. Verify hidden phone keyword lock is completely abolished
    assert brain._is_user_holding_or_refusing_phone('算了，我自己点吧，不用管') is False
    # 2. Verify numbered report lists are NEVER scraped as choices, while explicit <choices>, <think>, and ![img] work
    raw = '<think>余正在思考主人的问题喵</think>测试报告：\n1. 虚拟屏幕创建成功\n2. 点击正常\n![截图](desktop-latest.jpg)\n<choices>\n继续测试 | 帮我继续测下一项\n</choices>'
    cleaned, choices, thought, sticker = brain._extract_all_tags(raw)
    assert '虚拟屏幕创建成功' in cleaned
    assert '<think>' not in cleaned and '<choices>' not in cleaned
    assert thought == '余正在思考主人的问题喵'
    assert sticker == '/workspace-media/desktop-latest.jpg'
    assert len(choices) == 1 and choices[0]['label'] == '继续测试' and choices[0]['value'] == '帮我继续测下一项'
    # Plain numbered list without <choices> returns empty choices
    assert brain._extract_implicit_choices('1. 第一点\n2. 第二点\n3. 第三点') == []
