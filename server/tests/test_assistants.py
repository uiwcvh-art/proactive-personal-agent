import asyncio, json, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from main import create_app
from harness import Harness

def test_home_placement_preserves_hidden_bot_work_and_survives_restart(tmp_path,monkeypatch):
    monkeypatch.setenv('AGENT_TOKEN','test')
    app=create_app(tmp_path,scheduler=False)
    with TestClient(app,headers={'Authorization':'Bearer test'}) as client:
        original=client.get('/api/bots').json()['bots'][0]
        assert original['pinned'] is False and original['hidden'] is False
        routine=client.post('/api/routines',json={'goal':'保留这项任务','bot_id':original['id']}).json()
        message=client.post('/api/trigger-curiosity').json()
        pending=app.state.store.state['pending'].copy()
        path='/api/bots/'+original['id']+'/placement'
        assert client.patch(path,json={'hidden':True},headers={'Authorization':''}).status_code==401
        placed=client.patch(path,json={'hidden':True,'pinned':True}).json()
        assert placed['hidden'] and placed['pinned'] and placed['pinned_at']>0
        assert app.state.store.state['pending']==pending
        # Ordinary identity/permission saves must preserve placement metadata.
        saved=client.put('/api/bots/'+original['id'],json={**original,'name':'仍在工作的助手'}).json()
        assert saved['hidden'] and saved['pinned'] and saved['pinned_at']==placed['pinned_at']
        reply=client.post('/api/chat',json={'text':'隐藏后仍能对话','bot_id':original['id']})
        assert reply.status_code==200 and reply.json()['notify'] is True
        assert client.get('/api/routines').json()['routines'][0]['id']==routine['id']
        for bad in ({},{'hidden':None},{'hidden':'false'},{'notify':False},{'hidden':True,'tools':[]}):
            assert client.patch(path,json=bad).status_code==422
        assert client.patch('/api/bots/missing/placement',json={'hidden':True}).status_code==404
    restored=create_app(tmp_path,scheduler=False)
    with TestClient(restored,headers={'Authorization':'Bearer test'}) as client:
        bot=client.get('/api/bots').json()['bots'][0]
        assert bot['hidden'] and bot['pinned']
        assert restored.state.store.state['routines'][0]['enabled']
        assert any(m['id']==message['id'] for m in restored.state.store.state['messages'])
        shown=client.patch(path,json={'hidden':False}).json()
        assert not shown['hidden'] and shown['pinned']
        assert shown['pinned_at']==placed['pinned_at'] and shown['name']=='仍在工作的助手'
        assert client.patch(path,json={'pinned':False}).json()['pinned_at']==0

def test_bot_context_and_configuration_survive_restart(tmp_path,monkeypatch):
    monkeypatch.setenv('AGENT_TOKEN','test')
    app=create_app(tmp_path,scheduler=False)
    with TestClient(app,headers={'Authorization':'Bearer test'}) as client:
        assert client.get('/api/bots',headers={'Authorization':''}).status_code==401
        spec={'name':'计划员','description':'分解任务','instructions':'每次给出三个选项','engine':'small','shape':'star','color':'#1199FF','tools':['read_file']}
        bot=client.post('/api/bots',json=spec).json()
        client.post('/api/config',json={'small_brain':{'api_key':'test','model':'mock'}})
        captured=[]
        async def complete(messages,tools=None,big=False):
            captured.append(messages)
            return {'role':'assistant','content':json.dumps({'reply':'三个选项','traits':[],'opportunity':None,'deadline':None})}
        app.state.brain.complete=complete
        client.post('/api/chat',json={'text':'仅属于白kawa的对话'})
        response=client.post('/api/chat',json={'text':'仅属于计划员的对话','bot_id':bot['id']})
        assert response.status_code==200 and response.json()['bot_id']==bot['id']
        assert '每次给出三个选项' in captured[-1][0]['content']
        assert '仅属于白kawa的对话' not in json.dumps(captured[-1],ensure_ascii=False)
        history=client.get('/api/messages',params={'bot_id':bot['id']}).json()['messages']
        assert len(history)==2 and all(m['bot_id']==bot['id'] for m in history)
        assert client.post('/api/chat',json={'text':'hi','bot_id':'missing'}).status_code==404
        assert client.post('/api/bots',json={**spec,'tools':['arbitrary_shell']}).status_code==422
    restored=create_app(tmp_path,scheduler=False)
    assert restored.state.store.state['assistants'][-1]['instructions']==spec['instructions']

def test_big_chat_tool_permissions_and_routine_lifecycle(tmp_path,monkeypatch):
    monkeypatch.setenv('AGENT_TOKEN','test')
    app=create_app(tmp_path,scheduler=False)
    with TestClient(app,headers={'Authorization':'Bearer test'}) as client:
        spec={'name':'只读助手','engine':'big','tools':['read_file']}
        bot=client.post('/api/bots',json=spec).json()
        result=client.post('/api/chat',json={'text':'做一个快照','bot_id':bot['id']}).json()
        assert result['trace']==[]
        assert not (app.state.brain.harness.root/'research/opportunities-snapshot.md').exists()
        routine=client.post('/api/routines',json={'goal':'检查任务池','bot_id':bot['id'],'interval_hours':6}).json()
        assert routine['enabled'] is True
        paused=client.put('/api/routines/'+routine['id'],json={'enabled':False})
        assert paused.status_code==200 and paused.json()['enabled'] is False
        assert client.post('/api/routines/'+routine['id']+'/run').json()['bot_id']==bot['id']
        assert client.post('/api/routines',json={'goal':' ','interval_hours':0}).status_code==422
        assert client.put('/api/routines/missing',json={'enabled':False}).status_code==404

def test_model_cannot_execute_a_disabled_tool(tmp_path):
    harness=Harness(tmp_path)
    captured=[];events=[]
    async def complete(messages,tools=None):
        captured.append(tools)
        if len(captured)==1:
            return {'role':'assistant','content':None,'tool_calls':[{'id':'t','type':'function','function':{'name':'write_file','arguments':json.dumps({'path':'forbidden.txt','content':'no'})}}]}
        return {'role':'assistant','content':'不能写入'}
    async def event(value):events.append(value)
    result=asyncio.run(harness.run('只允许读取',complete,'',allowed_tools=['read_file'],on_event=event))
    assert not (tmp_path/'forbidden.txt').exists()
    assert [t['function']['name'] for t in captured[0]]==['read_file']
    assert 'error' in result['trace'][0]['result']
    assert [e['type'] for e in events]==['tool_start','tool_end']

def test_stopping_execution_releases_brain_and_preserves_history(tmp_path,monkeypatch):
    monkeypatch.setenv('AGENT_TOKEN','test')
    app=create_app(tmp_path,scheduler=False)
    brain=app.state.brain
    app.state.store.update_config({'big_brain':{'api_key':'test','model':'mock'}})
    async def scenario():
        entered=asyncio.Event()
        async def complete(*args,**kwargs):
            entered.set()
            await asyncio.sleep(30)
        brain.complete=complete
        task=asyncio.create_task(brain.research('等待执行',bot_id='research',record_user=True))
        await asyncio.wait_for(entered.wait(),3)
        assert brain.active_task is task and brain.lock.locked()
        task.cancel()
        result=await task
        assert result['kind']=='stopped' and result['bot_id']=='research'
        assert not brain.lock.locked() and brain.execution is None and brain.active_task is None
        assert any(m['role']=='user' and m['text']=='等待执行' for m in app.state.store.state['messages'])
    asyncio.run(scenario())

def test_notification_switch_stops_existing_and_future_escalation(tmp_path,monkeypatch):
    monkeypatch.setenv('AGENT_TOKEN','test')
    app=create_app(tmp_path,scheduler=False)
    with TestClient(app,headers={'Authorization':'Bearer test'}) as client:
        message=client.post('/api/trigger-curiosity').json()
        assert message['id'] in app.state.store.state['pending']
        bot=client.get('/api/bots').json()['bots'][0]
        bot['notify']=False
        assert client.put('/api/bots/'+bot['id'],json=bot).status_code==200
        assert not app.state.store.state['pending']
        next_message=client.post('/api/trigger-curiosity').json()
        assert next_message['notify'] is False
        assert next_message['id'] not in app.state.store.state['pending']

def test_explicit_processing_mode_overrides_bot_default(tmp_path,monkeypatch):
    monkeypatch.setenv('AGENT_TOKEN','test')
    app=create_app(tmp_path,scheduler=False)
    with TestClient(app,headers={'Authorization':'Bearer test'}) as client:
        result=client.post('/api/chat',json={'text':'执行一步','bot_id':'companion','mode':'execute'}).json()
        assert result['kind']=='result' and result['bot_id']=='companion'
        reply=client.post('/api/chat',json={'text':'只聊一聊','bot_id':'research','mode':'chat'}).json()
        assert reply['kind']=='curiosity' and reply['bot_id']=='research'

def test_stopping_through_encrypted_endpoint(tmp_path,monkeypatch):
    import httpx
    from test_system import sealed
    from security import CryptoBox
    monkeypatch.setenv('AGENT_TOKEN','test-private-token')
    app=create_app(tmp_path,scheduler=False)
    app.state.store.update_config({'big_brain':{'api_key':'test','model':'mock'}})
    async def scenario():
        entered=asyncio.Event()
        async def complete(*args,**kwargs):
            entered.set()
            await asyncio.sleep(30)
        app.state.brain.complete=complete
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            key,packet=sealed(app,{'method':'POST','path':'/api/chat','body':{'text':'等待执行','mode':'execute'}})
            request=asyncio.create_task(client.post('/api/secure',json=packet))
            await asyncio.wait_for(entered.wait(),3)
            stop_key,stop_packet=sealed(app,{'method':'POST','path':'/api/stop','body':{}})
            response=await client.post('/api/secure',json=stop_packet)
            assert CryptoBox.frame(stop_key,response.json())['body']['stopping'] is True
            reply=await asyncio.wait_for(request,3)
            assert CryptoBox.frame(key,reply.json())['body']['kind']=='stopped'
            assert not app.state.brain.lock.locked()
    asyncio.run(scenario())

def test_bot_to_bot_send_message_and_error_self_healing_via_workspace(tmp_path,monkeypatch):
    monkeypatch.setenv('AGENT_TOKEN','test')
    app=create_app(tmp_path,scheduler=False)
    with TestClient(app,headers={'Authorization':'Bearer test'}) as client:
        # 1. Unified config syncs settings.json and bots.json into workspace
        cfg=client.post('/api/config',json={'api_base_url':'https://api.openai.com/v1','api_key':'sk-test-key','default_model':'gpt-4.1-mini'}).json()
        assert cfg['config']['has_api_key'] is True and cfg['config']['default_model']=='gpt-4.1-mini'
        ws=app.state.brain.harness.root
        assert json.loads((ws/'settings.json').read_text('utf-8'))['api_key']=='sk-test-key'
        # Set research bot to a broken model first
        bots=client.get('/api/bots').json()['bots']
        research_bot=next(b for b in bots if b['id']=='research')
        client.put('/api/bots/research',json={**research_bot,'model':'broken-model-name'})
        assert json.loads((ws/'bots.json').read_text('utf-8'))[1]['model']=='broken-model-name'

        # 2. Simulate companion Bot A delegating to research Bot B:
        # Round 1: A calls send_message -> B fails with broken-model-name -> Error message posted to both B and A!
        # Round 2: A edits bots.json to fix B's model to 'gpt-4.1' and calls send_message again -> B succeeds!
        # Round 3: A calls notify_user and replies to user.
        step=0
        async def mock_complete(messages,tools=None,big=False):
            nonlocal step
            bot_id=app.state.brain.bot_context.get()
            bot=next(b for b in app.state.store.state['assistants'] if b['id']==bot_id)
            if bot_id=='research':
                if bot.get('model')=='broken-model-name':
                    raise ValueError('模型网关返回 HTTP 404 (model=broken-model-name)：model_not_found')
                return {'role':'assistant','content':'我已查好资料并整理完毕。'}
            step+=1
            if step==1:
                return {'role':'assistant','content':None,'tool_calls':[
                    {'id':'c1','type':'function','function':{'name':'send_message','arguments':json.dumps({'to_bot_id':'research','text':'请帮我查一下今日任务'})}}
                ]}
            if step==2:
                # Companion sees error in tool result, fixes bots.json via edit_file, and retries send_message
                return {'role':'assistant','content':None,'tool_calls':[
                    {'id':'c2','type':'function','function':{'name':'edit_file','arguments':json.dumps({'path':'bots.json','old':'"broken-model-name"','new':'"gpt-4.1"'})}},
                    {'id':'c3','type':'function','function':{'name':'send_message','arguments':json.dumps({'to_bot_id':'research','text':'模型修好了，请再查一次'})}},
                    {'id':'c4','type':'function','function':{'name':'notify_user','arguments':json.dumps({'title':'研究完成','body':'研究助手已完成任务','urgent':False})}},
                ]}
            return {'role':'assistant','content':json.dumps({'reply':'研究助手起初模型报错，余已帮它把模型修成 gpt-4.1 并拿到了结果！'})}

        app.state.brain.complete=mock_complete
        res=client.post('/api/chat',json={'text':'安排研究助手干活','bot_id':'companion'}).json()
        assert '修成 gpt-4.1' in res['text']
        # Verify research bot's model was updated via workspace/bots.json edit!
        updated_research=next(b for b in client.get('/api/bots').json()['bots'] if b['id']=='research')
        assert updated_research['model']=='gpt-4.1'
        # Verify companion window has both the error from research AND the subsequent reply from research
        comp_msgs=client.get('/api/messages',params={'bot_id':'companion'}).json()['messages']
        assert any(m['kind']=='error' and 'broken-model-name' in m['text'] for m in comp_msgs)
        assert any(m['kind']=='bot_reply' and '我已查好资料并整理完毕' in m['text'] for m in comp_msgs)
        assert any(m['kind']=='notification' and '研究完成' in m['text'] for m in comp_msgs)
        # Verify research window also has the delegated messages and the error
        res_msgs=client.get('/api/messages',params={'bot_id':'research'}).json()['messages']
        assert any(m['kind']=='bot_message' for m in res_msgs)
        assert any(m['kind']=='error' for m in res_msgs)
        assert any(m['kind']=='result' and '我已查好资料并整理完毕' in m['text'] for m in res_msgs)
