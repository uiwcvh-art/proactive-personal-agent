"""Private loopback RPC to this project's dedicated desktop service."""
from __future__ import annotations
import asyncio,os,time,re
from pathlib import Path
import httpx

class ComputerUnavailable(ValueError):pass

class ComputerClient:
    def __init__(self,token=None,transport=None):
        self.token=os.getenv('COMPUTER_CONTROL_TOKEN','') if token is None else token
        self.base='http://127.0.0.1:18193';self.transport=transport
        self.cached=None;self.cached_at=0;self.lock=asyncio.Lock()

    @property
    def configured(self):return bool(self.token)

    async def call(self,method,path,body=None,query=None):
        if not self.configured:raise ComputerUnavailable('云电脑尚未启用')
        if path not in ('/status','/frame','/takeover','/handback','/stop','/navigate','/input','/run','/steer','/files','/file','/library/uploads') and not re.fullmatch(r'/library/uploads/[a-f0-9]{32}(/finish)?',path):
            raise ValueError('云电脑操作无效')
        try:
            async with httpx.AsyncClient(base_url=self.base,timeout=2 if path=='/status' else 25,trust_env=False,transport=self.transport) as client:
                response=await client.request(method,path,json=body,params=query,headers={'Authorization':'Bearer '+self.token})
        except httpx.RequestError:raise ComputerUnavailable('暂时连不上云电脑，请稍后重试') from None
        if response.status_code==409:raise ValueError(str(response.json().get('detail','当前操作暂不可用'))[:250])
        if response.status_code>=400:raise ComputerUnavailable(f'云电脑操作失败（HTTP {response.status_code}）')
        self.cached_at=0 if method!='GET' else self.cached_at
        return response.json()

    async def status(self,force=False):
        async with self.lock:
            if not force and self.cached and time.monotonic()-self.cached_at<1:return self.cached
            try:
                result=await self.call('GET','/status');result['configured']=True
            except ComputerUnavailable as error:result={'configured':self.configured,'available':False,'error':str(error)}
            self.cached=result;self.cached_at=time.monotonic();return result

    async def get_file(self,path,target:Path):
        offset=0;temporary=target.with_suffix(target.suffix+'.partial')
        target.parent.mkdir(parents=True,exist_ok=True)
        try:
            import base64
            with temporary.open('wb') as output:
                while True:
                    packet=await self.call('GET','/file',query={'path':path,'offset':offset,'size':262144})
                    if packet['size']>50_000_000 or packet['offset']!=offset:raise ValueError('云电脑文件传输参数无效')
                    data=base64.b64decode(packet['data'],validate=True)
                    output.write(data)
                    if packet['next_offset']!=offset+len(data):raise ValueError('云电脑文件分块不连续')
                    offset+=len(data)
                    if packet['eof']:
                        if offset!=packet['size']:raise ValueError('云电脑文件大小不完整')
                        break
                    if not data:raise ValueError('云电脑文件传输未完成')
            temporary.replace(target)
            return {'path':path,'bytes':offset}
        finally:
            if temporary.exists():temporary.unlink()
