from __future__ import annotations
import asyncio, contextlib, hmac, json, os, re, secrets, time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
import httpx
from fastapi import FastAPI, Depends, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field
from cryptography.exceptions import InvalidTag
from brain import Brain
from escalation import Escalation
from harness import Harness, TOOLS
from security import CryptoBox
from storage import Store, compact_trace_list, run_storage_maintenance, normalize_node_source, node_display_name
from vault_manager import Vault, atomic_write
from assistants import AssistantInput, AssistantPlacement, RoutineInput, RoutineUpdate, get_assistant, save_assistant, place_assistant, resolve_bot_for_source, resolve_bots_for_source
from bot_identity import install_identity_routes
from computer_api import ComputerCoordinator, install_computer_routes
from computer_files import WorkspaceFiles, install_file_routes

class ChatInput(BaseModel):
    text:str=Field(min_length=1,max_length=12000)
    bot_id:str|None=Field(None,max_length=40)
    mode:Literal['auto','chat','execute']='auto'
    async_ack:bool=False
    client_msg_id:str|None=Field(None,max_length=80)
    source:str=Field('phone',max_length=60)
class StopInput(BaseModel):
    bot_id:str|None=Field(None,max_length=40)
    source:str|None=Field(None,max_length=60)
class VaultInput(BaseModel):
    path:str=Field(min_length=1,max_length=240)
    content:str=Field(max_length=512000)
class SnoozeInput(BaseModel):
    minutes:int=Field(60,ge=0,le=1440)
class AckInput(BaseModel):
    ids:list[str]=Field(default_factory=list,max_length=600)
class DecisionInput(BaseModel):
    id:str
    accepted:bool
class ResearchInput(BaseModel):
    goal:str|None=Field(None,max_length=3000)
    bot_id:str|None=Field(None,max_length=40)
    record_user:bool=False
class DeadlineInput(BaseModel):
    title:str=Field(min_length=1,max_length=300)
    due_at:str
class VisionInput(BaseModel):
    image:str=Field(max_length=4000000)
    goal:str=Field(min_length=1,max_length=2000)
    width:int=Field(ge=1,le=4096)
    height:int=Field(ge=1,le=4096)
    display_id:int=Field(ge=1)
class ModelsInput(BaseModel):
    base_url:str|None=Field(None,max_length=500)
    api_key:str|None=Field(None,max_length=4096)
class EditMessageInput(BaseModel):
    message_id:str=Field(min_length=1,max_length=80)
    text:str=Field(min_length=1,max_length=12000)
    bot_id:str=Field('companion',max_length=40)
    source:str|None=Field(None,max_length=60)
class RerollInput(BaseModel):
    bot_id:str=Field('companion',max_length=40)
    message_id:str|None=Field(None,max_length=80)
    source:str|None=Field(None,max_length=60)
class PinInput(BaseModel):
    action:Literal['add','remove','list','pin','unpin','remember','forget']='list'
    text:str|None=Field(None,max_length=500)
    pin_id:str|None=Field(None,max_length=40)
    category:str=Field('general',max_length=40)
    source:str=Field('phone',max_length=60)

def create_app(data_dir=None, scheduler=True):
    root=Path(data_dir or os.getenv('AGENT_DATA_DIR',Path(__file__).parent/'data'))
    store=Store(root)
    vault=Vault(root/'memory_vault')
    harness=Harness(root/'workspace',store=store,vault=vault)
    crypto=CryptoBox(root)
    token=os.getenv('AGENT_TOKEN')
    if not token:
        tp=root/'device-token'
        if not tp.exists(): atomic_write(tp,secrets.token_urlsafe(36))
        token=tp.read_text('utf-8').strip()
    clients:dict[str,dict[str,Any]]={}
    dsh_clients:dict[str,dict[str,Any]]={}
    def _event_node_source(event:dict)->str|None:
        if not isinstance(event,dict):
            return None
        etype=event.get('type')
        if etype not in ('message','stream','execution','stopped','messages_truncated','context_compacted','phone_workspace_command'):
            return None
        raw=event.get('source')
        if not raw and isinstance(event.get('message'),dict):
            raw=event['message'].get('source')
        if not raw and isinstance(event.get('stream'),dict):
            raw=event['stream'].get('source')
        if not raw and isinstance(event.get('execution'),dict):
            raw=event['execution'].get('source')
        return normalize_node_source(raw) if raw else None

    async def publish(event):
        ev_src=_event_node_source(event)
        for info in list(clients.values()):
            q=info['queue']
            c_src=info.get('source','phone')
            if ev_src is not None and ev_src!=c_src:
                continue
            if q.full():
                with contextlib.suppress(asyncio.QueueEmpty): q.get_nowait()
            q.put_nowait(event)
    async def dispatch_dsh(event):
        target_dev=str(event.get('device_id') or '').strip() if isinstance(event,dict) else ''
        for info in list(dsh_clients.values()):
            queue=info['queue'] if isinstance(info,dict) else info
            c_dev=str(info.get('device_id') or '').strip() if isinstance(info,dict) else ''
            if target_dev and target_dev!='pc_dsh' and c_dev and c_dev not in ('pc_dsh',target_dev):
                continue
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty): queue.get_nowait()
            queue.put_nowait(event)
    escalation=Escalation(store,vault,publish)
    brain=Brain(store,vault,harness,publish,escalation)
    brain.dsh_dispatch=dispatch_dsh
    brain.phone_online_checker=lambda: len(clients)>0
    computer=ComputerCoordinator(store,brain,publish)
    brain.computer=computer
    internal_client_holder:dict[str,httpx.AsyncClient]={}
    desktop_status_cache:dict[str,Any]={
        'ts':0.0,
        'refreshing':False,
        'data':{'configured':bool(computer.client.configured),'available':bool(computer.client.configured),'ready':bool(computer.client.configured)},
    }

    async def _periodic_storage_gc():
        await asyncio.sleep(5)
        while True:
            with contextlib.suppress(Exception):
                await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: run_storage_maintenance(
                        store, vault=vault, context_search=brain.context_search, workspace_dir=harness.root
                    ),
                )
            await asyncio.sleep(3600)

    @asynccontextmanager
    async def lifespan(app):
        internal_client_holder['client']=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local',timeout=httpx.Timeout(600.0,connect=30.0))
        task=asyncio.create_task(brain.scheduler()) if scheduler else None
        gc_task=asyncio.create_task(_periodic_storage_gc()) if scheduler else None
        brain.activity=brain.ready_state()
        computer.ensure_monitor()
        try: yield
        finally:
            ic=internal_client_holder.pop('client',None)
            if ic:
                with contextlib.suppress(Exception): await ic.aclose()
            await computer.close()
            if gc_task:
                gc_task.cancel()
                with contextlib.suppress(asyncio.CancelledError): await gc_task
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError): await task
            store.save()

    app=FastAPI(title='DeepSeek Claw · Proactive Agent',version='2.7.0',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.add_middleware(GZipMiddleware,minimum_size=500)
    app.state.store,app.state.vault,app.state.brain,app.state.escalation,app.state.crypto=store,vault,brain,escalation,crypto
    app.state.computer=computer
    phone_read_cache:dict[str,tuple[float,Any]]={}

    async def auth(authorization:str=Header('')):
        if not hmac.compare_digest(authorization,'Bearer '+token): raise HTTPException(401,'设备未配对或配对码不正确')

    install_computer_routes(app,auth,computer)
    install_file_routes(app,auth,WorkspaceFiles(harness.root,root/'.transfers'))
    install_identity_routes(app,auth,store,brain,publish)

    @app.middleware('http')
    async def size_limit(request:Request,call_next):
        if int(request.headers.get('content-length','0'))>6_000_000:
            from fastapi.responses import JSONResponse
            return JSONResponse({'detail':'请求过大'},status_code=413)
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Cache-Control']='no-store' if '/api/' in request.url.path else 'no-cache'
        return response

    @app.get('/health')
    async def health(): return {'status':'ok','service':'proactive-agent','version':'2.7.0'}

    @app.get('/api/transport')
    async def transport(): return crypto.public()

    @app.post('/api/secure')
    async def secure(request:Request):
        try:
            key,data=crypto.open(await request.json())
            if not hmac.compare_digest(str(data.get('token','')),token):
                return crypto.seal(key,{'status':401,'body':{'detail':'设备配对码不正确'}})
            path=str(data['path'] or '')
            if not path.startswith('/api/') or path in ('/api/secure','/api/transport') or '?' in path or '#' in path or '..' in path or '//' in path:
                raise ValueError('非法 API 路径')
            ic=internal_client_holder.get('client')
            if ic is not None:
                response=await ic.request(data['method'],path,params=data.get('query'),json=data.get('body'),headers={'Authorization':'Bearer '+token})
            else:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local',timeout=httpx.Timeout(600.0,connect=30.0)) as client:
                    response=await client.request(data['method'],path,params=data.get('query'),json=data.get('body'),headers={'Authorization':'Bearer '+token})
            try:
                resp_body=response.json()
            except Exception:
                resp_body={'detail':response.text[:1000] if response.text else f'HTTP {response.status_code}'}
            return crypto.seal(key,{'status':response.status_code,'body':resp_body})
        except (ValueError,KeyError,TypeError,InvalidTag): raise HTTPException(400,'加密请求无效、过期或重放')

    async def _refresh_desktop_status_once()->dict:
        try:
            desktop=await asyncio.wait_for(computer.client.status(),timeout=1.5)
            saved=store.state.get('computer_job',{})
            if (desktop.get('job') or {}).get('id')==saved.get('id'):
                desktop['bot_id']=saved.get('bot_id')
            desktop_status_cache['data']=desktop
        except Exception:
            pass
        finally:
            desktop_status_cache['ts']=time.time()
            desktop_status_cache['refreshing']=False
        return dict(desktop_status_cache['data'])

    async def _fast_desktop_status()->dict:
        now_t=time.time()
        if now_t-desktop_status_cache['ts']<3.0:
            return dict(desktop_status_cache['data'])
        if computer.client.transport is not None or desktop_status_cache['ts']==0.0:
            desktop_status_cache['ts']=now_t
            return await _refresh_desktop_status_once()
        if not desktop_status_cache.get('refreshing'):
            desktop_status_cache['refreshing']=True
            desktop_status_cache['ts']=now_t
            asyncio.create_task(_refresh_desktop_status_once())
        return dict(desktop_status_cache['data'])

    def _slim_exec(ex:dict|None)->dict|None:
        if not isinstance(ex,dict): return None
        out=dict(ex)
        if 'trace' in out:
            out['trace']=compact_trace_list(out['trace'])
        return out

    def _is_dsh_online()->bool:
        return brain.is_dsh_online(len(dsh_clients))

    def _get_dsh_peers()->list[dict]:
        return brain.get_dsh_peers(len(dsh_clients),len(clients))

    def _slim_dsh_status(st:dict|None)->dict:
        if not isinstance(st,dict): return {}
        return {k:v for k,v in st.items() if k not in ('sessions','dialogs','recent_dialogs')}

    def _slim_dsh_peers(peers:list[dict])->list[dict]:
        out=[]
        for p in (peers or []):
            if not isinstance(p,dict): continue
            item={k:v for k,v in p.items() if k not in ('sessions','dialogs','recent_dialogs')}
            if isinstance(item.get('status'),dict):
                item['status']=_slim_dsh_status(item['status'])
            out.append(item)
        return out

    async def _mark_dsh_online(payload:dict|None=None)->bool:
        was_online=_is_dsh_online()
        now_t=time.time()
        brain.update_dsh_peer(payload if isinstance(payload,dict) else None)
        if isinstance(payload,dict):
            ack_ids=payload.get('ack_ids')
            if isinstance(ack_ids,list) and ack_ids and isinstance(brain.dsh_bridge_info.get('inbox'),list):
                ack_set={str(x) for x in ack_ids}
                brain.dsh_bridge_info['inbox']=[item for item in brain.dsh_bridge_info['inbox'] if str(item.get('request_id') or item.get('id') or '') not in ack_set]
        if not was_online:
            peers_now=_get_dsh_peers()
            await publish({'type':'dsh_bridge_state','online':True,'dsh_online':True,'dsh_peers':peers_now,'last_seen':now_t})
            await publish({'type':'state','dsh_online':True,'dsh_peers':peers_now,'phone_online':len(clients)>0,'time':now_t})
        return not was_online

    def _public_msg(m:dict)->dict:
        return {k:v for k,v in m.items() if k!='llm_text'} if isinstance(m,dict) and 'llm_text' in m else m

    def _filter_msgs_by_source(msgs:list[dict],source:str|None=None)->list[dict]:
        s_norm=(source or 'phone').strip().lower()
        if s_norm=='all':
            return [_public_msg(m) for m in msgs]
        target=normalize_node_source(source)
        return [_public_msg(m) for m in msgs if normalize_node_source(m.get('source'))==target]

    def _filter_dict_by_source(d:dict|None,req_src:str)->dict:
        if not isinstance(d,dict):
            return {}
        return {k:v for k,v in d.items() if isinstance(v,dict) and normalize_node_source(v.get('source'))==req_src}

    def _get_streams_for_source(req_src:str)->dict:
        res:dict[str,Any]={}
        for k,v in getattr(brain,'streams_by_node_bot',{}).items():
            if isinstance(v,dict) and normalize_node_source(v.get('source'))==req_src:
                bid=str(v.get('bot_id') or k.split(':',1)[-1])
                res[bid]=v
        for k,v in brain.streams_by_bot.items():
            if k not in res and isinstance(v,dict) and normalize_node_source(v.get('source'))==req_src:
                res[k]=v
        return res

    def _get_execs_for_source(req_src:str)->dict:
        res:dict[str,Any]={}
        for k,v in getattr(brain,'executions_by_node_bot',{}).items():
            if isinstance(v,dict) and normalize_node_source(v.get('source'))==req_src:
                bid=str(v.get('bot_id') or k.split(':',1)[-1])
                res[bid]=_slim_exec(v)
        for k,v in brain.executions_by_bot.items():
            if k not in res and isinstance(v,dict) and normalize_node_source(v.get('source'))==req_src:
                res[k]=_slim_exec(v)
        return res

    @app.get('/api/status',dependencies=[Depends(auth)])
    @app.get('/api/state',dependencies=[Depends(auth)])
    async def status(after_id:str|None=None,source:str|None=None,messages_epoch:int|None=None):
        store.sync_from_workspace()
        s=store.state
        req_src=normalize_node_source(source)
        cur_epoch=int(s.get('messages_epoch') or 1)
        desktop=await _fast_desktop_status()
        has_key=bool(store.config.effective_api_key(big=False) or store.config.effective_api_key(big=True))
        has_model=bool(store.config.effective_model(big=False) or store.config.effective_model(big=True))
        raw_done=brain.last_completed_stream if (brain.last_completed_stream and time.time()-float(brain.last_completed_stream.get('completed_at') or 0)<15.0) else None
        last_done=raw_done if (raw_done and normalize_node_source(raw_done.get('source'))==req_src) else None
        all_msgs=_filter_msgs_by_source(s['messages'],req_src)
        epoch_reset=bool(messages_epoch is not None and int(messages_epoch)!=cur_epoch)
        if epoch_reset:
            recent_tail=all_msgs[-120:]
        elif after_id:
            idx=next((i for i in range(len(all_msgs)-1,-1,-1) if all_msgs[i].get('id')==after_id),-1)
            if idx>=0:
                recent_tail=all_msgs[idx+1:]
            else:
                epoch_reset=True
                recent_tail=all_msgs[-120:]
        else:
            recent_tail=all_msgs[-3:]
        slim_execs=_get_execs_for_source(req_src)
        filtered_streams=_get_streams_for_source(req_src)
        cur_exec=next(iter(slim_execs.values()),None) or (_slim_exec(brain.execution) if (isinstance(brain.execution,dict) and normalize_node_source(brain.execution.get('source'))==req_src) else None)
        cur_stream=next(iter(filtered_streams.values()),None) or (_slim_exec(brain.live_stream) if (isinstance(brain.live_stream,dict) and normalize_node_source(brain.live_stream.get('source'))==req_src) else None)
        dsh_on=_is_dsh_online()
        dsh_peers=_slim_dsh_peers(_get_dsh_peers())
        dsh_st=_slim_dsh_status(brain.dsh_bridge_info.get('status',{}))
        src_msg_ids={m['id'] for m in all_msgs}
        src_pending={k:v for k,v in s['pending'].items() if k in src_msg_ids}
        return {'ok':True,'online':True,'product_abbr':'DSB','activity':brain.activity,'enabled':store.config.enabled,'clients':len(clients),'phone_online':len(clients)>0,'snooze_until':s['snooze_until'],
                'unread':len(src_pending),'routine_count':len(s.get('routines',[])),'small_ready':bool(store.config.small_brain.api_key and store.config.small_brain.model) or (has_key and has_model),
                'big_ready':bool(store.config.big_brain.api_key and store.config.big_brain.model) or (has_key and has_model),'last_error':s['last_error'],
                'stage':max([p['stage'] for p in src_pending.values()] or [0]),'execution':cur_exec,'stream':cur_stream,
                'executions_by_bot':slim_execs,'streams_by_bot':filtered_streams,
                'context_by_bot':brain.memory_engine.get_all_bots_context_stats(),'auto_compact_tokens':store.config.auto_compact_tokens,
                'last_completed_stream':last_done,'recent_tail':recent_tail,'epoch_reset':epoch_reset,'messages_epoch':cur_epoch,
                **({'messages':recent_tail} if epoch_reset else {}),
                'bots':resolve_bots_for_source(s['assistants'],req_src),'primary_bot_id':s['primary_bot_id'],
                'seq':brain.stream_seq,'computer':desktop,
                'dsh_online':dsh_on,'dsh_peers':dsh_peers,
                'dsh':{'online':dsh_on,'dsh_online':dsh_on,'peers':dsh_peers,'last_seen':brain.dsh_bridge_info.get('last_seen',0.0),'status':dsh_st},
                'time':time.time()}

    @app.get('/api/sync',dependencies=[Depends(auth)])
    async def sync_delta(since_seq:int=0,bot_id:str|None=None,source:str|None=None,after_id:str|None=None,messages_epoch:int|None=None):
        store.sync_from_workspace()
        s=store.state
        req_src=normalize_node_source(source)
        cur_epoch=int(s.get('messages_epoch') or 1)
        desktop=await _fast_desktop_status()
        has_key=bool(store.config.effective_api_key(big=False) or store.config.effective_api_key(big=True))
        has_model=bool(store.config.effective_model(big=False) or store.config.effective_model(big=True))
        raw_done=brain.last_completed_stream if (brain.last_completed_stream and time.time()-float(brain.last_completed_stream.get('completed_at') or 0)<15.0) else None
        last_done=raw_done if (raw_done and normalize_node_source(raw_done.get('source'))==req_src) else None
        all_msgs=_filter_msgs_by_source(s['messages'],req_src)
        if bot_id:
            all_msgs=[m for m in all_msgs if m.get('bot_id','companion')==bot_id]
        epoch_reset=bool(messages_epoch is not None and int(messages_epoch)!=cur_epoch)
        is_delta=False
        if epoch_reset:
            delta_msgs=all_msgs[-120:]
            is_delta=False
        elif after_id:
            idx=next((i for i in range(len(all_msgs)-1,-1,-1) if all_msgs[i].get('id')==after_id),-1)
            if idx>=0:
                delta_msgs=all_msgs[idx+1:]
                is_delta=True
            else:
                delta_msgs=all_msgs[-120:]
                is_delta=False
                epoch_reset=True
        elif since_seq>0:
            delta_msgs=[m for m in all_msgs if int(m.get('seq') or 0)>since_seq]
            is_delta=True
        else:
            delta_msgs=all_msgs[-40:]
            is_delta=False
        slim_execs=_get_execs_for_source(req_src)
        filtered_streams=_get_streams_for_source(req_src)
        cur_exec=next(iter(slim_execs.values()),None) or (_slim_exec(brain.execution) if (isinstance(brain.execution,dict) and normalize_node_source(brain.execution.get('source'))==req_src) else None)
        cur_stream=next(iter(filtered_streams.values()),None) or (_slim_exec(brain.live_stream) if (isinstance(brain.live_stream,dict) and normalize_node_source(brain.live_stream.get('source'))==req_src) else None)
        dsh_on=_is_dsh_online()
        dsh_peers=_slim_dsh_peers(_get_dsh_peers())
        dsh_st=_slim_dsh_status(brain.dsh_bridge_info.get('status',{}))
        return {
            'ok':True,'online':True,'product_abbr':'DSB','activity':brain.activity,'enabled':store.config.enabled,'phone_online':len(clients)>0,'snooze_until':s['snooze_until'],
            'unread':len(s['pending']),'small_ready':has_key and has_model,'big_ready':has_key and has_model,
            'last_error':s['last_error'],'execution':cur_exec,'stream':cur_stream,
            'executions_by_bot':slim_execs,'streams_by_bot':filtered_streams,
            'context_by_bot':brain.memory_engine.get_all_bots_context_stats(),'auto_compact_tokens':store.config.auto_compact_tokens,
            'last_completed_stream':last_done,'delta':is_delta,'epoch_reset':epoch_reset,'messages_epoch':cur_epoch,
            'delta_messages':delta_msgs,'messages':delta_msgs,'recent_tail':all_msgs[-3:],
            'bots':resolve_bots_for_source(s['assistants'],req_src),'primary_bot_id':s['primary_bot_id'],
            'seq':brain.stream_seq,'computer':desktop,
            'dsh_online':dsh_on,'dsh_peers':dsh_peers,
            'dsh':{'online':dsh_on,'dsh_online':dsh_on,'peers':dsh_peers,'last_seen':brain.dsh_bridge_info.get('last_seen',0.0),'status':dsh_st},
            'time':time.time()
        }

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
            if not pc_online and (Path(__file__).resolve().parent.parent/'dsh-plugin'/'dsh_core.py').is_file():
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
        return await brain._tool_dsh_workspace(payload)

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

    @app.get('/api/config',dependencies=[Depends(auth)])
    async def get_config():
        store.sync_from_workspace()
        return store.config.public()

    @app.post('/api/config',dependencies=[Depends(auth)])
    async def config(update:dict[str,Any]):
        from pydantic import ValidationError
        try: result=store.update_config(update)
        except ValidationError: raise HTTPException(422,'配置格式无效；请检查 URL、SMTP 端口与时间范围')
        brain.wake.set()
        brain.activity=brain.ready_state()
        await publish({'type':'config_updated','activity':brain.activity})
        return {'saved':True,'harness_running':scheduler,'config':result}

    @app.post('/api/models',dependencies=[Depends(auth)])
    async def fetch_models(data:ModelsInput|None=None):
        base_url=(data.base_url.strip() if data and data.base_url else '') or store.config.effective_base_url()
        api_key=(data.api_key.strip() if data and data.api_key else '') or store.config.effective_api_key(big=True) or store.config.effective_api_key(big=False)
        fb_url,fb_key,fb_model=store.config.effective_fallback(big=True) if hasattr(store.config,'effective_fallback') else ('','','')
        models:list[str]=[]
        for u,k in [(base_url,api_key),(fb_url,fb_key)]:
            if not u or not k:
                continue
            try:
                async with httpx.AsyncClient(timeout=15,trust_env=False) as client:
                    r=await client.get(u.rstrip('/')+'/models',headers={'Authorization':'Bearer '+k})
                if r.status_code<400:
                    body=r.json()
                    raw_list=body.get('data') if isinstance(body,dict) else (body if isinstance(body,list) else [])
                    for item in (raw_list or []):
                        mid=str(item['id']) if isinstance(item,dict) and item.get('id') else str(item or '')
                        if mid and mid not in models:
                            models.append(mid)
            except Exception:
                pass
        for builtin_m in [fb_model,'commandcode/deepseek/deepseek-v4.1-flash','commandcode/deepseek/deepseek-v4-flash','commandcode/deepseek/deepseek-v4-pro','deepseek-v4.1-flash-eco']:
            if builtin_m and builtin_m not in models:
                models.append(builtin_m)
        return {'models':models,'base_url':base_url.rstrip('/')}

    @app.get('/api/messages',dependencies=[Depends(auth)])
    async def messages(bot_id:str|None=None,after_id:str|None=None,limit:int=120,source:str|None=None,messages_epoch:int|None=None):
        store.sync_from_workspace()
        cur_epoch=int(store.state.get('messages_epoch') or 1)
        cap=max(10,min(240,int(limit or 120)))
        all_msgs=_filter_msgs_by_source(store.state['messages'],source)
        if bot_id is not None:
            try: get_assistant(store,bot_id)
            except ValueError as e: raise HTTPException(404,str(e))
            all_msgs=[m for m in all_msgs if m.get('bot_id','companion')==bot_id]
        epoch_reset=bool(messages_epoch is not None and int(messages_epoch)!=cur_epoch)
        is_delta=False
        if after_id and not epoch_reset:
            idx=next((i for i in range(len(all_msgs)-1,-1,-1) if all_msgs[i].get('id')==after_id),-1)
            if idx>=0:
                selected=all_msgs[idx+1:]
                is_delta=True
            else:
                selected=all_msgs[-cap:]
                epoch_reset=True
        else:
            selected=all_msgs[-cap:]
        ids={m['id'] for m in all_msgs}
        return {'messages':selected,'pending':{k:v for k,v in store.state['pending'].items() if k in ids},'delta':is_delta,'epoch_reset':epoch_reset,'messages_epoch':cur_epoch}

    @app.post('/api/messages/edit',dependencies=[Depends(auth)])
    async def edit_message(data:EditMessageInput):
        try: return await brain.edit_and_rerun(data.message_id,data.text,bot_id=data.bot_id,source=data.source)
        except ValueError as e: raise HTTPException(400,str(e))

    @app.post('/api/messages/reroll',dependencies=[Depends(auth)])
    async def reroll_message(data:RerollInput):
        try: return await brain.reroll_last(bot_id=data.bot_id,message_id=data.message_id,source=data.source)
        except ValueError as e: raise HTTPException(400,str(e))

    @app.get('/api/watchdogs',dependencies=[Depends(auth)])
    async def list_watchdogs():
        return await brain._tool_watchdog_task({'action':'list'})

    @app.post('/api/watchdogs/{watchdog_id}/stop',dependencies=[Depends(auth)])
    async def stop_watchdog(watchdog_id:str):
        return await brain._tool_watchdog_task({'action':'stop','watchdog_id':watchdog_id})

    @app.get('/api/memories/pins',dependencies=[Depends(auth)])
    async def get_pins():
        return {'pins':brain.memory_engine.list_pins()}

    @app.post('/api/memories/pins',dependencies=[Depends(auth)])
    async def manage_pins(data:PinInput):
        return await brain._tool_memory_pin(data.model_dump())

    @app.post('/api/context/compact',dependencies=[Depends(auth)])
    async def compact_context(payload:dict[str,Any]|None=None):
        body=payload or {}
        target_bot=str(body.get('bot_id') or store.state.get('primary_bot_id') or 'companion').strip()
        src=normalize_node_source(body.get('source') or 'phone')
        if body.get('auto_compact_tokens'):
            try:
                val=max(8000,min(1000000,int(body['auto_compact_tokens'])))
                store.update_config({'auto_compact_tokens':val})
            except Exception:
                pass
        res=await brain._auto_compact_if_needed(target_bot,force=True,source=src)
        stats=brain.memory_engine.get_all_bots_context_stats(source=src)
        await publish({'type':'context_compacted','source':src,'bot_id':target_bot,'result':res,'context_by_bot':stats})
        return {'compacted':bool(res),'source':src,'result':res,'context':stats.get(target_bot),'context_by_bot':stats,'auto_compact_tokens':store.config.auto_compact_tokens}

    @app.post('/api/storage/gc',dependencies=[Depends(auth)])
    async def trigger_storage_gc(payload:dict[str,Any]|None=None):
        body=payload or {}
        force_vac=bool(body.get('force_vacuum',True))
        rep=await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: run_storage_maintenance(
                store, vault=vault, context_search=brain.context_search, workspace_dir=harness.root, force_vacuum=force_vac
            ),
        )
        return {'ok':True,'report':rep}

    @app.get('/api/bots',dependencies=[Depends(auth)])
    async def bots(request:Request,source:str|None=None):
        store.sync_from_workspace()
        req_src=normalize_node_source(source or request.headers.get('X-Node-Source'))
        return {'bots':resolve_bots_for_source(store.state['assistants'],req_src),'primary_bot_id':store.state['primary_bot_id']}

    @app.post('/api/bots',dependencies=[Depends(auth)])
    async def create_bot(request:Request,data:AssistantInput,source:str|None=None):
        try:
            req_src=normalize_node_source(source or data.source or request.headers.get('X-Node-Source'))
            bot=save_assistant(store,data,source=req_src)
            await publish({'type':'bots_changed','source':req_src,'bot_id':bot['id'],'bots':store.state['assistants'],'primary_bot_id':store.state['primary_bot_id']})
            return bot
        except ValueError as e: raise HTTPException(400,str(e))

    @app.patch('/api/bots/{bot_id}/placement',dependencies=[Depends(auth)])
    async def update_bot_placement(request:Request,bot_id:str,data:AssistantPlacement,source:str|None=None):
        try:
            req_src=normalize_node_source(source or data.source or request.headers.get('X-Node-Source'))
            bot=place_assistant(store,bot_id,data,source=req_src)
            await publish({'type':'bots_changed','source':req_src,'bot_id':bot['id'],'bots':store.state['assistants'],'primary_bot_id':store.state['primary_bot_id']})
            return bot
        except ValueError as e: raise HTTPException(404,str(e))

    @app.put('/api/bots/{bot_id}',dependencies=[Depends(auth)])
    async def update_bot(request:Request,bot_id:str,data:AssistantInput,source:str|None=None):
        try:
            req_src=normalize_node_source(source or data.source or request.headers.get('X-Node-Source'))
            bot=save_assistant(store,data,bot_id,source=req_src)
            canonical_id=bot['id']
            if not bot['notify']:
                ids={m['id'] for m in store.state['messages'] if m.get('bot_id','companion')==canonical_id and normalize_node_source(m.get('source'))==req_src}
                await escalation.ack([mid for mid in store.state['pending'] if mid in ids])
            await publish({'type':'bots_changed','source':req_src,'bot_id':canonical_id,'bots':store.state['assistants'],'primary_bot_id':store.state['primary_bot_id']})
            return bot
        except ValueError as e: raise HTTPException(404,str(e))

    @app.delete('/api/bots/{bot_id}',dependencies=[Depends(auth)])
    async def delete_bot(bot_id:str):
        if len(store.state['assistants'])<=1:
            raise HTTPException(400,'至少保留一位 Bot')
        try:
            target=get_assistant(store,bot_id)
        except ValueError:
            raise HTTPException(404,'助手不存在')
        canonical_id=target['id']
        if canonical_id==store.state.get('primary_bot_id'):
            raise HTTPException(400,'不能删除当前主助理，请先切换主助理')
        deleted_list=store.state.setdefault('deleted_bot_ids',[])
        if canonical_id not in deleted_list:
            deleted_list.append(canonical_id)
        store.state['assistants']=[b for b in store.state['assistants'] if b['id']!=canonical_id]
        store.state['routines']=[r for r in store.state.get('routines',[]) if r.get('bot_id')!=canonical_id]
        store.state['timers']=[t for t in store.state.get('timers',[]) if t.get('bot_id')!=canonical_id]
        store.state['messages_epoch']=int(store.state.get('messages_epoch',1))+1
        ids={m['id'] for m in store.state['messages'] if m.get('bot_id','companion')==canonical_id}
        if ids:
            await escalation.ack([mid for mid in list(store.state['pending']) if mid in ids])
        store.save()
        await publish({'type':'bots_changed','deleted_bot_id':canonical_id,'bots':store.state['assistants'],'primary_bot_id':store.state['primary_bot_id']})
        return {'deleted':True,'bot_id':canonical_id,'bots':store.state['assistants'],'primary_bot_id':store.state['primary_bot_id']}

    @app.get('/api/tools',dependencies=[Depends(auth)])
    async def tools():
        return {'tools':[{'id':t['function']['name'],'description':t['function']['description'],'parameters':t['function']['parameters'],'location':'cloud'} for t in TOOLS]}

    @app.post('/api/chat',dependencies=[Depends(auth)])
    async def chat(data:ChatInput):
        if not data.text.strip(): raise HTTPException(422,'消息不能为空')
        bot_id=data.bot_id or store.state['primary_bot_id']
        src=normalize_node_source(data.source)
        try: bot=get_assistant(store,bot_id)
        except ValueError as e: raise HTTPException(404,str(e))
        if data.async_ack:
            return await brain.enqueue_chat(data.text.strip(),bot_id,data.mode,client_msg_id=data.client_msg_id,source=src)
        try:
            res=await brain.chat(data.text.strip(),bot_id,data.mode,client_msg_id=data.client_msg_id,source=src)
            tail=_filter_msgs_by_source(store.state['messages'],src)[-10:]
            return {**(res if isinstance(res,dict) else {}),'recent_tail':tail,'messages_epoch':int(store.state.get('messages_epoch') or 1),'seq':brain.stream_seq}
        except Exception as e:
            err_msg=f'⚠️ [来自 {bot["name"]} ({bot_id}) 的报错]: {str(e)[:260]}'
            await brain.emit(err_msg,'error',track=False,bot_id=bot_id,source=src)
            primary_id=store.state.get('primary_bot_id','companion')
            if bot_id!=primary_id:
                await brain.emit(err_msg+'\n（你可以在当前窗口让主助理读取 bots.json / settings.json 并修复该 Bot 的模型配置）','error',track=False,bot_id=primary_id,from_bot_id=bot_id,from_bot_name=bot['name'],source=src)
            raise HTTPException(502,str(e)[:300])

    @app.get('/api/vault',dependencies=[Depends(auth)])
    async def files(path:str|None=None):
        try: return {'path':path,'content':vault.read(path)} if path else {'files':vault.files()}
        except ValueError as e: raise HTTPException(400,str(e))
        except FileNotFoundError: raise HTTPException(404,'Markdown 不存在')

    @app.put('/api/vault',dependencies=[Depends(auth)])
    async def edit(data:VaultInput):
        try: vault.write(data.path,data.content)
        except ValueError as e: raise HTTPException(400,str(e))
        if data.path!='INDEX.md': vault.reindex()
        await publish({'type':'vault_changed','path':data.path})
        return {'saved':True,'path':data.path}

    @app.post('/api/trigger-curiosity',dependencies=[Depends(auth)])
    async def curiosity():
        if brain.lock.locked(): raise HTTPException(409,'大脑正在忙，请稍候')
        try: return await brain.curiosity(manual=True)
        except Exception as e: raise HTTPException(502,str(e)[:300])

    @app.post('/api/research',dependencies=[Depends(auth)])
    async def research(data:ResearchInput):
        if brain.lock.locked(): raise HTTPException(409,'大脑正在忙，请稍候')
        try: get_assistant(store,data.bot_id or store.state['primary_bot_id'])
        except ValueError as e: raise HTTPException(404,str(e))
        try: return await brain.research(data.goal,manual=True,bot_id=data.bot_id,record_user=data.record_user and bool(data.goal))
        except Exception as e: raise HTTPException(502,str(e)[:300])

    @app.post('/api/confirm',dependencies=[Depends(auth)])
    async def confirm(data:DecisionInput):
        try: return await brain.confirm(data.id,data.accepted)
        except ValueError as e: raise HTTPException(404,str(e))

    @app.post('/api/stop',dependencies=[Depends(auth)])
    async def stop_execution(data:StopInput|None=None):
        bot_id=data.bot_id if data else None
        src=normalize_node_source(data.source) if (data and data.source) else None
        return await brain.stop_immediately(bot_id=bot_id,source=src)

    @app.post('/api/ack',dependencies=[Depends(auth)])
    async def ack(data:AckInput):
        await escalation.ack(data.ids)
        return {'acknowledged':len(data.ids)}

    @app.post('/api/snooze',dependencies=[Depends(auth)])
    async def snooze(data:SnoozeInput): return await escalation.snooze(data.minutes)

    @app.post('/api/test-escalation',dependencies=[Depends(auth)])
    async def test_escalation():
        if any(p['demo'] for p in store.state['pending'].values()): raise HTTPException(409,'已有演练正在进行，请先确认或暂停')
        m=await brain.emit('三级催单演练：这份草稿等你拍板。4 秒检查邮件，8 秒请求悬浮窗。演练不会伪造 SMTP 发送成功。','escalation_demo',track=False)
        await escalation.track(m,demo=True)
        return {'started':True,'id':m['id'],'stage2_seconds':4,'stage3_seconds':8}

    @app.post('/api/test-email',dependencies=[Depends(auth)])
    async def test_email(): return await escalation.mail('这是一封由 App 手动触发的验收邮件。',test=True)

    @app.get('/api/deadlines',dependencies=[Depends(auth)])
    async def deadlines(): return {'deadlines':store.state['deadlines']}

    @app.post('/api/deadlines',dependencies=[Depends(auth)])
    async def add_deadline(data:DeadlineInput):
        try: return brain.add_deadline(data.title,data.due_at)
        except ValueError as e: raise HTTPException(422,str(e))

    @app.get('/api/routines',dependencies=[Depends(auth)])
    async def routines(): return {'routines':store.state['routines']}

    @app.post('/api/routines',dependencies=[Depends(auth)])
    async def create_routine(data:RoutineInput):
        try: get_assistant(store,data.bot_id)
        except ValueError as e: raise HTTPException(404,str(e))
        if not data.goal.strip(): raise HTTPException(422,'任务不能为空')
        item={'id':secrets.token_hex(12),**data.model_dump(),'goal':data.goal.strip(),'last_run':time.time(),'created_at':time.time()}
        store.state['routines'].append(item);store.save()
        vault.append('playbooks/routines.md',f'## 用户创建 · {item["id"]}\n\n- 职责: {item["goal"]}\n- 触发: 每 {item["interval_hours"]} 小时\n- 助手: {item["bot_id"]}\n')
        return item

    @app.put('/api/routines/{routine_id}',dependencies=[Depends(auth)])
    async def update_routine(routine_id:str,data:RoutineUpdate):
        routine=next((r for r in store.state['routines'] if r['id']==routine_id),None)
        if not routine: raise HTTPException(404,'例行任务不存在')
        routine['enabled']=data.enabled;store.save()
        vault.append('playbooks/routines.md',f'- {time.strftime("%Y-%m-%d")} · {routine_id} · '+('已启用' if data.enabled else '已暂停'))
        return routine

    @app.post('/api/routines/{routine_id}/run',dependencies=[Depends(auth)])
    async def run_routine(routine_id:str):
        if brain.lock.locked(): raise HTTPException(409,'大脑正在忙，请稍候')
        routine=next((r for r in store.state['routines'] if r['id']==routine_id),None)
        if not routine: raise HTTPException(404,'例行任务不存在')
        try: result=await brain.research(routine['goal'],manual=True,bot_id=routine.get('bot_id','companion'))
        except Exception as e: raise HTTPException(502,str(e)[:300])
        routine['last_run']=time.time();store.save()
        return result

    @app.post('/api/vision',dependencies=[Depends(auth)])
    async def vision(data:VisionInput):
        try:
            message=await brain.complete([{'role':'system','content':f'根据截图和目标，只输出 JSON {{"action":"tap|none","x":0,"y":0,"reason":"..."}}。坐标必须在 {data.width}x{data.height} 内。不要确认付款、删除、授权等敏感操作；看不清或任务完成就 action=none。'},
                {'role':'user','content':[{'type':'text','text':data.goal},{'type':'image_url','image_url':{'url':'data:image/png;base64,'+data.image}}]}],big=True)
            raw=re.sub(r'^```(?:json)?\s*|\s*```$','',message['content'].strip())
            decision=json.loads(raw)
            x,y=decision.get('x',0),decision.get('y',0)
            if decision.get('action') not in ('tap','none') or not isinstance(x,(int,float)) or not isinstance(y,(int,float)) or not (0<=x<data.width and 0<=y<data.height):
                raise ValueError('VLM 返回坐标无效')
            decision.update(x=int(x),y=int(y),display_id=data.display_id)
            return decision
        except Exception as e: raise HTTPException(502,str(e)[:200])

    @app.websocket('/ws/client')
    async def websocket(ws:WebSocket):
        await ws.accept()
        queue=asyncio.Queue(128)
        client_id=secrets.token_hex(8)
        sender=None
        key=None
        try:
            envelope=await asyncio.wait_for(ws.receive_json(),10)
            key,hello=crypto.open(envelope)
            if not hmac.compare_digest(str(hello.get('token','')),token):
                await ws.close(code=4401); return
            client_src=normalize_node_source(hello.get('source') or hello.get('node_id') or ws.query_params.get('source'))
            clients[client_id]={'queue':queue,'source':client_src}
            async def send(event): await ws.send_json(crypto.seal(key,event))
            await send({'type':'connected','client_id':client_id,'source':client_src,'activity':brain.activity,'snooze_until':store.state['snooze_until'],'dsh_online':_is_dsh_online(),'dsh_peers':_get_dsh_peers(),'product_abbr':'DSB'})
            for p in store.state['pending'].values():
                if store.state['snooze_until']>time.time(): break
                await send({'type':'escalation','stage':p['stage'],'id':p['id'],'text':p['text'],'demo':p['demo'],'restored':True})
            async def pump():
                while True:
                    try: event=await asyncio.wait_for(queue.get(),25)
                    except asyncio.TimeoutError: event={'type':'heartbeat','time':time.time(),'activity':brain.activity,'dsh_online':_is_dsh_online(),'dsh_peers':_get_dsh_peers()}
                    await send(event)
            sender=asyncio.create_task(pump())
            last_nonce=set()
            while True:
                packet=await ws.receive_json()
                nonce=packet.get('nonce')
                if nonce in last_nonce: raise ValueError('重放')
                last_nonce.add(nonce)
                if len(last_nonce)>1000: last_nonce={nonce}
                event=crypto.frame(key,packet)
                if event.get('type')=='ack': await escalation.ack(event.get('ids',[])[:600])
                elif event.get('type')=='snooze': await escalation.snooze(60)
                elif event.get('type')=='ping':
                    if event.get('source') or event.get('node_id'):
                        clients[client_id]['source']=normalize_node_source(event.get('source') or event.get('node_id'))
                    queue.put_nowait({'type':'pong','time':time.time(),'dsh_online':_is_dsh_online(),'dsh_peers':_get_dsh_peers()})
                elif event.get('type')=='phone_workspace_result': brain.resolve_phone_command(event)
                elif event.get('type')=='device_state':
                    if event.get('source') or event.get('node_id'):
                        clients[client_id]['source']=normalize_node_source(event.get('source') or event.get('node_id'))
        except (WebSocketDisconnect,asyncio.TimeoutError,ValueError,KeyError,InvalidTag):
            with contextlib.suppress(Exception): await ws.close(code=4400)
        finally:
            clients.pop(client_id,None)
            if sender:
                sender.cancel()
                with contextlib.suppress(asyncio.CancelledError): await sender

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
        p=Path(__file__).resolve().parent/'static'/'avatars'/safe_file
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
        if not is_authed and not clean_rel.startswith('uploads/'):
            raise HTTPException(401,'访问非公开工作区图片需要携带有效鉴权')
        try:
            p=harness.path(clean_rel)
        except Exception:
            raise HTTPException(400,'非法路径')
        if not p.is_file() or p.suffix.lower() not in ('.png','.jpg','.jpeg','.webp','.gif','.bmp'):
            raise HTTPException(404,'图片不存在')
        media={'.png':'image/png','.webp':'image/webp','.gif':'image/gif','.bmp':'image/bmp'}.get(p.suffix.lower(),'image/jpeg')
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
        path=Path(os.getenv('AGENT_APK',Path(__file__).parent.parent/'dist/proactive-agent.apk'))
        if not path.exists(): raise HTTPException(404,'APK 尚未发布')
        return FileResponse(path,media_type='application/vnd.android.package-archive',filename='proactive-agent.apk')

    @app.get('/download/release.json')
    async def release_info():
        apk=Path(os.getenv('AGENT_APK',Path(__file__).parent.parent/'dist/proactive-agent.apk'))
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
        apk=Path(os.getenv('AGENT_APK',Path(__file__).parent.parent/'dist/proactive-agent.apk'))
        path=apk.with_name('mika-corresponding-source.tar.gz')
        if not path.exists():raise HTTPException(404,'源码包尚未发布')
        return FileResponse(path,media_type='application/gzip',filename=path.name)

    @app.get('/',response_class=HTMLResponse)
    async def landing():
        return '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><style>body{background:#0B0D12;color:#edf0fa;font:17px system-ui;max-width:650px;margin:12vh auto;padding:28px}small{color:#38bdf8;letter-spacing:4px}h1{font-size:48px}a{display:inline-block;background:linear-gradient(120deg,#0ea5e9,#28b9d1);color:white;padding:18px 28px;border-radius:18px;text-decoration:none}p{line-height:1.8;color:#a7aec3}</style><small>DSB · PROACTIVE AGENT · TOKYO</small><h1>DSB 🐳</h1><p>多 Bot 群组协作 · 电脑端 DeepSeek Harness 双向零配置互通 · 透明记忆 · 1C1G 极简云桌面<br>你的全天候跨端智能搭档，已在节点等候。</p><a href="./download/proactive-agent.apk">↓ 下载 DSB Android APK</a><p>安装后直接填入 API Base URL 与 API Key（或粘贴 AI 自动部署生成的配对串）即可一键连接。</p>'

    return app

app=create_app()
