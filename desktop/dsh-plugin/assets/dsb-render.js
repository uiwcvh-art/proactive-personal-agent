/**
 * [Module Responsibility]
 * DSB 桌面端无状态视觉渲染库 (`assets/dsb-render.js`)。
 * 提供：矢量 SVG 图标集、灵体头像渲染、Markdown/代码着色/LaTeX 排版、单行工具胶囊与独立思考折叠条 HTML 生成。
 *
 * [Public Interface]
 * - `window.__DSB_RENDER__`: `{ PULSE_SHAPE_PATHS, iconSvg, escapeHtml, resolveChatMediaUrl, renderRichMarkdown, renderBotAvatarSvg, formatCompactTokens, friendlyToolLabel, renderToolCapsuleHtml, renderThoughtPillHtml }`
 */
(function () {
  "use strict";
  var PULSE_SHAPE_PATHS = {
    flower: "M50 4 C61 4 64 17 73 20 C82 23 93 16 97 25 C101 35 89 43 89 52 C89 61 100 68 95 78 C90 87 78 81 70 86 C62 91 61 98 50 98 C39 98 38 91 30 86 C22 81 10 87 5 78 C0 68 11 61 11 52 C11 43 -1 35 3 25 C7 16 18 23 27 20 C36 17 39 4 50 4 Z",
    circle: "M50 5 A45 45 0 1 0 50 95 A45 45 0 1 0 50 5 Z",
    oval: "M50 3 A38 47 0 1 0 50 97 A38 47 0 1 0 50 3 Z",
    square: "M22 8 H78 A14 14 0 0 1 92 22 V78 A14 14 0 0 1 78 92 H22 A14 14 0 0 1 8 78 V22 A14 14 0 0 1 22 8 Z",
    pill: "M34 6 H66 A28 28 0 0 1 94 34 V66 A28 28 0 0 1 66 94 H34 A28 28 0 0 1 6 66 V34 A28 28 0 0 1 34 6 Z",
    triangle: "M50 6 C57 6 61 12 65 20 L92 74 C96 82 91 92 81 92 H19 C9 92 4 82 8 74 L35 20 C39 12 43 6 50 6 Z",
    diamond: "M50 5 L95 50 L50 95 L5 50 Z",
    star: "M50 4 C54 33 67 46 96 50 C67 54 54 67 50 96 C46 67 33 54 4 50 C33 46 46 33 50 4 Z",
    drop: "M50 5 C71 29 88 47 88 67 A38 38 0 1 1 12 67 C12 47 29 29 50 5 Z",
    whale: "M16 56 C16 28 38 14 63 18 C81 21 92 34 94 50 C96 63 87 76 74 80 C56 85 33 84 20 74 C14 69 16 62 16 56 Z",
    whalechan: "M26 8 H74 A18 18 0 0 1 92 26 V74 A18 18 0 0 1 74 92 H26 A18 18 0 0 1 8 74 V26 A18 18 0 0 1 26 8 Z"
  };

  var ICON_PATHS = {
    sparkles: '<path d="M12 2.5L14.3 8.2L20 10.5L14.3 12.8L12 18.5L9.7 12.8L4 10.5L9.7 8.2L12 2.5Z" stroke="currentColor" stroke-width="1.75" stroke-linejoin="round"/><path d="M19 16.5L19.9 18.6L22 19.5L19.9 20.4L19 22.5L18.1 20.4L16 19.5L18.1 18.6L19 16.5Z" fill="currentColor"/>',
    expand: '<path d="M15 4H20V9M9 20H4V15M20 4L13.5 10.5M4 20L10.5 13.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
    collapse: '<path d="M4 14H10V20M20 10H14V4M14 10L20.5 3.5M10 14L3.5 20.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
    sun: '<circle cx="12" cy="12" r="4.2" stroke="currentColor" stroke-width="1.8"/><path d="M12 2.5V5M12 19V21.5M4.2 4.2L6 6M18 18L19.8 19.8M2.5 12H5M19 12H21.5M4.2 19.8L6 18M18 6L19.8 4.2" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>',
    moon: '<path d="M20.2 14.6A8.2 8.2 0 0 1 9.4 3.8A8.3 8.3 0 1 0 20.2 14.6Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>',
    vault: '<path d="M4 6.5A2.5 2.5 0 0 1 6.5 4H19.5V19.5H6.5A2.5 2.5 0 0 1 4 17V6.5Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M4 17A2.5 2.5 0 0 1 6.5 14.5H19.5" stroke="currentColor" stroke-width="1.6"/><path d="M8.5 8.5H15.5" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>',
    sliders: '<path d="M4 7H10M14 7H20M10 4.5V9.5M4 17H13M17 17H20M13 14.5V19.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>',
    compress: '<path d="M8 4V8H4M16 4V8H20M8 20V16H4M16 20V16H20" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
    close: '<path d="M6 6L18 18M18 6L6 18" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/>',
    plus: '<path d="M12 5V19M5 12H19" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/>',
    search: '<circle cx="10.8" cy="10.8" r="6.3" stroke="currentColor" stroke-width="1.8"/><path d="M15.6 15.6L20 20" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>',
    pin: '<path d="M15.5 4.5L19.5 8.5L16.5 11.5L15.5 16L8 8.5L12.5 7.5L15.5 4.5Z" stroke="currentColor" stroke-width="1.75" stroke-linejoin="round"/><path d="M6 18L11.5 12.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>',
    copy: '<rect x="8.5" y="8.5" width="11" height="11" rx="2.2" stroke="currentColor" stroke-width="1.75"/><path d="M5.5 15.5H5A1.8 1.8 0 0 1 3.2 13.7V5A1.8 1.8 0 0 1 5 3.2H13.7A1.8 1.8 0 0 1 15.5 5V5.5" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"/>',
    reply: '<path d="M9.5 7L4.5 12L9.5 17" stroke="currentColor" stroke-width="1.85" stroke-linecap="round" stroke-linejoin="round"/><path d="M5 12H14.5A5 5 0 0 1 19.5 17V18" stroke="currentColor" stroke-width="1.85" stroke-linecap="round"/>',
    edit: '<path d="M15.8 4.6L19.4 8.2L8.4 19.2H4.8V15.6L15.8 4.6Z" stroke="currentColor" stroke-width="1.75" stroke-linejoin="round"/>',
    refresh: '<path d="M19.5 8.5A7.8 7.8 0 0 0 6.2 6.8L4.5 8.5M4.5 5V8.5H8M4.5 15.5A7.8 7.8 0 0 0 17.8 17.2L19.5 15.5M19.5 19V15.5H16" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
    paperclip: '<path d="M18.4 11.3L10.6 19.1A5 5 0 0 1 3.5 12L11.8 3.7A3.3 3.3 0 0 1 16.5 8.4L8.4 16.5A1.6 1.6 0 0 1 6.1 14.2L13.5 6.8" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
    mic: '<rect x="9" y="3.5" width="6" height="10.5" rx="3" stroke="currentColor" stroke-width="1.8"/><path d="M5.5 11A6.5 6.5 0 0 0 18.5 11M12 17.5V20.8M9 20.8H15" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>',
    send: '<path d="M12 19V5.5M6.5 10.5L12 5L17.5 10.5" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>',
    stop: '<rect x="7" y="7" width="10" height="10" rx="2.2" fill="currentColor"/>',
    check: '<path d="M5 12.5L9.6 17L19 7.5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
    alert: '<circle cx="12" cy="12" r="8.5" stroke="currentColor" stroke-width="1.8"/><path d="M12 8V12.8" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/><circle cx="12" cy="16.2" r="1" fill="currentColor"/>',
    chevron: '<path d="M9 6.5L14.5 12L9 17.5" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/>',
    share: '<path d="M7.5 8.5H17.5L14.2 5.2M16.5 15.5H6.5L9.8 18.8" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
    trash: '<path d="M5 7H19M9.5 7V5A1.5 1.5 0 0 1 11 3.5H13A1.5 1.5 0 0 1 14.5 5V7M7.5 7L8.2 19A1.8 1.8 0 0 0 10 20.5H14A1.8 1.8 0 0 0 15.8 19L16.5 7" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"/>',
    file: '<path d="M7 3.8H13.8L18 8V19.2A1.8 1.8 0 0 1 16.2 21H7A1.8 1.8 0 0 1 5.2 19.2V5.6A1.8 1.8 0 0 1 7 3.8Z" stroke="currentColor" stroke-width="1.75" stroke-linejoin="round"/><path d="M13.5 4V8.2H17.8" stroke="currentColor" stroke-width="1.75" stroke-linejoin="round"/>'
  };

  function iconSvg(name, size, extraClass) {
    var sz = size || 16;
    var cls = "dsb-ico" + (extraClass ? " " + extraClass : "");
    return '<svg class="' + cls + '" width="' + sz + '" height="' + sz + '" viewBox="0 0 24 24" fill="none" aria-hidden="true">' + (ICON_PATHS[name] || ICON_PATHS.sparkles) + "</svg>";
  }
  function escapeHtml(str) {
    return String(str === null || str === undefined ? "" : str)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function formatLatexMathHtml(tex, isBlock) {
    var s = String(tex || "").trim();
    if (!s) return "";
    var symMap = {
      "\\alpha": "α", "\\beta": "β", "\\gamma": "γ", "\\delta": "δ", "\\epsilon": "ε", "\\theta": "θ", "\\lambda": "λ",
      "\\mu": "μ", "\\pi": "π", "\\sigma": "σ", "\\tau": "τ", "\\phi": "φ", "\\omega": "ω", "\\Delta": "Δ", "\\Sigma": "Σ", "\\Omega": "Ω",
      "\\infty": "∞", "\\sum": "∑", "\\prod": "∏", "\\int": "∫", "\\partial": "∂", "\\nabla": "∇", "\\pm": "±", "\\times": "×", "\\div": "÷", "\\cdot": "·",
      "\\leq": "≤", "\\le": "≤", "\\geq": "≥", "\\ge": "≥", "\\neq": "≠", "\\ne": "≠", "\\approx": "≈", "\\equiv": "≡",
      "\\rightarrow": "→", "\\to": "→", "\\leftarrow": "←", "\\Rightarrow": "⇒", "\\Leftrightarrow": "⇔", "\\in": "∈", "\\notin": "∉", "\\sqrt": "√"
    };
    s = s.replace(/\\left\s*([(\[{|])/g, "$1").replace(/\\right\s*([)\]}|])/g, "$1").replace(/\\(?:text|mathrm|mathbf|mathit|operatorname)\s*\{([^{}]*)\}/g, "$1");
    for (var k = 0; k < 3; k++) s = s.replace(/\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}/g, "($1)/($2)").replace(/\\sqrt\s*\{([^{}]*)\}/g, "√($1)");
    s = s.replace(/\\[a-zA-Z]+/g, function (cmd) { return Object.prototype.hasOwnProperty.call(symMap, cmd) ? symMap[cmd] : cmd.slice(1); });
    var escaped = escapeHtml(s)
      .replace(/\^\{([^{}]+)\}/g, "<sup>$1</sup>").replace(/\^([a-zA-Z0-9+\-])/g, "<sup>$1</sup>")
      .replace(/_\{([^{}]+)\}/g, "<sub>$1</sub>").replace(/_([a-zA-Z0-9+\-])/g, "<sub>$1</sub>");
    return isBlock ? '<div class="dsb-math-block">' + escaped + "</div>" : '<span class="dsb-math-inline">' + escaped + "</span>";
  }
  function resolveChatMediaUrl(rawUrl) {
    var u = String(rawUrl || "").trim();
    if (!u) return "";
    if (/^https?:\/\//i.test(u) || /^data:image\//i.test(u)) return u;
    var relPath = u;
    if (relPath.indexOf("/workspace-media/") !== 0 && relPath.indexOf("/stickers/") !== 0 && relPath.indexOf("/static/") !== 0 && relPath.indexOf("/api/files/") !== 0) {
      relPath = "/workspace-media/" + relPath.replace(/^workspace\//i, "").replace(/^\/+/, "");
    }
    return "http://127.0.0.1:18195/proxy-media?path=" + encodeURIComponent(relPath);
  }
  function renderInlineRichMarkdown(lineText) {
    var s = String(lineText || "");
    if (!s) return "";
    var saved = [];
    function stash(html) { var idx = saved.length; saved.push(html); return "\x01DSB" + idx + "E\x01"; }
    s = s.replace(/`([^`\n]+)`/g, function (_, c) { return stash('<code class="dsb-md-inline-code">' + escapeHtml(c) + "</code>"); });
    s = s.replace(/\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|!\[([^\]]*)\]\(([^)\s]+)\)|<image:([^\s>]+)\s*\/?>|\$([^\$\n]{1,160}?)\$/g, function (full, dm1, dm2, im1, imgAlt, imgU1, imgU2, im2) {
      if (dm1 !== undefined || dm2 !== undefined) return stash(formatLatexMathHtml(dm1 !== undefined ? dm1 : dm2, true));
      if (im1 !== undefined || im2 !== undefined) {
        var cand = im1 !== undefined ? im1 : im2;
        if (im2 !== undefined && /^\d+(?:[.,]\d+)?$/.test(cand.trim())) return full;
        return stash(formatLatexMathHtml(cand, false));
      }
      var targetImg = resolveChatMediaUrl(imgU1 || imgU2 || "");
      if (!targetImg) return "";
      return stash('<div class="dsb-inline-img-wrap"><img class="dsb-chat-img" data-lightbox-src="' + escapeHtml(targetImg) + '" src="' + escapeHtml(targetImg) + '" alt="' + escapeHtml(imgAlt || "图片") + '" loading="lazy"/></div>');
    });
    s = escapeHtml(s)
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
      .replace(/~~([^~]+)~~/g, "<del>$1</del>")
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer" class="dsb-md-link">$1</a>');
    return s.replace(/\x01DSB(\d+)E\x01/g, function (_, idx) { return saved[Number(idx)] || ""; });
  }
  function highlightCodeHtml(codeText) {
    var src = String(codeText || "");
    var tokRe = /(\/\/[^\n]*|#[^\n]*|\/\*[\s\S]*?\*\/)|("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)|\b(function|return|const|let|var|if|else|for|while|async|await|import|from|export|default|class|new|try|catch|finally|throw|def|elif|None|True|False|true|false|null|interface|type|struct|pub|fn)\b|\b(\d+(?:\.\d+)?)\b/g;
    var out = "", lastIdx = 0, m;
    while ((m = tokRe.exec(src)) !== null) {
      if (m.index > lastIdx) out += escapeHtml(src.slice(lastIdx, m.index));
      if (m[1]) out += '<span class="dsb-tok-com">' + escapeHtml(m[1]) + "</span>";
      else if (m[2]) out += '<span class="dsb-tok-str">' + escapeHtml(m[2]) + "</span>";
      else if (m[3]) out += '<span class="dsb-tok-kw">' + escapeHtml(m[3]) + "</span>";
      else if (m[4]) out += '<span class="dsb-tok-num">' + escapeHtml(m[4]) + "</span>";
      lastIdx = tokRe.lastIndex;
    }
    if (lastIdx < src.length) out += escapeHtml(src.slice(lastIdx));
    return out;
  }
  function renderRichMarkdown(rawText) {
    var text = String(rawText || "").replace(/\r\n/g, "\n").trim();
    if (!text) return "";
    var savedBlocks = [];
    function stashBlock(html) { var idx = savedBlocks.length; savedBlocks.push(html); return "\n\x02DSBB" + idx + "E\x02\n"; }
    text = text.replace(/```([a-zA-Z0-9_+-]*)[ \t]*\n([\s\S]*?)```/g, function (_, lang, body) {
      var cleanCode = body.replace(/\n$/, "");
      var langLabel = (lang || "text").trim().toLowerCase();
      return stashBlock(
        '<div class="dsb-code-card"><div class="dsb-code-head"><span class="dsb-code-lang">' + escapeHtml(langLabel) +
        '</span><button type="button" class="dsb-code-copy" data-copy-code="' + encodeURIComponent(cleanCode) + '">' + iconSvg("copy", 12) + '<span>复制</span></button></div>' +
        '<pre class="dsb-code-pre"><code>' + highlightCodeHtml(cleanCode) + "</code></pre></div>"
      );
    });
    text = text.replace(/\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]/g, function (_, m1, m2) {
      return stashBlock(formatLatexMathHtml(m1 !== undefined ? m1 : m2, true));
    });
    var lines = text.split("\n"), htmlParts = [], i = 0;
    while (i < lines.length) {
      var ln = lines[i], trimmed = ln.trim();
      if (!trimmed) { i++; continue; }
      var bm = trimmed.match(/^\x02DSBB(\d+)E\x02$/);
      if (bm) { htmlParts.push(savedBlocks[Number(bm[1])] || ""); i++; continue; }
      if (/^(?:---|\*\*\*|___)\s*$/.test(trimmed)) { htmlParts.push('<hr class="dsb-md-hr"/>'); i++; continue; }
      var hm = trimmed.match(/^(#{1,4})\s+(.+)$/);
      if (hm) { htmlParts.push('<div class="dsb-md-h dsb-md-h' + hm[1].length + '">' + renderInlineRichMarkdown(hm[2]) + "</div>"); i++; continue; }
      if (trimmed.indexOf("|") !== -1 && i + 1 < lines.length && /^\s*\|?[\s:-]+\|[\s|:-]*$/.test(lines[i + 1])) {
        var parseRow = function (r) { return r.trim().replace(/^\||\|$/g, "").split("|").map(function (c) { return c.trim(); }); };
        var headers = parseRow(trimmed); i += 2; var rows = [];
        while (i < lines.length && lines[i].trim().indexOf("|") !== -1 && !/^\x02DSBB\d+E\x02$/.test(lines[i].trim())) { rows.push(parseRow(lines[i])); i++; }
        htmlParts.push('<div class="dsb-md-table-wrap"><table class="dsb-md-table"><thead><tr>' +
          headers.map(function (h) { return "<th>" + renderInlineRichMarkdown(h) + "</th>"; }).join("") + "</tr></thead><tbody>" +
          rows.map(function (r) { return "<tr>" + headers.map(function (_, ci) { return "<td>" + renderInlineRichMarkdown(r[ci] || "") + "</td>"; }).join("") + "</tr>"; }).join("") +
          "</tbody></table></div>");
        continue;
      }
      if (/^>\s?/.test(trimmed)) {
        var qLines = [];
        while (i < lines.length && /^>\s?/.test(lines[i].trim())) { qLines.push(lines[i].trim().replace(/^>\s?/, "")); i++; }
        htmlParts.push('<blockquote class="dsb-md-quote">' + qLines.map(renderInlineRichMarkdown).join("<br/>") + "</blockquote>"); continue;
      }
      if (/^(?:[-*+]\s+|\d+\.\s+)/.test(trimmed)) {
        var ordered = /^\d+\.\s+/.test(trimmed), items = [];
        while (i < lines.length && (ordered ? /^\d+\.\s+/ : /^[-*+]\s+/).test(lines[i].trim())) {
          items.push("<li>" + renderInlineRichMarkdown(lines[i].trim().replace(/^(?:[-*+]|\d+\.)\s+/, "")) + "</li>"); i++;
        }
        htmlParts.push("<" + (ordered ? "ol" : "ul") + ' class="dsb-md-list">' + items.join("") + "</" + (ordered ? "ol" : "ul") + ">"); continue;
      }
      var pLines = [];
      while (i < lines.length) {
        var cur = lines[i], ct = cur.trim();
        if (!ct || /^\x02DSBB\d+E\x02$/.test(ct) || /^(?:#{1,4}\s+|>\s?|[-*+]\s+|\d+\.\s+|---|\*\*\*|___)/.test(ct)) break;
        if (ct.indexOf("|") !== -1 && i + 1 < lines.length && /^\s*\|?[\s:-]+\|[\s|:-]*$/.test(lines[i + 1])) break;
        pLines.push(renderInlineRichMarkdown(cur)); i++;
      }
      if (pLines.length) htmlParts.push('<p class="dsb-md-p">' + pLines.join("<br/>") + "</p>");
    }
    return htmlParts.join("");
  }

  function renderBotAvatarSvg(bot, size, whaleAvatarUrl) {
    var sz = size || 28;
    var shapeKey = bot && bot.shape ? String(bot.shape).toLowerCase() : (bot && bot.id === "whalechan" ? "whalechan" : "flower");
    if ((shapeKey === "whalechan" || (bot && bot.id === "whalechan")) && whaleAvatarUrl) {
      return '<img src="' + escapeHtml(whaleAvatarUrl) + '" width="' + sz + '" height="' + sz + '" style="width:' + sz + "px;height:" + sz + 'px;border-radius:30%;object-fit:cover;display:block;flex-shrink:0;" alt=""/>';
    }
    var d = PULSE_SHAPE_PATHS[shapeKey] || PULSE_SHAPE_PATHS.circle;
    var fill = (bot && bot.color) || "#0A84FF";
    var eye = '<circle cx="38" cy="49" r="5" fill="#FFFFFF"/><circle cx="62" cy="49" r="5" fill="#FFFFFF"/><circle cx="39" cy="49" r="2.3" fill="#18181B"/><circle cx="63" cy="49" r="2.3" fill="#18181B"/>';
    return '<svg width="' + sz + '" height="' + sz + '" viewBox="0 0 100 100" style="display:block;flex-shrink:0;" aria-hidden="true"><path d="' + d + '" fill="' + escapeHtml(fill) + '"/>' + eye + "</svg>";
  }
  function formatCompactTokens(n) {
    var v = Number(n || 0);
    if (v >= 1000000) return (v / 1000000).toFixed(1) + "M";
    if (v >= 1000) return (v / 1000).toFixed(1) + "k";
    return String(Math.max(0, Math.round(v)));
  }
  function friendlyToolLabel(name, args) {
    var raw = String(name || "").trim();
    if (!raw) return "执行工具";
    if (raw.indexOf("send_message →") === 0 || raw.indexOf("收到「") === 0 || raw.indexOf("已执行") === 0 || raw.indexOf("并行") === 0) return raw;
    var a = args && typeof args === "object" ? args : {};
    var map = {
      read_file: "读取文件", write_file: "写入文件", edit_file: "编辑文件", list_dir: "浏览目录",
      glob_files: "搜索文件", grep_search: "全文检索", run_bash: "终端命令", web_search: "联网搜索",
      web_fetch: "抓取网页", memory_search: "检索记忆", conversation_search: "历史检索", ctx_search: "上下文检索",
      memory_pin: "置顶记忆", blackboard: "共享黑板", peek_bots: "进度窥视", send_message: "协作传讯", send_message_callback: "协作回执",
      dsh_workspace: "桌面工作区", cloud_desktop: "云端桌面", phone_workspace: "手机工作台",
      manage_bot: "管理助理", schedule_task: "定时日程", complete_goal: "完成目标", stay_silent: "静默收尾"
    };
    var base = map[raw] || raw;
    var hint = a.to_bot_id || a.path || a.query || a.op || a.action || a.command || "";
    return hint ? (base + " · " + String(hint).replace(/\s+/g, " ").trim().slice(0, 26)) : base;
  }
  function renderToolCapsuleHtml(traceList, isRunning, liveToolLabel) {
    var arr = Array.isArray(traceList) ? traceList : [];
    if (!arr.length && !liveToolLabel) return "";
    var totalMs = 0, hasErr = false, anyRun = Boolean(isRunning);
    for (var i = 0; i < arr.length; i++) {
      totalMs += Number(arr[i] && arr[i].duration_ms ? arr[i].duration_ms : 0);
      if (arr[i] && (arr[i].status === "error" || (arr[i].result && arr[i].result.error))) hasErr = true;
      if (arr[i] && arr[i].status === "running") anyRun = true;
    }
    var lastItem = arr.length ? arr[arr.length - 1] : null;
    var lastLabel = liveToolLabel ? friendlyToolLabel(liveToolLabel, {}) : friendlyToolLabel(lastItem && lastItem.tool, lastItem && lastItem.arguments);
    var summaryTitle = arr.length <= 1 ? (anyRun ? "正在执行 · " + lastLabel : lastLabel) : ((anyRun ? "正在执行 " : "已执行 ") + arr.length + " 步 · " + lastLabel);
    var durText = anyRun ? "运行中" : (totalMs >= 1000 ? (totalMs / 1000).toFixed(1) + "s" : (totalMs > 0 ? totalMs + "ms" : ""));
    var dotCls = anyRun ? "is-running" : (hasErr ? "is-error" : "is-done");
    var rowsHtml = arr.map(function (t) {
      var st = String((t && t.status) || "done");
      var isErr = st === "error" || Boolean(t && t.result && t.result.error);
      var label = friendlyToolLabel(t && t.tool, t && t.arguments);
      var ms = Number((t && t.duration_ms) || 0);
      var msStr = st === "running" ? "…" : (ms >= 1000 ? (ms / 1000).toFixed(1) + "s" : (ms > 0 ? ms + "ms" : ""));
      var peerPreview = "";
      if (t && (t.tool === "send_message" || t.tool === "send_message_callback") && t.result && typeof t.result === "object") {
        var rep = String(t.result.reply || t.result.error || t.result.note || "").trim();
        if (rep) peerPreview = '<div class="dsb-tool-peer-note">' + escapeHtml(rep.slice(0, 220)) + (rep.length > 220 ? "…" : "") + "</div>";
      }
      return '<div class="dsb-tool-step' + (isErr ? " is-err" : "") + '">' +
        '<span class="dsb-tool-step-ico">' + (isErr ? iconSvg("alert", 13) : (st === "running" ? '<span class="dsb-mini-spin"></span>' : iconSvg("check", 13))) + "</span>" +
        '<div class="dsb-tool-step-main"><div class="dsb-tool-step-row"><span class="dsb-tool-step-name">' + escapeHtml(label) + '</span><span class="dsb-tool-step-ms">' + escapeHtml(msStr) + "</span></div>" + peerPreview + "</div></div>";
    }).join("");
    return '<details class="dsb-accordion dsb-tool-capsule"><summary class="dsb-tool-summary">' +
      '<span class="dsb-tool-dot ' + dotCls + '"></span><span class="dsb-tool-title">' + escapeHtml(summaryTitle) + "</span>" +
      (durText ? '<span class="dsb-tool-dur">' + escapeHtml(durText) + "</span>" : "") +
      (arr.length ? '<span class="dsb-acc-chev">' + iconSvg("chevron", 13) + "</span>" : "") +
      "</summary>" + (rowsHtml ? '<div class="dsb-tool-body">' + rowsHtml + "</div>" : "") + "</details>";
  }
  function renderThoughtPillHtml(rawThought, apiCalls, isStreaming) {
    var clean = String(rawThought || "").trim();
    if (!clean || clean.indexOf("正在生成 ") === 0 || clean.indexOf("正在准备调用 ") === 0) return "";
    var label = isStreaming ? "思考过程（实时推理中）" : (Number(apiCalls) > 1 ? "查看思考过程 (" + Number(apiCalls) + "轮)" : "查看思考过程");
    return '<details class="dsb-accordion dsb-cot-pill"' + (isStreaming ? " open" : "") + '><summary class="dsb-cot-summary">' +
      '<span class="dsb-cot-label">' + escapeHtml(label) + '</span><span class="dsb-acc-chev">' + iconSvg("chevron", 13) + "</span></summary>" +
      '<div class="dsb-cot-body"><div class="dsb-cot-bar"></div><div class="dsb-cot-text">' + escapeHtml(clean) + "</div></div></details>";
  }

  function buildStudioSheetHtml(state, activeBot, presetColors, shapeOpts) {
    var isCreate = state.studioMode === "create";
    var previewBot = { id: isCreate ? "new" : state.activeBotId, name: state.editBotName || "新助理", color: state.editBotColor, shape: state.editBotShape };
    var modelOpts = ['<option value="">跟随系统默认模型</option>'].concat(state.availableModels.map(function (m) {
      var mid = typeof m === "string" ? m : (m.id || "");
      return '<option value="' + escapeHtml(mid) + '"' + (mid === state.editBotModel ? " selected" : "") + ">" + escapeHtml(mid) + "</option>";
    })).join("");
    return '<div class="dsb-sheet-mask" data-close-sheet="mask"><div class="dsb-sheet">' +
      '<div class="dsb-sheet-head"><span>' + (isCreate ? "新建专属助理" : "助理工作坊 · " + escapeHtml(activeBot.name)) + '</span><button type="button" class="dsb-icon-btn" data-close-sheet="1">' + iconSvg("close", 16) + "</button></div>" +
      '<div class="dsb-sheet-body">' +
      '<div style="display:flex;align-items:center;gap:12px;padding:10px;border-radius:12px;background:var(--dsb-elevated);">' + renderBotAvatarSvg(previewBot, 48, state.whaleAvatarUrl) +
      '<div style="flex:1;min-width:0;"><div style="font-size:14px;font-weight:650;">' + escapeHtml(previewBot.name) + '</div><div style="font-size:11.5px;color:var(--dsb-muted);">独立对话房间 · 支持档案导出与恢复</div></div>' +
      '<div style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end;">' +
      '<button type="button" class="dsb-btn-sec" style="height:28px;font-size:11.5px;" data-import-bot="1" aria-label="导入助理档案文件">导入档案</button>' +
      (!isCreate ? '<button type="button" class="dsb-btn-sec" style="height:28px;font-size:11.5px;" data-export-bot="1" aria-label="导出当前助理档案">导出档案</button>' : "") +
      (!isCreate ? '<button type="button" class="dsb-btn-sec" style="height:28px;font-size:11.5px;" data-open-create-bot="1">+ 新建</button>' : "") + "</div></div>" +
      '<div><label class="dsb-field-lbl">名称</label><input id="dsb-st-name" class="dsb-inp" maxlength="32" value="' + escapeHtml(state.editBotName) + '" placeholder="例如：架构师 / 论文审稿人"/></div>' +
      '<div><label class="dsb-field-lbl">职能简介</label><input id="dsb-st-desc" class="dsb-inp" maxlength="120" value="' + escapeHtml(state.editBotDesc) + '" placeholder="一句话说明这位助理擅长什么"/></div>' +
      '<div><label class="dsb-field-lbl">专属模型</label><select id="dsb-st-model" class="dsb-sel">' + modelOpts + "</select></div>" +
      '<div><label class="dsb-field-lbl">代表色</label><div class="dsb-swatch-row">' + presetColors.map(function (c) {
        return '<button type="button" class="dsb-swatch' + (c.toLowerCase() === String(state.editBotColor).toLowerCase() ? " is-sel" : "") + '" style="background:' + c + ';" data-pick-color="' + c + '"></button>';
      }).join("") + "</div></div>" +
      '<div><label class="dsb-field-lbl">灵体形态</label><div class="dsb-shape-grid">' + shapeOpts.map(function (sh) {
        return '<button type="button" class="dsb-shape-btn' + (sh.id === state.editBotShape ? " is-sel" : "") + '" data-pick-shape="' + sh.id + '">' + renderBotAvatarSvg({ color: state.editBotColor, shape: sh.id }, 20, state.whaleAvatarUrl) + "<span>" + sh.label + "</span></button>";
      }).join("") + "</div></div>" +
      '<div><label class="dsb-field-lbl">系统设定 / 人格指令 (System Persona)</label><textarea id="dsb-st-persona" class="dsb-ta" rows="5" placeholder="定义它的角色职责、说话风格与工具边界…">' + escapeHtml(state.editBotPersona) + "</textarea></div>" +
      '<label style="display:flex;align-items:center;gap:8px;font-size:12.5px;cursor:pointer;"><input id="dsb-st-curiosity" type="checkbox"' + (state.editBotCuriosity ? " checked" : "") + "/>允许空闲时主动巡检关怀</label>" +
      '</div><div class="dsb-sheet-foot">' +
      (!isCreate && !activeBot.primary && state.activeBotId !== "pc_engineer" ? '<button type="button" class="dsb-btn-danger" data-delete-bot="1">' + iconSvg("trash", 14) + "<span>删除助理</span></button>" : "<span></span>") +
      '<div style="display:flex;gap:8px;"><button type="button" class="dsb-btn-sec" data-close-sheet="1">取消</button><button type="button" class="dsb-btn-pri" data-save-bot="1">' + (isCreate ? "创建助理" : "保存设定") + "</button></div></div></div></div>";
  }

  function buildVaultSheetHtml(state) {
    var pins = Array.isArray(state.vaultPinned) ? state.vaultPinned : [];
    var files = state.vaultFiles.map(function (f) { return typeof f === "string" ? f : (f.path || ""); }).filter(Boolean);
    var tabBody = state.vaultTab === "pins"
      ? ('<div style="display:flex;gap:6px;"><input id="dsb-new-pin-inp" class="dsb-inp" placeholder="输入需要跨端永久记住的偏好或铁律…"/><button type="button" class="dsb-btn-pri" data-add-pin="1">钉住</button></div>' +
        '<div style="display:flex;flex-direction:column;gap:6px;">' + (pins.length ? pins.map(function (p) {
          return '<div class="dsb-vault-file" style="cursor:default;"><span style="word-break:break-word;padding-right:8px;">' + escapeHtml(p.text || "") + '</span><button type="button" class="dsb-icon-btn" data-unpin-id="' + escapeHtml(p.id || p.text) + '">' + iconSvg("trash", 14) + "</button></div>";
        }).join("") : '<div style="color:var(--dsb-faint);font-size:12.5px;text-align:center;padding:24px 0;">暂无钉住的核心记忆</div>') + "</div>")
      : ('<div style="max-height:150px;overflow-y:auto;display:flex;flex-direction:column;gap:4px;">' + (files.map(function (fp) {
          var clean = fp.replace(/^memory_vault\//, "");
          return '<button type="button" class="dsb-vault-file' + (clean === state.vaultSelectedPath ? " is-sel" : "") + '" data-vault-path="' + escapeHtml(clean) + '"><span>' + escapeHtml(clean) + "</span>" + iconSvg("chevron", 12) + "</button>";
        }).join("") || '<div style="font-size:12px;color:var(--dsb-faint);">正在加载记忆档案…</div>') + "</div>" +
        (state.vaultSelectedPath ? ('<div><label class="dsb-field-lbl">' + escapeHtml(state.vaultSelectedPath) + '</label><textarea id="dsb-vault-editor" class="dsb-ta" rows="11">' + escapeHtml(state.vaultSelectedContent) + "</textarea></div>") : ""));
    return '<div class="dsb-sheet-mask" data-close-sheet="mask"><div class="dsb-sheet">' +
      '<div class="dsb-sheet-head"><span>记忆金库与置顶规则</span><button type="button" class="dsb-icon-btn" data-close-sheet="1">' + iconSvg("close", 16) + "</button></div>" +
      '<div class="dsb-sheet-body"><div class="dsb-vault-tabs">' +
      '<button type="button" class="dsb-filter-chip' + (state.vaultTab === "notes" ? " is-active" : "") + '" data-vault-tab="notes">Markdown 档案 (' + files.length + ")</button>" +
      '<button type="button" class="dsb-filter-chip' + (state.vaultTab === "pins" ? " is-active" : "") + '" data-vault-tab="pins">置顶记忆 (' + pins.length + ")</button></div>" +
      tabBody + '</div><div class="dsb-sheet-foot"><span style="font-size:11.5px;color:var(--dsb-muted);">' + escapeHtml(state.vaultStatusMsg) + '</span>' +
      (state.vaultTab === "notes" && state.vaultSelectedPath ? '<button type="button" class="dsb-btn-pri" data-save-vault="1">保存文档</button>' : '<button type="button" class="dsb-btn-sec" data-close-sheet="1">完成</button>') +
      "</div></div></div>";
  }

  function buildMessageRowHtml(m, parsedUser) {
    if (m.kind === "compact_summary") {
      var meta = {};
      try { meta = JSON.parse(m.decision || "{}"); } catch (e) {}
      var bTok = Number(meta.before_tokens || 0), aTok = Number(meta.after_tokens || 0);
      return '<div class="dsb-compact-card"><div class="dsb-compact-top"><span class="dsb-compact-tag">' + escapeHtml(meta.mode || "上下文浓缩归档") + '</span><span style="color:var(--dsb-faint);">' +
        (bTok > 0 ? formatCompactTokens(bTok) + " → " + formatCompactTokens(aTok) + " · " : "") + escapeHtml(String(m.time || "").slice(0, 5)) + "</span></div>" +
        '<details class="dsb-accordion" style="border:none;background:transparent;"><summary style="font-size:12px;color:var(--dsb-accent);font-weight:500;">展开查看浓缩摘要正文</summary>' +
        '<div style="padding-top:6px;font-size:13px;">' + renderRichMarkdown(m.text) + "</div></details></div>";
    }
    var isInboundCollab = m.kind === "bot_message";
    var isCollabReply = m.kind === "bot_reply";
    var isUser = m.role === "user" && !isInboundCollab;
    var timeLabel = String(m.time || "").slice(0, 5);
    if (isUser) {
      var parsed = parsedUser || { quote: "", body: String(m.text || ""), files: [] };
      var attHtml = parsed.files.length ? ('<div class="dsb-att-chips">' + parsed.files.map(function (fp) {
        var bn = fp.split("/").pop();
        if (/\.(?:png|jpe?g|webp|gif|bmp)$/i.test(bn)) {
          var u = resolveChatMediaUrl(fp);
          return '<img class="dsb-chat-img" data-lightbox-src="' + escapeHtml(u) + '" src="' + escapeHtml(u) + '" alt="' + escapeHtml(bn) + '"/>';
        }
        return '<span class="dsb-att-chip">' + iconSvg("file", 13) + "<span>" + escapeHtml(bn) + "</span></span>";
      }).join("") + "</div>") : "";
      return '<div class="dsb-msg-row is-user" data-msg-id="' + escapeHtml(m.id) + '">' +
        '<div class="dsb-bubble" data-dbl-reply="' + escapeHtml(m.id) + '">' +
        (parsed.quote ? '<div class="dsb-quote-box">' + escapeHtml(parsed.quote) + "</div>" : "") + attHtml +
        (parsed.body ? renderRichMarkdown(parsed.body) : "") +
        '<div class="dsb-bubble-foot"><span>' + escapeHtml(timeLabel) + "</span></div></div>" +
        '<div class="dsb-msg-actions">' +
        '<button type="button" class="dsb-act-btn" data-act="copy" data-mid="' + escapeHtml(m.id) + '">' + iconSvg("copy", 12) + "<span>复制</span></button>" +
        '<button type="button" class="dsb-act-btn" data-act="reply" data-mid="' + escapeHtml(m.id) + '">' + iconSvg("reply", 12) + "<span>引用</span></button>" +
        '<button type="button" class="dsb-act-btn" data-act="edit" data-mid="' + escapeHtml(m.id) + '">' + iconSvg("edit", 12) + "<span>编辑</span></button>" +
        '<button type="button" class="dsb-act-btn" data-act="reroll" data-mid="' + escapeHtml(m.id) + '">' + iconSvg("refresh", 12) + "<span>重发</span></button>" +
        "</div></div>";
    }
    var traceArr = isInboundCollab ? [] : (Array.isArray(m.tool_trace) ? m.tool_trace : []);
    var thoughtStr = isInboundCollab ? "" : String(m.thought || "");
    var collabBanner = isInboundCollab
      ? ('<div class="dsb-collab-head">' + iconSvg("share", 13) + "<span>来自「" + escapeHtml(m.from_bot_name || m.from_bot_id || "同事助理") + "」的协作委托</span></div>")
      : (isCollabReply && m.to_bot_name ? ('<div class="dsb-collab-head">' + iconSvg("share", 13) + "<span>回传给「" + escapeHtml(m.to_bot_name) + "」的协作结论</span></div>") : "");
    var stickerHtml = m.sticker ? ('<img class="dsb-chat-img" data-lightbox-src="' + escapeHtml(resolveChatMediaUrl(m.sticker)) + '" src="' + escapeHtml(resolveChatMediaUrl(m.sticker)) + '" alt="图像"/>') : "";
    var perfParts = [];
    if (Number(m.cache_hit_pct) > 0) perfParts.push("缓存" + Math.round(Number(m.cache_hit_pct)) + "%");
    if (Number(m.ttft_ms) > 0) perfParts.push("首字" + Math.round(Number(m.ttft_ms)) + "ms");
    if (Number(m.tps) > 0) perfParts.push(Number(m.tps).toFixed(0) + "t/s");
    if (timeLabel) perfParts.push(timeLabel);
    var choicesHtml = Array.isArray(m.choices) && m.choices.length ? ('<div class="dsb-choices-wrap">' + m.choices.map(function (c) {
      var lbl = (c && (c.label || c.text || c)) || "", val = (c && (c.value || c.label || c)) || "";
      return '<button type="button" class="dsb-choice-btn" data-send-choice="' + escapeHtml(val) + '">' + escapeHtml(lbl) + "</button>";
    }).join("") + "</div>") : "";
    return '<div class="dsb-msg-row is-ai' + (isInboundCollab || isCollabReply ? " is-collab" : "") + (m.kind === "error" ? " is-err" : "") + '" data-msg-id="' + escapeHtml(m.id) + '">' +
      renderToolCapsuleHtml(traceArr, false, "") + renderThoughtPillHtml(thoughtStr, m.api_calls, false) + stickerHtml +
      (m.text ? ('<div class="dsb-bubble" data-dbl-reply="' + escapeHtml(m.id) + '">' + collabBanner + renderRichMarkdown(m.text) +
        (perfParts.length ? '<div class="dsb-bubble-foot"><span>' + escapeHtml(perfParts.join(" · ")) + "</span></div>" : "") + "</div>") : "") +
      choicesHtml + (!isInboundCollab ? ('<div class="dsb-msg-actions">' +
        '<button type="button" class="dsb-act-btn" data-act="copy" data-mid="' + escapeHtml(m.id) + '">' + iconSvg("copy", 12) + "<span>复制</span></button>" +
        '<button type="button" class="dsb-act-btn" data-act="reply" data-mid="' + escapeHtml(m.id) + '">' + iconSvg("reply", 12) + "<span>引用</span></button>" +
        '<button type="button" class="dsb-act-btn" data-act="reroll" data-mid="' + escapeHtml(m.id) + '">' + iconSvg("refresh", 12) + "<span>重答</span></button>" +
        '<button type="button" class="dsb-act-btn" data-act="pin" data-mid="' + escapeHtml(m.id) + '">' + iconSvg("pin", 12) + "<span>钉住</span></button></div>") : "") + "</div>";
  }

  function exportBotArchive(apiCall, botId, showToast) {
    showToast("正在打包助理档案…");
    apiCall("/local/bots/export", "POST", { bot_id: botId, include_messages: true }).then(function (r) {
      if (!r || !r.ok || !r.bundle) { showToast((r && r.error) || "导出失败"); return; }
      var fn = String(r.filename || (botId + ".dsb-bot.json"));
      var blob = new Blob([JSON.stringify(r.bundle, null, 2)], { type: "application/json;charset=utf-8" });
      var url = URL.createObjectURL(blob);
      var a = document.createElement("a");
      a.href = url; a.download = fn; document.body.appendChild(a); a.click();
      setTimeout(function () { if (a.parentNode) a.parentNode.removeChild(a); URL.revokeObjectURL(url); }, 400);
      showToast("已导出档案：" + fn);
    });
  }

  function pickAndImportBotArchive(apiCall, onDone, showToast) {
    var inp = document.createElement("input");
    inp.type = "file"; inp.accept = ".json,.dsb-bot.json,application/json"; inp.style.display = "none";
    inp.addEventListener("change", function () {
      var f = inp.files && inp.files[0];
      if (inp.parentNode) inp.parentNode.removeChild(inp);
      if (!f) return;
      var reader = new FileReader();
      reader.onload = function () {
        var parsed = null;
        try { parsed = JSON.parse(String(reader.result || "")); } catch (e) { showToast("档案文件不是合法的 JSON"); return; }
        showToast("正在导入助理档案…");
        apiCall("/local/bots/import", "POST", { bundle: parsed, mode: "merge", import_messages: true }).then(function (r) {
          if (!r || !r.ok) { showToast((r && r.error) || "导入失败"); return; }
          if (typeof onDone === "function") onDone(r);
          showToast("已导入助理「" + ((r.bot && r.bot.name) || r.bot_id || "") + "」");
        });
      };
      reader.readAsText(f, "utf-8");
    });
    document.body.appendChild(inp);
    inp.click();
  }

  window.__DSB_RENDER__ = {
    PULSE_SHAPE_PATHS: PULSE_SHAPE_PATHS,
    iconSvg: iconSvg,
    escapeHtml: escapeHtml,
    resolveChatMediaUrl: resolveChatMediaUrl,
    renderRichMarkdown: renderRichMarkdown,
    renderBotAvatarSvg: renderBotAvatarSvg,
    formatCompactTokens: formatCompactTokens,
    friendlyToolLabel: friendlyToolLabel,
    renderToolCapsuleHtml: renderToolCapsuleHtml,
    renderThoughtPillHtml: renderThoughtPillHtml,
    buildStudioSheetHtml: buildStudioSheetHtml,
    buildVaultSheetHtml: buildVaultSheetHtml,
    buildMessageRowHtml: buildMessageRowHtml,
    exportBotArchive: exportBotArchive,
    pickAndImportBotArchive: pickAndImportBotArchive
  };
})();
