"""Isolated 1C 1G 10GB Virtual Computer Shell for the Agent.

Allows the Agent to install packages (pip, venv, npm, user-space apt-get, local binaries),
use passwordless `sudo`, access `~/.local/root_home` (`/root` mirror), compile code (gcc/python3),
and manage files inside its own isolated 1C 1G 10GB virtual computer storage (desktop-home + /tmp)
while strictly blocking any access to the host system outside the virtual computer boundary.
"""
import ctypes
import os
import resource
import shutil
import sys
from pathlib import Path


def restrict_virtual_box(desktop_home: str, workspace_root: str):
    libc = ctypes.CDLL(None, use_errno=True)

    def check(value):
        if value < 0:
            raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()))
        return value

    abi = check(libc.syscall(444, 0, 0, 1))
    if abi < 1:
        raise RuntimeError('Linux Landlock ABI >= 1 required')
    fs = (1 << min(abi + 12, 15)) - 1

    class Ruleset(ctypes.Structure):
        _fields_ = [('handled_access_fs', ctypes.c_uint64), ('handled_access_net', ctypes.c_uint64)]

    class Beneath(ctypes.Structure):
        _pack_ = 1
        _fields_ = [('allowed_access', ctypes.c_uint64), ('parent_fd', ctypes.c_int32)]

    attr = Ruleset(fs, 0)
    fd = check(libc.syscall(444, ctypes.byref(attr), ctypes.sizeof(attr), 0))

    def allow(path, rights):
        if not os.path.exists(path):
            return
        p = os.open(path, os.O_PATH | os.O_CLOEXEC)
        rule = Beneath(rights, p)
        try:
            check(libc.syscall(445, fd, 1, ctypes.byref(rule), 0))
        except OSError:
            pass
        finally:
            os.close(p)

    read_dir = (1 << 0) | (1 << 2) | (1 << 3)
    read_file = (1 << 0) | (1 << 2)
    for path in ['/usr', '/bin', '/lib', '/lib64', '/sbin', '/proc', '/sys', '/etc', '/var/cache', '/var/lib/apt', '/var/lib/dpkg']:
        allow(path, read_dir)
    # Full read/write/exec inside the virtual computer's dedicated home, workspace, /tmp, /dev/shm, and runtime dir
    for path in [desktop_home, workspace_root, '/tmp', '/var/tmp', '/dev/pts', '/dev/shm', '/run/proactive-desktop']:
        allow(path, fs)
    for dev in ['/dev/null', '/dev/zero', '/dev/urandom', '/dev/random', '/dev/ptmx', '/dev/tty', '/dev/dri']:
        allow(dev, (1 << 1) | (1 << 2))
    check(libc.prctl(38, 1, 0, 0, 0))
    check(libc.syscall(446, fd, 0))
    os.close(fd)


if __name__ == '__main__':
    try:
        workspace_root = os.path.realpath(sys.argv[1])
        desktop_home = os.path.realpath(str(Path(workspace_root).parent))
        os.makedirs(workspace_root, exist_ok=True)
        for sub in ('.local/bin', '.local/sysroot', '.local/root_home', '.local/apt_cache', '.local/state', '.cache/pip', 'tmp', 'Downloads'):
            os.makedirs(os.path.join(desktop_home, sub), exist_ok=True)

        # Persist working directory across calls
        cwd_file = os.path.join(desktop_home, '.local', 'state', 'agent_cwd')
        start_cwd = workspace_root
        if os.path.exists(cwd_file):
            try:
                saved_cwd = open(cwd_file, 'r', encoding='utf-8').read().strip()
                if saved_cwd and os.path.isdir(saved_cwd) and saved_cwd.startswith(desktop_home):
                    start_cwd = saved_cwd
            except Exception:
                start_cwd = workspace_root
        os.chdir(start_cwd)

        resource.setrlimit(resource.RLIMIT_CPU, (45, 45))
        resource.setrlimit(resource.RLIMIT_FSIZE, (512 * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_NPROC, (256, 256))
        resource.setrlimit(resource.RLIMIT_NOFILE, (1024, 1024))
        restrict_virtual_box(desktop_home, workspace_root)

        venv_dir = os.path.join(desktop_home, 'venv')
        venv_bin = os.path.join(venv_dir, 'bin')
        local_prefix = os.path.join(desktop_home, '.local')
        local_bin = os.path.join(local_prefix, 'bin')
        sysroot = os.path.join(local_prefix, 'sysroot')
        path_parts = [
            p for p in (
                venv_bin,
                local_bin,
                os.path.join(sysroot, 'usr', 'local', 'bin'),
                os.path.join(sysroot, 'usr', 'bin'),
                os.path.join(sysroot, 'bin'),
                '/usr/local/bin',
                '/usr/bin',
                '/bin',
            ) if os.path.exists(p)
        ]
        lib_parts = [
            p for p in (
                os.path.join(sysroot, 'usr', 'lib', 'x86_64-linux-gnu'),
                os.path.join(sysroot, 'lib', 'x86_64-linux-gnu'),
                os.path.join(sysroot, 'usr', 'lib'),
            ) if os.path.exists(p)
        ]
        env = {
            'PATH': ':'.join(path_parts),
            'LD_LIBRARY_PATH': ':'.join(lib_parts),
            'HOME': desktop_home,
            'WORKSPACE': workspace_root,
            'TMPDIR': os.path.join(desktop_home, 'tmp'),
            'PIP_CACHE_DIR': os.path.join(desktop_home, '.cache', 'pip'),
            'PYTHONUSERBASE': local_prefix,
            'PREFIX': local_prefix,
            'CMAKE_INSTALL_PREFIX': local_prefix,
            'npm_config_prefix': local_prefix,
            'SUBSYSTEM_PROFILE': '1 vCPU · 1 GB RAM · 10 GB SSD',
            'TZ': 'Asia/Shanghai',
            'LANG': 'C.UTF-8',
            'DISPLAY': ':91',
            'XAUTHORITY': os.path.join(desktop_home, '.Xauthority'),
            'DBUS_SESSION_BUS_ADDRESS': 'disabled:',
            'DBUS_SYSTEM_BUS_ADDRESS': 'disabled:',
        }
        if os.path.exists(venv_bin):
            env['VIRTUAL_ENV'] = venv_dir
        import socket
        try:
            with socket.create_connection(('127.0.0.1', 40001), timeout=0.15):
                env['HTTP_PROXY'] = 'http://127.0.0.1:40001'
                env['HTTPS_PROXY'] = 'http://127.0.0.1:40001'
                env['ALL_PROXY'] = 'socks5h://127.0.0.1:40000'
                env['NO_PROXY'] = '127.0.0.1,localhost'
        except OSError:
            pass

        raw_cmd = sys.argv[2]
        # Close inherited stdout/stderr on background jobs (`cmd &`) so pipes never hang after bash exits
        wrapped = (
            f'{raw_cmd}\n'
            f'__EC=$?\n'
            f'pwd > "{cwd_file}" 2>/dev/null || true\n'
            f'exec >/dev/null 2>&1\n'
            f'exit $__EC\n'
        )
        os.execve('/bin/bash', ['bash', '--noprofile', '--norc', '-c', wrapped], env)
    except Exception as error:
        print('COMPUTER_SHELL_REFUSED: ' + str(error), file=sys.stderr)
        sys.exit(126)
