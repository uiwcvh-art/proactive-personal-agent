"""Real private-desktop acceptance. Uses only our own cgroup/profile and a free LLM fixture."""
import asyncio,base64,hashlib,json,os,socket,subprocess,threading,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import httpx
import websockets

BASE=Path('/opt/proactive-agent/cloud-computer');OUT=BASE/'bootstrap'
TOKEN=(BASE/'control.env').read_text().strip().split('=',1)[1]
HEADERS={'Authorization':'Bearer '+TOKEN};checks={};fixture_calls=[]
delay=1.5;called=threading.Event();counter=0

class CDPPage:
    """Read our test page without Playwright changing its download context defaults."""
    def __init__(self,socket,session):self.socket=socket;self.session=session;self.sequence=0
    async def send(self,method,params=None,root=False):
        self.sequence+=1;packet={'id':self.sequence,'method':method,'params':params or {}}
        if not root:packet['sessionId']=self.session
        await self.socket.send(json.dumps(packet))
        while True:
            r=json.loads(await self.socket.recv())
            if r.get('id')==self.sequence:
                if 'error' in r:raise ValueError(r['error'])
                return r.get('result',{})
    async def evaluate(self,expression,arg=None):
        code='('+expression+')('+json.dumps(arg)+')' if arg is not None else expression
        r=await self.send('Runtime.evaluate',{'expression':code,'returnByValue':True,'awaitPromise':True})
        if 'exceptionDetails' in r:raise ValueError(r['exceptionDetails'])
        return r.get('result',{}).get('value')
    async def set_content(self,html):await self.evaluate('(s)=>{document.open();document.write(s);document.close();}',html)
    async def bring_to_front(self):await self.send('Page.bringToFront')
    async def title(self):return await self.evaluate('document.title')
    async def wait_for_function(self,code,timeout=20000):
        deadline=time.monotonic()+timeout/1000
        while time.monotonic()<deadline:
            if await self.evaluate(code):return
            await asyncio.sleep(.1)
        raise AssertionError('Page condition timed out')
    def locator(self,selector):
        page=self
        class Locator:
            async def evaluate(self,expression):return await page.evaluate('('+expression+')(document.querySelector('+json.dumps(selector)+'))')
            async def input_value(self):return await self.evaluate('(e)=>e.value')
            async def get_attribute(self,name):return await self.evaluate('(e)=>e.getAttribute('+json.dumps(name)+')')
        return Locator()

class ModelFixture(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_POST(self):
        global counter
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        counter+=1;number=counter
        fixture_calls.append({'request':number,'model':body.get('model'),'has_screenshot':any(isinstance(m.get('content'),list) and any(isinstance(c,dict) and c.get('type')=='image_url' for c in m['content']) for m in body.get('messages',[])),'steering_received':'继续验收' in json.dumps(body,ensure_ascii=False)})
        called.set();time.sleep(delay)
        action={'navigate':{'url':'https://example.com/?from=browser-use','new_tab':False}} if number==1 else {'done':{'text':'Fixture completed the real browser navigation.','success':True,'files_to_display':[]}}
        content={'thinking':'Controlled free acceptance fixture.','evaluation_previous_goal':'The prior step was checked.','memory':'This is an isolated acceptance run.','next_goal':'Navigate' if number==1 else 'Finish','action':[action]}
        response={'id':'cloud-acceptance','object':'chat.completion','created':int(time.time()),'model':body.get('model','fixture'),'choices':[{'index':0,'message':{'role':'assistant','content':json.dumps(content)},'finish_reason':'stop'}],'usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20}}
        packet=json.dumps(response).encode()
        try:self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(packet)));self.end_headers();self.wfile.write(packet)
        except (BrokenPipeError,ConnectionResetError):pass

async def main():
    global counter,delay
    async with httpx.AsyncClient(base_url='http://127.0.0.1:18193',headers=HEADERS,timeout=30,trust_env=False) as client:
        async def call(method,path,body=None):
            r=await client.request(method,path,json=body);r.raise_for_status();return r.json()
        async def wait_status(predicate,seconds=30):
            deadline=time.monotonic()+seconds
            while time.monotonic()<deadline:
                s=await call('GET','/status')
                if predicate(s):return s
                await asyncio.sleep(.2)
            raise AssertionError('Desktop status did not reach the required state: '+json.dumps(s))
        s=await call('GET','/status');assert s['available']
        assert not s.get('job') or s['job']['state'] not in ('running','paused','waiting_user'),'Refuse to interfere with an active user task'
        assert (await client.get('/frame',headers={'Authorization':'Bearer invalid'})).status_code==401
        assert (await client.post('/input',json={'kind':'click','x':50,'y':50})).status_code==409
        checks['private_auth_and_manual_guard']=True
        group=subprocess.check_output(['systemctl','show','proactive-desktop.service','-p','ControlGroup','--value'],text=True).strip()
        pids=[int(p) for p in (Path('/sys/fs/cgroup')/group.lstrip('/')/'cgroup.procs').read_text().split()]
        browser=None
        for pid in pids:
            try:args=(Path('/proc')/str(pid)/'cmdline').read_bytes().decode().split('\0')
            except FileNotFoundError:continue
            if any(a=='--user-data-dir='+str(BASE/'desktop-home/browser-profile') for a in args) and not any(a.startswith('--type=') for a in args):
                port=next((a.split('=',1)[1] for a in args if a.startswith('--remote-debugging-port=')),None)
                if port:browser=(pid,int(port),args);break
        assert browser,'No browser belonging to this project was found'
        assert '--no-sandbox' not in browser[2]
        checks['chromium_sandbox_retained']=True
        checks['own_browser_pid']=browser[0]
        async with httpx.AsyncClient(trust_env=False) as raw:
            version=(await raw.get('http://127.0.0.1:'+str(browser[1])+'/json/version')).json()
        async with websockets.connect(version['webSocketDebuggerUrl'],max_size=20000000) as cdp_socket:
            page=CDPPage(cdp_socket,None);targets=await page.send('Target.getTargets',root=True)
            target=next(t for t in targets['targetInfos'] if t['type']=='page')
            session=await page.send('Target.attachToTarget',{'targetId':target['targetId'],'flatten':True},root=True);page.session=session['sessionId']
            await page.set_content('<html><meta charset="utf-8"><title>Cloud computer acceptance</title><style>body{font:24px sans-serif;margin:80px;background:#f7f7f5;color:#19191b}textarea{font:24px sans-serif;width:760px;height:100px;margin:20px 0}button{font:24px sans-serif;padding:18px 28px;border:0;background:#19191b;color:white;border-radius:16px}</style><h1>云电脑 · 真实输入验收</h1><p>使用屏幕坐标点击和系统剪贴板输入。</p><textarea id="proof"></textarea><br><button id="count" onclick="this.textContent=\'点击已到达\';this.dataset.clicked=\'yes\'">点一下</button></html>')
            await page.bring_to_front();await asyncio.sleep(.4)
            await call('POST','/takeover')
            async def coords(selector):
                d=await page.locator(selector).evaluate('(e)=>{const r=e.getBoundingClientRect();return {x:screenX+r.left+r.width/2,y:screenY+outerHeight-innerHeight+r.top+r.height/2}}')
                return {'x':int(d['x']),'y':int(d['y'])}
            await call('POST','/input',{'kind':'click',**await coords('#proof')})
            text='中文输入：白kawa 与 你好，云电脑 🌙'
            await call('POST','/input',{'kind':'type','text':text});await asyncio.sleep(.2)
            assert await page.locator('#proof').input_value()==text,'Unicode clipboard input did not reach the browser'
            await call('POST','/input',{'kind':'click',**await coords('#count')})
            assert await page.locator('#count').get_attribute('data-clicked')=='yes','Coordinate click did not reach its target'
            checks['real_coordinate_click']=True;checks['real_unicode_input']=True
            frame=await call('GET','/frame');(OUT/'input-proof.jpg').write_bytes(base64.b64decode(frame['image']))
            payload=('真实云电脑分块下载验收\n'.encode()+bytes(range(256))*3000)
            encoded=base64.b64encode(payload).decode()
            download_name=f'cloud-computer-proof-{time.time_ns()}.bin'
            await page.evaluate('([s,name])=>{const bytes=Uint8Array.from(atob(s),c=>c.charCodeAt(0));const a=document.createElement("a");a.href=URL.createObjectURL(new Blob([bytes]));a.download=name;a.click();}',[encoded,download_name])
            name=None
            for _ in range(40):
                files=(await call('GET','/files'))['files'];name=next((f['path'] for f in files if f['path'].endswith(download_name)),None)
                if name:break
                await asyncio.sleep(.2)
            assert name,'Chromium download did not reach its own download directory'
            data=bytearray()
            while True:
                r=await client.get('/file',params={'path':name,'offset':len(data),'size':262144});r.raise_for_status();p=r.json()
                assert p['offset']==len(data);data.extend(base64.b64decode(p['data']))
                if p['eof']:break
            assert hashlib.sha256(data).digest()==hashlib.sha256(payload).digest()
            checks['real_browser_download']={'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'path':name}
            assert (await client.post('/navigate',json={'url':'http://127.0.0.1:18190/health'})).status_code==409
            assert (await client.post('/navigate',json={'url':'http://216.23.82.196/'})).status_code==409
            checks['host_destinations_rejected']=True
            await call('POST','/navigate',{'url':'https://example.com/'})
            await page.wait_for_function('document.title === "Example Domain"',timeout=20000)
            checks['real_public_navigation']=await page.title()
            await call('POST','/handback')
            with socket.socket() as probe:probe.bind(('127.0.0.1',18196))
            fixture=ThreadingHTTPServer(('127.0.0.1',18196),ModelFixture);threading.Thread(target=fixture.serve_forever,daemon=True).start()
            try:
                counter=0;called.clear()
                run=await call('POST','/run',{'goal':'[isolated acceptance] Navigate to Example Domain, then finish.','base_url':'http://127.0.0.1:18196/v1','api_key':'free-fixture-only','model':'cloud-proof-fixture','max_steps':4})
                checks['real_harness_job']=run['job']['id']
                assert await asyncio.to_thread(called.wait,20),'Browser Use did not call the local model fixture'
                assert (await call('POST','/takeover'))['control']=='pausing'
                paused=await wait_status(lambda s:s['control']=='user',25)
                assert paused['job']['state']=='waiting_user'
                count=len(fixture_calls);await asyncio.sleep(.4);assert len(fixture_calls)==count,'Agent continued while user held control'
                checks['pause_at_step_boundary']=True
                await call('POST','/steer',{'text':'继续验收，保留当前页面。'})
                await call('POST','/handback')
                done=await wait_status(lambda s:(s.get('job') or {}).get('state') in ('done','failed'),30)
                assert done['job']['state']=='done',json.dumps(done['job'])
                assert 'from=browser-use' in await page.evaluate('location.href'),'The real harness did not navigate the browser'
                assert done['job']['trace'] and any('navigate' in a for t in done['job']['trace'] for a in t['actions'])
                checks['real_harness_navigation']=True;checks['harness_trace']=done['job']['trace']
                checks['fixture_saw_screenshot']=any(c['has_screenshot'] for c in fixture_calls)
                assert checks['fixture_saw_screenshot']
                checks['steering_received']=any(c['steering_received'] for c in fixture_calls)
                assert checks['steering_received']
                await call('POST','/handback');counter=0;called.clear();delay=4
                await call('POST','/run',{'goal':'[isolated acceptance] Stop this test before completion.','base_url':'http://127.0.0.1:18196/v1','api_key':'free-fixture-only','model':'cloud-proof-fixture','max_steps':4})
                assert await asyncio.to_thread(called.wait,15)
                stopped=await call('POST','/stop');assert stopped['job']['state']=='stopped'
                await asyncio.sleep(.5);assert (await call('GET','/status'))['job']['state']=='stopped'
                checks['real_harness_stop']=True
            finally:fixture.shutdown();fixture.server_close()
            frame=await call('GET','/frame');(OUT/'browser-proof.jpg').write_bytes(base64.b64decode(frame['image']))
            # Disconnect Playwright without closing the service's browser.
        checks['paid_model_calls']=0;checks['fixture_calls']=fixture_calls
    (OUT/'acceptance.json').write_text(json.dumps(checks,indent=2,ensure_ascii=False))
    print(json.dumps(checks,ensure_ascii=True))

try:asyncio.run(main())
except Exception as error:
    (OUT/'acceptance-failed.json').write_text(json.dumps({'error':repr(error),'checks':checks,'fixture_calls':fixture_calls},indent=2))
    raise
