import asyncio,base64,hashlib
from pathlib import Path
import httpx,pytest
from pydantic import ValidationError
from computer_client import ComputerClient,ComputerUnavailable
from computer_desktop import CloudDesktop,create_app
from computer_models import InputAction
from computer_security import public_addresses,PublicWebProxy


class FixtureDesktop(CloudDesktop):
    def __init__(self,root):
        super().__init__();self.home=root;self.files=root/'Downloads';self.workspace=root/'workspace';self.calls=[]
    async def start(self):self.files.mkdir();self.workspace.mkdir();self.started=True
    async def close(self):self.started=False
    def frame(self):self.calls.append('frame');return {'image':'eA==','mime':'image/jpeg','width':1280,'height':800}
    async def input(self,body):self.calls.append(body.model_dump());return {'ok':True}
    async def navigate(self,url):self.calls.append(url);return self.status()


def test_desktop_authentication_and_takeover_boundaries(tmp_path):
    async def scenario():
        desktop=FixtureDesktop(tmp_path);app=create_app(desktop,token='private-desktop-token')
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://desktop') as client:
                for path in ['/status','/frame','/files']:
                    assert (await client.get(path)).status_code==401
                assert (await client.post('/input',json={'kind':'click','x':2,'y':2})).status_code==401
                assert not desktop.calls
                client.headers['Authorization']='Bearer private-desktop-token'
                assert (await client.get('/status')).json()['available']
                assert (await client.post('/takeover')).json()['control_ready']
                assert (await client.post('/input',json={'kind':'type','text':'中文输入'})).status_code==200
                assert desktop.calls[-1]['text']=='中文输入'
                await client.post('/handback')
    asyncio.run(scenario())


def test_browser_files_are_scoped_and_downloaded_without_truncation(tmp_path):
    async def scenario():
        desktop=FixtureDesktop(tmp_path/'desktop');desktop.home.mkdir()
        app=create_app(desktop,token='file-token')
        async with app.router.lifespan_context(app):
            data=bytes(range(256))*2900
            (desktop.files/'资料.bin').write_bytes(data)
            rpc=ComputerClient(token='file-token',transport=httpx.ASGITransport(app=app))
            target=tmp_path/'download/资料.bin'
            receipt=await rpc.get_file('downloads/资料.bin',target)
            assert receipt['bytes']==len(data)
            assert hashlib.sha256(target.read_bytes()).digest()==hashlib.sha256(data).digest()
            for path in ['downloads/../../outside','workspace/../Downloads/资料.bin','/etc/passwd','downloads/C:\\secret']:
                with pytest.raises(ComputerUnavailable):await rpc.call('GET','/file',query={'path':path})
            assert not target.with_suffix('.bin.partial').exists()
    asyncio.run(scenario())


def test_failed_chunk_keeps_the_previous_download_and_removes_partial(tmp_path):
    async def scenario():
        def response(request):
            return httpx.Response(200,json={'size':5,'offset':99,'next_offset':102,'data':base64.b64encode(b'bad').decode(),'eof':False})
        client=ComputerClient(token='private',transport=httpx.MockTransport(response));target=tmp_path/'kept.txt';target.write_text('previous')
        with pytest.raises(ValueError):await client.get_file('downloads/test.txt',target)
        assert target.read_text()=='previous'
        assert not target.with_suffix('.txt.partial').exists()
    asyncio.run(scenario())


def test_public_browser_destinations_reject_private_and_mixed_dns(monkeypatch):
    async def scenario():
        loop=asyncio.get_running_loop()
        async def resolve_private(*args,**kwargs):return [(2,1,6,'',('127.0.0.1',80))]
        monkeypatch.setattr(loop,'getaddrinfo',resolve_private)
        with pytest.raises(ValueError):await public_addresses('public-looking.example',80)
        async def resolve_mixed(*args,**kwargs):return [(2,1,6,'',('93.184.216.34',443)),(2,1,6,'',('169.254.169.254',443))]
        monkeypatch.setattr(loop,'getaddrinfo',resolve_mixed)
        with pytest.raises(ValueError):await public_addresses('mixed.example',443)
        async def resolve_public(*args,**kwargs):return [(2,1,6,'',('93.184.216.34',443))]
        monkeypatch.setattr(loop,'getaddrinfo',resolve_public)
        assert await public_addresses('example.org',443)==['93.184.216.34']
        for host in ['localhost','x.localhost','metadata.google.internal','216.23.82.196']:
            with pytest.raises(ValueError):await public_addresses(host,443,{'216.23.82.196'})
    asyncio.run(scenario())


def test_proxy_rejects_loopback_without_contacting_its_target():
    async def scenario():
        proxy=PublicWebProxy(port=0)
        await proxy.start()
        try:
            port=proxy.server.sockets[0].getsockname()[1]
            reader,writer=await asyncio.open_connection('127.0.0.1',port)
            writer.write(b'CONNECT 127.0.0.1:18190 HTTP/1.1\r\nHost: 127.0.0.1:18190\r\n\r\n');await writer.drain()
            packet=await asyncio.wait_for(reader.read(),3)
            assert packet.startswith(b'HTTP/1.1 403 Forbidden')
            header,body=packet.split(b'\r\n\r\n',1)
            assert f'Content-Length: {len(body)}'.encode() in header
            writer.close();await writer.wait_closed()
        finally:await proxy.close()
    asyncio.run(scenario())


def test_unconfigured_computer_has_no_network_side_effect():
    async def scenario():
        client=ComputerClient(token='')
        status=await client.status()
        assert status['configured'] is False and status['available'] is False
        with pytest.raises(ComputerUnavailable):await client.call('POST','/stop')
    asyncio.run(scenario())
