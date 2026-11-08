"""Pairing-protected cloud-computer routes and durable delivery into a Bot's chat."""
from __future__ import annotations
import asyncio,contextlib,time
from typing import Any
from fastapi import APIRouter,Body,Depends,HTTPException
from assistants import get_assistant
from computer_client import ComputerClient,ComputerUnavailable
from computer_models import InputAction,NavigateInput,SteerInput,UserComputerTask
from computer_files import UploadStart,UploadChunk

class ComputerCoordinator:
    def __init__(self,store,brain,publish,client=None):
        self.store=store;self.brain=brain;self.publish=publish
        self.client=client or ComputerClient();self.monitor=None;self.run_lock=asyncio.Lock()

    async def run(self,data:UserComputerTask):
        async with self.run_lock:
            try:bot=get_assistant(self.store,data.bot_id)
            except ValueError as error:raise HTTPException(404,str(error)) from None
            cfg=self.store.config
            base_url=cfg.effective_base_url()
            api_key=cfg.effective_api_key(big=True) or cfg.effective_api_key(big=False)
            model_name=cfg.effective_model(bot_model=bot.get('model',''),big=True) or cfg.effective_model(bot_model=bot.get('model',''),big=False)
            if not api_key or not model_name:raise HTTPException(409,'先配置执行模型，或接管云电脑自己操作')
            previous=self.store.state.get('computer_job')
            if previous and not previous.get('reported'):
                await self.settle(previous,await self.client.status(force=True))
            result=await self.client.call('POST','/run',{'goal':data.goal,'base_url':base_url,'api_key':api_key,'model':model_name,'max_steps':data.max_steps})
            job=result.get('job')
            if not job or not job.get('id'):raise ComputerUnavailable('云电脑没有返回任务编号')
            self.store.message('user',data.goal,'chat',bot_id=data.bot_id)
            self.store.state['computer_job']={'id':job['id'],'bot_id':data.bot_id,'goal':data.goal,'reported':False,'question_reported':''}
            self.store.save();self.ensure_monitor()
            return {'started':True,'job':job,'bot_id':bot['id']}

    def ensure_monitor(self):
        saved=self.store.state.get('computer_job')
        if saved and not saved.get('reported') and (self.monitor is None or self.monitor.done()):
            self.monitor=asyncio.create_task(self.watch(saved['id']))

    async def watch(self,job_id):
        while True:
            await asyncio.sleep(1.5)
            saved=self.store.state.get('computer_job',{})
            if saved.get('id')!=job_id or saved.get('reported'):return
            status=await self.client.status(force=True)
            job=status.get('job')
            if not status.get('available'):continue
            if job and job.get('id')==job_id:
                question=job.get('question','')
                if status.get('control')=='user' and question and question!=saved.get('question_reported'):
                    await self.deliver('云电脑需要你接管：'+question,'computer_question',saved,job)
                    saved['question_reported']=question;self.store.save()
            if await self.settle(saved,status):return

    async def settle(self,saved,status):
        if saved.get('reported') or not status.get('available'):return False
        job=status.get('job')
        if not job or job.get('id')!=saved['id']:
            saved['reported']=True;self.store.save();return True
        if job.get('state') not in ('done','failed','stopped','interrupted'):return False
        text=job.get('result') or '本轮结束，请查看云电脑和任务文件。'
        kind='computer_result' if job.get('state')=='done' else 'stopped' if job.get('state')=='stopped' else 'error'
        await self.deliver(text,kind,saved,job or {})
        saved['reported']=True;self.store.save();return True

    async def deliver(self,text,kind,saved,job):
        try:bot=get_assistant(self.store,saved['bot_id'])
        except ValueError:return
        await self.brain.emit(text,kind,track=True,bot_id=bot['id'],notify=bot.get('notify',True),
            computer_job=saved['id'],goal=saved['goal'],computer_trace=job.get('trace',[]),engine='cloud_computer')

    async def close(self):
        if self.monitor and not self.monitor.done():
            self.monitor.cancel()
            with contextlib.suppress(asyncio.CancelledError):await self.monitor

def install_computer_routes(app,auth,coordinator):
    router=APIRouter(prefix='/api/computer',dependencies=[Depends(auth)])
    async def forward(method,path,body=None,query=None):
        try:return await coordinator.client.call(method,path,body,query)
        except ComputerUnavailable as error:raise HTTPException(503,str(error)) from None
        except ValueError as error:raise HTTPException(409,str(error)) from None
    @router.get('/status')
    @router.get('/state')
    async def status():
        coordinator.ensure_monitor();result=await coordinator.client.status()
        cfg=coordinator.store.config
        result['ok']=bool(result.get('available',False))
        result['model_ready']=bool((cfg.effective_api_key(big=True) or cfg.effective_api_key(big=False)) and (cfg.effective_model(big=True) or cfg.effective_model(big=False)))
        saved=coordinator.store.state.get('computer_job',{})
        if (result.get('job') or {}).get('id')==saved.get('id'):result['bot_id']=saved.get('bot_id')
        return result
    @router.get('/frame')
    @router.get('/screenshot')
    async def frame(since:str='',quality:int=62):
        q={'since':since,'quality':max(30,min(90,quality))} if since else {'quality':max(30,min(90,quality))}
        res=await forward('GET','/frame',query=q)
        if isinstance(res,dict):
            res.setdefault('ok',True)
            if 'image' in res:
                res.setdefault('screenshot',res['image'])
                res.setdefault('frame',res['image'])
        return res
    @router.post('/screenshot')
    async def post_screenshot(payload:dict[str,Any]|None=Body(default=None)):
        body=payload or {}
        q_val=int(body.get('quality') or 62)
        since_val=str(body.get('since') or '')
        return await frame(since=since_val,quality=q_val)
    @router.post('/takeover')
    async def takeover():return await forward('POST','/takeover')
    @router.post('/handback')
    async def handback():return await forward('POST','/handback')
    @router.post('/stop')
    async def stop():return await forward('POST','/stop')
    @router.post('/navigate')
    async def navigate(data:NavigateInput):return await forward('POST','/navigate',data.model_dump())
    @router.post('/search')
    async def search_web_desktop(payload:dict[str,Any]):
        raw=dict(payload or {})
        q=str(raw.get('query') or raw.get('text') or '').strip()
        if not q:raise HTTPException(422,'搜索词不能为空')
        return await forward('POST','/search',{'query':q,'engine':str(raw.get('engine') or 'bing'),'include_frame':bool(raw.get('include_frame',True))})
    @router.post('/input')
    async def input_action(payload:dict[str,Any]):
        raw=dict(payload or {})
        if 'kind' not in raw and ('action' in raw or 'op' in raw):
            raw['kind']=raw.pop('action',None) or raw.pop('op',None)
        alias_map={'tap':'click','press':'key','press_key':'key','type_text':'type','shell':'bash','exec':'bash'}
        if isinstance(raw.get('kind'),str) and raw['kind'] in alias_map:
            raw['kind']=alias_map[raw['kind']]
        from pydantic import ValidationError
        try:
            data=InputAction.model_validate(raw)
        except ValidationError as exc:
            raise HTTPException(422,exc.errors()) from None
        return await forward('POST','/input',data.model_dump())
    @router.post('/action')
    async def computer_action(payload:dict[str,Any]):
        raw=dict(payload or {})
        op=str(raw.get('op') or raw.get('action') or raw.get('kind') or 'status').strip().lower()
        if op in ('status','state'):
            return await status()
        if op in ('screenshot','frame','capture'):
            return await frame(since=str(raw.get('since') or ''),quality=int(raw.get('quality') or 62))
        if op=='takeover':
            return await forward('POST','/takeover')
        if op in ('handback','release'):
            return await forward('POST','/handback')
        if op=='stop':
            return await forward('POST','/stop')
        if op=='navigate':
            return await navigate(NavigateInput(url=str(raw.get('url') or raw.get('target') or ''),include_frame=bool(raw.get('include_frame',True))))
        if op=='search':
            return await search_web_desktop(raw)
        if coordinator.brain is not None and hasattr(coordinator.brain,'_tool_cloud_desktop'):
            res=await coordinator.brain._tool_cloud_desktop({**raw,'op':op})
            if isinstance(res,dict):
                return res
        return await input_action({**raw,'kind':op})
    @router.post('/steer')
    async def steer(data:SteerInput):return await forward('POST','/steer',data.model_dump())
    @router.post('/run')
    async def run(data:UserComputerTask):
        try:return await coordinator.run(data)
        except ComputerUnavailable as error:raise HTTPException(503,str(error)) from None
        except ValueError as error:raise HTTPException(409,str(error)) from None
    @router.get('/files')
    async def files():return await forward('GET','/files')
    @router.get('/file')
    async def read_file(path:str,offset:int=0,size:int=262144,version:str|None=None):
        if offset<0 or not 1<=size<=524288:raise HTTPException(400,'文件分块参数无效')
        query={'path':path,'offset':offset,'size':size}
        if version:query['version']=version
        return await forward('GET','/file',query=query)
    @router.post('/uploads')
    async def start_upload(data:UploadStart):return await forward('POST','/library/uploads',data.model_dump())
    @router.put('/uploads/{key}')
    async def upload_chunk(key:str,data:UploadChunk):return await forward('PUT','/library/uploads/'+key,data.model_dump())
    @router.post('/uploads/{key}/finish')
    async def finish_upload(key:str):return await forward('POST','/library/uploads/'+key+'/finish')
    @router.delete('/uploads/{key}')
    async def cancel_upload(key:str):return await forward('DELETE','/library/uploads/'+key)
    app.include_router(router)
