import asyncio,base64,hashlib
from pathlib import Path
import httpx,pytest
from main import create_app
from computer_files import WorkspaceFiles,UploadStart,UploadChunk

def test_encrypted_library_upload_round_trip_and_offsets(tmp_path):
    async def scenario():
        app=create_app(tmp_path,scheduler=False)
        token=(tmp_path/'device-token').read_text()
        data=bytes(range(256))*3100;digest=hashlib.sha256(data).hexdigest()
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://app') as client:
                assert (await client.get('/api/files')).status_code==401
                client.headers['Authorization']='Bearer '+token
                upload=(await client.post('/api/files/uploads',json={'name':'资料.bin','size':len(data),'sha256':digest})).json()['id']
                path='/api/files/uploads/'+upload
                assert (await client.post(path+'/finish')).status_code==409
                offset=0
                for chunk in [data[:400000],data[400000:]]:
                    packet={'offset':offset,'data':base64.b64encode(chunk).decode()}
                    r=await client.put(path,json=packet);assert r.status_code==200;offset=r.json()['offset']
                    assert (await client.put(path,json=packet)).status_code==409
                receipt=(await client.post(path+'/finish')).json();assert receipt['sha256']==digest
                listed=(await client.get('/api/files')).json()['files'];assert listed[0]['path']==receipt['path']
                result=bytearray();version=None
                while True:
                    query={'path':receipt['path'],'offset':len(result),'size':128000}
                    if version:query['version']=version
                    packet=(await client.get('/api/files/file',params=query)).json();version=packet['version']
                    result.extend(base64.b64decode(packet['data']))
                    if packet['eof']:break
                assert hashlib.sha256(result).hexdigest()==digest
                assert not list((tmp_path/'.transfers').iterdir())
    asyncio.run(scenario())

def test_file_boundaries_cancel_and_changed_file_detection(tmp_path):
    files=WorkspaceFiles(tmp_path/'workspace',tmp_path/'.transfers')
    for name in ['../private','secret/key','C:\\keys','/etc/passwd','.hidden']:
        with pytest.raises(ValueError):files.start(UploadStart(name=name,size=1,sha256=hashlib.sha256(b'x').hexdigest()))
    for path in ['workspace/../device-token','workspace/C:/keys','workspace/.secret','workspace//bad','/etc/passwd']:
        with pytest.raises(ValueError):files.path(path)
    upload=files.start(UploadStart(name='incomplete.txt',size=4,sha256=hashlib.sha256(b'good').hexdigest()))
    files.chunk(upload['id'],UploadChunk(offset=0,data=base64.b64encode(b'bad!').decode()))
    with pytest.raises(ValueError):files.finish(upload['id'])
    assert not files.files() and not list(files.transfers.iterdir())
    target=files.root/'draft.txt';target.write_text('before')
    packet=files.read('workspace/draft.txt',0,3)
    target.write_text('afterwards')
    with pytest.raises(ValueError):files.read('workspace/draft.txt',3,3,packet['version'])

def test_computer_task_delivers_to_requested_bot_and_survives_monitor_restart(tmp_path):
    async def scenario():
        app=create_app(tmp_path,scheduler=False);computer=app.state.computer
        class Desktop:
            def __init__(self):self.job={'id':'job-proof','goal':'check','state':'running','trace':[]};self.calls=[]
            async def call(self,method,path,body=None,query=None):self.calls.append((method,path));return {'available':True,'control':'agent','job':self.job.copy()}
            async def status(self,force=False):return {'available':True,'control':'agent','job':self.job.copy()}
        desktop=Desktop();computer.client=desktop
        token=(tmp_path/'device-token').read_text()
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://app',headers={'Authorization':'Bearer '+token}) as client:
                assert (await client.post('/api/computer/run',json={'goal':'check','bot_id':'research'})).status_code==409
                assert not desktop.calls
                app.state.store.update_config({'big_brain':{'api_key':'fixture-key','model':'fixture-model'}})
                started=await client.post('/api/computer/run',json={'goal':'check','bot_id':'research'});assert started.status_code==200
                await computer.close();assert not app.state.store.state['computer_job']['reported']
                desktop.job.update(state='done',result='Verified result',trace=[{'step':1,'actions':[{'done':{'text':'Verified result'}}]}])
                computer.ensure_monitor();await asyncio.wait_for(computer.monitor,4)
                messages=app.state.store.state['messages'];result=next(m for m in messages if m['kind']=='computer_result')
                assert result['bot_id']=='research' and result['text']=='Verified result'
                assert result['computer_job']=='job-proof' and app.state.store.state['computer_job']['reported']
                computer.ensure_monitor();await asyncio.sleep(0)
                assert sum(m['kind']=='computer_result' for m in messages)==1
    asyncio.run(scenario())

def test_starting_next_task_first_delivers_the_previous_completed_result(tmp_path):
    async def scenario():
        app=create_app(tmp_path,scheduler=False);computer=app.state.computer
        class Desktop:
            job={'id':'previous','state':'done','result':'Keep this result','trace':[]}
            async def status(self,force=False):return {'available':True,'job':self.job}
            async def call(self,*args,**kw):self.job={'id':'next','state':'running'};return {'available':True,'job':self.job}
        computer.client=Desktop();store=app.state.store
        store.update_config({'big_brain':{'api_key':'fixture-only','model':'fixture-only'}})
        store.state['computer_job']={'id':'previous','bot_id':'research','goal':'previous goal','reported':False}
        from computer_models import UserComputerTask
        await computer.run(UserComputerTask(goal='next goal',bot_id='research'))
        assert any(m['text']=='Keep this result' and m['computer_job']=='previous' for m in store.state['messages'])
        assert store.state['computer_job']['id']=='next'
        await computer.close()
    asyncio.run(scenario())
