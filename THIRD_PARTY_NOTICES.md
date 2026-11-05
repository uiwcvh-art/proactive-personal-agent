# Third-party components and UI references

The Android app bundles marked 18.0.13 (MIT) and DOMPurify 3.4.15 (Apache-2.0 OR MPL-2.0). Their original license files are included in `android/app/src/main/assets/vendor/`.

The sanitized Markdown rendering pattern in `app.js` is adapted from OpenBot's `frontend/src/components/MarkdownContent.tsx`, revision `0bda7e31fcfb25e22446d6225b08325b7753157e`, Copyright (c) 2026 regnull, MIT. The complete original MIT license is bundled as `vendor/OpenBot-LICENSE.txt`.

UI and navigation references:

- Grok Bot's official Android store screenshots and mobile documentation: https://play.google.com/store/apps/details?id=ai.x.grok.bot and https://docs.x.ai/grok-bot/mobile
- Rakazo mobile assistant profile, settings and routine navigation, revision `91aeb75a885afe48900068eb6288065fb3d13dc6`: https://github.com/elie222/rakazo (Apache-2.0). Studied as an open-source implementation reference; no Rakazo runtime or backend is bundled.
- RikkaHub 3.1.1: https://github.com/rikkahub/rikkahub (AGPL-3.0). Its distributed app was operated in an isolated emulator to study chat, menu and settings interaction. No RikkaHub source is bundled in this project.

Pulse Bot is an independent personal-assistant client and is not an official Grok Bot client. The third-party reference APKs and store screenshots are research evidence only, not redistributed inside the app.

## Mika tool references and selected source

Pulse 1.7 uses its own Application, Activities, preferences, Root task harness and model loop. It does not include Mika's app modules, navigation, terminal, PRoot, Alpine or model configuration screens. Downloaded reference modules remain outside the build.

Selected Markdown renderer/parser source was adapted into `dev.bubuki.proactive.markdown`. Root frame geometry, trusted independent-focus flags, display-local IME policy and clipboard Binder access reference Mika, Copyright (c) 2026 Mika authors, Apache-2.0: https://github.com/wangzan101/Mika, pinned to `e9daf53d6834b236e713c6a62c3a095338eb7f95` (reference APK v0.1.33). The original license is bundled in `assets/mika-LICENSE.txt`; adapted files carry attribution. Changes include our namespace/theme/link routing, Root-only authenticated IPC, explicit owned display validation, task protection, bounded tools, shared configuration and encrypted execution history. Clipboard-only child access drops to the real shell-package uid after Root startup; coordinate injection stays in the uid 0 parent. This adapts Android's package/uid clipboard ownership checks without a non-Root authorization fallback. Source is supplied in `proactive-personal-agent-source.zip`.

The earlier 1.6 APK included Mika/native subprocesses. Its corresponding native source remains separately available as `mika-corresponding-source.tar.gz` for that historical release; these binaries are absent from 1.7.

## Cloud computer

The separately installed cloud engine uses Browser Use 0.13.14 (MIT), https://github.com/browser-use/browser-use, and Playwright 1.56.0 (Apache-2.0), https://github.com/microsoft/playwright. They run in this project's dedicated Python environment; their original distribution metadata and license files are retained by pip. Chromium is downloaded from Playwright's official browser distribution into this project's private runtime. No existing browser installation or profile is bundled or reconfigured.
