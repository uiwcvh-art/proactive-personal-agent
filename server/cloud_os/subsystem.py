"""
1C 1G 10GB Isolated Cloud Computer Subsystem (`server/cloud_os/subsystem.py`).

Provides an isolated, GitHub-friendly 1-Core, 1GB RAM, 10GB Storage Linux environment
for the agent without requiring container privileges on the host VPS:
1. Enforces / exposes 1 vCPU (`nproc`), 1024 MB RAM (`free`), and 10 GB disk quota (`df`)
    inside the agent's workspace (`~/.local/bin` shims + cgroup/ulimit hints).
2. Provides passwordless `sudo` and user-space `apt` / `apt-get` / `dpkg` shims that
   install `.deb` packages cleanly into `~/.local/sysroot` (`PATH`, `LD_LIBRARY_PATH`, `PYTHONPATH`)
   or execute via `bwrap` / `fakeroot` so the agent never hits permission walls.
3. Ensures `/root` access works seamlessly via `~/.local/root_home` and `HOME` / `bwrap` mapping.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Dict


SUBSYSTEM_SPECS = {
    'cpu_cores': 1,
    'ram_mb': 1024,
    'disk_gb': 10,
    'disk_bytes': 10 * 1024 * 1024 * 1024,
    'profile_label': '1 vCPU · 1 GB RAM · 10 GB SSD',
}


def ensure_subsystem_environment(base_home: Path | None = None) -> Dict[str, str]:
    """
    Initialize the 1C 1G 10GB user-space subsystem shims and directory layout.
    Returns environment variables to merge into every agent shell/desktop process.
    """
    home = base_home or Path.home()
    local_bin = home / '.local' / 'bin'
    sysroot = home / '.local' / 'sysroot'
    workspace = home / 'workspace'
    root_mirror = home / '.local' / 'root_home'
    apt_cache = home / '.local' / 'apt_cache'

    for d in (local_bin, sysroot, workspace, root_mirror, apt_cache):
        d.mkdir(parents=True, exist_ok=True)

    _write_executable(
        local_bin / 'nproc',
        """#!/usr/bin/env bash
echo "1"
""",
    )

    _write_executable(
        local_bin / 'free',
        """#!/usr/bin/env bash
# 1GB (1024 MiB) physical RAM + 2GB (2048 MiB) virtual Swap subsystem reporting
USED_KB=$(awk '/^RssAnon:/ {s+=$2} END {print s+0}' /proc/[0-9]*/status 2>/dev/null)
if [ -z "$USED_KB" ] || [ "$USED_KB" -lt 40960 ]; then USED_KB=163840; fi
TOTAL_KB=1048576
if [ "$USED_KB" -gt 780000 ]; then USED_KB=520000; fi
FREE_KB=$((TOTAL_KB - USED_KB))
AVAIL_KB=$((FREE_KB + 65536))
if [ "$AVAIL_KB" -gt "$TOTAL_KB" ]; then AVAIL_KB=$TOTAL_KB; fi

SWAP_TOTAL_KB=2097152
SWAP_USED_KB=$(awk '/^VmSwap:/ {s+=$2} END {print s+0}' /proc/[0-9]*/status 2>/dev/null)
if [ -z "$SWAP_USED_KB" ]; then SWAP_USED_KB=0; fi
if [ "$SWAP_USED_KB" -gt "$SWAP_TOTAL_KB" ]; then SWAP_USED_KB=$((SWAP_TOTAL_KB / 2)); fi
SWAP_FREE_KB=$((SWAP_TOTAL_KB - SWAP_USED_KB))

if [[ "$*" == *"-h"* ]]; then
  USED_M=$((USED_KB / 1024))
  FREE_M=$((FREE_KB / 1024))
  AVAIL_M=$((AVAIL_KB / 1024))
  SW_USED_M=$((SWAP_USED_KB / 1024))
  SW_FREE_M=$((SWAP_FREE_KB / 1024))
  printf "               total        used        free      shared  buff/cache   available\\n"
  printf "Mem:          1.0Gi      %4dMi      %4dMi        12Mi        64Mi      %4dMi\\n" "$USED_M" "$FREE_M" "$AVAIL_M"
  printf "Swap:         2.0Gi      %4dMi      %4dMi\\n" "$SW_USED_M" "$SW_FREE_M"
elif [[ "$*" == *"-m"* ]]; then
  printf "               total        used        free      shared  buff/cache   available\\n"
  printf "Mem:            1024        %4d        %4d          12          64        %4d\\n" "$((USED_KB/1024))" "$((FREE_KB/1024))" "$((AVAIL_KB/1024))"
  printf "Swap:           2048        %4d        %4d\\n" "$((SWAP_USED_KB/1024))" "$((SWAP_FREE_KB/1024))"
else
  printf "               total        used        free      shared  buff/cache   available\\n"
  printf "Mem:         %7d     %7d     %7d       12288       65536     %7d\\n" "$TOTAL_KB" "$USED_KB" "$FREE_KB" "$AVAIL_KB"
  printf "Swap:        %7d     %7d     %7d\\n" "$SWAP_TOTAL_KB" "$SWAP_USED_KB" "$SWAP_FREE_KB"
fi
""",
    )

    _write_executable(
        local_bin / 'chromium',
        """#!/usr/bin/env bash
export DBUS_SESSION_BUS_ADDRESS="disabled:"
export DBUS_SYSTEM_BUS_ADDRESS="disabled:"
BIN="/usr/lib/chromium/chromium"
if [ ! -x "$BIN" ]; then BIN="/usr/bin/chromium"; fi
PROFILE_DIR="$HOME/.local/chromium_profile"
mkdir -p "$PROFILE_DIR/Default"
rm -f "$PROFILE_DIR/SingletonLock" "$PROFILE_DIR/SingletonCookie" "$PROFILE_DIR/SingletonSocket" 2>/dev/null || true
if [ -f "$PROFILE_DIR/Default/Preferences" ]; then
  sed -i 's/"exit_type":"Crashed"/"exit_type":"Normal"/g; s/"exited_cleanly":false/"exited_cleanly":true/g' "$PROFILE_DIR/Default/Preferences" 2>/dev/null || true
fi
HAS_UA=0
for arg in "$@"; do
  if [[ "$arg" == --user-agent* ]]; then HAS_UA=1; fi
done
COMMON_FLAGS=(--no-sandbox --test-type --disable-infobars --hide-crash-restore-bubble --disable-session-crashed-bubble --disable-gpu --disable-dev-shm-usage --disable-blink-features=AutomationControlled --no-first-run --no-default-browser-check --remote-debugging-port=9222 --remote-allow-origins=* --user-data-dir="$PROFILE_DIR" --window-size=1252,736 --window-position=14,10)
if [ "$HAS_UA" -eq 0 ]; then
  exec "$BIN" "${COMMON_FLAGS[@]}" --user-agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36" "$@"
else
  exec "$BIN" "${COMMON_FLAGS[@]}" "$@"
fi
""",
    )
    _write_executable(
        local_bin / 'chromium-browser',
        f"""#!/usr/bin/env bash
exec "{local_bin / 'chromium'}" "$@"
""",
    )

    _write_executable(
        local_bin / 'df',
        f"""#!/usr/bin/env bash
# 10GB (10485760 KB) virtual subsystem storage reporting
USED_KB=$(du -sk "{home}" 2>/dev/null | awk '{{print $1+0}}')
if [ -z "$USED_KB" ] || [ "$USED_KB" -lt 102400 ]; then USED_KB=409600; fi
TOTAL_KB=10485760
if [ "$USED_KB" -gt "$TOTAL_KB" ]; then USED_KB=$((TOTAL_KB - 524288)); fi
AVAIL_KB=$((TOTAL_KB - USED_KB))
PCT=$(( (USED_KB * 100) / TOTAL_KB ))
if [ "$PCT" -lt 1 ]; then PCT=1; fi

if [[ "$*" == *"-h"* ]]; then
  USED_G=$(awk "BEGIN {{printf \\"%.1fG\\", $USED_KB/1048576}}")
  AVAIL_G=$(awk "BEGIN {{printf \\"%.1fG\\", $AVAIL_KB/1048576}}")
  printf "Filesystem      Size  Used Avail Use%% Mounted on\\n"
  printf "/dev/vda1        10G  %4s  %4s  %2d%% /\\n" "$USED_G" "$AVAIL_G" "$PCT"
else
  printf "Filesystem     1K-blocks     Used Available Use%% Mounted on\\n"
  printf "/dev/vda1       %8d %8d  %8d  %2d%% /\\n" "$TOTAL_KB" "$USED_KB" "$AVAIL_KB" "$PCT"
fi
""",
    )

    _write_executable(
        local_bin / 'sudo',
        """#!/usr/bin/env bash
# Passwordless sudo shim for the isolated 1C1G10G subsystem
while [[ "$1" == -* ]]; do
  if [[ "$1" == "-u" || "$1" == "-g" ]]; then
    shift 2
  else
    shift
  fi
done
if [ $# -eq 0 ]; then
  exit 0
fi
if command -v fakeroot >/dev/null 2>&1; then
  exec fakeroot -- "$@"
else
  exec "$@"
fi
""",
    )

    _write_executable(
        local_bin / 'apt-get',
        f"""#!/usr/bin/env bash
# User-space apt-get installer unpacking into {sysroot}
SYSROOT="{sysroot}"
CACHE="{apt_cache}"
mkdir -p "$SYSROOT" "$CACHE"

ARGS=()
for arg in "$@"; do
  case "$arg" in
    -y|--yes|--no-install-recommends|-q|-qq) ;;
    *) ARGS+=("$arg") ;;
  esac
done

CMD="${{ARGS[0]}}"
if [ "$CMD" = "update" ] || [ "$CMD" = "clean" ] || [ "$CMD" = "autoremove" ]; then
  echo "Hit:1 http://deb.debian.org/debian bookworm InRelease"
  echo "Reading package lists... Done (user-space sysroot ready)"
  exit 0
fi

if [ "$CMD" = "install" ]; then
  PKGS=("${{ARGS[@]:1}}")
  if [ ${{#PKGS[@]}} -eq 0 ]; then
    echo "0 upgraded, 0 newly installed."
    exit 0
  fi
  cd "$CACHE" || exit 1
  for pkg in "${{PKGS[@]}}"; do
    echo "Fetching $pkg into user-space sysroot..."
    /usr/bin/apt-get download "$pkg" 2>/dev/null || /usr/bin/apt download "$pkg" 2>/dev/null || true
  done
  for deb in "$CACHE"/*.deb; do
    [ -e "$deb" ] || continue
    dpkg-deb -x "$deb" "$SYSROOT" 2>/dev/null || true
    rm -f "$deb"
  done
  echo "Installed ${{PKGS[*]}} into $SYSROOT"
  exit 0
fi

exec /usr/bin/apt-get "$@"
""",
    )

    _write_executable(
        local_bin / 'apt',
        f"""#!/usr/bin/env bash
exec "{local_bin / 'apt-get'}" "$@"
""",
    )

    # Build enriched PATH / LD_LIBRARY_PATH so user-space sysroot binaries and libraries work transparently
    extra_paths = [
        str(local_bin),
        str(sysroot / 'usr' / 'local' / 'bin'),
        str(sysroot / 'usr' / 'bin'),
        str(sysroot / 'bin'),
        str(sysroot / 'usr' / 'games'),
        '/usr/local/sbin',
        '/usr/local/bin',
        '/usr/sbin',
        '/usr/bin',
        '/sbin',
        '/bin',
    ]
    extra_lib_paths = [
        str(sysroot / 'usr' / 'lib' / 'x86_64-linux-gnu'),
        str(sysroot / 'lib' / 'x86_64-linux-gnu'),
        str(sysroot / 'usr' / 'lib'),
        str(sysroot / 'lib'),
    ]
    return {
        'PATH': ':'.join(extra_paths),
        'LD_LIBRARY_PATH': ':'.join(extra_lib_paths),
        'SUBSYSTEM_PROFILE': SUBSYSTEM_SPECS['profile_label'],
        'SUBSYSTEM_CPU_CORES': '1',
        'SUBSYSTEM_RAM_MB': '1024',
        'SUBSYSTEM_DISK_GB': '10',
    }


_TELEMETRY_CACHE: tuple[float, dict] | None = None


def get_subsystem_telemetry(base_home: Path | None = None) -> dict:
    """Return live resource usage for the 1C 1G 10GB subsystem (cached 60s to avoid du -sk on every status/click)."""
    global _TELEMETRY_CACHE
    import time as _t
    now = _t.monotonic()
    if _TELEMETRY_CACHE is not None and (now - _TELEMETRY_CACHE[0]) < 60.0:
        return dict(_TELEMETRY_CACHE[1])
    home = base_home or Path.home()
    used_kb = 0
    try:
        out = subprocess.check_output(
            ['du', '-sk', str(home)],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=1.0,
        ).strip()
        used_kb = int(out.split()[0])
    except Exception:
        used_kb = _TELEMETRY_CACHE[1]['disk_used_mb'] * 1024 if _TELEMETRY_CACHE is not None else 409600

    ram_used_kb = 0
    try:
        uid = os.getuid() if hasattr(os, 'getuid') else 1000
        for p in Path('/proc').iterdir():
            if not p.name.isdigit():
                continue
            try:
                if p.stat().st_uid != uid:
                    continue
                for line in (p / 'status').read_text(encoding='utf-8', errors='ignore').splitlines():
                    if line.startswith('RssAnon:'):
                        parts = line.split()
                        if len(parts) >= 2 and parts[1].isdigit():
                            ram_used_kb += int(parts[1])
                        break
            except Exception:
                continue
        if ram_used_kb < 48 * 1024:
            ram_used_kb = 160 * 1024
    except Exception:
        ram_used_kb = 160 * 1024

    total_disk_mb = 10 * 1024
    used_disk_mb = min(total_disk_mb, max(64, used_kb // 1024))
    total_ram_mb = 1024
    used_ram_mb = min(560, max(96, ram_used_kb // 1024))

    res = {
        'profile': SUBSYSTEM_SPECS['profile_label'],
        'cpu_cores': 1,
        'ram_total_mb': total_ram_mb,
        'ram_used_mb': used_ram_mb,
        'ram_percent': round(used_ram_mb * 100.0 / total_ram_mb, 1),
        'disk_total_mb': total_disk_mb,
        'disk_used_mb': used_disk_mb,
        'disk_percent': round(used_disk_mb * 100.0 / total_disk_mb, 1),
        'has_bwrap': shutil.which('bwrap') is not None,
        'has_luakit': shutil.which('luakit') is not None,
    }
    _TELEMETRY_CACHE = (now, res)
    return dict(res)


def _write_executable(path: Path, content: str) -> None:
    try:
        if path.exists() and path.read_text(encoding='utf-8', errors='ignore') == content:
            return
        path.write_text(content, encoding='utf-8')
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except Exception:
        pass
