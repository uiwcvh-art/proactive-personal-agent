"""Bound this project's desktop to its own isolated virtual computer storage and public web destinations.

No host firewall, network, Docker or existing browser configuration is changed.
Landlock applies to this process and its children only. The HTTP proxy allows
safe public package/web downloads (pip, git, curl, NetSurf) while blocking all
loopback/private/host-metadata addresses.
"""
from __future__ import annotations
import asyncio, ctypes, ipaddress, os, socket, sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def restrict_desktop(read_paths:list[Path], write_paths:list[Path]):
    if sys.platform!='linux':raise RuntimeError('The isolated cloud desktop requires Linux')
    libc=ctypes.CDLL(None,use_errno=True)
    def check(value):
        if value<0:raise OSError(ctypes.get_errno(),os.strerror(ctypes.get_errno()))
        return value
    if check(libc.syscall(444,0,0,1))<6:raise RuntimeError('Landlock ABI >= 6 is required for desktop filesystem, signal and abstract-socket isolation')
    fs=(1<<15)-1
    class Ruleset(ctypes.Structure):_fields_=[('fs',ctypes.c_uint64),('net',ctypes.c_uint64),('scoped',ctypes.c_uint64)]
    class Beneath(ctypes.Structure):
        _pack_=1
        _fields_=[('rights',ctypes.c_uint64),('parent',ctypes.c_int32)]
    ruleset=Ruleset(fs,0,3);fd=check(libc.syscall(444,ctypes.byref(ruleset),ctypes.sizeof(ruleset),0))
    def allow(path,rights):
        path=Path(path)
        if not path.exists():return
        if not path.is_dir():rights&=(1<<0)|(1<<1)|(1<<2)
        parent=os.open(path,os.O_PATH|os.O_CLOEXEC)
        try:check(libc.syscall(445,fd,1,ctypes.byref(Beneath(rights,parent)),0))
        finally:os.close(parent)
    read=(1<<0)|(1<<2)|(1<<3)
    for path in ['/usr','/bin','/sbin','/lib','/lib64','/proc','/sys','/etc','/var/cache','/var/lib/apt','/var/lib/dpkg']:
        allow(path,read)
    for path in read_paths:allow(path,read)
    allow('/proc',read|(1<<1))
    for path in write_paths:allow(path,fs)
    for path in ['/tmp','/var/tmp','/dev/pts','/dev/shm','/run']:allow(path,fs)
    for path in ['/dev/null','/dev/zero','/dev/urandom','/dev/random','/dev/ptmx','/dev/tty','/dev/dri']:
        allow(path,(1<<1)|(1<<2))
    check(libc.prctl(38,1,0,0,0));check(libc.syscall(446,fd,0));os.close(fd)


async def public_addresses(host:str,port:int,blocked_hosts=()):
    name=host.rstrip('.').lower()
    if name in {str(item).lower() for item in blocked_hosts} or name=='localhost' or name.endswith(('.localhost','.local','.internal')):
        raise ValueError('这个地址不属于云电脑可访问的公开网站')
    if port not in (80,443):raise ValueError('云电脑只访问公开 HTTP(S) 网站')
    records=await asyncio.get_running_loop().getaddrinfo(name,port,type=socket.SOCK_STREAM)
    addresses=list(dict.fromkeys(record[4][0] for record in records))
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError('云电脑不访问宿主机、内网或元数据服务')
    if any(address in blocked_hosts for address in addresses):raise ValueError('这个服务器地址不在云电脑访问范围内')
    return addresses


class PublicWebProxy:
    def __init__(self,port=18194,blocked_hosts=()):
        self.port=port;self.blocked_hosts=set(blocked_hosts);self.server=None
        self.slots=asyncio.Semaphore(48)

    async def start(self):self.server=await asyncio.start_server(self.handle,'127.0.0.1',self.port,limit=65536)
    async def close(self):
        if self.server:self.server.close();await self.server.wait_closed()

    async def handle(self,reader,writer):
        upstream=None;responded=False
        try:
            async with self.slots:
                head=await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'),15)
                if len(head)>65536:raise ValueError('Proxy header too large')
                first,*headers=head.decode('iso-8859-1').split('\r\n')
                method,target,version=first.split(' ',2)
                if method=='CONNECT':
                    parsed=urlsplit('//'+target);host,port=parsed.hostname,parsed.port or 443
                else:
                    parsed=urlsplit(target);host,port=parsed.hostname,parsed.port or 80
                    if parsed.scheme!='http' or parsed.username or parsed.password:raise ValueError('HTTP proxy URL invalid')
                if not host:raise ValueError('Missing host')
                local_home=method=='GET' and host=='127.0.0.1' and port==18193 and parsed.path=='/home' and not parsed.query
                addresses=['127.0.0.1'] if local_home else await public_addresses(host,port,self.blocked_hosts)
                upstream_reader,upstream=await asyncio.wait_for(asyncio.open_connection(addresses[0],port),15)
                if method=='CONNECT':writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n');await writer.drain();responded=True
                else:
                    origin=urlunsplit(('', '',parsed.path or '/',parsed.query,''))
                    filtered=[line for line in headers if line and line.split(':',1)[0].lower() not in ('proxy-connection','proxy-authorization','connection')]
                    request=(f'{method} {origin} {version}\r\n'+'\r\n'.join(filtered)+'\r\nConnection: close\r\n\r\n').encode('iso-8859-1')
                    upstream.write(request);await upstream.drain()
                async def relay(source,destination):
                    try:
                        while data:=await source.read(65536):destination.write(data);await destination.drain()
                    finally:
                        try:destination.write_eof()
                        except (OSError,RuntimeError,AttributeError):pass
                tasks=[asyncio.create_task(relay(reader,upstream)),asyncio.create_task(relay(upstream_reader,writer))]
                try:await asyncio.wait_for(asyncio.gather(*tasks),300)
                finally:
                    for task in tasks:task.cancel()
        except Exception:
            try:
                if not responded:
                    body=b'Public websites only.\r\n';writer.write(b'HTTP/1.1 403 Forbidden\r\nContent-Length: '+str(len(body)).encode()+b'\r\nConnection: close\r\n\r\n'+body);await writer.drain()
            except (OSError,RuntimeError):pass
        finally:
            if upstream:upstream.close()
            writer.close()
