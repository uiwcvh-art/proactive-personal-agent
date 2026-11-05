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
from storage import Store, compact_trace_list
from vault_manager import Vault, atomic_write
from assistants import AssistantInput, AssistantPlacement, RoutineInput, RoutineUpdate, get_assistant, save_assistant, place_assistant
from bot_identity import install_identity_routes
from computer_api import ComputerCoordinator, install_computer_routes
from computer_files import WorkspaceFiles, install_file_routes

class ChatInput(BaseModel):
    text:str=Field(min_length=1,max_length=12000)
    bot_id:str|None=Field(None,max_length=40)
    mode:Literal['auto','chat','execute']='auto'
    async_ack:bool=False
    client_msg_id:str|None=Field(None,max_length=80)
class StopInput(BaseModel):
    bot_id:str|None=Field(None,max_length=40)
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
class RerollInput(BaseModel):
    bot_id:str=Field('companion',max_length=40)
    message_id:str|None=Field(None,max_length=80)
class PinInput(BaseModel):
    action:Literal['add','remove','list']='list'
    text:str|None=Field(None,max_length=500)
    pin_id:str|None=Field(None,max_length=40)
    category:str=Field('general',max_length=40)

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
    clients={}
    async def publish(event):
        for queue in list(clients.values()):
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty): queue.get_nowait()
            queue.put_nowait(event)
    escalation=Escalation(store,vault,publish)
    brain=Brain(store,vault,harness,publish,escalation)
    computer=ComputerCoordinator(store,brain,publish)
    brain.computer=computer
    internal_client_holder:dict[str,httpx.AsyncClient]={}
    desktop_status_cache:dict[str,Any]={'ts':0.0,'data':{'ready':False}}

    @asynccontextmanager
    async def lifespan(app):
        internal_client_holder['client']=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local',timeout=httpx.Timeout(600.0,connect=30.0))
        task=asyncio.create_task(brain.scheduler()) if scheduler else None
        brain.activity=brain.ready_state()
        computer.ensure_monitor()
        try: yield
        finally:
            ic=internal_client_holder.pop('client',None)
            if ic:
                with contextlib.suppress(Exception): await ic.aclose()
            await computer.close()
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError): await task
            store.save()

    app=FastAPI(title='Pulse · Proactive Agent',version='2.7.0',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
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
            path=data['path']
            if not path.startswith('/api/') or path in ('/api/secure','/api/transport') or '?' in path or '#' in path:
                raise ValueError('非法 API 路径')
            ic=internal_client_holder.get('client')
            if ic is not None:
                response=await ic.request(data['method'],path,params=data.get('query'),json=data.get('body'),headers={'Authorization':'Bearer '+token})
            else:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local',timeout=httpx.Timeout(600.0,connect=30.0)) as client:
                    response=await client.request(data['method'],path,params=data.get('query'),json=data.get('body'),headers={'Authorization':'Bearer '+token})
            return crypto.seal(key,{'status':response.status_code,'body':response.json()})
        except (ValueError,KeyError,TypeError,InvalidTag): raise HTTPException(400,'加密请求无效、过期或重放')

    async def _fast_desktop_status()->dict:
        now_t=time.time()
        if now_t-desktop_status_cache['ts']<1.8:
            return dict(desktop_status_cache['data'])
        try:
            desktop=await asyncio.wait_for(computer.client.status(),timeout=0.35)
            saved=store.state.get('computer_job',{})
            if (desktop.get('job') or {}).get('id')==saved.get('id'):
                desktop['bot_id']=saved.get('bot_id')
            desktop_status_cache['ts']=now_t
            desktop_status_cache['data']=desktop
            return dict(desktop)
        except Exception:
            return dict(desktop_status_cache['data'])

    def _slim_exec(ex:dict|None)->dict|None:
        if not isinstance(ex,dict): return None
        out=dict(ex)
        if 'trace' in out:
            out['trace']=compact_trace_list(out['trace'])
        return out

    @app.get('/api/status',dependencies=[Depends(auth)])
    async def status(after_id:str|None=None):
        store.sync_from_workspace()
        s=store.state
        desktop=await _fast_desktop_status()
        has_key=bool(store.config.effective_api_key(big=False) or store.config.effective_api_key(big=True))
        has_model=bool(store.config.effective_model(big=False) or store.config.effective_model(big=True))
        last_done=brain.last_completed_stream if (brain.last_completed_stream and time.time()-float(brain.last_completed_stream.get('completed_at') or 0)<15.0) else None
        all_msgs=s['messages']
        if after_id:
            idx=next((i for i in range(len(all_msgs)-1,-1,-1) if all_msgs[i].get('id')==after_id),-1)
            recent_tail=all_msgs[idx+1:] if idx>=0 else all_msgs[-3:]
        else:
            recent_tail=all_msgs[-3:]
        slim_execs={k:_slim_exec(v) for k,v in (brain.executions_by_bot or {}).items() if v}
        return {'online':True,'activity':brain.activity,'enabled':store.config.enabled,'clients':len(clients),'snooze_until':s['snooze_until'],
                'unread':len(s['pending']),'routine_count':len(s.get('routines',[])),'small_ready':bool(store.config.small_brain.api_key and store.config.small_brain.model) or (has_key and has_model),
                'big_ready':bool(store.config.big_brain.api_key and store.config.big_brain.model) or (has_key and has_model),'last_error':s['last_error'],
                'stage':max([p['stage'] for p in s['pending'].values()] or [0]),'execution':_slim_exec(brain.execution),'stream':_slim_exec(brain.live_stream),
                'executions_by_bot':slim_execs,'streams_by_bot':brain.streams_by_bot,
                'context_by_bot':brain.memory_engine.get_all_bots_context_stats(),'auto_compact_tokens':store.config.auto_compact_tokens,
                'last_completed_stream':last_done,'recent_tail':recent_tail,'seq':brain.stream_seq,'computer':desktop,'time':time.time()}

    @app.get('/api/sync',dependencies=[Depends(auth)])
    async def sync_delta(since_seq:int=0,bot_id:str|None=None):
        store.sync_from_workspace()
        s=store.state
        desktop=await _fast_desktop_status()
        has_key=bool(store.config.effective_api_key(big=False) or store.config.effective_api_key(big=True))
        has_model=bool(store.config.effective_model(big=False) or store.config.effective_model(big=True))
        last_done=brain.last_completed_stream if (brain.last_completed_stream and time.time()-float(brain.last_completed_stream.get('completed_at') or 0)<15.0) else None
        all_msgs=s['messages']
        if since_seq>0:
            delta_msgs=[m for m in all_msgs if int(m.get('seq') or 0)>since_seq and (not bot_id or m.get('bot_id','companion')==bot_id)]
        else:
            delta_msgs=[m for m in all_msgs if not bot_id or m.get('bot_id','companion')==bot_id][-40:]
        slim_execs={k:_slim_exec(v) for k,v in (brain.executions_by_bot or {}).items() if v}
        return {
            'online':True,'activity':brain.activity,'enabled':store.config.enabled,'snooze_until':s['snooze_until'],
            'unread':len(s['pending']),'small_ready':has_key and has_model,'big_ready':has_key and has_model,
            'last_error':s['last_error'],'execution':_slim_exec(brain.execution),'stream':_slim_exec(brain.live_stream),
            'executions_by_bot':slim_execs,'streams_by_bot':brain.streams_by_bot,
            'context_by_bot':brain.memory_engine.get_all_bots_context_stats(),'auto_compact_tokens':store.config.auto_compact_tokens,
            'last_completed_stream':last_done,'delta_messages':delta_msgs,'recent_tail':all_msgs[-3:],
            'bots':s['assistants'],'primary_bot_id':s['primary_bot_id'],
            'seq':brain.stream_seq,'computer':desktop,'time':time.time()
        }

    @app.post('/api/phone/result',dependencies=[Depends(auth)])
    async def phone_result_fallback(payload:dict[str,Any]):
        brain.resolve_phone_command(payload)
        return {'accepted':True}

    @app.post('/api/phone/command',dependencies=[Depends(auth)])
    async def phone_command(payload:dict[str,Any]):
        op=str(payload.get('op') or 'status').strip()
        force=bool(payload.get('force',False))
        now=time.time()
        ttl=300.0 if op=='apps' else (1.5 if op in ('status','ui_tree') else 0.0)
        if ttl>0 and not force and op in phone_read_cache:
            ts,cached_val=phone_read_cache[op]
            if now-ts<ttl:
                return {'cached':True,**cached_val} if isinstance(cached_val,dict) else cached_val
        res=await brain._tool_phone_workspace(payload)
        if ttl>0 and isinstance(res,dict) and not res.get('error') and not res.get('warning'):
            phone_read_cache[op]=(now,res)
        elif op in ('launch','tap_node','type_node','tap','swipe','key','shell','release','auto_release'):
            phone_read_cache.pop('ui_tree',None)
            phone_read_cache.pop('status',None)
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
        if not base_url or not api_key:
            raise HTTPException(400,'请先填写 API Base URL 和 API Key')
        base_url=base_url.rstrip('/')
        try:
            async with httpx.AsyncClient(timeout=25,trust_env=False) as client:
                r=await client.get(base_url+'/models',headers={'Authorization':'Bearer '+api_key})
            if r.status_code>=400:
                raise HTTPException(502,f'获取 /models 失败 (HTTP {r.status_code})：{r.text[:200]}')
            body=r.json()
            raw_list=body.get('data') if isinstance(body,dict) else (body if isinstance(body,list) else [])
            models=[]
            for item in (raw_list or []):
                if isinstance(item,dict) and item.get('id'):
                    models.append(str(item['id']))
                elif isinstance(item,str):
                    models.append(item)
            return {'models':sorted(list(dict.fromkeys(models))),'base_url':base_url}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(502,'请求 /models 失败：'+str(e)[:200])

    @app.get('/api/messages',dependencies=[Depends(auth)])
    async def messages(bot_id:str|None=None,after_id:str|None=None,limit:int=120):
        store.sync_from_workspace()
        cap=max(10,min(240,int(limit or 120)))
        all_msgs=store.state['messages']
        if bot_id is not None:
            try: get_assistant(store,bot_id)
            except ValueError as e: raise HTTPException(404,str(e))
            all_msgs=[m for m in all_msgs if m.get('bot_id','companion')==bot_id]
        is_delta=False
        if after_id:
            idx=next((i for i in range(len(all_msgs)-1,-1,-1) if all_msgs[i].get('id')==after_id),-1)
            if idx>=0:
                selected=all_msgs[idx+1:]
                is_delta=True
            else:
                selected=all_msgs[-cap:]
        else:
            selected=all_msgs[-cap:]
        if bot_id is None:
            return {'messages':selected,'pending':store.state['pending'],'delta':is_delta}
        ids={m['id'] for m in all_msgs}
        return {'messages':selected,'pending':{k:v for k,v in store.state['pending'].items() if k in ids},'delta':is_delta}

    @app.post('/api/messages/edit',dependencies=[Depends(auth)])
    async def edit_message(data:EditMessageInput):
        try: return await brain.edit_and_rerun(data.message_id,data.text,bot_id=data.bot_id)
        except ValueError as e: raise HTTPException(400,str(e))

    @app.post('/api/messages/reroll',dependencies=[Depends(auth)])
    async def reroll_message(data:RerollInput):
        try: return await brain.reroll_last(bot_id=data.bot_id,message_id=data.message_id)
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
        if body.get('auto_compact_tokens'):
            try:
                val=max(8000,min(1000000,int(body['auto_compact_tokens'])))
                store.update_config({'auto_compact_tokens':val})
            except Exception:
                pass
        res=await brain._auto_compact_if_needed(target_bot,force=True)
        stats=brain.memory_engine.get_all_bots_context_stats()
        await publish({'type':'context_compacted','bot_id':target_bot,'result':res,'context_by_bot':stats})
        return {'compacted':bool(res),'result':res,'context':stats.get(target_bot),'context_by_bot':stats,'auto_compact_tokens':store.config.auto_compact_tokens}

    @app.get('/api/bots',dependencies=[Depends(auth)])
    async def bots():
        store.sync_from_workspace()
        return {'bots':store.state['assistants'],'primary_bot_id':store.state['primary_bot_id']}

    @app.post('/api/bots',dependencies=[Depends(auth)])
    async def create_bot(data:AssistantInput):
        try: return save_assistant(store,data)
        except ValueError as e: raise HTTPException(400,str(e))

    @app.patch('/api/bots/{bot_id}/placement',dependencies=[Depends(auth)])
    async def update_bot_placement(bot_id:str,data:AssistantPlacement):
        try: return place_assistant(store,bot_id,data)
        except ValueError as e: raise HTTPException(404,str(e))

    @app.put('/api/bots/{bot_id}',dependencies=[Depends(auth)])
    async def update_bot(bot_id:str,data:AssistantInput):
        try:
            bot=save_assistant(store,data,bot_id)
            if not bot['notify']:
                ids={m['id'] for m in store.state['messages'] if m.get('bot_id','companion')==bot_id}
                await escalation.ack([mid for mid in store.state['pending'] if mid in ids])
            return bot
        except ValueError as e: raise HTTPException(404,str(e))

    @app.delete('/api/bots/{bot_id}',dependencies=[Depends(auth)])
    async def delete_bot(bot_id:str):
        if len(store.state['assistants'])<=1:
            raise HTTPException(400,'至少保留一位 Bot')
        if bot_id==store.state.get('primary_bot_id'):
            raise HTTPException(400,'不能删除当前主助理，请先切换主助理')
        before=len(store.state['assistants'])
        store.state['assistants']=[b for b in store.state['assistants'] if b['id']!=bot_id]
        if len(store.state['assistants'])==before:
            raise HTTPException(404,'助手不存在')
        store.save()
        await publish({'type':'bots_changed','deleted_bot_id':bot_id})
        return {'deleted':True,'bot_id':bot_id}

    @app.get('/api/tools',dependencies=[Depends(auth)])
    async def tools():
        return {'tools':[{'id':t['function']['name'],'description':t['function']['description'],'parameters':t['function']['parameters'],'location':'cloud'} for t in TOOLS]}

    @app.post('/api/chat',dependencies=[Depends(auth)])
    async def chat(data:ChatInput):
        if not data.text.strip(): raise HTTPException(422,'消息不能为空')
        bot_id=data.bot_id or store.state['primary_bot_id']
        try: bot=get_assistant(store,bot_id)
        except ValueError as e: raise HTTPException(404,str(e))
        if data.async_ack:
            return await brain.enqueue_chat(data.text.strip(),bot_id,data.mode,client_msg_id=data.client_msg_id)
        try:
            res=await brain.chat(data.text.strip(),bot_id,data.mode,client_msg_id=data.client_msg_id)
            return {**(res if isinstance(res,dict) else {}),'recent_tail':store.state['messages'][-10:],'seq':brain.stream_seq}
        except Exception as e:
            err_msg=f'⚠️ [来自 {bot["name"]} ({bot_id}) 的报错]: {str(e)[:260]}'
            await brain.emit(err_msg,'error',track=False,bot_id=bot_id)
            primary_id=store.state.get('primary_bot_id','companion')
            if bot_id!=primary_id:
                await brain.emit(err_msg+'\n（你可以在当前窗口让主助理读取 bots.json / settings.json 并修复该 Bot 的模型配置）','error',track=False,bot_id=primary_id,from_bot_id=bot_id,from_bot_name=bot['name'])
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
        return await brain.stop_immediately(bot_id=bot_id)

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
            clients[client_id]=queue
            async def send(event): await ws.send_json(crypto.seal(key,event))
            await send({'type':'connected','client_id':client_id,'activity':brain.activity,'snooze_until':store.state['snooze_until']})
            for p in store.state['pending'].values():
                if store.state['snooze_until']>time.time(): break
                await send({'type':'escalation','stage':p['stage'],'id':p['id'],'text':p['text'],'demo':p['demo'],'restored':True})
            async def pump():
                while True:
                    try: event=await asyncio.wait_for(queue.get(),25)
                    except asyncio.TimeoutError: event={'type':'heartbeat','time':time.time(),'activity':brain.activity}
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
                elif event.get('type')=='ping': queue.put_nowait({'type':'pong','time':time.time()})
                elif event.get('type')=='phone_workspace_result': brain.resolve_phone_command(event)
                elif event.get('type')=='device_state': pass
        except (WebSocketDisconnect,asyncio.TimeoutError,ValueError,KeyError,InvalidTag):
            with contextlib.suppress(Exception): await ws.close(code=4400)
        finally:
            clients.pop(client_id,None)
            if sender:
                sender.cancel()
                with contextlib.suppress(asyncio.CancelledError): await sender

    @app.api_route('/download/proactive-agent.apk',methods=['GET','HEAD'])
    async def download():
        path=Path(os.getenv('AGENT_APK',Path(__file__).parent.parent/'dist/proactive-agent.apk'))
        if not path.exists(): raise HTTPException(404,'APK 尚未发布')
        return FileResponse(path,media_type='application/vnd.android.package-archive',filename='proactive-agent.apk')

    @app.get('/download/release.json')
    async def release_info():
        apk=Path(os.getenv('AGENT_APK',Path(__file__).parent.parent/'dist/proactive-agent.apk'))
        path=apk.with_name('release.json')
        if not path.exists():raise HTTPException(404,'尚无发布元数据')
        return json.loads(path.read_text('utf-8-sig'))

    @app.api_route('/download/mika-corresponding-source.tar.gz',methods=['GET','HEAD'])
    async def mika_corresponding_source():
        apk=Path(os.getenv('AGENT_APK',Path(__file__).parent.parent/'dist/proactive-agent.apk'))
        path=apk.with_name('mika-corresponding-source.tar.gz')
        if not path.exists():raise HTTPException(404,'源码包尚未发布')
        return FileResponse(path,media_type='application/gzip',filename=path.name)

    @app.get('/',response_class=HTMLResponse)
    async def landing():
        return '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><style>body{background:#0B0D12;color:#edf0fa;font:17px system-ui;max-width:650px;margin:12vh auto;padding:28px}small{color:#8a91a8;letter-spacing:4px}h1{font-size:48px}a{display:inline-block;background:linear-gradient(120deg,#7c5cff,#28b9d1);color:white;padding:18px 28px;border-radius:18px;text-decoration:none}p{line-height:1.8;color:#a7aec3}</style><small>PROACTIVE PERSONAL AGENT · TOKYO · v2.0</small><h1>共生，与你同频。</h1><p>多 Bot 群组协作 · 报错自愈 · 透明记忆 · 极简同权工具<br>你的外部前额叶，已在日本节点等候。</p><a href="./download/proactive-agent.apk">↓ 下载 Android APK (v2.0.0)</a><p>安装后直接填入 API Base URL 与 API Key 即可一键拉取模型并同步。</p>'

    return app

app=create_app()
