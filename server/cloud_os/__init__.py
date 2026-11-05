"""
Domain 2: Cloud OS & Virtual Computer Subsystem (`server/cloud_os`).

Provides:
- `subsystem`: 1C 1G 10GB Isolated Linux Subsystem (quota shims, passwordless sudo, user-space apt-get, writable /root)
- `tmux_terminal`: Single persistent tmux terminal session with zero X11 window leak
- `window_manager`: EWMH X11 window stack telemetry, focus/close/tile, clipboard, and visual action crosshair
- `agent_browser`: Modern JS browser (Luakit WebKit2GTK / Low-RAM Chromium CDP) with @b1..@bN DOM extraction
"""
