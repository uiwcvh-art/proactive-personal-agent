import asyncio, base64, io, json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin
from main import create_app
from security import CryptoBox
from test_system import sealed


def photo(size=(96, 96)):
    image = Image.new('RGB', size, '#7c5cff')
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text('private-note', 'camera-owner-location')
    out = io.BytesIO()
    image.save(out, 'PNG', pnginfo=metadata)
    return 'data:image/png;base64,' + base64.b64encode(out.getvalue()).decode()


def test_identity_avatar_privacy_validation_and_restart(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENT_TOKEN', 'test')
    app = create_app(tmp_path, scheduler=False)
    headers = {'Authorization':'Bearer test'}
    with TestClient(app, headers=headers) as client:
        path = '/api/bots/research/identity'
        original = client.get('/api/bots').json()['bots'][1]
        client.patch('/api/bots/research/placement', json={'hidden':True,'pinned':True})
        data = {'name':'资料管家','title':'负责旅行与资料整理','image':photo()}
        assert client.patch(path, json=data, headers={'Authorization':''}).status_code == 401
        changed = client.patch(path, json=data).json()
        version = changed['avatar_version']
        assert len(version) == 64 and changed['hidden'] and changed['pinned']
        assert changed['tools'] == original['tools'] and changed['instructions'] == original['instructions']
        roster = client.get('/api/bots')
        assert len(roster.content) < 20_000 and 'base64' not in roster.text
        assert client.get('/api/bots/research/avatar', headers={'Authorization':''}).status_code == 401
        avatar = client.get('/api/bots/research/avatar').json()
        raw = base64.b64decode(avatar['image'].split(',')[1])
        assert avatar['version'] == version and b'camera-owner-location' not in raw
        with Image.open(io.BytesIO(raw)) as image:
            assert image.size == (96, 96) and not image.info
        for invalid in ({}, {'name':'  '}, {'color':'red'}, {'image':'data:image/svg+xml;base64,PHN2Zz4='},
                        {'image':'data:image/png;base64,bm90LWFuLWltYWdl'}, {'image':photo((1025, 1))}, {'avatar_version':'../state'}):
            assert client.patch(path, json=invalid).status_code == 422
        # An older client saving tool settings must not erase a new identity label/photo.
        client.put('/api/bots/research', json={key:value for key,value in original.items() if key!='title'})
        bot = client.get('/api/bots').json()['bots'][1]
        assert bot['title'] == data['title'] and bot['avatar_version'] == version
        assert client.patch('/api/bots/missing/identity', json={'title':'x'}).status_code == 404
    restored = create_app(tmp_path, scheduler=False)
    with TestClient(restored, headers=headers) as client:
        assert client.get('/api/bots/research/avatar').json() == avatar
        cleared = client.patch(path, json={'image':'','shape':'diamond'}).json()
        assert cleared['avatar_version'] == '' and cleared['shape'] == 'diamond'
        assert client.get('/api/bots/research/avatar').status_code == 404
        assert not list((tmp_path/'avatars').glob('*.txt'))


def test_primary_replacement_really_routes_curiosity_and_survives_restart(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENT_TOKEN', 'test')
    app = create_app(tmp_path, scheduler=False)
    with TestClient(app, headers={'Authorization':'Bearer test'}) as client:
        original = client.post('/api/trigger-curiosity').json()
        assert original['bot_id'] == 'companion'
        bot = client.post('/api/bots', json={'name':'新主助手','instructions':'只关心今天最卡的事','notify':False}).json()
        assert client.put('/api/bots/primary', json={'bot_id':bot['id']}, headers={'Authorization':''}).status_code == 401
        assert client.put('/api/bots/primary', json={'bot_id':'missing'}).status_code == 404
        selected = client.put('/api/bots/primary', json={'bot_id':bot['id']}).json()
        assert selected['primary_bot_id'] == bot['id']
        response = client.post('/api/trigger-curiosity').json()
        assert response['bot_id'] == bot['id'] and response['notify'] is False
        assert response['id'] not in app.state.store.state['pending']
        assert original['id'] in app.state.store.state['pending']
        assert client.get('/api/bots').json()['primary_bot_id'] == bot['id']
        assert client.post('/api/chat',json={'text':'默认对话也进入主助手'}).json()['bot_id'] == bot['id']
        assert client.post('/api/research',json={}).json()['bot_id'] == bot['id']
        async def deadline_scenario():
            done=asyncio.Event();messages=[]
            async def receive(event):
                if event.get('message',{}).get('kind')=='deadline':
                    messages.append(event['message']);done.set()
            brain=app.state.brain;brain.publish=receive
            app.state.store.update_config({'quiet_start_hour':0,'quiet_end_hour':0})
            app.state.store.state['deadlines'].append({'title':'主助手到期提醒','due_at':time.time()-1,'notified':False})
            task=asyncio.create_task(brain.scheduler());brain.wake.set()
            try:
                await asyncio.wait_for(done.wait(),3)
                assert messages[0]['bot_id']==bot['id'] and messages[0]['notify'] is False
                assert app.state.store.state['deadlines'][-1]['notified'] is True
            finally:
                task.cancel()
                try:await task
                except asyncio.CancelledError:pass
        asyncio.run(deadline_scenario())
    restored = create_app(tmp_path, scheduler=False)
    with TestClient(restored, headers={'Authorization':'Bearer test'}) as client:
        assert client.get('/api/bots').json()['primary_bot_id'] == bot['id']
        assert client.post('/api/trigger-curiosity').json()['bot_id'] == bot['id']


def test_primary_change_during_model_request_keeps_the_inflight_conversation(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENT_TOKEN','test-private-token')
    app = create_app(tmp_path, scheduler=False)
    app.state.store.update_config({'small_brain':{'api_key':'fixture','model':'fixture'}})
    entered, finish = asyncio.Event(), asyncio.Event()
    captured = []
    async def complete(messages, tools=None, big=False, **kwargs):
        captured.append(messages)
        entered.set()
        await finish.wait()
        return {'role':'assistant','content':'给当前对话的问题'}
    app.state.brain.complete = complete
    app.state.brain.llm.complete_silent = complete
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            async def encrypted(method, path, body):
                key, packet = sealed(app, {'method':method,'path':path,'body':body})
                response = await client.post('/api/secure',json=packet)
                return CryptoBox.frame(key,response.json())
            pending = asyncio.create_task(encrypted('POST','/api/trigger-curiosity',{}))
            await asyncio.wait_for(entered.wait(),3)
            selected = await encrypted('PUT','/api/bots/primary',{'bot_id':'research'})
            assert selected['status'] == 200
            finish.set()
            first = await asyncio.wait_for(pending,3)
            assert first['body']['bot_id'] == 'companion'
            second = await encrypted('POST','/api/trigger-curiosity',{})
            assert second['body']['bot_id'] == 'research'
            assert '研究助手' in captured[-1][0]['content']
            assert '最近对话摘要' in captured[-1][1]['content']
    asyncio.run(scenario())


def test_generated_avatar_uses_the_configured_provider_and_stays_a_draft(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENT_TOKEN','test')
    app = create_app(tmp_path, scheduler=False)
    calls = []
    def provider(request):
        calls.append(json.loads(request.content))
        assert request.url == 'https://images.example/v1/images/generations'
        assert request.headers['authorization'] == 'Bearer fixture-image-key'
        return httpx.Response(200, json={'data':[{'b64_json':photo().split(',')[1]}]})
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda *args,**kwargs:real_client(*args,**{**kwargs,'transport':httpx.MockTransport(provider)}))
    with TestClient(app, headers={'Authorization':'Bearer test'}) as client:
        path = '/api/bots/research/avatar/generate'
        data = {'model':'fixture-image-model','prompt':'一个紫色的星球'}
        assert client.post(path,json=data,headers={'Authorization':''}).status_code == 401
        assert client.post(path,json=data).status_code == 409
        app.state.store.update_config({'big_brain':{'api_key':'fixture-image-key','base_url':'https://images.example/v1','model':'chat-model'}})
        generated = client.post(path,json=data)
        assert generated.status_code == 200 and generated.json()['width'] == 96
        assert len(calls) == 1 and calls[0]['model'] == data['model'] and calls[0]['size'] == '1024x1024'
        assert 'minimalist 2D Bot-style avatar' in calls[0]['prompt'] and data['prompt'] in calls[0]['prompt']
        assert not app.state.store.state['assistants'][1]['avatar_version']
        assert client.get('/api/bots/research/avatar').status_code == 404
        assert client.post(path,json={**data,'model':' '}).status_code == 422
        # Explicitly accepting the preview is the only operation that persists the picture.
        saved = client.patch('/api/bots/research/identity',json={'image':generated.json()['image']})
        assert saved.status_code == 200 and saved.json()['avatar_version']
        # Also verify 'whale' and 'whalechan' shapes validate cleanly on IdentityInput and AssistantInput
        whale_bot = client.patch('/api/bots/research/identity',json={'shape':'whalechan'}).json()
        assert whale_bot['shape'] == 'whalechan'
        whale2_bot = client.patch('/api/bots/research/identity',json={'shape':'whale'}).json()
        assert whale2_bot['shape'] == 'whale'
