"""Test actual cloud uploads and the real Agent's Bash tool; no paid providers."""
import asyncio,base64,hashlib,json,shlex,socket,threading,time
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import httpx
BASE=Path('/opt/proactive-agent/cloud-computer');OUT=BASE/'bootstrap'
TOKEN=(BASE/'control.env').read_text().strip().split('=',1)[1]
command='';requests_seen=0
class Fixture(BaseHTTPRequestHandler):
    def log_message(self,*a):pass
    def do_POST(self):
        global requests_seen
        self.rfile.read(int(self.headers['Content-Length']));requests_seen+=1
        action={'run_bash':{'command':command}} if requests_seen==1 else {'done':{'text':'Workspace Bash fixture completed.','success':True,'files_to_display':[]}}
        content={'thinking':'Isolated fixture.','evaluation_previous_goal':'Checked.','memory':'Workspace acceptance.','next_goal':'Run Bash' if requests_seen==1 else 'Finish','action':[action]}
        packet=json.dumps({'id':'workspace-proof','object':'chat.completion','model':'workspace-fixture','choices':[{'index':0,'message':{'role':'assistant','content':json.dumps(content)},'finish_reason':'stop'}],'usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20}}).encode()
        self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(packet)));self.end_headers();self.wfile.write(packet)
async def main():
    global command
    checks={}
    async with httpx.AsyncClient(base_url='http://127.0.0.1:18193',headers={'Authorization':'Bearer '+TOKEN},timeout=30,trust_env=False) as client:
        async def call(method,path,body=None):
            r=await client.request(method,path,json=body);r.raise_for_status();return r.json()
        s=await call('GET','/status');assert not s.get('job') or s['job']['state'] not in ('running','paused','waiting_user')
        data=bytes(range(251))*2900;digest=hashlib.sha256(data).hexdigest()
        key=(await call('POST','/library/uploads',{'name':'cloud-upload-proof.bin','size':len(data),'sha256':digest}))['id']
        offset=0
        for pos in range(0,len(data),262144):
            p=await call('PUT','/library/uploads/'+key,{'offset':offset,'data':base64.b64encode(data[pos:pos+262144]).decode()});offset=p['offset']
        receipt=await call('POST','/library/uploads/'+key+'/finish');assert receipt['sha256']==digest and receipt['bytes']==len(data)
        checks['real_cloud_upload']=receipt
        proof_name='cloud-harness-proof-'+str(time.time_ns())+'.json'
        script="""import hashlib,json,socket
from pathlib import Path
upload=Path(%r)
assert hashlib.sha256(upload.read_bytes()).hexdigest()==%r
try:
    Path('../browser-profile/Local State').read_bytes()
    raise RuntimeError('browser profile was readable by the shell tool')
except PermissionError:pass
with socket.socket() as s:
    try:
        s.connect(('127.0.0.1',18190))
        raise RuntimeError('shell TCP connection was allowed')
    except PermissionError:pass
proof={'actual_bash':True,'uploaded_file_sha256':%r,'browser_profile_blocked':True,'tcp_blocked':True}
Path(%r).write_text(json.dumps(proof))
print(json.dumps(proof))
"""%(receipt['path'].removeprefix('workspace/'),digest,digest,proof_name)
        command='python3 -c '+shlex.quote(script)
        fixture=ThreadingHTTPServer(('127.0.0.1',18197),Fixture);threading.Thread(target=fixture.serve_forever,daemon=True).start()
        try:
            await call('POST','/handback')
            run=await call('POST','/run',{'goal':'[isolated workspace acceptance] Check the uploaded fixture and the sandbox, then finish.','base_url':'http://127.0.0.1:18197/v1','api_key':'free-fixture-only','model':'workspace-fixture','max_steps':4})
            deadline=time.monotonic()+45
            while time.monotonic()<deadline:
                s=await call('GET','/status')
                if s['job']['state'] in ('done','failed'):break
                await asyncio.sleep(.3)
            assert s['job']['state']=='done',json.dumps(s)
            assert any('run_bash' in a for t in s['job']['trace'] for a in t['actions'])
            r=await client.get('/file',params={'path':'workspace/'+proof_name});r.raise_for_status();proof=json.loads(base64.b64decode(r.json()['data']))
            assert proof=={'actual_bash':True,'uploaded_file_sha256':digest,'browser_profile_blocked':True,'tcp_blocked':True}
            checks.update(proof);checks['job']=run['job']['id'];checks['paid_model_calls']=0;checks['fixture_calls']=requests_seen
        finally:
            s=await call('GET','/status')
            if s.get('job') and s['job']['goal'].startswith('[isolated workspace acceptance]') and s['job']['state'] in ('running','paused','waiting_user'):await call('POST','/stop')
            fixture.shutdown();fixture.server_close()
    (OUT/'workspace-acceptance.json').write_text(json.dumps(checks,indent=2));print(json.dumps(checks))
if __name__=='__main__':asyncio.run(main())
