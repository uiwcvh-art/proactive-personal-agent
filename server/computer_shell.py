"""Isolated Virtual Computer Shell for the Agent.

Allows the Agent to install packages (pip, venv, npm, local binaries), compile code (gcc/python3),
and manage files inside its own isolated virtual computer storage (desktop-home + /tmp) while
strictly blocking any access to the host system outside the virtual computer boundary.
"""
import ctypes, os, resource, sys
from pathlib import Path

def restrict_virtual_box(desktop_home: str, workspace_root: str):
    libc = ctypes.CDLL(None, use_errno=True)
    def check(value):
        if value < 0: raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()))
        return value
    abi = check(libc.syscall(444, 0, 0, 1))
    if abi < 1: raise RuntimeError('Linux Landlock ABI >= 1 required')
    fs = (1 << min(abi + 12, 15)) - 1
    class Ruleset(ctypes.Structure):
        _fields_ = [('handled_access_fs', ctypes.c_uint64), ('handled_access_net', ctypes.c_uint64)]
    class Beneath(ctypes.Structure):
        _pack_ = 1
        _fields_ = [('allowed_access', ctypes.c_uint64), ('parent_fd', ctypes.c_int32)]
    attr = Ruleset(fs, 0)
    fd = check(libc.syscall(444, ctypes.byref(attr), ctypes.sizeof(attr), 0))
    def allow(path, rights):
        if not os.path.exists(path): return
        p = os.open(path, os.O_PATH | os.O_CLOEXEC)
        rule = Beneath(rights, p)
        try: check(libc.syscall(445, fd, 1, ctypes.byref(rule), 0))
        except OSError: pass
        finally: os.close(p)
    read_dir = (1 << 0) | (1 << 2) | (1 << 3)
    read_file = (1 << 0) | (1 << 2)
    for path in ['/usr', '/bin', '/lib', '/lib64', '/sbin', '/proc', '/sys', '/etc/alternatives', '/etc/ssl', '/etc/ca-certificates', '/etc/pki']:
        allow(path, read_dir)
    for path in ['/etc/ld.so.cache', '/etc/resolv.conf', '/etc/hosts', '/etc/nsswitch.conf', '/etc/localtime', '/etc/timezone', '/etc/passwd', '/etc/group']:
        allow(path, read_file)
    # Full read/write/exec inside the virtual computer's dedicated home, workspace, and /tmp
    allow(desktop_home, fs)
    allow(workspace_root, fs)
    allow('/tmp', fs)
    allow('/var/tmp', fs)
    allow('/dev/pts', fs)
    for dev in ['/dev/null', '/dev/zero', '/dev/urandom', '/dev/random', '/dev/ptmx', '/dev/tty']:
        allow(dev, (1 << 1) | (1 << 2))
    check(libc.prctl(38, 1, 0, 0, 0))
    check(libc.syscall(446, fd, 0))
    os.close(fd)

if __name__ == '__main__':
    try:
        workspace_root = os.path.realpath(sys.argv[1])
        desktop_home = os.path.realpath(str(Path(workspace_root).parent))
        os.makedirs(workspace_root, exist_ok=True)
        for sub in ('.local/bin', '.cache/pip', 'tmp', 'Downloads'):
            os.makedirs(os.path.join(desktop_home, sub), exist_ok=True)
        os.chdir(workspace_root)
        resource.setrlimit(resource.RLIMIT_CPU, (35, 35))
        resource.setrlimit(resource.RLIMIT_FSIZE, (256 * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_NPROC, (256, 256))
        resource.setrlimit(resource.RLIMIT_NOFILE, (1024, 1024))
        restrict_virtual_box(desktop_home, workspace_root)
        venv_dir = os.path.join(desktop_home, 'venv')
        venv_bin = os.path.join(venv_dir, 'bin')
        local_prefix = os.path.join(desktop_home, '.local')
        local_bin = os.path.join(local_prefix, 'bin')
        path_parts = [p for p in (venv_bin, local_bin, '/usr/local/bin', '/usr/bin', '/bin') if os.path.exists(p)]
        env = {
            'PATH': ':'.join(path_parts),
            'HOME': desktop_home,
            'WORKSPACE': workspace_root,
            'TMPDIR': os.path.join(desktop_home, 'tmp'),
            'PIP_CACHE_DIR': os.path.join(desktop_home, '.cache', 'pip'),
            'PYTHONUSERBASE': local_prefix,
            'PREFIX': local_prefix,
            'CMAKE_INSTALL_PREFIX': local_prefix,
            'npm_config_prefix': local_prefix,
            'TZ': 'Asia/Shanghai',
            'LANG': 'C.UTF-8',
            'DISPLAY': ':91',
            'http_proxy': 'http://127.0.0.1:18194',
            'https_proxy': 'http://127.0.0.1:18194',
            'HTTP_PROXY': 'http://127.0.0.1:18194',
            'HTTPS_PROXY': 'http://127.0.0.1:18194',
        }
        if os.path.exists(venv_bin):
            env['VIRTUAL_ENV'] = venv_dir
        os.execve('/bin/bash', ['bash', '--noprofile', '--norc', '-c', sys.argv[2]], env)
    except Exception as error:
        print('COMPUTER_SHELL_REFUSED: ' + str(error), file=sys.stderr)
        sys.exit(126)
