"""
[Module Responsibility]
跨设备互联桥接与外部网关路由模块 (`server/core/bridge_routes.py`)。
负责处理 PC 端 DeepSeek Harness 桥接 (`/api/dsh/*`, `/ws/dsh-bridge`)、
手机端通知与传感器上报 (`/api/phone/*`)、跨分身协作通信 (`/api/message-colleague`)、
外部工作日志 (`/api/work-log`)、设备配对码 (`/api/pairing-info`) 及静态文件与语音转写接口。

[Public Interface]
- `slim_dsh_status(st: dict | None) -> dict`: 精简 DSH 状态快照用于前端同步
- `slim_dsh_peers(peers: list[dict]) -> list[dict]`: 精简 DSH 在线节点列表
- `register_bridge_routes(app, auth, store, vault, brain, hub) -> None`: 注册全部跨端桥接 HTTP/WS 路由

[Dependencies]
- `FastAPI` 路由及鉴权 `auth` 依赖项
- `Store`, `Vault`, `Brain` 实例与运行时状态共享容器 `hub`
"""
from __future__ import annotations
import asyncio, contextlib, hmac, json, os, re, secrets, time
from pathlib import Path
from typing import Any
from fastapi import Depends, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from cryptography.exceptions import InvalidTag
from assistants import get_assistant
from storage import normalize_node_source, node_display_name


def slim_dsh_status(st: dict | None) -> dict:
    if not isinstance(st, dict):
        return {}
    return {k: v for k, v in st.items() if k not in ('sessions', 'dialogs', 'recent_dialogs')}


def slim_dsh_peers(peers: list[dict]) -> list[dict]:
    out = []
    for p in (peers or []):
        if not isinstance(p, dict):
            continue
        item = {k: v for k, v in p.items() if k not in ('sessions', 'dialogs', 'recent_dialogs')}
        if isinstance(item.get('status'), dict):
            item['status'] = slim_dsh_status(item['status'])
        out.append(item)
    return out


def register_bridge_routes(app, auth, store, vault, brain, hub: dict[str, Any] | None = None):
    hub = hub or {}
    harness = hub['harness']
    crypto = hub['crypto']
    token = hub['token']
    clients = hub['clients']
    dsh_clients = hub['dsh_clients']
    phone_read_cache = hub['phone_read_cache']
    publish = hub['publish']
    _is_dsh_online = hub['_is_dsh_online']
    _get_dsh_peers = hub['_get_dsh_peers']
    server_dir = Path(__file__).resolve().parent.parent

    async def _mark_dsh_online(payload: dict | None = None) -> bool:
        was_online = _is_dsh_online()
        now_t = time.time()
        brain.update_dsh_peer(payload if isinstance(payload, dict) else None)
        if isinstance(payload, dict):
            ack_ids = payload.get('ack_ids')
            if isinstance(ack_ids, list) and ack_ids and isinstance(brain.dsh_bridge_info.get('inbox'), list):
                ack_set = {str(x) for x in ack_ids}
                brain.dsh_bridge_info['inbox'] = [
                    item for item in brain.dsh_bridge_info['inbox']
                    if str(item.get('request_id') or item.get('id') or '') not in ack_set
                ]
        if not was_online:
            peers_now = _get_dsh_peers()
            await publish({'type': 'dsh_bridge_state', 'online': True, 'dsh_online': True, 'dsh_peers': peers_now, 'last_seen': now_t})
            await publish({'type': 'state', 'dsh_online': True, 'dsh_peers': peers_now, 'phone_online': len(clients) > 0, 'time': now_t})
        return not was_online

    @app.get('/api/dsh/status',dependencies=[Depends(auth)])
    async def dsh_status(refresh:bool=False,limit:int=3,source:str|None=None,device_id:str|None=None):
        store.sync_from_workspace()
        cap=max(1,min(100,int(limit or 3)))
        pc_online=_is_dsh_online()
        target_id,target_peer=brain.resolve_target_dsh_peer(device_id)
        if refresh:
            res=await brain._tool_dsh_workspace({'op':'status','limit':cap,'device_id':device_id or ''})
            pc_online=bool(res.get('pc_online',pc_online))
        else:
            res=(target_peer.get('status') if isinstance(target_peer,dict) and isinstance(target_peer.get('status'),dict) else None) or brain.dsh_bridge_info.get('status') or {}
            if not pc_online and (server_dir.parent/'dsh-plugin'/'dsh_core.py').is_file():
                res=await brain._tool_dsh_workspace({'op':'status','limit':cap,'device_id':device_id or ''})
                pc_online=bool(res.get('pc_online',False))
        dsh_peers=_get_dsh_peers()
        phone_dialogs,phone_last_time=_extract_phone_last_3_dialogs(limit=cap,source='phone')
        pc_chat_dialogs,pc_chat_time=_extract_phone_last_3_dialogs(limit=cap,source='pc')
        raw_pc_dsh=res.get('last_3_dialogs') if isinstance(res,dict) and isinstance(res.get('last_3_dialogs'),list) else []
        pc_dialogs=(pc_chat_dialogs+raw_pc_dsh)[-cap:]
        pc_last_time=(res.get('last_active_time') if isinstance(res,dict) else '') or pc_chat_time
        req_src=(source or '').strip().lower()
        target_dialogs=pc_dialogs if req_src=='pc' else phone_dialogs
        return {
            'ok':True,
            'product_abbr':'DSB',
            'limit':cap,
            'source':req_src or 'phone',
            'device_id':target_id,
            'dsh_online':pc_online,
            'pc_online':pc_online,
            'dsh_peers':dsh_peers,
            'pc_sentinel_connected':bool(dsh_clients),
            'pc_last_seen':brain.dsh_bridge_info.get('last_seen',0.0),
            'pc_last_active_time':pc_last_time,
            'pc_last_3_dialogs':pc_dialogs,
            'pc_dialogs':pc_dialogs,
            'pc_dsh_status':res,
            'phone_online':len(clients)>0,
            'phone_clients_count':len(clients),
            'phone_last_active_time':phone_last_time,
            'phone_last_3_dialogs':phone_dialogs,
            'phone_dialogs':phone_dialogs,
            'dialogs':target_dialogs,
            'shared_memories':_get_shared_memories_payload(),
            'cloud_bots':[{'id':b['id'],'name':b['name'],'title':b.get('title',''),'description':b.get('description',''),'shape':b.get('shape','circle'),'color':b.get('color','#B568F7'),'avatar_version':b.get('avatar_version',''),'primary':b['id']==store.state.get('primary_bot_id')} for b in store.state.get('assistants',[])],
            'primary_bot_id':store.state.get('primary_bot_id','companion'),
            'work_log_tail':vault.read_work_log_tail(20),
        }

    def _extract_phone_last_3_dialogs(limit:int=3,source:str='phone')->tuple[list[dict],str]:
        return brain.extract_dialogs_by_source(source=source,limit=limit)

    def _get_shared_memories_payload()->dict[str,Any]:
        pins=brain.memory_engine.list_pins()
        return {
            'pins':pins,
            'pinned_memories_md':vault.read('profile/pinned_memories.md')[:4000],
            'user_traits':vault.read('profile/user_traits.md')[:3000],
            'personal_impression':vault.read('profile/personal_impression.md')[:4000],
        }

    @app.api_route('/api/dsh/shared-memory',methods=['GET','POST'],dependencies=[Depends(auth)])
    async def dsh_shared_memory(request:Request):
        payload:dict[str,Any]={}
        if request.method=='POST':
            with contextlib.suppress(Exception):
                raw=await request.json()
                if isinstance(raw,dict):
                    payload=raw
        await _mark_dsh_online(payload)
        op=str(payload.get('op') or payload.get('action') or request.query_params.get('op') or 'list').strip().lower()
        src_raw=str(payload.get('source') or request.query_params.get('source') or 'pc').strip().lower()
        src='phone' if src_raw=='phone' else 'pc'
        if op in ('remember','pin','add','save'):
            text=str(payload.get('text') or payload.get('content') or payload.get('memory') or '').strip()
            if not text:
                raise HTTPException(422,'text 不能为空')
            res=brain.memory_engine.add_pin(text,source=src)
            return {'ok':True,'op':'remember','source':src,'result':res,'shared_memories':_get_shared_memories_payload()}
        if op in ('forget','unpin','remove','delete'):
            target=str(payload.get('pin_id') or payload.get('id') or payload.get('text') or payload.get('query') or '').strip()
            if not target:
                raise HTTPException(422,'pin_id 或 text 不能为空')
            res=brain.memory_engine.remove_pin(target)
            return {'ok':True,'op':'forget','result':res,'shared_memories':_get_shared_memories_payload()}
        if op in ('search','search_memories','query'):
            q=str(payload.get('query') or payload.get('text') or request.query_params.get('query') or '').strip()
            res=await brain._tool_memory_search({'query':q or '主人'})
            return {'ok':True,'op':'search','result':res,'shared_memories':_get_shared_memories_payload()}
        if op in ('update_traits','update_impression'):
            target_file='profile/user_traits.md' if op=='update_traits' else 'profile/personal_impression.md'
            content=str(payload.get('content') or payload.get('text') or '').strip()
            if content:
                vault.write(target_file,content)
            return {'ok':True,'op':op,'shared_memories':_get_shared_memories_payload()}
        return {'ok':True,'op':'list','shared_memories':_get_shared_memories_payload()}

    @app.api_route('/api/dsh/heartbeat',methods=['GET','POST'],dependencies=[Depends(auth)])
    async def dsh_heartbeat(request:Request):
        payload:dict[str,Any]={}
        if request.method=='POST':
            with contextlib.suppress(Exception):
                raw=await request.json()
                if isinstance(raw,dict):
                    payload=raw
        flipped=await _mark_dsh_online(payload)
        cap=max(1,min(100,int(payload.get('limit') or request.query_params.get('limit') or 3)))
        inbox=list(brain.dsh_bridge_info.get('inbox') or [])
        phone_last_3,phone_last_time=_extract_phone_last_3_dialogs(limit=cap,source='phone')
        return {
            'ok':True,
            'dsh_online':True,
            'pc_online':True,
            'flipped':flipped,
            'limit':cap,
            'phone_online':len(clients)>0,
            'phone_last_active_time':phone_last_time,
            'phone_last_3_dialogs':phone_last_3,
            'phone_dialogs':phone_last_3,
            'shared_memories':_get_shared_memories_payload(),
            'dsh_peers':_get_dsh_peers(),
            'inbox':inbox,
            'pending_commands':inbox,
            'primary_bot_id':store.state.get('primary_bot_id','companion'),
            'time':time.time(),
        }

    @app.api_route('/api/dsh/inbox',methods=['GET','POST'],dependencies=[Depends(auth)])
    async def dsh_inbox(request:Request):
        payload:dict[str,Any]={}
        if request.method=='POST':
            with contextlib.suppress(Exception):
                raw=await request.json()
                if isinstance(raw,dict):
                    payload=raw
        await _mark_dsh_online(payload)
        if payload.get('request_id') and ('result' in payload or 'data' in payload):
            brain.resolve_dsh_command(payload)
        caller_dev=str(payload.get('device_id') or payload.get('peer_id') or request.query_params.get('device_id') or '').strip()
        wait_sec=min(15.0,max(0.0,float(payload.get('wait_sec') or request.query_params.get('wait_sec') or 0.0)))

        def _pick_inbox()->tuple[list[dict],list[dict]]:
            cur_all=list(brain.dsh_bridge_info.get('inbox') or [])
            if caller_dev and caller_dev!='pc_dsh':
                picked=[
                    item for item in cur_all
                    if not item.get('device_id') or item.get('device_id') in ('pc_dsh',caller_dev)
                ]
            else:
                picked=cur_all
            return cur_all,picked

        all_inbox,inbox=_pick_inbox()
        if not inbox and wait_sec>0:
            deadline=time.monotonic()+wait_sec
            while time.monotonic()<deadline:
                await asyncio.sleep(0.25)
                brain.dsh_bridge_info['last_seen']=time.time()
                brain.dsh_bridge_info['online']=True
                all_inbox,inbox=_pick_inbox()
                if inbox:
                    break

        if inbox and (payload.get('drain') or request.query_params.get('drain') in ('1','true')):
            if caller_dev and caller_dev!='pc_dsh':
                drained_ids={id(x) for x in inbox}
                brain.dsh_bridge_info['inbox']=[item for item in all_inbox if id(item) not in drained_ids]
            else:
                brain.dsh_bridge_info['inbox']=[]
        if payload.get('slim') or request.query_params.get('slim') in ('1','true'):
            return {
                'ok':True,
                'dsh_online':True,
                'phone_online':len(clients)>0,
                'inbox':inbox,
                'commands':inbox,
                'time':time.time(),
            }
        cap=max(1,min(100,int(payload.get('limit') or request.query_params.get('limit') or 3)))
        phone_last_3,phone_last_time=_extract_phone_last_3_dialogs(limit=cap,source='phone')
        return {
            'ok':True,
            'dsh_online':True,
            'limit':cap,
            'phone_online':len(clients)>0,
            'phone_last_active_time':phone_last_time,
            'phone_last_3_dialogs':phone_last_3,
            'phone_dialogs':phone_last_3,
            'shared_memories':_get_shared_memories_payload(),
            'dsh_peers':_get_dsh_peers(),
            'inbox':inbox,
            'commands':inbox,
            'work_log_tail':vault.read_work_log_tail(20),
            'time':time.time(),
        }

    @app.api_route('/api/dsh/api',methods=['GET','POST'],dependencies=[Depends(auth)])
    async def dsh_unified_api(request:Request):
        payload:dict[str,Any]={}
        if request.method=='POST':
            with contextlib.suppress(Exception):
                raw=await request.json()
                if isinstance(raw,dict):
                    payload=raw
        await _mark_dsh_online(payload)
        op=str(payload.get('op') or request.query_params.get('op') or 'status').strip().lower()
        if op=='heartbeat':
            return await dsh_heartbeat(request)
        if op=='inbox':
            return await dsh_inbox(request)
        if op in ('result','dsh_workspace_result'):
            brain.resolve_dsh_command(payload)
            return {'ok':True,'accepted':True}
        if op in ('message_phone_bot','message_colleague','send_message'):
            return await dsh_message_phone_bot(payload)
        if op in ('shared_memory','remember','forget','list_memories','search_memories','pin','unpin'):
            return await dsh_shared_memory(request)
        cap=int(payload.get('limit') or request.query_params.get('limit') or 3)
        src=payload.get('source') or request.query_params.get('source')
        dev_id=payload.get('device_id') or request.query_params.get('device_id')
        return await dsh_status(refresh=bool(payload.get('refresh',False)),limit=cap,source=src,device_id=dev_id)

    @app.post('/api/dsh/command',dependencies=[Depends(auth)])
    async def dsh_command(payload:dict[str,Any]):
        tok=brain.source_context.set(str(payload.get('source') or 'pc'))
        try:
            return await brain._tool_dsh_workspace(payload)
        finally:
            brain.source_context.reset(tok)

    @app.post('/api/computer/command',dependencies=[Depends(auth)])
    async def computer_command_direct(payload:dict[str,Any]):
        return await brain._tool_cloud_desktop(payload)

    @app.get('/api/pairing-info',dependencies=[Depends(auth)])
    async def pairing_info(request:Request):
        import urllib.parse as _up
        host_hdr=request.headers.get('x-forwarded-host') or request.headers.get('host') or '216.23.82.196'
        scheme=request.headers.get('x-forwarded-proto') or request.url.scheme or 'http'
        base_url=os.getenv('DS_CLAW_PUBLIC_URL') or f'{scheme}://{host_hdr}/proactive-agent'
        pub=crypto.public()
        pair_uri=f"dsclaw://pair?server={_up.quote(base_url,safe='')}&token={_up.quote(token,safe='')}&fp={_up.quote(str(pub.get('fingerprint','')),safe='')}"
        return {
            'ok':True,
            'app_name':'DSB',
            'product_abbr':'DSB',
            'server_url':base_url,
            'token':token,
            'fingerprint':pub.get('fingerprint',''),
            'pairing_uri':pair_uri,
            'one_paste_code':f'DSCLAW1:{base_url}|{token}',
            'pair_code':f'DSCLAW1:{base_url}|{token}',
        }

    @app.post('/api/dsh/result',dependencies=[Depends(auth)])
    async def dsh_result_fallback(payload:dict[str,Any]):
        await _mark_dsh_online(payload)
        brain.resolve_dsh_command(payload)
        return {'accepted':True,'ok':True}

    @app.post('/api/dsh/message-phone-bot',dependencies=[Depends(auth)])
    @app.post('/api/dsh/message-colleague',dependencies=[Depends(auth)])
    @app.post('/api/message-colleague',dependencies=[Depends(auth)])
    async def dsh_message_phone_bot(payload:dict[str,Any]):
        await _mark_dsh_online(payload)
        op=str(payload.get('op') or 'send_message').strip().lower()
        if op in ('status','peek_dialogs','list_bots'):
            cap=max(1,min(100,int(payload.get('limit') or 3)))
            src=normalize_node_source(payload.get('source') or 'phone')
            dialogs,last_time=_extract_phone_last_3_dialogs(limit=cap,source=src)
            return {
                'ok':True,
                'op':op,
                'source':src,
                'limit':cap,
                'phone_online':len(clients)>0,
                'last_active_time':last_time,
                'dialogs':dialogs,
                'phone_dialogs':dialogs,
                'bots':store.state['assistants'],
            }
        text=str(payload.get('message') or payload.get('text') or '').strip()
        if not text:
            raise HTTPException(422,'message 不能为空')
        store.sync_from_workspace()
        raw_bot=str(payload.get('bot_id') or payload.get('to_bot_id') or payload.get('target') or payload.get('colleague') or store.state.get('primary_bot_id') or 'companion').strip()
        explicit_target_node=str(payload.get('target_node') or payload.get('node') or payload.get('source') or '').strip()
        parsed_src,bot_part=brain._parse_cross_node_target(raw_bot,explicit_target_node,'pc')
        if explicit_target_node or ':' in raw_bot or '@' in raw_bot or '·' in raw_bot or '/' in raw_bot:
            target_src=parsed_src or normalize_node_source(explicit_target_node or 'phone')
        else:
            target_src=parsed_src if (parsed_src and parsed_src!='pc') else 'phone'
        target_bot=brain._resolve_bot(bot_part or raw_bot)
        if target_bot is None:
            try:
                target_bot=get_assistant(store,store.state.get('primary_bot_id','companion'))
            except Exception:
                raise HTTPException(404,f'未找到目标 Bot: {raw_bot}')
        target_id=target_bot['id']
        raw_sess=str(payload.get('from_dsh_session') or payload.get('session_id') or '').strip()[:24]
        from_sess=f'DSH({raw_sess})' if raw_sess and not raw_sess.upper().startswith('DSH') else (raw_sess or 'Windows一')
        mode=str(payload.get('mode') or 'auto').strip()
        if mode not in ('auto','chat','execute'):
            mode='auto'
        phone_online=len(clients)>0
        prefixed_text=f'[来自「{from_sess}」的跨端消息]:\n{text}'
        res=await brain.chat(prefixed_text,target_id,mode,source=target_src)
        reply_text=str((res or {}).get('text') or '')
        wline=vault.append_work_log(f'[{from_sess} -> {node_display_name(target_src)}·{target_bot["name"]}] 「{text[:40].replace(chr(10)," ")}」→ {reply_text[:55].replace(chr(10)," ")}')
        return {
            'ok':True,
            'phone_online':phone_online,
            'dsh_online':True,
            'source':target_src,
            'node_name':node_display_name(target_src),
            'bot_id':target_id,
            'bot_name':target_bot['name'],
            'reply':reply_text,
            'trace':(res or {}).get('trace',[]),
            'work_log_line':wline,
            'seq':brain.stream_seq,
        }

    @app.get('/api/peek-bots',dependencies=[Depends(auth)])
    async def api_peek_bots(target:str='all',limit:int=5):
        return brain.blackboard_engine.peek_bots({'target':target,'limit':limit})

    @app.api_route('/api/blackboard',methods=['GET','POST'],dependencies=[Depends(auth)])
    async def api_blackboard(request:Request):
        payload:dict[str,Any]={}
        if request.method=='POST':
            with contextlib.suppress(Exception):
                raw=await request.json()
                if isinstance(raw,dict):
                    payload=dict(raw)
        else:
            payload={k:v for k,v in request.query_params.items()}
        payload.setdefault('action','list')
        if payload.get('bot_id') and '_caller_bot_id' not in payload:
            payload['_caller_bot_id']=str(payload['bot_id'])
        if payload.get('source') and '_caller_source' not in payload:
            payload['_caller_source']=str(payload['source'])
        return await brain.blackboard_engine.execute(payload)

    @app.get('/api/work-log',dependencies=[Depends(auth)])
    async def get_work_log(limit:int=30):
        return {'ok':True,'entries':vault.read_work_log_tail(limit),'content':vault.read('timeline/work_log.md')}

    @app.post('/api/work-log',dependencies=[Depends(auth)])
    async def add_work_log(payload:dict[str,Any]):
        summary=str(payload.get('summary') or payload.get('line') or '').strip()
        if not summary:
            raise HTTPException(422,'summary 不能为空')
        entry=vault.append_work_log(summary)
        return {'ok':True,'entry':entry,'entries':vault.read_work_log_tail(20)}

    @app.post('/api/phone/result',dependencies=[Depends(auth)])
    async def phone_result_fallback(payload:dict[str,Any]):
        brain.resolve_phone_command(payload)
        return {'accepted':True}

    @app.post('/api/phone/command',dependencies=[Depends(auth)])
    async def phone_command(payload:dict[str,Any]):
        op=str(payload.get('op') or 'status').strip()
        src_key=normalize_node_source(payload.get('source') or 'phone')
        cache_key=f'{src_key}:{op}'
        force=bool(payload.get('force',False))
        now=time.time()
        ttl=300.0 if op=='apps' else (1.5 if op in ('status','ui_tree') else 0.0)
        if ttl>0 and not force and cache_key in phone_read_cache:
            ts,cached_val=phone_read_cache[cache_key]
            if now-ts<ttl:
                return {'cached':True,**cached_val} if isinstance(cached_val,dict) else cached_val
        res=await brain._tool_phone_workspace(payload)
        if ttl>0 and isinstance(res,dict) and not res.get('error') and not res.get('warning'):
            phone_read_cache[cache_key]=(now,res)
        elif op in ('launch','tap_node','type_node','tap','swipe','key','shell','release','auto_release'):
            phone_read_cache.pop(f'{src_key}:ui_tree',None)
            phone_read_cache.pop(f'{src_key}:status',None)
        return res


    @app.websocket('/ws/dsh-bridge')
    async def dsh_bridge_ws(ws:WebSocket):
        await ws.accept()
        queue=asyncio.Queue(128)
        bridge_id='dsh-'+secrets.token_hex(6)
        sender=None
        key=None
        try:
            envelope=await asyncio.wait_for(ws.receive_json(),12)
            if isinstance(envelope,dict) and 'key' in envelope and 'data' in envelope:
                key,hello=crypto.open(envelope)
            else:
                hello=envelope if isinstance(envelope,dict) else {}
            if not hmac.compare_digest(str(hello.get('token','')),token):
                await ws.close(code=4401); return
            hello_dev=str(hello.get('device_id') or hello.get('peer_id') or 'pc_dsh').strip()
            dsh_clients[bridge_id]={'queue':queue,'device_id':hello_dev}
            brain.dsh_bridge_info['online']=True
            brain.dsh_bridge_info['ws_connected']=True
            brain.dsh_bridge_info['last_seen']=time.time()
            brain.update_dsh_peer(hello if isinstance(hello,dict) else None)
            async def send(event):
                if key is not None:
                    await ws.send_json(crypto.seal(key,event))
                else:
                    await ws.send_json(event)
            await send({'type':'connected','bridge_id':bridge_id,'phone_online':len(clients)>0,'dsh_online':True,'dsh_peers':_get_dsh_peers(),'product_abbr':'DSB','time':time.time()})
            await publish({'type':'dsh_bridge_state','online':True,'dsh_online':True,'dsh_peers':_get_dsh_peers(),'last_seen':brain.dsh_bridge_info['last_seen']})
            async def pump():
                while True:
                    try: event=await asyncio.wait_for(queue.get(),25)
                    except asyncio.TimeoutError: event={'type':'heartbeat','phone_online':len(clients)>0,'dsh_online':True,'dsh_peers':_get_dsh_peers(),'time':time.time()}
                    await send(event)
            sender=asyncio.create_task(pump())
            while True:
                packet=await ws.receive_json()
                event=crypto.frame(key,packet) if (key is not None and isinstance(packet,dict) and 'data' in packet) else packet
                if not isinstance(event,dict): continue
                brain.dsh_bridge_info['last_seen']=time.time()
                brain.dsh_bridge_info['online']=True
                if event.get('device_id') or event.get('peer_id'):
                    dsh_clients[bridge_id]['device_id']=str(event.get('device_id') or event.get('peer_id')).strip()
                etype=event.get('type')
                if etype=='ping':
                    brain.update_dsh_peer(event)
                    queue.put_nowait({'type':'pong','phone_online':len(clients)>0,'dsh_online':True,'dsh_peers':_get_dsh_peers(),'time':time.time()})
                elif etype in ('dsh_workspace_result','result'):
                    brain.resolve_dsh_command(event)
                elif etype=='status_update' and isinstance(event.get('status'),dict):
                    brain.update_dsh_peer(event)
        except (WebSocketDisconnect,asyncio.TimeoutError,ValueError,KeyError,InvalidTag):
            with contextlib.suppress(Exception): await ws.close(code=4400)
        finally:
            dsh_clients.pop(bridge_id,None)
            brain.dsh_bridge_info['ws_connected']=len(dsh_clients)>0
            brain.dsh_bridge_info['online']=_is_dsh_online()
            if sender:
                sender.cancel()
                with contextlib.suppress(asyncio.CancelledError): await sender
            with contextlib.suppress(Exception):
                await publish({'type':'dsh_bridge_state','online':_is_dsh_online(),'dsh_online':_is_dsh_online(),'dsh_peers':_get_dsh_peers(),'last_seen':brain.dsh_bridge_info['last_seen']})

    @app.get('/static/avatars/{filename}')
    @app.get('/avatars/{filename}')
    async def get_static_avatar(filename:str):
        safe_file=Path(filename).name
        p=server_dir/'static'/'avatars'/safe_file
        if not p.is_file() or p.suffix.lower() not in ('.png','.jpg','.jpeg','.webp'):
            raise HTTPException(404,'预设头像不存在')
        media='image/png' if p.suffix.lower()=='.png' else ('image/webp' if p.suffix.lower()=='.webp' else 'image/jpeg')
        return FileResponse(p,media_type=media)

    @app.get('/api/avatars/presets')
    async def list_avatar_presets():
        return {
            'ok':True,
            'style':'grok_bot',
            'presets':[
                {'id':'whalechan','name':'Grok Bot · 鲸鱼娘 (Whale-chan)','shape':'whalechan','url':'/static/avatars/whalechan_grok.png'},
                {'id':'whale','name':'Grok Bot · 大鲸鱼 (Big Whale)','shape':'whale','url':'/static/avatars/whale_grok.png'},
            ],
        }

    @app.get('/stickers/{pack_id}/{filename}')
    async def get_sticker_file(pack_id:str,filename:str):
        safe_pack=re.sub(r'[^a-zA-Z0-9_-]','',pack_id)
        safe_file=Path(filename).name
        p=harness.root/'plugins'/'stickers'/safe_pack/safe_file
        if not p.is_file(): raise HTTPException(404,'表情包不存在')
        media='image/webp' if safe_file.endswith('.webp') else 'image/png'
        return FileResponse(p,media_type=media)

    @app.get('/workspace-media/{rel_path:path}')
    async def get_workspace_media(rel_path:str,request:Request,authorization:str=Header('')):
        clean_rel=rel_path.removeprefix('workspace/').lstrip('/').replace('\\','/')
        q_tok=str(request.query_params.get('token') or '').strip()
        is_authed=(authorization and hmac.compare_digest(authorization,'Bearer '+token)) or (q_tok and hmac.compare_digest(q_tok,token))
        is_public_img=clean_rel.startswith('uploads/') or ('/' not in clean_rel and not clean_rel.startswith('.'))
        if not is_authed and not is_public_img:
            raise HTTPException(401,'访问非公开工作区图片需要携带有效鉴权')
        try:
            p=harness.path(clean_rel)
        except Exception:
            raise HTTPException(400,'非法路径')
        if not p.is_file() or p.suffix.lower() not in ('.png','.jpg','.jpeg','.webp','.gif','.bmp','.svg'):
            raise HTTPException(404,'图片不存在')
        media={'.png':'image/png','.webp':'image/webp','.gif':'image/gif','.bmp':'image/bmp','.svg':'image/svg+xml'}.get(p.suffix.lower(),'image/jpeg')
        return FileResponse(p,media_type=media)

    @app.get('/api/plugins/stickers',dependencies=[Depends(auth)])
    async def list_sticker_packs():
        harness.extension_registry.discover_plugins_dir(harness.root/'plugins')
        return {'packs':harness.extension_registry.summary().get('sticker_packs',[])}

    @app.post('/api/plugins/stickers/toggle',dependencies=[Depends(auth)])
    async def toggle_sticker_pack(request:Request):
        body=await request.json()
        pid=str(body.get('pack_id') or 'fubuki').strip()
        en=bool(body.get('enabled',True))
        ok=harness.extension_registry.set_sticker_pack_enabled(pid,en)
        return {'ok':ok,'packs':harness.extension_registry.summary().get('sticker_packs',[])}

    @app.api_route('/download/proactive-agent.apk',methods=['GET','HEAD'])
    async def download():
        path=Path(os.getenv('AGENT_APK',server_dir.parent/'dist/proactive-agent.apk'))
        if not path.exists(): raise HTTPException(404,'APK 尚未发布')
        return FileResponse(path,media_type='application/vnd.android.package-archive',filename='proactive-agent.apk')

    @app.get('/download/release.json')
    async def release_info():
        apk=Path(os.getenv('AGENT_APK',server_dir.parent/'dist/proactive-agent.apk'))
        path=apk.with_name('release.json')
        data:dict={}
        if path.exists():
            try:
                data=json.loads(path.read_text('utf-8-sig'))
            except Exception:
                data={}
        ver=str(data.get('versionName') or data.get('version_name') or data.get('version') or str(data.get('tag_name') or '').lstrip('v') or '3.0.3').strip()
        code=int(data.get('versionCode') or data.get('version_code') or data.get('build') or 55)
        notes=str(data.get('notes') or data.get('body') or f'v{ver} (Build {code})').strip()
        apk_size=apk.stat().st_size if apk.exists() else int(data.get('apk_size') or 0)
        return {
            **data,
            'version':ver,
            'version_name':ver,
            'versionName':ver,
            'tag_name':f'v{ver}',
            'version_code':code,
            'versionCode':code,
            'build':code,
            'apk_size':apk_size,
            'download_url':data.get('download_url','/proactive-agent/download/proactive-agent.apk'),
            'notes':notes,
            'body':notes,
        }

    @app.api_route('/download/mika-corresponding-source.tar.gz',methods=['GET','HEAD'])
    async def mika_corresponding_source():
        apk=Path(os.getenv('AGENT_APK',server_dir.parent/'dist/proactive-agent.apk'))
        path=apk.with_name('mika-corresponding-source.tar.gz')
        if not path.exists():raise HTTPException(404,'源码包尚未发布')
        return FileResponse(path,media_type='application/gzip',filename=path.name)

    @app.get('/',response_class=HTMLResponse)
    async def landing():
        return '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><style>body{background:#0B0D12;color:#edf0fa;font:17px system-ui;max-width:650px;margin:12vh auto;padding:28px}small{color:#38bdf8;letter-spacing:4px}h1{font-size:48px}a{display:inline-block;background:linear-gradient(120deg,#0ea5e9,#28b9d1);color:white;padding:18px 28px;border-radius:18px;text-decoration:none}p{line-height:1.8;color:#a7aec3}</style><small>DSB · PROACTIVE AGENT · TOKYO</small><h1>DSB 🐳</h1><p>多 Bot 群组协作 · 电脑端 DeepSeek Harness 双向零配置互通 · 透明记忆 · 1C1G 极简云桌面<br>你的全天候跨端智能搭档，已在节点等候。</p><a href="./download/proactive-agent.apk">↓ 下载 DSB Android APK</a><p>安装后直接填入 API Base URL 与 API Key（或粘贴 AI 自动部署生成的配对串）即可一键连接。</p>'

