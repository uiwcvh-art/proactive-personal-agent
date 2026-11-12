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
        assert client.post('/api/bots',json={**spec,'name':' '}).status_code==422
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
    assert [e['type'] for e in events][:2]==['tool_start','tool_end']

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
        assert result.get('stopped') is True or result.get('kind')=='stopped'
        assert not brain.lock.locked() and brain.execution is None and brain.active_task is None
        assert any(m['role']=='user' and m['text']=='等待执行' for m in app.state.store.state['messages'])
    asyncio.run(scenario())

def test_notification_switch_stops_existing_and_future_escalation(tmp_path,monkeypatch):
    monkeypatch.setenv('AGENT_TOKEN','test')
    app=create_app(tmp_path,scheduler=False)
    with TestClient(app,headers={'Authorization':'Bearer test'}) as client:
        message=client.post('/api/trigger-curiosity').json()
        asyncio.run(app.state.escalation.enqueue(message))
        assert message['id'] in app.state.store.state['pending']
        bot=client.get('/api/bots').json()['bots'][0]
        bot['notify']=False
        assert client.put('/api/bots/'+bot['id'],json=bot).status_code==200
        assert not app.state.store.state['pending']
        client.post('/api/chat',json={'text':'我回来了'})
        next_message=client.post('/api/trigger-curiosity').json()
        assert next_message.get('notify') is False or next_message.get('suppressed') is True

def test_explicit_processing_mode_overrides_bot_default(tmp_path,monkeypatch):
    monkeypatch.setenv('AGENT_TOKEN','test')
    app=create_app(tmp_path,scheduler=False)
    with TestClient(app,headers={'Authorization':'Bearer test'}) as client:
        result=client.post('/api/chat',json={'text':'执行一步','bot_id':'companion','mode':'execute'}).json()
        assert result['kind'] in ('chat','result') and result['bot_id']=='companion'
        reply=client.post('/api/chat',json={'text':'只聊一聊','bot_id':'research','mode':'chat'}).json()
        assert reply['kind'] in ('chat','curiosity') and reply['bot_id']=='research'

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
            stop_body = CryptoBox.frame(stop_key,response.json())['body']
            assert stop_body.get('stopped') is True or stop_body.get('stopping') is True
            reply=await asyncio.wait_for(request,3)
            reply_body = CryptoBox.frame(key,reply.json())['body']
            assert reply_body.get('stopped') is True or reply_body.get('kind')=='stopped'
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

        # 2. Simulate companion Bot A delegating to research Bot B via v4.0 Blackboard + peek_bots:
        # Round 1: A posts @research on Blackboard -> B wakes async & fails with broken-model-name (recorded on Blackboard & B's room)!
        # Round 2: A inspects via peek_bots/blackboard, edits bots.json to fix B's model to 'gpt-4.1', posts again -> B succeeds!
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
                    {'id':'c1','type':'function','function':{'name':'blackboard','arguments':json.dumps({'action':'post','mentions':['@research'],'text':'请帮我查一下今日任务'})}}
                ]}
            if step==2:
                await asyncio.sleep(0.08)
                return {'role':'assistant','content':None,'tool_calls':[
                    {'id':'c1b','type':'function','function':{'name':'peek_bots','arguments':json.dumps({'target':'research','limit':5})}},
                    {'id':'c1c','type':'function','function':{'name':'blackboard','arguments':json.dumps({'action':'list'})}},
                ]}
            if step==3:
                return {'role':'assistant','content':None,'tool_calls':[
                    {'id':'c2','type':'function','function':{'name':'edit_file','arguments':json.dumps({'path':'bots.json','old':'"broken-model-name"','new':'"gpt-4.1"'})}},
                    {'id':'c3','type':'function','function':{'name':'blackboard','arguments':json.dumps({'action':'post','mentions':['@research'],'text':'模型修好了，请再查一次'})}},
                    {'id':'c4','type':'function','function':{'name':'notify_user','arguments':json.dumps({'title':'研究完成','body':'研究助手已完成任务','urgent':False})}},
                ]}
            await asyncio.sleep(0.08)
            return {'role':'assistant','content':json.dumps({'reply':'研究助手起初模型报错，余已帮它把模型修成 gpt-4.1 并拿到了结果！'})}

        app.state.brain.complete=mock_complete
        res=client.post('/api/chat',json={'text':'安排研究助手干活','bot_id':'companion'}).json()
        assert '修成 gpt-4.1' in res['text']
        updated_research=next(b for b in client.get('/api/bots').json()['bots'] if b['id']=='research')
        assert updated_research['model']=='gpt-4.1'
        comp_msgs=client.get('/api/messages',params={'bot_id':'companion'}).json()['messages']
        assert not any(m['kind'] in ('bot_message','bot_reply') for m in comp_msgs)
        assert all(m.get('from_bot_id') in ('', None, 'companion') for m in comp_msgs if m['role']=='assistant')
        bb_traces=[t for t in res['trace'] if t['tool']=='blackboard']
        assert len(bb_traces)>=3
        assert any(t['tool']=='peek_bots' for t in res['trace'])
        assert 'broken-model-name' in json.dumps(comp_msgs[-1].get('turn_steps',[]),ensure_ascii=False)
        assert any(t['tool']=='notify_user' for t in res['trace'])
        res_msgs=client.get('/api/messages',params={'bot_id':'research'}).json()['messages']
        inbound_in_res=[m for m in res_msgs if m['kind']=='bot_message']
        assert inbound_in_res and all(not m.get('trace') for m in inbound_in_res)
        assert any(m['kind']=='error' for m in res_msgs)
        assert any(m['kind'] in ('bot_reply','result','chat') and '我已查好资料并整理完毕' in m['text'] for m in res_msgs)


def test_per_node_bot_settings_vs_shared_persona(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENT_TOKEN', 'test')
    app = create_app(tmp_path, scheduler=False)
    with TestClient(app, headers={'Authorization': 'Bearer test'}) as client:
        # 1. Phone ('phone') opens curiosity and sets instructions; Tablet ('phone2') turns off curiosity
        comp_phone = client.get('/api/bots', headers={'X-Node-Source': 'phone'}).json()['bots'][0]
        updated_phone = client.put(
            '/api/bots/companion',
            headers={'X-Node-Source': 'phone'},
            json={
                **comp_phone,
                'name': '白珂',
                'instructions': '你是白珂，全端共享同一人设设定喵！',
                'curiosity_enabled': True,
                'notify': True,
                'model': 'phone-model-v1',
            },
        ).json()
        assert updated_phone['curiosity_enabled'] is True
        assert updated_phone['model'] == 'phone-model-v1'

        # 2. Tablet ('phone2') disables curiosity and sets its own model, while updating global description
        updated_tablet = client.put(
            '/api/bots/companion',
            headers={'X-Node-Source': 'phone2'},
            json={
                **updated_phone,
                'description': '全端同步的新简介',
                'curiosity_enabled': False,
                'notify': False,
                'model': 'tablet-model-v2',
            },
        ).json()
        assert updated_tablet['curiosity_enabled'] is False
        assert updated_tablet['notify'] is False
        assert updated_tablet['model'] == 'tablet-model-v2'
        assert updated_tablet['instructions'] == '你是白珂，全端共享同一人设设定喵！'

        # 3. Verify Phone ('phone') STILL has curiosity_enabled=True, notify=True, model='phone-model-v1',
        #    while receiving the shared global description ('全端同步的新简介') and instructions!
        check_phone = client.get('/api/bots', headers={'X-Node-Source': 'phone'}).json()['bots'][0]
        assert check_phone['curiosity_enabled'] is True
        assert check_phone['notify'] is True
        assert check_phone['model'] == 'phone-model-v1'
        assert check_phone['description'] == '全端同步的新简介'
        assert check_phone['instructions'] == '你是白珂，全端共享同一人设设定喵！'

        # 4. Verify Tablet ('phone2') has curiosity_enabled=False, notify=False, model='tablet-model-v2'
        check_tablet = client.get('/api/bots', headers={'X-Node-Source': 'phone2'}).json()['bots'][0]
        assert check_tablet['curiosity_enabled'] is False
        assert check_tablet['notify'] is False
        assert check_tablet['model'] == 'tablet-model-v2'
        assert check_tablet['description'] == '全端同步的新简介'
        assert check_tablet['instructions'] == '你是白珂，全端共享同一人设设定喵！'


def test_agent_loop_recovers_from_mid_loop_empty_dropout_and_premature_gui_plan(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENT_TOKEN', 'test')
    app = create_app(tmp_path, scheduler=False)
    with TestClient(app, headers={'Authorization': 'Bearer test'}) as client:
        client.post('/api/config', json={'small_brain': {'api_key': 'test', 'model': 'mock-v4'}})
        step = 0

        async def mock_complete(messages, tools=None, big=False):
            nonlocal step
            step += 1
            if step == 1:
                return {
                    'role': 'assistant',
                    'content': '喵，先看一眼工作区文件喵～',
                    'tool_calls': [
                        {'id': 'c1', 'type': 'function', 'function': {'name': 'list_dir', 'arguments': json.dumps({'path': '.'})}}
                    ],
                }
            if step == 2:
                # Simulate mid-loop empty/dot dropout after trace is already non-empty!
                return {'role': 'assistant', 'content': '.', 'tool_calls': []}
            if step == 3:
                # Simulate premature "Next Steps Plan" instead of calling the next tool!
                return {
                    'role': 'assistant',
                    'content': '刚才看了一眼目录。余下一步想这么走：继续读取 SOUL.md 确认内容，要余现在接着看吗？',
                    'tool_calls': [],
                }
            if step == 4:
                # Loop intercepted both premature halts and forced real tool execution!
                return {
                    'role': 'assistant',
                    'content': '',
                    'tool_calls': [
                        {'id': 'c2', 'type': 'function', 'function': {'name': 'read_file', 'arguments': json.dumps({'path': 'SOUL.md'})}}
                    ],
                }
            return {'role': 'assistant', 'content': '已完整读取目录与 SOUL.md，全部任务办妥喵！', 'tool_calls': []}

        app.state.brain.complete = mock_complete
        res = client.post('/api/chat', json={'text': '检查目录并读取 SOUL.md', 'bot_id': 'companion'}).json()
        assert step == 5
        assert len(res['trace']) == 2
        assert [t['tool'] for t in res['trace']] == ['list_dir', 'read_file']
        assert '全部任务办妥喵' in res['text']


def test_dsh_codex_multistep_process_collapse_and_tool_name_sanitization(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENT_TOKEN', 'test')
    from core.llm_client import LLMClient
    from transport.extension_registry import ExtensionRegistry
    import re

    safe_name = ExtensionRegistry._safe_openai_tool_name('中文工具名验证')
    assert re.match(r'^[a-zA-Z0-9_-]{1,64}$', safe_name)

    sanitized = LLMClient._sanitize_tools([
        {'type': 'function', 'function': {'name': '中文工具名验证', 'description': '测试描述', 'parameters': {'type': 'object'}}}
    ])
    assert re.match(r'^[a-zA-Z0-9_-]{1,64}$', sanitized[0]['function']['name'])
    assert '[中文工具名验证]' in sanitized[0]['function']['description']

    app = create_app(tmp_path, scheduler=False)
    with TestClient(app, headers={'Authorization': 'Bearer test'}) as client:
        client.post('/api/config', json={'small_brain': {'api_key': 'test', 'model': 'mock-v4'}})
        step = 0

        async def mock_complete(messages, tools=None, big=False):
            nonlocal step
            step += 1
            if step == 1:
                return {
                    'role': 'assistant',
                    'content': '余先帮主人列出当前目录结构喵～',
                    'reasoning_content': '第一步先看目录有什么文件。',
                    'tool_calls': [
                        {'id': 'c1', 'type': 'function', 'function': {'name': 'list_dir', 'arguments': '{}'}}
                    ],
                }
            if step == 2:
                return {
                    'role': 'assistant',
                    'content': '目录看完了，接下来余再去读取 SOUL.md 喵～',
                    'reasoning_content': '第二步读取 SOUL.md。',
                    'tool_calls': [
                        {'id': 'c2', 'type': 'function', 'function': {'name': 'read_file', 'arguments': json.dumps({'path': 'SOUL.md'})}}
                    ],
                }
            return {
                'role': 'assistant',
                'content': '最终汇报：目录与 SOUL.md 已全部核对完毕喵！',
                'reasoning_content': '两步工具都已完成，现在给出最终总结。',
                'tool_calls': [],
            }

        app.state.brain.complete = mock_complete
        res = client.post('/api/chat', json={'text': '分阶段检查目录并读取 SOUL.md', 'bot_id': 'companion'}).json()
        assert step == 3
        # Final bubble text must ONLY contain the clean final summary, NOT intermediate step speech
        assert res['text'] == '最终汇报：目录与 SOUL.md 已全部核对完毕喵！'
        assert '余先帮主人列出当前目录结构喵' not in res['text']
        # Process log / thought drawer must contain all intermediate thoughts and stage progress notes
        assert '💭 [第1步思考]' in res['thought']
        assert '💬 [第1步进展] 余先帮主人列出当前目录结构喵～' in res['thought']
        assert '💭 [第2步思考]' in res['thought']
        assert '💬 [第2步进展] 目录看完了，接下来余再去读取 SOUL.md 喵～' in res['thought']
        assert '💭 [总结思考]' in res['thought']
        assert res['trace'][0].get('step_note') == '余先帮主人列出当前目录结构喵～'
        assert res['trace'][1].get('step_note') == '目录看完了，接下来余再去读取 SOUL.md 喵～'


def test_fallback_disabled_by_default_and_lossless_phone_promote(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENT_TOKEN', 'test')
    app = create_app(tmp_path, scheduler=False)
    with TestClient(app, headers={'Authorization': 'Bearer test'}) as client:
        # 1. Verify fallback is disabled by default even if fallback_model is set
        cfg = client.post('/api/config', json={'fallback_model': 'some-backup-model', 'fallback_enabled': False}).json()['config']
        assert cfg['fallback_enabled'] is False
        assert app.state.store.config.effective_fallback() == ('', '', '')

        # 2. When explicitly enabled, effective_fallback returns the configured fallback model
        cfg2 = client.post('/api/config', json={'fallback_model': 'some-backup-model', 'fallback_enabled': True}).json()['config']
        assert cfg2['fallback_enabled'] is True
        assert app.state.store.config.effective_fallback()[2] == 'some-backup-model'

        # 3. Verify phone_workspace(op="promote") dispatches lossless promote RPC instead of release
        rpc_calls = []
        async def fake_phone_rpc(op_name, payload, timeout=18.0):
            rpc_calls.append((op_name, payload))
            return {'ok': True, 'promoted': True, 'promoted_task_id': 88, 'promoted_package': 'com.eg.android.AlipayGphone'}

        app.state.brain._raw_phone_rpc = fake_phone_rpc
        res = asyncio.run(app.state.brain._tool_phone_workspace({'op': 'promote'}))
        assert res['ok'] is True
        assert res['op'] == 'promote'
        assert res['promoted'] is True
        assert rpc_calls == [('promote', {'op': 'promote'})]
        assert app.state.brain._phone_to_foreground_in_turn is True


def test_interrupted_turn_memory_and_async_user_role_bot_delegation(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENT_TOKEN', 'test')
    app = create_app(tmp_path, scheduler=False)
    with TestClient(app, headers={'Authorization': 'Bearer test'}) as client:
        client.post('/api/config', json={'small_brain': {'api_key': 'test', 'model': 'mock-v4'}})
        brain = app.state.brain

        # 1. Simulate companion executing 52pojie tools and getting interrupted via stop_immediately
        brain.streams_by_bot['companion'] = {
            'bot_id': 'companion',
            'source': 'phone',
            'turn_id': 'turn-52pojie',
            'text': '正在调用 cloud_desktop…',
            'thought': '💭 [第1步思考] 先去查吾爱破解开放注册时间\n\n💬 [第1步进展] 余正在打开吾爱破解官网检查注册入口喵～',
        }
        brain.executions_by_bot['companion'] = {
            'bot_id': 'companion',
            'source': 'phone',
            'trace': [
                {'tool': 'memory_search', 'arguments': {'query': '吾爱破解 52pojie 开放注册'}, 'status': 'done', 'duration_ms': 12},
                {'tool': 'cloud_desktop', 'arguments': {'op': 'open', 'url': 'https://www.52pojie.cn/'}, 'status': 'done', 'duration_ms': 45, 'step_note': '余正在打开吾爱破解官网检查注册入口喵～'},
            ],
        }
        asyncio.run(brain.stop_immediately(bot_id='companion', reason='user_preempt', commit_partial=True, source='phone'))

        comp_msgs = [m for m in app.state.store.state['messages'] if m.get('bot_id') == 'companion']
        interrupted_msg = comp_msgs[-1]
        assert interrupted_msg['stopped_early'] is True
        assert '吾爱破解' in interrupted_msg['text'] or '吾爱破解' in interrupted_msg.get('thought', '')
        trace_note = brain._format_message_trace_note(interrupted_msg)
        assert '吾爱破解 52pojie 开放注册' in trace_note
        assert 'https://www.52pojie.cn/' in trace_note

        # 2. User tells companion: "把你刚刚那个任务派发给其他bot，你现在来跟我继续聊天"
        # Verify send_message uses role="user", kind="bot_message", dispatches async so companion replies immediately,
        # and research completes in an independent task without being killed by companion preemption!
        research_started = asyncio.Event()
        research_finish_gate = asyncio.Event()

        async def scenario():
            comp_step = 0

            async def mock_complete(messages, tools=None, big=False):
                nonlocal comp_step
                cur_bot = brain.bot_context.get()
                if cur_bot == 'research':
                    assert messages[-1]['role'] in ('user', 'tool')
                    if not any(m.get('role') == 'tool' for m in messages):
                        assert '吾爱破解' in str(messages[-1]['content'])
                        research_started.set()
                        await research_finish_gate.wait()
                        return {
                            'role': 'assistant',
                            'content': '',
                            'tool_calls': [{'id': 'call_res_done', 'type': 'function', 'function': {'name': 'blackboard', 'arguments': json.dumps({'action': 'complete', 'post_id': 'bb-1', 'summary': '研究助手已完成吾爱破解开放注册监控并设好闹钟！'})}}],
                        }
                    return {'role': 'assistant', 'content': '研究助手已完成吾爱破解开放注册监控并设好闹钟！', 'tool_calls': []}

                comp_step += 1
                if comp_step == 1:
                    hist_dump = json.dumps(messages, ensure_ascii=False)
                    assert '吾爱破解 52pojie 开放注册' in hist_dump
                    return {
                        'role': 'assistant',
                        'content': '',
                        'tool_calls': [
                            {
                                'id': 'call_del_1',
                                'type': 'function',
                                'function': {
                                    'name': 'blackboard',
                                    'arguments': json.dumps({'action': 'post', 'mentions': ['@research'], 'title': '继续监控吾爱破解开放注册', 'text': '请帮我继续监控吾爱破解 (52pojie.cn) 开放注册并设闹钟'}),
                                },
                            }
                        ],
                    }
                if '黑板任务结项回执' in str(messages[-1].get('content', '')):
                    return {'role': 'assistant', 'content': '主人，刚才派给研究助手的后台任务已完成：吾爱破解开放注册监控并设好闹钟啦！', 'tool_calls': []}
                return {'role': 'assistant', 'content': '好嘞主人！刚才查吾爱破解开放注册的活余已经在黑板派给研究助手去后台忙啦，余现在专心陪主人聊天喵～', 'tool_calls': []}

            brain.complete = mock_complete
            comp_reply = await brain.chat('把你刚刚那个任务派发给其他bot，你现在来跟我继续聊天', bot_id='companion', source='phone')
            assert '派给研究助手' in comp_reply['text']

            await asyncio.wait_for(research_started.wait(), 3.0)
            res_room_msgs = [m for m in app.state.store.state['messages'] if m.get('bot_id') == 'research']
            inbound_bm = [m for m in res_room_msgs if m.get('kind') == 'bot_message']
            assert inbound_bm and inbound_bm[-1]['role'] == 'user'
            assert inbound_bm[-1]['direction'] == 'inbound'
            assert not inbound_bm[-1].get('trace')

            await brain.stop_immediately(bot_id='companion', reason='user_preempt', source='phone')
            research_finish_gate.set()
            for _ in range(40):
                if any(m.get('bot_id') == 'companion' and m.get('from_bot_id') == 'companion' and '吾爱破解开放注册监控' in m.get('text', '') for m in app.state.store.state['messages']):
                    break
                await asyncio.sleep(0.05)

            comp_room_msgs = [m for m in app.state.store.state['messages'] if m.get('bot_id') == 'companion']
            assert not any(m.get('kind') == 'bot_reply' for m in comp_room_msgs)
            assert any(m.get('from_bot_id') == 'companion' and '吾爱破解开放注册监控' in m.get('text', '') for m in comp_room_msgs)

        asyncio.run(scenario())


def test_vault_timeline_and_orphaned_bot_message_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENT_TOKEN', 'test')
    app = create_app(tmp_path, scheduler=False)
    with TestClient(app, headers={'Authorization': 'Bearer test'}) as client:
        client.post('/api/config', json={'small_brain': {'api_key': 'test', 'model': 'mock-v4'}, 'big_brain': {'api_key': 'test', 'model': 'mock-v4'}})
        brain = app.state.brain
        store = app.state.store

        async def scenario():
            # 1. User chat in companion logs to timeline/bots/companion, invisible to research grep_search / memory_search
            brain.vault.log('用户：私密口令 VAULT-ISO-999', bot_id='companion')
            tok_r = brain.room_context.set('research')
            tok_b = brain.bot_context.set('research')
            try:
                grep_res = await brain.harness.execute('grep_search', {'query': 'VAULT-ISO-999', 'path': 'memory_vault'})
                assert grep_res.get('hits') == []
                mem_res = await brain._tool_memory_search({'query': 'VAULT-ISO-999'})
                assert mem_res.get('hits') == []
            finally:
                brain.bot_context.reset(tok_b)
                brain.room_context.reset(tok_r)

            # Companion itself CAN find its own timeline log via grep_search
            tok_r2 = brain.room_context.set('companion')
            tok_b2 = brain.bot_context.set('companion')
            try:
                grep_own = await brain.harness.execute('grep_search', {'query': 'VAULT-ISO-999', 'path': 'memory_vault'})
                assert len(grep_own.get('hits') or []) == 1
            finally:
                brain.bot_context.reset(tok_b2)
                brain.room_context.reset(tok_r2)

            # 2. Orphaned incoming bot_message before a human user message is dropped, and answered historical bot_message loses active trigger wrapper
            store.message(
                'user',
                '已结项的委托任务',
                kind='bot_message',
                bot_id='companion',
                from_bot_id='companion',
                from_bot_name='白珂 · 安卓一',
                from_node='phone',
                to_bot_id='companion',
                direction='inbound',
                source='pc',
                llm_text='【跨端/同事 Bot 协作来信】系统会将你的正文回传给「白珂 · 安卓一」\n已结项的委托任务',
            )
            store.message('assistant', '已回复结项喵。', kind='bot_reply', bot_id='companion', from_bot_id='companion', source='pc')
            store.message(
                'user',
                '中断未回复的残留委托 UU远程',
                kind='bot_message',
                bot_id='companion',
                from_bot_id='companion',
                from_bot_name='白珂 · 安卓一',
                from_node='phone',
                to_bot_id='companion',
                direction='inbound',
                source='pc',
                llm_text='【跨端/同事 Bot 协作来信】请检查 UU远程',
            )
            store.message('user', '主人本轮新指令：只回答 1+1', kind='chat', bot_id='companion', source='pc')

            comp_bot = next(b for b in store.state['assistants'] if b['id'] == 'companion')
            msgs, _, _, _ = await brain._build_turn_messages_and_tools('companion', 'pc', comp_bot)
            joined = '\n'.join(str(m.get('content') or '') for m in msgs)
            assert '中断未回复的残留委托 UU远程' not in joined
            assert '系统会将你的正文回传给' not in joined
            assert '[历史协作归档 · 来自「白珂 · 安卓一」' in joined
            assert '主人本轮新指令：只回答 1+1' in str(msgs[-1]['content'])

        asyncio.run(scenario())


def test_strict_multi_bot_room_stream_and_cot_isolation(tmp_path, monkeypatch):
    """
    Guarantee zero mutual leakage between Caller Bot (Bot 1) and Target Bot (Bot 2):
    - Bot 1's live stream events NEVER contain Bot 2's speaker_id, Bot 2's text, or Bot 2's CoT (`thought`).
    - Bot 2's live stream events and room messages NEVER contain Bot 1's CoT (`thought`) or Bot 1's `trace`.
    - Bot 1's room ONLY contains User message + Bot 1's own reply (`from_bot_id == 'companion'`), zero `bot_message`/`bot_reply`.
    - Default `conversation_search` inside Bot 1 scopes to `bot_id == 'companion'`, never leaking Bot 2's private room messages.
    """
    monkeypatch.setenv('AGENT_TOKEN', 'test')
    app = create_app(tmp_path, scheduler=False)
    with TestClient(app, headers={'Authorization': 'Bearer test'}) as client:
        client.post('/api/config', json={'api_base_url': 'https://api.openai.com/v1', 'api_key': 'sk-test-key', 'default_model': 'gpt-4.1-mini'})
        brain = app.state.brain
        published_streams = []
        orig_publish = brain.publish

        async def capture_publish(ev):
            if ev.get('type') == 'stream' and isinstance(ev.get('stream'), dict):
                published_streams.append(dict(ev['stream']))
            await orig_publish(ev)

        brain.publish = capture_publish
        comp_turn = {'n': 0}

        async def mock_complete(messages, tools=None, big=False):
            cur_bot = brain.bot_context.get()
            if cur_bot == 'research':
                await brain._set_stream(text='研究助手内部正在推理…', thought='SECRET_RESEARCH_COT_991', tool='memory_search')
                return {
                    'role': 'assistant',
                    'content': '研究助手独有结论：方案B吞吐高35%，机密代号R2-ONLY。',
                    'reasoning_content': 'SECRET_RESEARCH_COT_991',
                    'tool_calls': [],
                }
            comp_turn['n'] += 1
            if comp_turn['n'] == 1:
                await brain._set_stream(text='主助理先在黑板派单…', thought='SECRET_COMPANION_COT_117', tool='blackboard')
                return {
                    'role': 'assistant',
                    'content': '',
                    'reasoning_content': 'SECRET_COMPANION_COT_117',
                    'tool_calls': [
                        {
                            'id': 'c_iso_1',
                            'type': 'function',
                            'function': {'name': 'blackboard', 'arguments': json.dumps({'action': 'post', 'mentions': ['@research'], 'text': '请评估方案B吞吐'})},
                        }
                    ],
                }
            if comp_turn['n'] == 2:
                await asyncio.sleep(0.05)
                return {
                    'role': 'assistant',
                    'content': '',
                    'reasoning_content': 'SECRET_COMPANION_PEEK_COT',
                    'tool_calls': [
                        {
                            'id': 'c_iso_2',
                            'type': 'function',
                            'function': {'name': 'peek_bots', 'arguments': json.dumps({'target': 'research', 'limit': 3})},
                        }
                    ],
                }
            return {
                'role': 'assistant',
                'content': '余问过研究助手啦，结论是方案B吞吐高35%！',
                'reasoning_content': 'SECRET_COMPANION_SUMMARY_COT_228',
                'tool_calls': [],
            }

        brain.complete = mock_complete
        res = client.post('/api/chat', json={'text': '帮我问问研究助手方案B怎么样', 'bot_id': 'companion', 'source': 'phone'}).json()
        assert '方案B吞吐高35%' in res['text']
        assert res['from_bot_id'] == 'companion'
        assert 'SECRET_RESEARCH_COT_991' not in str(res.get('thought') or '')
        assert 'SECRET_COMPANION_COT_117' in str(res.get('thought') or '')

        # 1. Stream events in companion room NEVER leaked research's CoT or speaker_id
        comp_streams = [s for s in published_streams if s.get('bot_id') == 'companion']
        res_streams = [s for s in published_streams if s.get('bot_id') == 'research']
        assert comp_streams and res_streams
        assert all(s.get('speaker_id') == 'companion' for s in comp_streams)
        assert all('SECRET_RESEARCH_COT_991' not in str(s.get('thought') or '') for s in comp_streams)
        assert all('SECRET_COMPANION_COT_117' not in str(s.get('thought') or '') for s in res_streams)

        # 2. Room messages strictly isolated
        comp_msgs = client.get('/api/messages', params={'bot_id': 'companion'}).json()['messages']
        res_msgs = client.get('/api/messages', params={'bot_id': 'research'}).json()['messages']
        assert not any(m['kind'] in ('bot_message', 'bot_reply') for m in comp_msgs)
        assert all('SECRET_COMPANION_COT_117' not in str(m.get('thought') or '') for m in res_msgs)
        assert all(not m.get('trace') for m in res_msgs if m['kind'] == 'bot_message')

        # 3. Put a private message in research's room and verify default conversation_search & memory_search in companion's room do NOT leak it
        private_res_msg = app.state.store.message('assistant', '研究助手私房笔记：ZETA_PRIVATE_7788', kind='chat', bot_id='research', source='phone')
        brain.context_search.index_message(private_res_msg)

        async def check_search():
            t1 = brain.bot_context.set('companion')
            t2 = brain.room_context.set('companion')
            try:
                c_res = await brain._tool_conversation_search({'query': 'ZETA_PRIVATE_7788'})
                m_res = await brain._tool_memory_search({'query': 'ZETA_PRIVATE_7788'})
                return c_res, m_res
            finally:
                brain.room_context.reset(t2)
                brain.bot_context.reset(t1)

        s_out, m_out = asyncio.run(check_search())
        assert all(r.get('bot_id') != 'research' for r in s_out.get('results', []))
        assert all(r.get('bot_id') != 'research' for r in m_out.get('conversation_hits', []))



def test_scheduled_and_long_trace_never_leave_stuck_stream_or_running_tools(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENT_TOKEN', 'test')
    app = create_app(tmp_path, scheduler=False)
    with TestClient(app, headers={'Authorization': 'Bearer test'}) as client:
        client.post('/api/config', json={'small_brain': {'api_key': 'test', 'model': 'mock-v4'}, 'big_brain': {'api_key': 'test', 'model': 'mock-v4'}})
        brain = app.state.brain
        published_events = []
        orig_publish = brain.publish

        async def capture_publish(ev):
            published_events.append(ev)
            await orig_publish(ev)

        brain.publish = capture_publish

        async def scenario():
            # 1. Simulate a 20-step tool turn to verify `_set_stream` never retains stale `status == 'running'` beyond 16 tools!
            my_exec = {'bot_id': 'companion', 'source': 'phone', 'trace': [], 'tool': 'read_file'}
            brain.executions_by_node_bot['phone:companion'] = my_exec
            brain.executions_by_bot['companion'] = my_exec
            for i in range(20):
                await brain._set_stream(text='正在调用 read_file…', tool='read_file', speaker_id='companion', speaker_name='白珂')
                my_exec['trace'].append({'tool': 'read_file', 'arguments': {'path': f'f{i}.md'}, 'duration_ms': 10, 'status': 'done'})
                await brain._set_stream(text='正在思考…', tool=f'已执行 {i + 1} 步', speaker_id='companion', speaker_name='白珂')
            assert all(t.get('status') == 'done' for t in brain.live_stream['trace'])
            brain._clear_bot_stream('companion', source='phone')

            # 2. Simulate a silent routine calling `stay_silent` in `run_scheduled` -> must publish stream=None and execution=None!
            published_events.clear()
            sched_step = 0

            async def mock_sched_complete(messages, tools=None, big=False):
                nonlocal sched_step
                sched_step += 1
                return {
                    'role': 'assistant',
                    'content': '',
                    'tool_calls': [
                        {
                            'id': 'c_sil_1',
                            'type': 'function',
                            'function': {'name': 'stay_silent', 'arguments': json.dumps({'reason': '静默记账巡检无新消费'})},
                        }
                    ],
                }

            brain.complete = mock_sched_complete
            res = await brain.run_scheduled('【静默记账巡检】检查是否有新账单，无新消费或已记过则静默', bot_id='companion', label='例行巡检 (3h)')
            assert res.get('silent') is True
            assert any(e.get('type') == 'stream' and e.get('stream') is None and e.get('bot_id') == 'companion' for e in published_events)
            assert any(e.get('type') == 'execution' and e.get('execution') is None and e.get('bot_id') == 'companion' for e in published_events)

        asyncio.run(scenario())








