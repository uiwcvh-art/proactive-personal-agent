/**
 * [Module Responsibility]
 * DSB 桌面端伴侣与双栏全屏工作台前端核心模块 (`client.js`)。
 * 仿照手机端 DSB (`NativePulseScreen` / `PulseScreenChatItems` / `PulseScreenComposer`) 视觉与交互体系重构：
 * 1. [Rich Markdown, Code & LaTeX Renderer]: 富文本代码块语法着色、LaTeX 公式、安全内联图片与灯箱预览。
 * 2. [Vector Icons, Bot Avatars & Tool Labels]: 全套内联 SVG 矢量图标（零 Emoji 界面杂讯）、11 种灵体形状头像与工具语义摘要。
 * 3. [Theme & Dual-Layout Engine]: 支持「右侧伴侣分栏」与「沉浸双栏全屏工作台」一键切换，内置日间/夜间双色板与现代 CSS 手风琴动画。
 * 4. [Strict 1-on-1 Multi-Bot Stream]: 各守各房、思维链严格隔离；单行工具胶囊与独立思考折叠条分离，支持引用回复、拖拽/粘贴传图、置顶记忆栏。
 * 5. [Bot Studio & Memory Vault Sheets]: 抽屉式新建/编辑助理工坊（彻底告别原生弹窗）与记忆金库/钉住记忆管理器。
 *
 * [Public Interface]
 * - `window.__ModuleLoader__.load({ id: "dsh-deepseek-claw", factory })` -> `{ name, apply(ctx) }`
 */
window.__ModuleLoader__.load({
  id: "dsh-deepseek-claw",
  factory: function (require) {
    "use strict";
    var cordisClient = null;
    try { cordisClient = require("@cordisjs/client"); } catch (e) {}

    var DEFAULT_BOTS = [
      { id: "pc_engineer", name: "司工", color: "#0A84FF", shape: "square", primary: true, kind: "engineer", description: "电脑端主控总工程师，代码架构、Git Bash 与云电脑操控" },
      { id: "pc_research", name: "探微", color: "#3478F6", shape: "circle", primary: false, kind: "research", description: "深度技术调研、文献考据与源码审计专家" },
      { id: "pc_reviewer", name: "审校", color: "#FF8A3D", shape: "diamond", primary: false, kind: "reviewer", description: "独立代码审查、漏洞审视与方案验收" },
      { id: "pc_companion", name: "云汐", color: "#2EC4B6", shape: "flower", primary: false, kind: "companion", description: "轻快灵感陪伴、文稿润色与日常协理" }
    ];
    var PRESET_COLORS = ["#0A84FF", "#B568F7", "#2EC4B6", "#3478F6", "#FF8A3D", "#30D158", "#FF453A", "#5E5CE6", "#FF375F"];
    var SHAPE_OPTIONS = [
      { id: "flower", label: "花朵" }, { id: "circle", label: "正圆" }, { id: "oval", label: "椭圆" },
      { id: "square", label: "方角" }, { id: "pill", label: "胶囊" }, { id: "triangle", label: "三角" },
      { id: "diamond", label: "菱形" }, { id: "star", label: "星芒" }, { id: "drop", label: "水滴" },
      { id: "whale", label: "鲸跃" }, { id: "whalechan", label: "鲸娘" }
    ];
    var SLASH_COMMANDS = [
      { cmd: "/goal ", desc: "启动引擎级持久目标模式，自动推进直至完成" },
      { cmd: "/compact", desc: "立即浓缩归档当前对话历史，释放上下文空间" },
      { cmd: "/pin ", desc: "将关键偏好或规则钉入永久置顶记忆" },
      { cmd: "/pins", desc: "查看所有已钉住的跨端核心记忆" },
      { cmd: "/status", desc: "查看当前中枢、节点在线率与上下文指标" },
      { cmd: "/doctor", desc: "运行全链路通道健康自检" },
      { cmd: "/undo", desc: "撤回上一轮对话记录" }
    ];
    var savedPaneW = 420, savedTheme = "dark", savedFullMode = false;
    try {
      var rawW = Number(window.localStorage && window.localStorage.getItem("dsb_pane_width"));
      if (rawW >= 360 && rawW <= 680) savedPaneW = Math.round(rawW);
      var rawT = window.localStorage && window.localStorage.getItem("dsb_theme");
      if (rawT === "light" || rawT === "dark") savedTheme = rawT;
      savedFullMode = (window.localStorage && window.localStorage.getItem("dsb_full_workspace")) === "1";
    } catch (e) {}
    var state = {
      bots: DEFAULT_BOTS.slice(), activeBotId: "pc_engineer", viewActive: false, fullWorkspace: savedFullMode,
      theme: savedTheme, paneWidth: savedPaneW, botSearch: "", botFilter: "all", botSwitcherOpen: false,
      pinBarExpanded: false, messages: [], messagesEpoch: 1, streamsByBot: {}, sendingByBot: {}, botContextById: {},
      vpsOnline: true, rttMs: 0, phoneOnline: false, editingMsgId: null, editingText: "", replyingToMsg: null,
      pendingAttachments: [], uploadingCount: 0, slashMenuOpen: false, lightboxUrl: "", toastMsg: "",
      studioOpen: false, studioMode: "edit", editBotName: "", editBotDesc: "", editBotColor: "#0A84FF",
      editBotShape: "square", editBotPersona: "", editBotModel: "", editBotCuriosity: false, availableModels: [],
      vaultOpen: false, vaultTab: "notes", vaultCategory: "all", vaultQuery: "", vaultFiles: [], vaultPinned: [],
      vaultSelectedPath: "", vaultSelectedContent: "", vaultLoading: false, vaultStatusMsg: "",
      compactBusy: false, whaleAvatarUrl: "", lastStreamSig: "", lastLiveSig: "", isComposing: false,
      lastCompositionEndAt: 0, recordingVoice: false, transcribingVoice: false, voiceStatusHint: ""
    };
    var voiceRecorder = { stream: null, audioCtx: null, source: null, processor: null, chunks: [] };
    var toastTimer = null;

    // === [1. Stateless Render Library Bridge (Loaded from assets/dsb-render.js)] ===
    function R() { return window.__DSB_RENDER__ || null; }
    function escapeHtml(s) {
      return R() ? R().escapeHtml(s) : String(s === null || s === undefined ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    }
    function iconSvg(name, sz, cls) { return R() ? R().iconSvg(name, sz, cls) : ""; }
    function resolveChatMediaUrl(u) { return R() ? R().resolveChatMediaUrl(u) : String(u || ""); }
    function renderRichMarkdown(t) { return R() ? R().renderRichMarkdown(t) : escapeHtml(t); }
    function renderBotAvatarSvg(bot, sz) {
      if (R()) return R().renderBotAvatarSvg(bot, sz, state.whaleAvatarUrl);
      var s = (sz || 22) + "px", c = escapeHtml((bot && bot.color) || "#B568F7");
      return '<span style="width:' + s + ";height:" + s + ";border-radius:50%;background:" + c + ';display:inline-block;flex-shrink:0;"></span>';
    }
    function formatCompactTokens(n) { return R() ? R().formatCompactTokens(n) : String(n || 0); }
    function friendlyToolLabel(n, a) { return R() ? R().friendlyToolLabel(n, a) : String(n || ""); }
    function renderToolCapsuleHtml(tr, run, lbl) { return R() ? R().renderToolCapsuleHtml(tr, run, lbl) : ""; }
    function renderThoughtPillHtml(th, calls, stream) { return R() ? R().renderThoughtPillHtml(th, calls, stream) : ""; }
    function getActiveBot() {
      for (var i = 0; i < state.bots.length; i++) { if (state.bots[i].id === state.activeBotId) return state.bots[i]; }
      return state.bots[0] || DEFAULT_BOTS[0];
    }

    // === [4. Style Sheet Loader (Loaded from assets/dsb-ui.css via /local/ui-assets)] ===
    function injectStyles(fullCss) {
      var el = document.getElementById("dsb-companion-styles");
      if (!el) {
        el = document.createElement("style");
        el.id = "dsb-companion-styles";
        el.textContent = '#dsb-split-pane{display:none;position:fixed;top:var(--dsb-top-offset,38px);right:0;bottom:0;width:var(--dsb-pane-w,420px);z-index:2147483000;background:#090F17;color:#F1F5F9;}#dsb-split-pane.is-open{display:flex;}#dsb-sidebar-dock{display:flex;flex-direction:column;gap:2px;padding:6px 8px 4px;margin:2px 0;}.dsb-dock-head{display:flex;align-items:center;justify-content:space-between;padding:2px 8px 3px;font-size:11px;font-weight:600;color:rgba(161,161,170,0.78);}.dsb-dock-add{width:20px;height:20px;border:none;background:transparent;color:inherit;cursor:pointer;}.dsb-nav-bot-row{position:relative;display:flex;align-items:center;gap:9px;width:100%;padding:6px 10px;border-radius:8px;border:none;background:transparent;color:inherit;font-size:13px;text-align:left;cursor:pointer;}.dsb-nav-bot-row:hover{background:rgba(255,255,255,0.06);}.dsb-nav-bot-row[aria-current="page"]{background:rgba(10,132,255,0.15);color:#F8FAFC;font-weight:600;}.dsb-nav-bot-row[aria-current="page"]::before{content:"";position:absolute;left:2px;top:7px;bottom:7px;width:3px;border-radius:99px;background:#38BDF8;}.dsb-nav-bot-name{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}#dsb-floating-trigger{position:fixed;right:18px;bottom:18px;height:36px;padding:0 13px;border-radius:999px;border:1px solid rgba(148,163,184,0.22);background:#17212B;color:#F1F5F9;font-size:12.5px;font-weight:600;display:none;align-items:center;gap:7px;cursor:pointer;z-index:2147482900;}';
        document.head.appendChild(el);
      }
      if (fullCss && el.getAttribute("data-loaded") !== "1") {
        el.textContent = fullCss;
        el.setAttribute("data-loaded", "1");
      }
    }

    // === [5. Local Gateway API & File Upload Helpers] ===
    function showToast(msg) {
      state.toastMsg = String(msg || "");
      var el = document.getElementById("dsb-toast-el");
      if (el) { el.textContent = state.toastMsg; el.style.display = state.toastMsg ? "block" : "none"; }
      if (toastTimer) clearTimeout(toastTimer);
      toastTimer = setTimeout(function () {
        state.toastMsg = "";
        var el2 = document.getElementById("dsb-toast-el");
        if (el2) el2.style.display = "none";
      }, 2400);
    }
    function apiCall(localPath, method, body) {
      var payload = JSON.stringify({ localPath: localPath, cloudPath: "", method: method || "POST", body: body || {} });
      return fetch("/api/dsh-deepseek-claw", { method: "POST", headers: { "Content-Type": "application/json" }, body: payload })
        .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error("HTTP " + r.status)); })
        .catch(function () {
          return fetch("http://127.0.0.1:18195/api/dsh-deepseek-claw", { method: "POST", headers: { "Content-Type": "application/json" }, body: payload })
            .then(function (r2) { return r2.json(); });
        })
        .catch(function (err) { return { ok: false, error: String((err && err.message) || err) }; });
    }
    function uploadFileObject(file) {
      if (!file) return;
      if (file.size > 25 * 1024 * 1024) { showToast("单个附件不能超过 25MB"); return; }
      var tempItem = { name: file.name || ("paste-" + Date.now() + ".png"), path: "", isImage: /^image\//i.test(file.type || ""), uploading: true };
      state.pendingAttachments.push(tempItem);
      state.uploadingCount++;
      renderComposerBanners();
      var reader = new FileReader();
      reader.onload = function () {
        var resStr = String(reader.result || "");
        var b64 = resStr.indexOf(",") !== -1 ? resStr.split(",")[1] : resStr;
        apiCall("/local/upload", "POST", { name: tempItem.name, data_b64: b64 }).then(function (res) {
          state.uploadingCount = Math.max(0, state.uploadingCount - 1);
          if (res && res.ok && res.path) {
            tempItem.path = res.path;
            tempItem.uploading = false;
            showToast("已附加 " + tempItem.name);
          } else {
            var idx = state.pendingAttachments.indexOf(tempItem);
            if (idx !== -1) state.pendingAttachments.splice(idx, 1);
            showToast("上传失败: " + ((res && res.error) || "网络异常"));
          }
          renderComposerBanners();
        });
      };
      reader.readAsDataURL(file);
    }

    // === [6. Voice Dictation (16kHz Mono PCM WAV -> SenseVoice)] ===
    function encodeMonoWav(samples, sampleRate) {
      var buffer = new ArrayBuffer(44 + samples.length * 2), view = new DataView(buffer);
      function wStr(off, s) { for (var i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i)); }
      wStr(0, "RIFF"); view.setUint32(4, 36 + samples.length * 2, true); wStr(8, "WAVE"); wStr(12, "fmt ");
      view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
      view.setUint32(24, sampleRate, true); view.setUint32(28, sampleRate * 2, true);
      view.setUint16(32, 2, true); view.setUint16(34, 16, true); wStr(36, "data"); view.setUint32(40, samples.length * 2, true);
      for (var i = 0; i < samples.length; i++) {
        var s = Math.max(-1, Math.min(1, samples[i]));
        view.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
      }
      var bytes = new Uint8Array(buffer), bin = "";
      for (var j = 0; j < bytes.length; j++) bin += String.fromCharCode(bytes[j]);
      return window.btoa(bin);
    }
    function toggleVoiceDictation() {
      if (state.transcribingVoice) return;
      if (state.recordingVoice) {
        state.recordingVoice = false;
        state.transcribingVoice = true;
        updateComposerButtons();
        var chunks = voiceRecorder.chunks.slice();
        var sr = (voiceRecorder.audioCtx && voiceRecorder.audioCtx.sampleRate) || 16000;
        try { if (voiceRecorder.processor) voiceRecorder.processor.disconnect(); } catch (e) {}
        try { if (voiceRecorder.source) voiceRecorder.source.disconnect(); } catch (e) {}
        try { if (voiceRecorder.stream) voiceRecorder.stream.getTracks().forEach(function (t) { t.stop(); }); } catch (e) {}
        try { if (voiceRecorder.audioCtx) voiceRecorder.audioCtx.close(); } catch (e) {}
        voiceRecorder = { stream: null, audioCtx: null, source: null, processor: null, chunks: [] };
        var totalLen = chunks.reduce(function (a, c) { return a + c.length; }, 0);
        if (totalLen < 800) { state.transcribingVoice = false; updateComposerButtons(); return; }
        var merged = new Float32Array(totalLen), off = 0;
        for (var i = 0; i < chunks.length; i++) { merged.set(chunks[i], off); off += chunks[i].length; }
        apiCall("/local/speech-to-text", "POST", { audio_b64: encodeMonoWav(merged, sr), language: "auto" }).then(function (res) {
          state.transcribingVoice = false;
          if (res && res.ok && res.text) {
            var ta = document.getElementById("dsb-chat-input");
            if (ta) { ta.value = (ta.value ? ta.value + " " : "") + res.text; autoFitTextarea(ta); ta.focus(); }
          } else {
            showToast((res && res.error) || "未识别到语音");
          }
          updateComposerButtons();
        });
        return;
      }
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) { showToast("当前环境不支持麦克风录音"); return; }
      navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
        var AC = window.AudioContext || window.webkitAudioContext;
        var audioCtx = new AC({ sampleRate: 16000 });
        var source = audioCtx.createMediaStreamSource(stream);
        var proc = audioCtx.createScriptProcessor(4096, 1, 1);
        voiceRecorder = { stream: stream, audioCtx: audioCtx, source: source, processor: proc, chunks: [] };
        proc.onaudioprocess = function (ev) { if (state.recordingVoice) voiceRecorder.chunks.push(new Float32Array(ev.inputBuffer.getChannelData(0))); };
        source.connect(proc); proc.connect(audioCtx.destination);
        state.recordingVoice = true;
        showToast("正在聆听，再次点击麦克风完成转写");
        updateComposerButtons();
      }).catch(function () { showToast("无法访问麦克风权限"); });
    }

    // === [7. Strict Room Filtering & Message Parsing] ===
    function getRoomMessages(botId) {
      var target = botId || state.activeBotId;
      var out = [];
      for (var i = 0; i < state.messages.length; i++) {
        var m = state.messages[i];
        if (!m || m.bot_id !== target) continue;
        // Strict 1-on-1 Room Isolation: never display outbound cross-bot bubbles or foreign bot_reply in caller's room
        if (m.kind === "bot_message" && m.direction === "outbound") continue;
        if (m.role === "assistant" && m.from_bot_id && m.from_bot_id !== target && m.kind !== "bot_message") continue;
        out.push(m);
      }
      return out;
    }
    function parseUserQuoteAndAttachments(rawText) {
      var s = String(rawText || "");
      var quote = "";
      if (s.indexOf("> ") === 0 && s.indexOf("\n\n") !== -1) {
        var splitAt = s.indexOf("\n\n");
        quote = s.slice(0, splitAt).split("\n").map(function (l) { return l.replace(/^>\s?/, "").trim(); }).join(" ");
        s = s.slice(splitAt + 2);
      }
      var marker = "\n\n附件（工作区相对路径";
      var files = [];
      var mIdx = s.indexOf(marker);
      if (mIdx !== -1) {
        var tail = s.slice(mIdx + marker.length);
        var colonIdx = tail.indexOf("：\n");
        if (colonIdx !== -1) {
          files = tail.slice(colonIdx + 2).split("\n").map(function (p) { return p.trim(); }).filter(Boolean);
        }
        s = s.slice(0, mIdx).trim();
      }
      if (s === "请查看这些附件。" && files.length) s = "";
      return { quote: quote, body: s, files: files };
    }

    // === [8. DOM Construction, Native Sidebar Dock & Incremental Rendering] ===
    function findGlobalSidebarNav() {
      var direct = document.querySelector('nav[aria-label="全局面板"], nav[aria-label="Global panels"], aside nav, [class*="Sidebar"] nav');
      if (direct && !direct.closest("#dsb-split-pane")) return direct;
      var navs = document.querySelectorAll("nav");
      for (var i = 0; i < navs.length; i++) {
        if (navs[i].closest("#dsb-split-pane")) continue;
        var r = navs[i].getBoundingClientRect();
        if (r.left <= 72 && r.width >= 96 && r.width <= 380 && r.height >= 44) return navs[i];
      }
      return null;
    }
    function renderSidebarDock() {
      var globalNav = findGlobalSidebarNav();
      var navVisible = Boolean(globalNav && globalNav.getBoundingClientRect().width >= 72);
      var floatBtn = document.getElementById("dsb-floating-trigger");
      if (!floatBtn && document.body) {
        floatBtn = document.createElement("button");
        floatBtn.id = "dsb-floating-trigger"; floatBtn.type = "button";
        floatBtn.innerHTML = (iconSvg("sparkles", 15) || "✦") + "<span>智能助理</span>";
        floatBtn.addEventListener("click", function () {
          state.viewActive = true; applyLayoutClasses(); syncNow(true); renderChatStream(true);
        });
        document.body.appendChild(floatBtn);
      }
      if (floatBtn) floatBtn.style.display = (!state.viewActive && !navVisible) ? "inline-flex" : "none";
      if (!navVisible) return;
      var dock = document.getElementById("dsb-sidebar-dock");
      if (!dock) {
        dock = document.createElement("div");
        dock.id = "dsb-sidebar-dock"; dock.setAttribute("role", "region"); dock.setAttribute("aria-label", "智能助理");
        dock.addEventListener("click", function (ev) {
          var addBtn = ev.target.closest(".dsb-dock-add");
          if (addBtn) { state.viewActive = true; applyLayoutClasses(); openBotStudio("create"); syncNow(true); return; }
          var rowBtn = ev.target.closest("[data-dock-bot]");
          if (!rowBtn) return;
          var bid = rowBtn.getAttribute("data-dock-bot");
          if (state.viewActive && state.activeBotId === bid) { state.viewActive = false; applyLayoutClasses(); return; }
          state.viewActive = true; selectBot(bid); applyLayoutClasses(); syncNow(true);
          var ta = document.getElementById("dsb-chat-input"); if (ta) ta.focus();
        });
      }
      if (dock.previousElementSibling !== globalNav) globalNav.insertAdjacentElement("afterend", dock);
      var sig = (R() ? "1" : "0") + "|" + state.viewActive + "|" + state.activeBotId + "|" + state.whaleAvatarUrl + "|" +
        state.bots.map(function (b) { return [b.id, b.name, b.color, b.shape, (state.streamsByBot[b.id] || state.sendingByBot[b.id]) ? 1 : 0].join(":"); }).join(",");
      if (dock.getAttribute("data-sig") === sig) return;
      dock.setAttribute("data-sig", sig);
      dock.innerHTML = '<div class="dsb-dock-head"><span>智能助理</span><button type="button" class="dsb-dock-add" title="新建专属助理" aria-label="新建专属助理">' + (iconSvg("plus", 14) || "+") + "</button></div>" +
        state.bots.map(function (b) {
          var isCur = Boolean(state.viewActive && state.activeBotId === b.id);
          var isBusy = Boolean(state.streamsByBot[b.id] || state.sendingByBot[b.id]);
          return '<button type="button" class="dsb-nav-bot-row"' + (isCur ? ' aria-current="page"' : "") + ' data-dock-bot="' + escapeHtml(b.id) + '">' +
            renderBotAvatarSvg(b, 20) + '<span class="dsb-nav-bot-name">' + escapeHtml(b.name) + "</span>" +
            (isBusy ? '<span class="dsb-nav-live-pill">思考中</span>' : "") + "</button>";
        }).join("");
    }
    function applyLayoutClasses() {
      var pane = document.getElementById("dsb-split-pane"), mainEl = document.querySelector("main");
      var topOff = 38;
      if (mainEl) {
        var mr = mainEl.getBoundingClientRect();
        topOff = (mr.top >= 24 && mr.top <= 68) ? Math.round(mr.top) : (mr.top < 12 ? 0 : 38);
        mainEl.style.marginRight = (state.viewActive && !state.fullWorkspace) ? (state.paneWidth + "px") : "";
      }
      document.documentElement.style.setProperty("--dsb-top-offset", topOff + "px");
      document.documentElement.style.setProperty("--dsb-pane-w", state.paneWidth + "px");
      document.documentElement.style.setProperty("--dsb-host-rail-w", "0px");
      if (pane) {
        pane.setAttribute("data-theme", state.theme);
        pane.classList.toggle("is-open", state.viewActive);
        pane.classList.toggle("is-full", Boolean(state.viewActive && state.fullWorkspace));
      }
      document.body.classList.toggle("dsb-pane-open", Boolean(state.viewActive && !state.fullWorkspace));
      document.body.classList.toggle("dsb-pane-full", Boolean(state.viewActive && state.fullWorkspace));
      renderSidebarDock();
    }
    function autoFitTextarea(ta) {
      if (!ta) return;
      if (!(window.CSS && CSS.supports && CSS.supports("field-sizing", "content"))) {
        ta.style.height = "auto";
        ta.style.height = Math.min(148, Math.max(22, ta.scrollHeight)) + "px";
      }
    }
    function renderHomeRail() {
      var listEl = document.getElementById("dsb-contact-list");
      var connEl = document.getElementById("dsb-conn-pill");
      if (connEl) {
        var rttTxt = state.vpsOnline ? (state.rttMs > 0 ? state.rttMs + "ms" : "已连接") : "重连中";
        connEl.innerHTML = '<span class="dsb-conn-dot' + (state.vpsOnline ? "" : " is-off") + '"></span><span>' + escapeHtml(rttTxt + (state.phoneOnline ? " · 手机在线" : "")) + "</span>";
      }
      var fBtns = document.querySelectorAll("[data-bot-filter]");
      for (var f = 0; f < fBtns.length; f++) {
        fBtns[f].classList.toggle("is-active", fBtns[f].getAttribute("data-bot-filter") === state.botFilter);
      }
      if (!listEl) return;
      var q = state.botSearch.trim().toLowerCase();
      var html = state.bots.filter(function (b) {
        var isRun = Boolean(state.streamsByBot[b.id] || state.sendingByBot[b.id]);
        if (state.botFilter === "running" && !isRun) return false;
        if (state.botFilter === "pinned" && !(b.pinned || b.id === "companion")) return false;
        if (q && String(b.name || "").toLowerCase().indexOf(q) === -1 && String(b.description || "").toLowerCase().indexOf(q) === -1) return false;
        return true;
      }).map(function (b) {
        var roomMsgs = getRoomMessages(b.id);
        var lastM = roomMsgs.length ? roomMsgs[roomMsgs.length - 1] : null;
        var liveSt = state.streamsByBot[b.id];
        var isRun = Boolean(liveSt || state.sendingByBot[b.id]);
        var sub = isRun
          ? (liveSt && liveSt.tool ? "正在执行 · " + friendlyToolLabel(liveSt.tool, {}) : "正在思考…")
          : (lastM ? String(lastM.text || "").replace(/\s+/g, " ").slice(0, 42) : (b.description || "点击开始独立对话"));
        var timeStr = lastM && lastM.time ? String(lastM.time).slice(0, 5) : "";
        return '<button type="button" class="dsb-contact-item' + (b.id === state.activeBotId ? " is-active" : "") + '" data-select-bot="' + escapeHtml(b.id) + '">' +
          renderBotAvatarSvg(b, 38) +
          '<div class="dsb-contact-body"><div class="dsb-contact-r1"><span class="dsb-contact-name">' + escapeHtml(b.name) +
          (b.id === "companion" ? '<span class="dsb-role-badge">主控</span>' : "") +
          '</span><span class="dsb-contact-time">' + escapeHtml(timeStr) + '</span></div>' +
          '<div class="dsb-contact-sub' + (isRun ? " is-live" : "") + '">' + escapeHtml(sub) + "</div></div></button>";
      }).join("");
      listEl.innerHTML = html || '<div style="padding:24px 12px;text-align:center;color:var(--dsb-faint);font-size:12px;">暂无匹配的助理</div>';
    }
    function renderHeaderAndPins() {
      var bot = getActiveBot();
      var ctx = state.botContextById[bot.id] || {};
      var liveSt = state.streamsByBot[bot.id];
      var isBusy = Boolean(liveSt || state.sendingByBot[bot.id]);
      var curTok = Number(ctx.current_tokens || 0);
      var maxTok = Math.max(1, Number(ctx.threshold_tokens || 200000));
      var hitPct = Math.round(Number(ctx.cache_hit_ratio || 0) * 100);
      var tps = Number(ctx.tps || 0);
      var modelLabel = (bot.model || "默认模型").split("/").pop();
      var subText = isBusy
        ? (liveSt && liveSt.tool ? "正在执行 · " + friendlyToolLabel(liveSt.tool, {}) : "正在思考响应…")
        : (modelLabel + (curTok > 0 ? " · " + formatCompactTokens(curTok) + (hitPct > 0 ? " (缓存" + hitPct + "%)" : "") : "") + (tps > 0 ? " · " + tps.toFixed(0) + "t/s" : "") + (!state.vpsOnline ? " · 离线重连中" : ""));

      var trigEl = document.getElementById("dsb-bot-trigger");
      if (trigEl) {
        var dots = state.bots.map(function (b) {
          return '<span class="dsb-dot-ind' + (b.id === bot.id ? " is-cur" : "") + '"></span>';
        }).join("");
        trigEl.innerHTML = renderBotAvatarSvg(bot, 32) +
          '<div class="dsb-head-meta"><div class="dsb-head-title-row"><span class="dsb-head-name">' + escapeHtml(bot.name) + "</span>" +
          (bot.id === "companion" ? '<span class="dsb-role-badge">主控</span>' : "") +
          '<span class="dsb-dots-row">' + dots + '</span></div><div class="dsb-head-sub' + (isBusy ? " is-live" : "") + '">' + escapeHtml(subText) + "</div></div>";
      }
      var fillEl = document.getElementById("dsb-ctx-fill");
      if (fillEl) fillEl.style.width = Math.min(100, Math.round((curTok / maxTok) * 100)) + "%";
      var expBtn = document.getElementById("dsb-btn-layout");
      if (expBtn) {
        expBtn.innerHTML = iconSvg(state.fullWorkspace ? "collapse" : "expand", 16);
        expBtn.title = state.fullWorkspace ? "收回到右侧伴侣分栏" : "展开双栏全屏工作台";
        expBtn.classList.toggle("is-active", state.fullWorkspace);
      }
      var thBtn = document.getElementById("dsb-btn-theme");
      if (thBtn) {
        thBtn.innerHTML = iconSvg(state.theme === "dark" ? "sun" : "moon", 16);
        thBtn.title = state.theme === "dark" ? "切换浅色模式" : "切换深色模式";
      }
      var cpBtn = document.getElementById("dsb-btn-compact");
      if (cpBtn) cpBtn.innerHTML = state.compactBusy ? '<span class="dsb-mini-spin"></span>' : iconSvg("compress", 16);

      var popEl = document.getElementById("dsb-switcher-pop");
      if (popEl) {
        if (state.botSwitcherOpen && !state.fullWorkspace) {
          popEl.style.display = "flex";
          popEl.innerHTML = state.bots.map(function (b) {
            return '<button type="button" class="dsb-pop-item' + (b.id === bot.id ? " is-active" : "") + '" data-select-bot="' + escapeHtml(b.id) + '">' +
              renderBotAvatarSvg(b, 24) + '<span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + escapeHtml(b.name) + "</span>" +
              (b.id === bot.id ? iconSvg("check", 14) : "") + "</button>";
          }).join("") + '<div class="dsb-pop-div"></div><button type="button" class="dsb-pop-item" data-open-create-bot="1">' + iconSvg("plus", 15) + "<span>新建专属助理…</span></button>";
        } else {
          popEl.style.display = "none";
        }
      }

      var pinBar = document.getElementById("dsb-pin-bar");
      if (pinBar) {
        var pins = Array.isArray(state.vaultPinned) ? state.vaultPinned : [];
        if (!pins.length) {
          pinBar.style.display = "none";
        } else {
          pinBar.style.display = "block";
          var latestPin = String((pins[pins.length - 1] && pins[pins.length - 1].text) || "").replace(/^\[来源:[^\]]+\]\s*/, "");
          var rows = state.pinBarExpanded ? ('<div class="dsb-pin-list">' + pins.map(function (p) {
            var cleanP = String(p.text || "").replace(/^\[来源:[^\]]+\]\s*/, "");
            return '<div class="dsb-pin-row"><span>' + escapeHtml(cleanP) + '</span><button type="button" class="dsb-icon-btn" style="width:20px;height:20px;" data-unpin-id="' + escapeHtml(p.id || p.text) + '" title="移除置顶">' + iconSvg("close", 12) + "</button></div>";
          }).join("") + "</div>") : "";
          pinBar.innerHTML = '<div class="dsb-pin-head" data-toggle-pinbar="1"><span class="dsb-pin-badge">' + iconSvg("pin", 13) + "<span>置顶记忆 #" + pins.length + '</span></span><span class="dsb-pin-preview">' + escapeHtml(latestPin) + "</span>" + iconSvg("chevron", 13) + "</div>" + rows;
        }
      }
    }

    function renderSingleMessageHtml(m) {
      if (!R()) return '<div class="dsb-msg-row"><div class="dsb-bubble">' + escapeHtml(m && m.text) + "</div></div>";
      var parsed = (m && m.role === "user" && m.kind !== "bot_message") ? parseUserQuoteAndAttachments(m.text) : null;
      return R().buildMessageRowHtml(m, parsed);
    }

    function renderChatStream(forceScroll) {
      var historyEl = document.getElementById("dsb-history-box");
      var liveEl = document.getElementById("dsb-live-box");
      var scrollEl = document.getElementById("dsb-chat-scroll");
      if (!historyEl || !liveEl || !scrollEl) return;
      var nearBottom = forceScroll || (scrollEl.scrollHeight - scrollEl.scrollTop - scrollEl.clientHeight < 120);
      var bot = getActiveBot();
      var roomMsgs = getRoomMessages(bot.id);
      var histSig = bot.id + "|" + state.messagesEpoch + "|" + roomMsgs.map(function (m) {
        return [m.id, m.text.length, (m.thought || "").length, (m.tool_trace || []).length, m.tps || 0].join(":");
      }).join(",");
      if (histSig !== state.lastStreamSig) {
        state.lastStreamSig = histSig;
        if (!roomMsgs.length) {
          historyEl.innerHTML = '<div class="dsb-empty-state">' + renderBotAvatarSvg(bot, 52) +
            '<div class="dsb-empty-title">' + escapeHtml(bot.name) + '</div><div class="dsb-empty-sub">' +
            escapeHtml(bot.description || "独立会话与专属思维链空间，支持拖拽/粘贴图片、引用回复与跨端协同。") + "</div></div>";
        } else {
          historyEl.innerHTML = roomMsgs.map(function (m) { return renderSingleMessageHtml(m, bot); }).join("");
        }
      }

      var liveSt = state.streamsByBot[bot.id];
      var isSending = Boolean(state.sendingByBot[bot.id]);
      var liveSig = JSON.stringify(liveSt || null) + "|" + isSending;
      if (liveSig !== state.lastLiveSig) {
        state.lastLiveSig = liveSig;
        if (liveSt || isSending) {
          var stText = liveSt ? String(liveSt.text || "").trim() : "";
          var stThought = liveSt ? String(liveSt.thought || "").trim() : "";
          var stTrace = liveSt && Array.isArray(liveSt.trace) ? liveSt.trace : [];
          var stTool = liveSt ? String(liveSt.tool || "") : "";
          liveEl.innerHTML = '<div class="dsb-msg-row is-ai">' +
            renderToolCapsuleHtml(stTrace, true, stTool || (!stText ? "正在思考…" : "")) +
            renderThoughtPillHtml(stThought, 1, true) +
            (stText ? '<div class="dsb-bubble">' + renderRichMarkdown(stText) + "</div>" : "") + "</div>";
        } else {
          liveEl.innerHTML = "";
        }
      }
      if (nearBottom) scrollEl.scrollTop = scrollEl.scrollHeight;
    }

    function renderComposerBanners() {
      var bannerBox = document.getElementById("dsb-composer-banners");
      var slashEl = document.getElementById("dsb-slash-pop");
      if (bannerBox) {
        var parts = [];
        if (state.editingMsgId) {
          parts.push('<div class="dsb-ctx-banner"><span style="font-weight:600;color:var(--dsb-accent);">编辑重发</span><span class="dsb-ctx-banner-txt">' + escapeHtml(state.editingText) + '</span><button type="button" class="dsb-icon-btn" style="width:22px;height:22px;" data-cancel-edit="1">' + iconSvg("close", 13) + "</button></div>");
        } else if (state.replyingToMsg) {
          var who = state.replyingToMsg.role === "user" ? "我" : (getActiveBot().name || "助理");
          parts.push('<div class="dsb-ctx-banner"><span style="font-weight:600;color:var(--dsb-accent);">引用 ' + escapeHtml(who) + '</span><span class="dsb-ctx-banner-txt">' + escapeHtml(String(state.replyingToMsg.text || "").replace(/\s+/g, " ").slice(0, 90)) + '</span><button type="button" class="dsb-icon-btn" style="width:22px;height:22px;" data-cancel-reply="1">' + iconSvg("close", 13) + "</button></div>");
        }
        if (state.pendingAttachments.length) {
          parts.push('<div class="dsb-att-strip">' + state.pendingAttachments.map(function (a, idx) {
            return '<span class="dsb-att-pill">' + (a.uploading ? '<span class="dsb-mini-spin"></span>' : iconSvg("file", 13)) + "<span>" + escapeHtml(a.name) + '</span><button type="button" data-rm-att="' + idx + '">' + iconSvg("close", 12) + "</button></span>";
          }).join("") + "</div>");
        }
        bannerBox.innerHTML = parts.join("");
      }
      if (slashEl) {
        slashEl.style.display = state.slashMenuOpen ? "block" : "none";
        if (state.slashMenuOpen) {
          slashEl.innerHTML = SLASH_COMMANDS.map(function (sc) {
            return '<button type="button" class="dsb-slash-item" data-pick-slash="' + escapeHtml(sc.cmd) + '"><span class="dsb-slash-cmd">' + escapeHtml(sc.cmd.trim()) + '</span><span style="color:var(--dsb-muted);font-size:11.5px;">' + escapeHtml(sc.desc) + "</span></button>";
          }).join("");
        }
      }
      updateComposerButtons();
    }
    function updateComposerButtons() {
      var bot = getActiveBot();
      var isBusy = Boolean(state.streamsByBot[bot.id] || state.sendingByBot[bot.id]);
      var sendBtn = document.getElementById("dsb-send-btn");
      if (sendBtn) {
        sendBtn.classList.toggle("is-stop", isBusy);
        sendBtn.type = isBusy ? "button" : "submit";
        sendBtn.title = isBusy ? "立即停止生成" : "发送消息 (Enter)";
        sendBtn.innerHTML = isBusy ? iconSvg("stop", 15) : iconSvg("send", 16);
      }
      var micBtn = document.getElementById("dsb-mic-btn");
      if (micBtn) {
        micBtn.classList.toggle("is-rec", state.recordingVoice);
        micBtn.innerHTML = state.transcribingVoice ? '<span class="dsb-mini-spin"></span>' : iconSvg("mic", 16);
      }
    }

    // === [9. Bot Studio & Memory Vault Slide-Over Sheets] ===
    function openBotStudio(mode) {
      var bot = getActiveBot();
      state.studioMode = mode === "create" ? "create" : "edit";
      state.studioOpen = true; state.vaultOpen = false; state.botSwitcherOpen = false;
      if (state.studioMode === "create") {
        state.editBotName = ""; state.editBotDesc = "";
        state.editBotColor = PRESET_COLORS[state.bots.length % PRESET_COLORS.length];
        state.editBotShape = "circle"; state.editBotPersona = ""; state.editBotModel = ""; state.editBotCuriosity = false;
      } else {
        state.editBotName = bot.name || ""; state.editBotDesc = bot.description || "";
        state.editBotColor = bot.color || "#B568F7"; state.editBotShape = bot.shape || "flower";
        state.editBotPersona = bot.instructions || bot.persona || ""; state.editBotModel = bot.model || "";
        state.editBotCuriosity = Boolean(bot.curiosity_enabled);
      }
      renderSheets();
      apiCall("/local/models", "POST", {}).then(function (r) {
        if (r && Array.isArray(r.models)) { state.availableModels = r.models; if (state.studioOpen) renderSheets(); }
      });
    }
    function openVaultSheet(initialPath) {
      state.vaultOpen = true; state.studioOpen = false; state.botSwitcherOpen = false; state.vaultLoading = true;
      renderSheets();
      apiCall("/local/vault", "POST", { op: "list" }).then(function (res) {
        state.vaultLoading = false;
        state.vaultFiles = Array.isArray(res && res.files) ? res.files : [];
        state.vaultPinned = Array.isArray(res && (res.pins || res.pinned_memories)) ? (res.pins || res.pinned_memories) : state.vaultPinned;
        var pick = initialPath || state.vaultSelectedPath || (state.vaultFiles[0] && (state.vaultFiles[0].path || state.vaultFiles[0]));
        if (pick) loadVaultFile(String(pick));
        else renderSheets();
        renderHeaderAndPins();
      });
    }
    function loadVaultFile(relPath) {
      var clean = String(relPath || "").replace(/^memory_vault\//, "");
      state.vaultSelectedPath = clean;
      apiCall("/local/vault", "POST", { op: "read", path: clean }).then(function (r) {
        state.vaultSelectedContent = (r && typeof r.content === "string") ? r.content : "";
        renderSheets();
      });
    }
    function renderSheets() {
      var host = document.getElementById("dsb-sheet-host");
      if (!host) return;
      if (!state.studioOpen && !state.vaultOpen) { host.innerHTML = ""; return; }
      if (!R()) return;
      host.innerHTML = state.studioOpen
        ? R().buildStudioSheetHtml(state, getActiveBot(), PRESET_COLORS, SHAPE_OPTIONS)
        : R().buildVaultSheetHtml(state);
    }

    // === [10. Turn Submission, Sync Loop & Event Delegation] ===
    function submitTurn() {
      var ta = document.getElementById("dsb-chat-input");
      if (!ta) return;
      var raw = String(ta.value || "").trim();
      var readyAtts = state.pendingAttachments.filter(function (a) { return !a.uploading && a.path; });
      if (!raw && !readyAtts.length) return;
      if (state.uploadingCount > 0) { showToast("附件正在上传，请稍候…"); return; }

      var bot = getActiveBot();
      var finalText = raw || "请查看这些附件。";
      if (state.replyingToMsg && !state.editingMsgId) {
        var qWho = state.replyingToMsg.role === "user" ? "我" : bot.name;
        var qLine = String(state.replyingToMsg.text || "").replace(/\s+/g, " ").trim().slice(0, 120);
        finalText = "> [" + qWho + "] " + qLine + "\n\n" + finalText;
      }
      if (readyAtts.length) {
        finalText += "\n\n附件（工作区相对路径，可直接读取或分析）：\n" + readyAtts.map(function (a) { return a.path; }).join("\n");
      }

      var editId = state.editingMsgId;
      ta.value = ""; autoFitTextarea(ta);
      state.editingMsgId = null; state.editingText = ""; state.replyingToMsg = null;
      state.pendingAttachments = []; state.slashMenuOpen = false; state.sendingByBot[bot.id] = true;
      renderComposerBanners(); renderSidebarDock();

      if (!editId) {
        var optimisticId = "pc-" + Date.now();
        state.messages.push({
          id: optimisticId, role: "user", kind: "chat", text: finalText, bot_id: bot.id,
          source: "pc", time: new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false })
        });
        renderChatStream(true);
        apiCall("/local/infinite-chat/send", "POST", { bot_id: bot.id, text: finalText, client_msg_id: optimisticId }).then(function (res) {
          delete state.sendingByBot[bot.id];
          applySyncPayload(res);
          renderChatStream(true);
        });
      } else {
        apiCall("/local/infinite-chat/edit", "POST", { bot_id: bot.id, message_id: editId, text: finalText }).then(function (res) {
          delete state.sendingByBot[bot.id];
          applySyncPayload(res);
          renderChatStream(true);
        });
      }
    }

    function applySyncPayload(res) {
      if (!res || !res.ok) return;
      if (Array.isArray(res.bots) && res.bots.length) state.bots = res.bots;
      if (Array.isArray(res.messages)) state.messages = res.messages;
      if (res.messages_epoch) state.messagesEpoch = Number(res.messages_epoch) || 1;
      if (res.streams_by_bot && typeof res.streams_by_bot === "object") state.streamsByBot = res.streams_by_bot;
      if (res.bot_context_by_id && typeof res.bot_context_by_id === "object") state.botContextById = res.bot_context_by_id;
      if (typeof res.vps_online === "boolean") state.vpsOnline = res.vps_online;
      if (typeof res.rtt_ms === "number") state.rttMs = res.rtt_ms;
      if (typeof res.phone_online === "boolean") state.phoneOnline = res.phone_online;
      var pins = res.pins || res.pinned_memories;
      if (Array.isArray(pins)) state.vaultPinned = pins;
      renderSidebarDock(); renderHomeRail(); renderHeaderAndPins(); updateComposerButtons();
    }

    function syncNow(force) {
      if (!state.viewActive && !force) return;
      apiCall("/local/infinite-chat/sync", "POST", { force: Boolean(force) }).then(function (res) {
        applySyncPayload(res);
        renderChatStream(false);
      });
    }

    function selectBot(botId) {
      if (!botId) return;
      state.activeBotId = botId; state.botSwitcherOpen = false; state.editingMsgId = null; state.replyingToMsg = null;
      apiCall("/local/bots/select", "POST", { bot_id: botId });
      renderSidebarDock(); renderHomeRail(); renderHeaderAndPins(); renderComposerBanners(); renderChatStream(true);
    }

    function ensureDomMounted() {
      injectStyles();
      renderSidebarDock();
      if (document.getElementById("dsb-split-pane")) return;

      var pane = document.createElement("aside");
      pane.id = "dsb-split-pane";
      pane.setAttribute("aria-label", "DSB 跨端智能助理工作台");
      pane.innerHTML =
        '<div class="dsb-resizer" id="dsb-resizer"></div><div class="dsb-home-rail"><div class="dsb-home-top">' +
        '<div class="dsb-home-brand-row"><div class="dsb-home-brand">' + iconSvg("sparkles", 17) + '<span>DSB 工作台</span></div><div class="dsb-conn-pill" id="dsb-conn-pill"></div></div>' +
        '<div class="dsb-search-wrap">' + iconSvg("search", 14) + '<input id="dsb-bot-search" class="dsb-search-inp" type="search" placeholder="搜索助理或职责…"/></div>' +
        '<div class="dsb-filter-row"><button type="button" class="dsb-filter-chip is-active" data-bot-filter="all">全部</button><button type="button" class="dsb-filter-chip" data-bot-filter="running">运行中</button><button type="button" class="dsb-filter-chip" data-bot-filter="pinned">主控/常驻</button></div>' +
        '</div><div class="dsb-contact-list" id="dsb-contact-list"></div><div class="dsb-home-foot"><button type="button" class="dsb-new-bot-btn" data-open-create-bot="1">' + iconSvg("plus", 15) + '<span>新建专属助理</span></button></div></div>' +
        '<div class="dsb-chat-stage"><header class="dsb-header"><div class="dsb-head-left"><button type="button" class="dsb-bot-trigger" id="dsb-bot-trigger"></button></div><div class="dsb-head-actions">' +
        '<button type="button" class="dsb-icon-btn" id="dsb-btn-layout" title="切换全屏双栏工作台">' + iconSvg("expand", 16) + '</button><button type="button" class="dsb-icon-btn" id="dsb-btn-theme" title="切换日夜主题">' + iconSvg("sun", 16) + '</button>' +
        '<button type="button" class="dsb-icon-btn" id="dsb-btn-vault" title="记忆金库与置顶规则">' + iconSvg("vault", 16) + '</button><button type="button" class="dsb-icon-btn" id="dsb-btn-studio" title="助理工作坊">' + iconSvg("sliders", 16) + '</button>' +
        '<button type="button" class="dsb-icon-btn" id="dsb-btn-compact" title="浓缩归档上下文 (/compact)">' + iconSvg("compress", 16) + '</button><button type="button" class="dsb-icon-btn" id="dsb-btn-close" title="收起面板">' + iconSvg("close", 16) + '</button></div>' +
        '<div class="dsb-ctx-hairline"><div class="dsb-ctx-hairline-fill" id="dsb-ctx-fill" style="width:0%"></div></div><div class="dsb-switcher-pop" id="dsb-switcher-pop" style="display:none;"></div></header>' +
        '<div class="dsb-pin-bar" id="dsb-pin-bar" style="display:none;"></div>' +
        '<div class="dsb-chat-scroll" id="dsb-chat-scroll"><div class="dsb-chat-inner"><div id="dsb-history-box" style="display:flex;flex-direction:column;gap:10px;"></div><div id="dsb-live-box"></div></div></div>' +
        '<div class="dsb-composer-wrap"><div class="dsb-slash-pop" id="dsb-slash-pop" style="display:none;"></div><form id="dsb-composer-form" class="dsb-composer-inner"><div id="dsb-composer-banners" style="display:flex;flex-direction:column;gap:5px;"></div>' +
        '<div class="dsb-composer-row"><input id="dsb-file-picker" type="file" multiple style="display:none;"/><button type="button" class="dsb-tool-circle" id="dsb-attach-btn" title="上传图片或文件附件">' + iconSvg("paperclip", 16) + '</button>' +
        '<div class="dsb-input-capsule"><label for="dsb-chat-input" class="dsb-sr-only">输入消息</label><textarea id="dsb-chat-input" rows="1" placeholder="发消息、粘贴截图，输入 / 唤出指令…"></textarea></div>' +
        '<button type="button" class="dsb-tool-circle" id="dsb-mic-btn" title="语音听写">' + iconSvg("mic", 16) + '</button><button type="submit" class="dsb-send-circle" id="dsb-send-btn" title="发送消息">' + iconSvg("send", 16) + '</button></div></form></div>' +
        '<div id="dsb-toast-el" class="dsb-toast" style="display:none;"></div><div id="dsb-sheet-host"></div></div>';
      document.body.appendChild(pane);

      // Resizer drag
      var resizer = document.getElementById("dsb-resizer");
      resizer.addEventListener("mousedown", function (ev) {
        if (state.fullWorkspace) return;
        ev.preventDefault();
        resizer.classList.add("is-dragging");
        var onMove = function (e2) {
          var w = Math.max(360, Math.min(680, window.innerWidth - e2.clientX));
          state.paneWidth = Math.round(w);
          document.documentElement.style.setProperty("--dsb-pane-w", state.paneWidth + "px");
          var mEl = document.querySelector("main"); if (mEl && state.viewActive && !state.fullWorkspace) mEl.style.marginRight = state.paneWidth + "px";
        };
        var onUp = function () {
          resizer.classList.remove("is-dragging");
          try { window.localStorage.setItem("dsb_pane_width", String(state.paneWidth)); } catch (e) {}
          window.removeEventListener("mousemove", onMove);
          window.removeEventListener("mouseup", onUp);
        };
        window.addEventListener("mousemove", onMove);
        window.addEventListener("mouseup", onUp);
      });

      // IME-safe Enter Submit (`ime-safe-enter-submit`) + Auto-fit (`form-fields-automatically-fit-contents`) + Clipboard Image Paste
      var formEl = document.getElementById("dsb-composer-form");
      var taEl = document.getElementById("dsb-chat-input");
      taEl.addEventListener("compositionstart", function () { state.isComposing = true; });
      taEl.addEventListener("compositionend", function (ev) { state.isComposing = false; state.lastCompositionEndAt = ev.timeStamp || performance.now(); });
      taEl.addEventListener("keydown", function (ev) {
        if (ev.key === "Escape") {
          if (state.slashMenuOpen) { state.slashMenuOpen = false; renderComposerBanners(); return; }
          if (state.editingMsgId || state.replyingToMsg) { state.editingMsgId = null; state.replyingToMsg = null; renderComposerBanners(); return; }
        }
        if (ev.key !== "Enter" || ev.shiftKey) return;
        var justEndedIme = ((ev.timeStamp || performance.now()) - state.lastCompositionEndAt) < 50;
        if (ev.isComposing || state.isComposing || ev.keyCode === 229 || justEndedIme) return;
        ev.preventDefault();
        var curBot = getActiveBot();
        if (state.streamsByBot[curBot.id] || state.sendingByBot[curBot.id]) return;
        formEl.requestSubmit();
      });
      taEl.addEventListener("input", function () {
        autoFitTextarea(taEl);
        var v = taEl.value;
        var wantSlash = v.indexOf("/") === 0 && v.indexOf(" ") === -1 && v.length <= 14;
        if (wantSlash !== state.slashMenuOpen) { state.slashMenuOpen = wantSlash; renderComposerBanners(); }
      });
      taEl.addEventListener("paste", function (ev) {
        var items = ev.clipboardData && ev.clipboardData.items;
        if (!items) return;
        for (var i = 0; i < items.length; i++) {
          if (items[i].kind === "file") {
            var f = items[i].getAsFile();
            if (f) { ev.preventDefault(); uploadFileObject(f); }
          }
        }
      });
      formEl.addEventListener("submit", function (ev) { ev.preventDefault(); submitTurn(); });

      // Drag & Drop File Upload
      pane.addEventListener("dragover", function (ev) { ev.preventDefault(); });
      pane.addEventListener("drop", function (ev) {
        ev.preventDefault();
        var files = ev.dataTransfer && ev.dataTransfer.files;
        if (files && files.length) { for (var i = 0; i < files.length; i++) uploadFileObject(files[i]); }
      });

      var filePicker = document.getElementById("dsb-file-picker");
      filePicker.addEventListener("change", function () {
        if (filePicker.files) { for (var i = 0; i < filePicker.files.length; i++) uploadFileObject(filePicker.files[i]); }
        filePicker.value = "";
      });
      document.getElementById("dsb-bot-search").addEventListener("input", function (ev) {
        state.botSearch = ev.target.value || "";
        renderHomeRail();
      });

      // Delegated Clicks & Double-Click Quote Reply
      pane.addEventListener("dblclick", function (ev) {
        var bub = ev.target.closest("[data-dbl-reply]");
        if (!bub) return;
        var mid = bub.getAttribute("data-dbl-reply");
        var hit = state.messages.find(function (x) { return x.id === mid; });
        if (hit) { state.replyingToMsg = hit; state.editingMsgId = null; renderComposerBanners(); taEl.focus(); }
      });

      pane.addEventListener("click", function (ev) {
        var t = ev.target;
        var imgEl = t.closest("[data-lightbox-src]");
        if (imgEl) { openLightbox(imgEl.getAttribute("data-lightbox-src")); return; }
        var copyCodeBtn = t.closest("[data-copy-code]");
        if (copyCodeBtn) {
          navigator.clipboard.writeText(decodeURIComponent(copyCodeBtn.getAttribute("data-copy-code") || ""));
          showToast("代码已复制"); return;
        }
        if (t.closest("#dsb-bot-trigger")) {
          if (state.fullWorkspace) openBotStudio("edit");
          else { state.botSwitcherOpen = !state.botSwitcherOpen; renderHeaderAndPins(); }
          return;
        }
        if (!t.closest("#dsb-switcher-pop") && state.botSwitcherOpen) { state.botSwitcherOpen = false; renderHeaderAndPins(); }
        if (t.closest("#dsb-btn-layout")) {
          state.fullWorkspace = !state.fullWorkspace;
          try { window.localStorage.setItem("dsb_full_workspace", state.fullWorkspace ? "1" : "0"); } catch (e) {}
          applyLayoutClasses(); renderHomeRail(); renderHeaderAndPins(); return;
        }
        if (t.closest("#dsb-btn-theme")) {
          state.theme = state.theme === "dark" ? "light" : "dark";
          try { window.localStorage.setItem("dsb_theme", state.theme); } catch (e) {}
          applyLayoutClasses(); renderHeaderAndPins(); return;
        }
        if (t.closest("#dsb-btn-vault")) { openVaultSheet(); return; }
        if (t.closest("#dsb-btn-studio")) { openBotStudio("edit"); return; }
        if (t.closest("#dsb-btn-compact")) {
          if (state.compactBusy) return;
          state.compactBusy = true; renderHeaderAndPins();
          apiCall("/local/infinite-chat/compact", "POST", { bot_id: state.activeBotId }).then(function (r) {
            state.compactBusy = false; applySyncPayload(r); renderChatStream(true); showToast("上下文浓缩归档完成");
          });
          return;
        }
        if (t.closest("#dsb-btn-close")) { state.viewActive = false; applyLayoutClasses(); return; }
        if (t.closest("#dsb-attach-btn")) { filePicker.click(); return; }
        if (t.closest("#dsb-mic-btn")) { toggleVoiceDictation(); return; }
        if (t.closest("#dsb-send-btn") && t.closest("#dsb-send-btn").classList.contains("is-stop")) {
          ev.preventDefault();
          var bId = state.activeBotId;
          delete state.sendingByBot[bId]; delete state.streamsByBot[bId];
          apiCall("/local/infinite-chat/stop", "POST", { bot_id: bId }).then(function (r) { applySyncPayload(r); renderChatStream(false); });
          updateComposerButtons(); renderChatStream(false); return;
        }
        var selBot = t.closest("[data-select-bot]");
        if (selBot) { selectBot(selBot.getAttribute("data-select-bot")); return; }
        var fltBtn = t.closest("[data-bot-filter]");
        if (fltBtn) { state.botFilter = fltBtn.getAttribute("data-bot-filter"); renderHomeRail(); return; }
        if (t.closest("[data-open-create-bot]")) { openBotStudio("create"); return; }
        if (t.closest("[data-toggle-pinbar]")) { state.pinBarExpanded = !state.pinBarExpanded; renderHeaderAndPins(); return; }
        var unpinBtn = t.closest("[data-unpin-id]");
        if (unpinBtn) {
          ev.stopPropagation();
          apiCall("/local/vault", "POST", { op: "unpin", pin_id: unpinBtn.getAttribute("data-unpin-id") }).then(function (r) {
            if (r && Array.isArray(r.pins)) state.vaultPinned = r.pins;
            renderHeaderAndPins(); if (state.vaultOpen) renderSheets(); showToast("已移除置顶记忆");
          });
          return;
        }
        var slashPick = t.closest("[data-pick-slash]");
        if (slashPick) {
          var cmd = slashPick.getAttribute("data-pick-slash") || "";
          state.slashMenuOpen = false;
          if (cmd.slice(-1) === " ") { taEl.value = cmd; autoFitTextarea(taEl); renderComposerBanners(); taEl.focus(); }
          else { taEl.value = cmd; submitTurn(); }
          return;
        }
        if (t.closest("[data-cancel-edit]")) { state.editingMsgId = null; state.editingText = ""; taEl.value = ""; autoFitTextarea(taEl); renderComposerBanners(); return; }
        if (t.closest("[data-cancel-reply]")) { state.replyingToMsg = null; renderComposerBanners(); return; }
        var rmAtt = t.closest("[data-rm-att]");
        if (rmAtt) { state.pendingAttachments.splice(Number(rmAtt.getAttribute("data-rm-att")), 1); renderComposerBanners(); return; }
        var choiceBtn = t.closest("[data-send-choice]");
        if (choiceBtn) { taEl.value = choiceBtn.getAttribute("data-send-choice") || ""; submitTurn(); return; }

        var actBtn = t.closest("[data-act]");
        if (actBtn) {
          var act = actBtn.getAttribute("data-act"), mid = actBtn.getAttribute("data-mid");
          var msgObj = state.messages.find(function (x) { return x.id === mid; });
          if (!msgObj) return;
          if (act === "copy") { navigator.clipboard.writeText(msgObj.text || ""); showToast("已复制到剪贴板"); }
          else if (act === "reply") { state.replyingToMsg = msgObj; state.editingMsgId = null; renderComposerBanners(); taEl.focus(); }
          else if (act === "edit") {
            var cleanEdit = parseUserQuoteAndAttachments(msgObj.text).body || msgObj.text;
            state.editingMsgId = msgObj.id; state.editingText = cleanEdit; state.replyingToMsg = null;
            taEl.value = cleanEdit; autoFitTextarea(taEl); renderComposerBanners(); taEl.focus();
          } else if (act === "reroll") {
            state.sendingByBot[state.activeBotId] = true; updateComposerButtons();
            apiCall("/local/infinite-chat/reroll", "POST", { bot_id: state.activeBotId, message_id: msgObj.id }).then(function (r) {
              delete state.sendingByBot[state.activeBotId]; applySyncPayload(r); renderChatStream(true);
            });
          } else if (act === "pin") {
            apiCall("/local/vault", "POST", { op: "pin", text: String(msgObj.text || "").slice(0, 360) }).then(function (r) {
              if (r && Array.isArray(r.pins)) state.vaultPinned = r.pins;
              renderHeaderAndPins(); showToast("已钉入跨端置顶记忆");
            });
          }
          return;
        }

        // Sheet actions
        var closeS = t.closest("[data-close-sheet]");
        if (closeS && (closeS.getAttribute("data-close-sheet") !== "mask" || t === closeS)) {
          state.studioOpen = false; state.vaultOpen = false; renderSheets(); return;
        }
        var pickCol = t.closest("[data-pick-color]");
        if (pickCol) { syncStudioDraftFromInputs(); state.editBotColor = pickCol.getAttribute("data-pick-color"); renderSheets(); return; }
        var pickSh = t.closest("[data-pick-shape]");
        if (pickSh) { syncStudioDraftFromInputs(); state.editBotShape = pickSh.getAttribute("data-pick-shape"); renderSheets(); return; }
        if (t.closest("[data-export-bot]")) { if (R() && R().exportBotArchive) R().exportBotArchive(apiCall, state.activeBotId, showToast); return; }
        if (t.closest("[data-import-bot]")) {
          if (R() && R().pickAndImportBotArchive) R().pickAndImportBotArchive(apiCall, function (r) {
            if (r && Array.isArray(r.bots)) state.bots = r.bots;
            if (r && r.active_bot_id) state.activeBotId = r.active_bot_id;
            if (r && Array.isArray(r.messages)) state.messages = r.messages;
            state.studioOpen = false; renderSheets(); renderSidebarDock(); renderHomeRail(); renderHeaderAndPins(); renderChatStream(true);
          }, showToast);
          return;
        }
        if (t.closest("[data-save-bot]")) {
          syncStudioDraftFromInputs();
          if (!state.editBotName.trim()) { showToast("请输入助理名称"); return; }
          var isCr = state.studioMode === "create";
          apiCall(isCr ? "/local/bots/create" : "/local/bots/update", "POST", {
            bot_id: isCr ? "" : state.activeBotId, name: state.editBotName.trim(), description: state.editBotDesc.trim(),
            color: state.editBotColor, shape: state.editBotShape, instructions: state.editBotPersona, persona: state.editBotPersona, model: state.editBotModel, curiosity_enabled: state.editBotCuriosity
          }).then(function (r) {
            if (r && Array.isArray(r.bots)) state.bots = r.bots;
            if (isCr && r && r.bot && r.bot.id) state.activeBotId = r.bot.id;
            state.studioOpen = false; renderSheets(); renderSidebarDock(); renderHomeRail(); renderHeaderAndPins(); renderChatStream(true); showToast(isCr ? "已创建新助理" : "助理设定已保存");
          });
          return;
        }
        if (t.closest("[data-delete-bot]")) {
          apiCall("/local/bots/delete", "POST", { bot_id: state.activeBotId }).then(function (r) {
            if (r && Array.isArray(r.bots)) state.bots = r.bots;
            state.activeBotId = r.active_bot_id || "pc_engineer";
            state.studioOpen = false; renderSheets(); renderSidebarDock(); renderHomeRail(); renderHeaderAndPins(); renderChatStream(true); showToast("助理已删除");
          });
          return;
        }
        var vTab = t.closest("[data-vault-tab]");
        if (vTab) { state.vaultTab = vTab.getAttribute("data-vault-tab"); renderSheets(); return; }
        var vPath = t.closest("[data-vault-path]");
        if (vPath) { loadVaultFile(vPath.getAttribute("data-vault-path")); return; }
        if (t.closest("[data-save-vault]")) {
          var ed = document.getElementById("dsb-vault-editor");
          if (!ed || !state.vaultSelectedPath) return;
          state.vaultSelectedContent = ed.value;
          apiCall("/local/vault", "POST", { op: "write", path: state.vaultSelectedPath, content: ed.value }).then(function (r) {
            state.vaultStatusMsg = r && r.ok ? "已保存 " + new Date().toLocaleTimeString("zh-CN", { hour12: false }) : "保存失败";
            renderSheets(); showToast(r && r.ok ? "记忆文档已保存" : "保存失败");
          });
          return;
        }
        if (t.closest("[data-add-pin]")) {
          var pinInp = document.getElementById("dsb-new-pin-inp"), pTxt = pinInp ? pinInp.value.trim() : "";
          if (!pTxt) return;
          apiCall("/local/vault", "POST", { op: "pin", text: pTxt }).then(function (r) {
            if (r && Array.isArray(r.pins)) state.vaultPinned = r.pins;
            renderHeaderAndPins(); renderSheets(); showToast("已添加置顶记忆");
          });
        }
      });
      applyLayoutClasses(); renderHomeRail(); renderHeaderAndPins(); renderComposerBanners();
    }
    function syncStudioDraftFromInputs() {
      var n = document.getElementById("dsb-st-name"), d = document.getElementById("dsb-st-desc"), m = document.getElementById("dsb-st-model"), p = document.getElementById("dsb-st-persona"), c = document.getElementById("dsb-st-curiosity");
      if (n) state.editBotName = n.value; if (d) state.editBotDesc = d.value; if (m) state.editBotModel = m.value; if (p) state.editBotPersona = p.value; if (c) state.editBotCuriosity = Boolean(c.checked);
    }
    function openLightbox(src) {
      if (!src) return;
      var lb = document.createElement("div"); lb.className = "dsb-lightbox"; lb.innerHTML = '<img src="' + escapeHtml(src) + '" alt="预览"/>';
      var closeLb = function () { if (lb.parentNode) lb.parentNode.removeChild(lb); window.removeEventListener("keydown", onEsc); };
      var onEsc = function (e) { if (e.key === "Escape") closeLb(); };
      lb.addEventListener("click", closeLb); window.addEventListener("keydown", onEsc); document.body.appendChild(lb);
    }

    var assetsLoading = false;
    function loadUiAssets() {
      if (assetsLoading) return;
      assetsLoading = true;
      apiCall("/local/ui-assets", "GET", null).then(function (r) {
        assetsLoading = false;
        if (r && r.css) injectStyles(r.css);
        if (r && r.render_js && !window.__DSB_RENDER__) {
          var s = document.createElement("script"); s.textContent = r.render_js; document.head.appendChild(s);
        }
        if (r && r.data_url) state.whaleAvatarUrl = r.data_url;
        var oldPane = document.getElementById("dsb-split-pane");
        if (oldPane && window.__DSB_RENDER__ && !oldPane.querySelector("svg")) oldPane.remove();
        state.lastStreamSig = ""; state.lastLiveSig = ""; ensureDomMounted(); applyLayoutClasses(); syncNow(true);
      });
    }
    var pluginExport = {
      name: "dsh-deepseek-claw",
      apply: function (ctx) {
        if (window.__dsbTimer) clearInterval(window.__dsbTimer);
        var boot = function () { ensureDomMounted(); applyLayoutClasses(); loadUiAssets(); syncNow(true); };
        if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
        else setTimeout(boot, 30);
        var tick = 0, timer = setInterval(function () {
          tick++; ensureDomMounted();
          if (!window.__DSB_RENDER__ && tick % 3 === 0) loadUiAssets();
          if (state.viewActive || Object.keys(state.sendingByBot).length > 0 || Object.keys(state.streamsByBot).length > 0) syncNow(false);
        }, 1200);
        window.__dsbTimer = timer;
        var dispose = function () { clearInterval(timer); if (window.__dsbTimer === timer) window.__dsbTimer = null; };
        if (ctx && typeof ctx.effect === "function") ctx.effect(function () { return dispose; });
        else if (ctx && typeof ctx.on === "function") ctx.on("dispose", dispose);
      }
    };
    return { default: pluginExport, name: "dsh-deepseek-claw", apply: pluginExport.apply };
  }
});
