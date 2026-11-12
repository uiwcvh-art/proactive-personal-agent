# DeepSeek Claw Computer Use (`ds-claw-cua`)

Zero-dependency Windows desktop & browser automation MCP server (`ds-claw-cua`), featuring:
- **240Hz Silky-Smooth Virtual Cursor & Zero Hardware Mouse Lag**: Binary-patched to eliminate `WH_MOUSE_LL` global mouse hooks and system cursor suppression while upgrading virtual cursor trajectory sampling from 60Hz to 240Hz.
- **Dynamic Bot Identity & Theme Colors**: Displays **`「主助理 正在操控你的电脑」`** (Sakura Pink `#f38ba8`) or **`「鲸鱼娘 正在操控你的电脑」`** (Ocean Neon Blue `#4da6ff`) via both the native DirectComposition border glow/status pill and a zero-flicker, click-through topmost per-pixel alpha HUD overlay (`hud-banner.ps1`).
- **Separation of Duties**: PC Bot controls the Windows PC (`cua_desktop` / `ds-claw-cua`), while Phone Bot controls the Android phone, collaborating via colleague messaging (`message_phone_bot`).

## Layout

```
cua/
├── index.mjs                  # MCP stdio server (ds-claw-cua)
├── hud-banner.ps1             # Click-through topmost per-pixel alpha HUD banner
├── agent.mjs                  # Standalone agent loop for testing
└── bin/
    └── codex-computer-use.exe # Patched 240Hz native UIA + WGC + DirectComposition engine
```

## Tools (15)

| Tool | Purpose |
|---|---|
| `set_active_bot` | Dynamically switch active Bot identity (`主助理` / `鲸鱼娘`) and accent color |
| `list_windows` | All open windows with numeric `window_id` |
| `list_apps` | Installed apps + open windows |
| `get_window` | Window metadata |
| `launch_app` | Start app by id or `.exe` path |
| `get_window_state` | Compact UIA tree with clickable `element_index` numbers + optional screenshot |
| `click` | Click by `element_index` (preferred) or `(x, y)` |
| `type_text` | Type text (`element_index`, `clear`, `submit` optional) |
| `set_value` | Set input value by `element_index` with automatic fallback |
| `press_key` | Keysym or `+` chord (`Return`, `Control_L+a`, `Alt_L+F4`, …) |
| `scroll` / `scroll_element` | Coordinate or page scroll |
| `drag` | Drag `(from_x, from_y)` → `(to_x, to_y)` |
| `perform_secondary_action` | Invoke UIA action (`Raise`, `Expand`, `Collapse`, …) |
| `wait_for` | Poll window UIA tree until regex matches (or disappears) |
| `activate_window` / `reset_engine` | Foreground window / restart engine |
