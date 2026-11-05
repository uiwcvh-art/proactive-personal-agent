"""Linux Landlock sandbox for the model's Bash tool.

Confines filesystem writes to the workspace while permitting outbound HTTP/HTTPS
requests (e.g. curl $API_BASE_URL/models) and read-only system libraries/certs.
"""
import ctypes, os, resource, sys

def restrict(root):
    libc = ctypes.CDLL(None, use_errno=True)
    def check(value):
        if value < 0: raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()))
        return value
    abi = check(libc.syscall(444, 0, 0, 1))
    if abi < 1: raise RuntimeError('Bash requires Linux Landlock ABI >= 1')
    # EXECUTE, WRITE, READ, READ_DIR, REMOVE_*, MAKE_* and REFER/TRUNCATE.
    fs = (1 << min(abi + 12, 15)) - 1
    class Ruleset(ctypes.Structure):
        _fields_=[('handled_access_fs',ctypes.c_uint64),('handled_access_net',ctypes.c_uint64)]
    class Beneath(ctypes.Structure):
        _pack_=1
        _fields_=[('allowed_access',ctypes.c_uint64),('parent_fd',ctypes.c_int32)]
    attr = Ruleset(fs, 0)
    fd = check(libc.syscall(444,ctypes.byref(attr),ctypes.sizeof(attr),0))
    def allow(path, rights):
        if not os.path.exists(path): return
        p = os.open(path, os.O_PATH | os.O_CLOEXEC)
        rule = Beneath(rights, p)
        try: check(libc.syscall(445,fd,1,ctypes.byref(rule),0))
        except OSError: pass
        finally: os.close(p)
    read_dir = (1<<0)|(1<<2)|(1<<3)
    read_file = (1<<0)|(1<<2)
    for path in ['/usr','/bin','/lib','/lib64','/sbin','/etc/alternatives','/etc/ssl','/etc/ca-certificates','/etc/pki']:
        allow(path,read_dir)
    for path in ['/etc/resolv.conf','/etc/hosts','/etc/nsswitch.conf','/etc/localtime']:
        allow(path,read_file)
    allow(root,fs)
    allow('/dev/null',(1<<1)|(1<<2))
    allow('/dev/urandom',1<<2)
    check(libc.prctl(38,1,0,0,0))
    check(libc.syscall(446,fd,0))
    os.close(fd)

if __name__ == '__main__':
    try:
        root = os.path.realpath(sys.argv[1])
        resource.setrlimit(resource.RLIMIT_CPU,(25,25))
        resource.setrlimit(resource.RLIMIT_FSIZE,(16*1024*1024,)*2)
        resource.setrlimit(resource.RLIMIT_NPROC,(64,64))
        resource.setrlimit(resource.RLIMIT_NOFILE,(256,256))
        os.chdir(root)
        restrict(root)
        env = {'PATH':'/usr/bin:/bin','HOME':root,'TMPDIR':root,'LANG':'C.UTF-8'}
        for k in ('API_BASE_URL','API_KEY','DEFAULT_MODEL'):
            if os.environ.get(k): env[k] = os.environ[k]
        os.execve('/bin/bash',['bash','--noprofile','--norc','-c',sys.argv[2]], env)
    except Exception as e:
        print(f'SANDBOX_REFUSED: {e}',file=sys.stderr)
        sys.exit(126)
