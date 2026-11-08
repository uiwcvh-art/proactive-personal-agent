"""Linux Landlock sandbox for the model's Bash tool.

Confines filesystem writes to the workspace while permitting outbound HTTP/HTTPS
requests (e.g. curl $API_BASE_URL/models) and read-only system libraries/certs.
"""
import os, resource, sys
from computer_security import apply_landlock

def restrict(root):
    ro = [
        '/usr','/bin','/lib','/lib64','/sbin',
        '/opt/proactive-agent/venv',
        '/etc/alternatives','/etc/ssl','/etc/ca-certificates','/etc/pki',
        '/etc/resolv.conf','/etc/hosts','/etc/nsswitch.conf','/etc/localtime',
        '/etc/debian_version','/etc/os-release','/etc/fonts','/proc',
    ]
    apply_landlock(ro, [root], ('/dev/null', '/dev/urandom'), min_abi=1)

if __name__ == '__main__':
    try:
        root = os.path.realpath(sys.argv[1])
        resource.setrlimit(resource.RLIMIT_CPU,(25,25))
        resource.setrlimit(resource.RLIMIT_FSIZE,(16*1024*1024,)*2)
        resource.setrlimit(resource.RLIMIT_NPROC,(64,64))
        resource.setrlimit(resource.RLIMIT_NOFILE,(256,256))
        os.chdir(root)
        restrict(root)
        py_user_base = os.environ.get('PYTHONUSERBASE') or os.path.join(root, '.python_packages')
        py_user_bin = os.path.join(py_user_base, 'bin')
        venv_bin = '/opt/proactive-agent/venv/bin'
        path_parts = [p for p in (py_user_bin, venv_bin, '/usr/local/bin', '/usr/bin', '/bin') if os.path.isdir(p)]
        base_path = ':'.join(path_parts) if path_parts else '/usr/local/bin:/usr/bin:/bin'
        env = {
            'PATH': base_path,
            'HOME': root,
            'TMPDIR': root,
            'LANG': os.environ.get('LANG', 'C.UTF-8'),
            'TZ': os.environ.get('TZ', 'Asia/Shanghai'),
            'PYTHONUSERBASE': py_user_base,
            'PIP_USER': os.environ.get('PIP_USER', '1'),
        }
        for k in ('API_BASE_URL', 'API_KEY', 'DEFAULT_MODEL', 'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY'):
            if os.environ.get(k):
                env[k] = os.environ[k]
        os.execve('/bin/bash', ['bash', '--noprofile', '--norc', '-c', sys.argv[2]], env)
    except Exception as e:
        print(f'SANDBOX_REFUSED: {e}',file=sys.stderr)
        sys.exit(126)
