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
        self.cached=None;self.cached_at=0.0;self.lock=asyncio.Lock()
        self._http_client:httpx.AsyncClient|None=None

    @property
    def configured(self):return bool(self.token)

    def _get_client(self)->httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client=httpx.AsyncClient(
                base_url=self.base,
                timeout=httpx.Timeout(45.0,connect=3.0),
                trust_env=False,
                transport=self.transport,
            )
        return self._http_client

    async def call(self,method,path,body=None,query=None):
        if not self.configured:raise ComputerUnavailable('云电脑尚未启用')
        if path not in ('/status','/frame','/takeover','/handback','/stop','/navigate','/search','/input','/run','/steer','/files','/file','/library/uploads') and not re.fullmatch(r'/library/uploads/[a-f0-9]{32}(/finish)?',path):
            raise ValueError('云电脑操作无效')
        req_timeout=6.0 if path=='/status' else (12.0 if path=='/frame' else 45.0)
        if isinstance(body,dict) and body.get('timeout'):
            try:req_timeout=max(req_timeout,float(body['timeout'])+15.0)
            except (ValueError,TypeError):pass
        response=None
        for attempt in range(2):
            try:
                client=self._get_client()
                response=await client.request(method,path,json=body,params=query,headers={'Authorization':'Bearer '+self.token},timeout=req_timeout)
                break
            except httpx.TimeoutException:
                raise ComputerUnavailable(f'云电脑操作响应超时（{path} > {int(req_timeout)}s），请缩短单次命令耗时或稍后重试') from None
            except httpx.RequestError:
                if self._http_client and not self._http_client.is_closed:
                    try:await self._http_client.aclose()
                    except Exception:pass
                self._http_client=None
                if attempt==0:
                    await asyncio.sleep(0.25)
                    continue
                raise ComputerUnavailable('暂时连不上本机云电脑服务（127.0.0.1:18193），请稍后重试') from None
        if response.status_code==409:raise ValueError(str(response.json().get('detail','当前操作暂不可用'))[:250])
        if response.status_code>=400:
            detail=''
            try:detail=str(response.json().get('detail',''))[:180]
            except Exception:detail=(response.text or '')[:180]
            raise ComputerUnavailable(f'云电脑操作失败（HTTP {response.status_code}{": "+detail if detail else ""}）')
        self.cached_at=0.0 if method!='GET' else self.cached_at
        return response.json()

    async def status(self,force=False):
        if not force and self.cached and time.monotonic()-self.cached_at<2.0:
            return dict(self.cached)
        async with self.lock:
            if not force and self.cached and time.monotonic()-self.cached_at<2.0:
                return dict(self.cached)
            try:
                result=await self.call('GET','/status');result['configured']=True
            except ComputerUnavailable as error:
                if self.cached and self.cached.get('available') and time.monotonic()-self.cached_at<15.0:
                    stale=dict(self.cached);stale['stale']=True;return stale
                result={'configured':self.configured,'available':False,'error':str(error)}
            self.cached=result;self.cached_at=time.monotonic();return dict(result)

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

    async def upload_file(self, src_path: Path, remote_name: str | None = None):
        import base64
        p = Path(src_path)
        if not p.is_file():
            raise ValueError(f'待上传文件不存在: {p}')
        raw_bytes = p.read_bytes()
        total_size = len(raw_bytes)
        if total_size > 20_000_000:
            raise ValueError('上传到云电脑的文件不能超过 20 MB')
        fname = remote_name or p.name
        start_res = await self.call('POST', '/library/uploads', {'name': fname, 'size': total_size})
        key = start_res.get('key')
        if not key:
            raise ValueError('云电脑未返回上传句柄')
        offset = 0
        chunk_sz = 262_144
        try:
            while offset < total_size or total_size == 0:
                piece = raw_bytes[offset:offset + chunk_sz]
                b64_data = base64.b64encode(piece).decode('ascii')
                await self.call('PUT', f'/library/uploads/{key}', {'offset': offset, 'data': b64_data})
                offset += len(piece)
                if total_size == 0:
                    break
            return await self.call('POST', f'/library/uploads/{key}/finish')
        except Exception:
            try:
                await self.call('DELETE', f'/library/uploads/{key}')
            except Exception:
                pass
            raise
