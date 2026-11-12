/**
 * [Module Responsibility]
 * DSH 会话全景扫描、跨会话调度与宿主机控制模块 (`lib-dsh-sessions.mjs`)。
 * 负责：
 * 1. DSH 本地 `.proj_cache` 会话历史解析、全景摘要 (`getDshPanorama`)、全文检索 (`searchDshSessions`) 与详情查看 (`inspectDshSessionDetail`)。
 * 2. 跨进程 Host IPC 代理 (`proxyToDshHostIpc`)、DSH 桌面端唤醒 (`launchDshDesktopApp`) 与跨会话指令派发 (`dispatchToDshSession`)。
 * 3. 受控的本机 PowerShell 命令执行 (`runLocalPcShellCommand`) 与本地文件系统结构化操作 (`runLocalPcFsOperation`)。
 * 4. 向 DSH ToolRegistry 注册全套跨端协同工具 (`registerAllDshTools`)。
 *
 * [Public Interface]
 * - `getDshPanorama(context, allowIpcProxy)`, `searchDshSessions(context, query, limit)`, `inspectDshSessionDetail(context, sessionId, maxTurns)`
 * - `dispatchToDshSession(context, opts)`, `proxyToDshHostIpc(action, payload, timeoutMs)`, `isDshHostIpcAlive()`, `launchDshDesktopApp()`
 * - `runLocalPcShellCommand(command, cwd, timeoutSec)`, `runLocalPcFsOperation(payload)`
 * - `registerAllDshTools(toolRegistry, context)`
 *
 * [Dependencies]
 * - Node.js 标准库: `node:fs`, `node:path`, `node:os`, `node:http`, `node:child_process`
 * - `./lib-chat-cache.mjs`: `HOSTNAME`, `PC_DEVICE_ID`, `PC_DEVICE_NAME`, `DSH_HOST_IPC_PORT`, `runtimeState`, `getService`, `findBotByIdOrName`, `executeCloudChatTurn`
 */
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import http from "node:http";
import crypto from "node:crypto";
import { spawn, execFile } from "node:child_process";
import { callCuaTool } from "./cua-bridge.mjs";
import {
  HOSTNAME,
  PC_DEVICE_ID,
  PC_DEVICE_NAME,
  DSH_HOST_IPC_PORT,
  runtimeState,
  loadLocalConfig,
  getService,
  findBotByIdOrName,
  cloudFetch,
  executeCloudChatTurn,
} from "./lib-chat-cache.mjs";

export function formatAgo(tsMs) {
  if (!tsMs) return "未知";
  const diffSec = Math.max(0, Math.floor((Date.now() - Number(tsMs)) / 1000));
  if (diffSec < 60) return diffSec < 15 ? "刚刚" : `${diffSec}秒前`;
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}分钟前`;
  const diffHr = Math.floor(diffMin / 60);
  return diffHr < 24 ? `${diffHr}小时前` : `${Math.floor(diffHr / 24)}天前`;
}
export function parseProjCacheSession(sid, fullPath, liveAgent, now) {
  const stat = fs.statSync(fullPath); const raw = JSON.parse(fs.readFileSync(fullPath, "utf8"));
  const rec = raw?.record || {}; const rows = rec?.rows || {};
  const rawTurns = Array.isArray(rows?.turnOutline?.val?.turns) ? rows.turnOutline.val.turns : [];
  const turns = rawTurns.map((t, idx) => ({
    turn: Number(t?.turn ?? idx + 1), seq: Number(t?.seq ?? 0), prompt: String(t?.prompt || "").trim(),
    response: String(t?.response || "").trim(),
  }));
  const isBlank = Boolean(rows?.sessionListMetadata?.val?.blank);
  const rawTitle = String(rows?.title?.val || rows?.titleInput?.val?.first?.text || turns[0]?.prompt?.slice(0, 48) || "").trim();
  if ((isBlank && !turns.length) || (!rawTitle && !turns.length) || (!turns.length && !liveAgent)) return null;
  if (["主助理", "DSB 主助理", "研究助手", "杠精", "鲸鱼娘", "司工", "探微", "审校", "云汐"].includes(rawTitle) && (!turns.length || !turns[0]?.prompt)) return null;
  const rawStats = rows?.sessionStats?.val || {}; const totals = rows?.tokenUsage?.val?.totals || {};
  const tokenUsage = {
    uncachedInputTokens: Number(totals.uncachedInputTokens || 0), outputTokens: Number(totals.outputTokens || 0),
    cacheReadTokens: Number(totals.cacheReadTokens || 0), cacheWriteTokens: Number(totals.cacheWriteTokens || 0),
  };
  tokenUsage.totalTokens = tokenUsage.uncachedInputTokens + tokenUsage.outputTokens + tokenUsage.cacheReadTokens + tokenUsage.cacheWriteTokens;
  const sessionStats = {
    turns: Number(rawStats.turns || turns.length || 0), steps: Number(rawStats.steps || 0),
    pendingCalls: rawStats.pendingCalls || {},
  };
  const pendingTools = Object.values(sessionStats.pendingCalls).map((c) => c?.name || c?.tool || "tool").filter(Boolean);
  const boundary = rows?.turnBoundary?.val || {}; let status = "idle";
  if (liveAgent?.status && liveAgent.status !== "idle") status = "running";
  else if (boundary.openTurnStartSeq != null && now - stat.mtimeMs < 600000) status = "running";
  const subCatalog = rows?.subagentCatalog?.val?.head?.values || [];
  const teamMembers = rows?.agentTeam?.val?.members || []; const subById = new Map();
  for (const c of [...subCatalog, ...teamMembers]) {
    const cid = String(c?.childId || c?.id || "").trim();
    if (!cid) continue;
    subById.set(cid, {
      id: cid.slice(0, 8), full_id: cid, name: String(c?.name || c?.label || "子代理").slice(0, 48),
      task: String(c?.description || c?.label || "").slice(0, 180), phase: String(c?.phase || "active"),
    });
  }
  const subagents = Array.from(subById.values()); const goalObj = rows?.goal?.val?.current?.goal || null;
  const goal = goalObj ? String(goalObj.objective || "").trim() : "";
  const cwd = rec?.identity?.cwd || liveAgent?.session?.meta?.cwd || "";
  const lastTurn = turns[turns.length - 1] || {};
  const lastPrompt = String(lastTurn.prompt || rows?.titleInput?.val?.first?.text || "").trim();
  const lastResponse = String(lastTurn.response || "").trim();
  const updatedAt = Number(rows?.sessionListMetadata?.val?.lastPromptAt || stat.mtimeMs || rec?.identity?.createdAt || 0);
  const summary = {
    id: sid, short_id: sid.replace(/^session-/, "").slice(0, 8), device_id: runtimeState.deviceId,
    device_name: runtimeState.deviceName, device_type: "pc", title: String(rawTitle || "未命名会话").slice(0, 90),
    workspace: cwd, workspace_name: cwd ? path.basename(cwd) : "default-workspace",
    status,
    live: Boolean(liveAgent), turns_count: sessionStats.turns, steps_count: sessionStats.steps,
    subagents_count: subagents.length,
    subagents,
    pending_tools: pendingTools,
    goal,
    tokenUsage,
    sessionStats,
    activity_summary: status === "running" ? `正在运行 (${sessionStats.steps} 步)` : `已完成 ${sessionStats.turns} 轮`,
    last_prompt: lastPrompt.slice(0, 240), last_response: lastResponse.slice(0, 320), updated_at: updatedAt,
    updated_ago: formatAgo(updatedAt),
  };
  runtimeState.sessionDetailById.set(sid, { session: summary, turns, goal: goalObj, subagents, tokenUsage, sessionStats });
  return summary;
}
export function getDshPanorama(ctx = null, force = false) {
  const now = Date.now();
  if (!force && runtimeState.cachedPanorama && now - runtimeState.cachedPanoramaAt < 1200) {
    return runtimeState.cachedPanorama;
  }
  const cfg = loadLocalConfig();
  const projDir = path.join(cfg.dsh_home || path.join(os.homedir(), ".dsh"), "storages", "session_projcache", "sessions");
  const liveAgentsBySid = new Map();
  try {
    for (const agent of getService(ctx || runtimeState.activeCtx, "agents")?.roots?.() || []) {
      if (agent?.session?.id) liveAgentsBySid.set(agent.session.id, agent);
    }
  } catch {}
  const sessions = []; let subagentsTotalCount = 0;
  let runningCount = 0;
  try {
    if (fs.existsSync(projDir)) {
      for (const f of fs.readdirSync(projDir)) {
        if (!f.endsWith(".json")) continue;
        const sid = f.replace(/\.json$/, "");
        if (!sid.startsWith("session-")) { subagentsTotalCount++; continue; }
        if (sid.startsWith("session-bot-root-") || sid.startsWith("session-dsb-permanent-")) continue;
        try {
          const item = parseProjCacheSession(sid, path.join(projDir, f), liveAgentsBySid.get(sid), now);
          if (item) {
            if (item.status === "running") runningCount++;
            sessions.push(item);
          }
        } catch {}
      }
    }
  } catch {}
  sessions.sort((a, b) => {
    if (a.status === "running" && b.status !== "running") return -1;
    if (a.status !== "running" && b.status === "running") return 1;
    return Number(b.updated_at || 0) - Number(a.updated_at || 0);
  });
  const panorama = {
    ok: true, pc_online: true, device_id: runtimeState.deviceId,
    device_name: runtimeState.deviceName, device_type: "pc", sentinel: runtimeState.bridgeId,
    sessions_count: sessions.length, running_count: runningCount, live_in_memory_count: liveAgentsBySid.size,
    subagents_total_count: subagentsTotalCount, sessions: sessions.slice(0, 100),
    last_3_dialogs: sessions.filter((s) => s.last_prompt || s.last_response).slice(0, 3).map((s) => ({
      session_id: s.short_id, device_name: runtimeState.deviceName, time: s.updated_ago,
      user: `[${s.title}] ${s.last_prompt}`,
      assistant: s.last_response || s.activity_summary,
    })),
    updated_at: now,
  };
  runtimeState.cachedPanorama = panorama;
  runtimeState.cachedPanoramaAt = now;
  return panorama;
}
export function searchDshSessions(ctx, query = "", statusFilter = "all") {
  const panorama = getDshPanorama(ctx, false); const q = String(query || "").trim().toLowerCase();
  const terms = q.split(/\s+/).filter(Boolean); const sf = String(statusFilter || "all").toLowerCase();
  const matched = (panorama.sessions || []).filter((s) => {
    if (sf === "running" && s.status !== "running") return false;
    if (sf === "subagents" && !(s.subagents_count > 0)) return false;
    if (!terms.length) return true;
    const turns = runtimeState.sessionDetailById.get(s.id)?.turns || [];
    const hay = [
      s.title, s.id, s.short_id, s.workspace_name, s.goal,
      ...turns.map((t) => `${t.prompt} ${t.response}`),
    ].join(" ").toLowerCase();
    return terms.every((term) => hay.includes(term));
  });
  return { ok: true, query: q, total_matched: matched.length, sessions: matched };
}
function resolveDshSessionFromPanorama(panorama, rawQuery) {
  const sessions = Array.isArray(panorama?.sessions) ? panorama.sessions : [];
  const qRaw = String(rawQuery || "").trim(); const q = qRaw.toLowerCase();
  const qClean = q.replace(/^session-/, "");
  if (!q || q === "latest" || q === "new" || q === "create") {
    const first = sessions[0] || null;
    return first
      ? { ok: true, session: first }
      : { ok: false, error: "未找到任何本机 DeepSeek Harness 会话" };
  }
  const exact = sessions.find((s) => {
    const sid = String(s.id || "").toLowerCase(); const sidClean = sid.replace(/^session-/, "");
    const shortId = String(s.short_id || "").toLowerCase(); const title = String(s.title || "").toLowerCase();
    return sid === q || sidClean === qClean || shortId === qClean || title === q;
  });
  if (exact) return { ok: true, session: exact };
  const prefixOrTitle = sessions.find((s) => {
    const sid = String(s.id || "").toLowerCase(); const sidClean = sid.replace(/^session-/, "");
    const shortId = String(s.short_id || "").toLowerCase(); const title = String(s.title || "").toLowerCase();
    return (
      sid.startsWith(q) ||
      (qClean.length >= 4 && sidClean.startsWith(qClean)) ||
      (qClean.length >= 4 && shortId.startsWith(qClean)) ||
      title.includes(q)
    );
  });
  if (prefixOrTitle) return { ok: true, session: prefixOrTitle };
  return {
    ok: false,
    error: `未找到匹配的本机 DSH 会话: "${qRaw}"（请使用 list_sessions 中的 short_id、完整 session_id 或标题，或传 "latest"）`,
    available_sessions: sessions.slice(0, 12).map((s) => ({
      id: s.id, short_id: s.short_id, title: s.title,
    })),
  };
}
export function extractTurnsFromLiveEvents(events = []) {
  const turns = []; let curTurn = null;
  for (const ev of events) {
    if (!ev || typeof ev !== "object") continue;
    if (ev.type === "user/message") {
      const txt = (ev.data?.message?.content || [])
        .filter((b) => b?.type === "text")
        .map((b) => b.text)
        .join("")
        .trim();
      if (txt) {
        if (curTurn) turns.push(curTurn);
        curTurn = {
          turn: turns.length + 1, seq: Number(ev.seq ?? 0), prompt: txt,
          response: "",
        };
      }
    } else if (ev.type === "assistant/message") {
      const txt = (ev.data?.message?.content || [])
        .filter((b) => b?.type === "text")
        .map((b) => b.text)
        .join("")
        .trim();
      if (txt) {
        if (!curTurn) {
          curTurn = {
            turn: turns.length + 1, seq: Number(ev.seq ?? 0), prompt: "",
            response: txt,
          };
        } else { curTurn.response = txt; }
      }
    }
  }
  if (curTurn) turns.push(curTurn);
  return turns;
}
export async function inspectDshSessionDetail(ctx, sessionIdOrQuery) {
  const targetCtx = ctx || runtimeState.activeCtx;
  if (!targetCtx && (await isDshHostIpcAlive())) {
    const proxied = await proxyToDshHostIpc("/local/infinite-chat/inspect_session", { session_id: sessionIdOrQuery }, 15000);
    if (proxied && typeof proxied === "object") return proxied;
  }
  const panorama = getDshPanorama(targetCtx, true);
  const resolved = resolveDshSessionFromPanorama(panorama, sessionIdOrQuery);
  if (!resolved.ok) return resolved;
  const matched = resolved.session;
  const detail = runtimeState.sessionDetailById.get(matched.id) || { session: { ...matched }, turns: [] };
  let turns = Array.isArray(detail.turns) ? [...detail.turns] : []; let liveStatus = matched.status;
  let recentEvents = [];
  if (targetCtx) {
    try {
      const agentsSvc = getService(targetCtx, "agents"); const sessionsSvc = getService(targetCtx, "sessions");
      const sessionController = getService(targetCtx, "sessionController");
      const liveAgent = agentsSvc?.get?.(matched.id);
      if (liveAgent?.status) { liveStatus = liveAgent.status === "idle" ? "idle" : "running"; }
      let liveEvents = null; const memSession = liveAgent?.session || sessionsSvc?.get?.(matched.id);
      if (memSession && typeof memSession.snapshotEvents === "function") {
        liveEvents = memSession.snapshotEvents();
      } else if (sessionController && typeof sessionController.inspect === "function") {
        const inspected = await sessionController.inspect(matched.id, AbortSignal.timeout(8000));
        if (Array.isArray(inspected?.events)) liveEvents = inspected.events;
      } else if (sessionController?.commands && typeof sessionController.commands.readSessionState === "function") {
        const state = await sessionController.commands.readSessionState(matched.id);
        if (Array.isArray(state?.events)) liveEvents = state.events;
      }
      if (Array.isArray(liveEvents) && liveEvents.length > 0) {
        recentEvents = liveEvents.slice(-12).map((ev) => ({
          seq: ev?.seq, type: ev?.type, data: ev?.data,
        }));
        const liveTurns = extractTurnsFromLiveEvents(liveEvents);
        if (liveTurns.length >= turns.length) { turns = liveTurns; }
      }
    } catch {}
  }
  const lastTurn = turns[turns.length - 1] || {};
  const enrichedSession = {
    ...detail.session,
    status: liveStatus, turns_count: Math.max(Number(detail.session?.turns_count || 0), turns.length),
    last_prompt: String(lastTurn.prompt || detail.session?.last_prompt || "").slice(0, 240),
    last_response: String(lastTurn.response || detail.session?.last_response || "").slice(0, 320),
  };
  return {
    ok: true,
    ...detail,
    session: enrichedSession,
    turns,
    turns_count: turns.length, last_error: runtimeState.lastAgentErrorBySid.get(matched.id) || null,
    recent_events: recentEvents,
  };
}
export function deepFreeze(obj) {
  if (obj && typeof obj === "object" && !Object.isFrozen(obj)) {
    Object.freeze(obj);
    for (const k of Reflect.ownKeys(obj)) deepFreeze(obj[k]);
  }
  return obj;
}
export async function proxyToDshHostIpc(localPath, body = {}, timeoutMs = 90000) {
  try {
    const res = await fetch(`http://127.0.0.1:${DSH_HOST_IPC_PORT}/api/dsh-deepseek-claw`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ localPath, method: "POST", body }),
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (!res.ok) return null;
    return await res.json();
  } catch { return null; }
}
export async function isDshHostIpcAlive() {
  try {
    const res = await fetch(`http://127.0.0.1:${DSH_HOST_IPC_PORT}/health`, {
      method: "GET", signal: AbortSignal.timeout(1200),
    });
    return res.ok;
  } catch { return false; }
}
export async function launchDshDesktopApp(waitForHostIpc = false, maxWaitMs = 25000) {
  const exeCandidates = [
    "D:\\dsh\\DeepSeek Harness.exe",
    path.join(process.env.LOCALAPPDATA || "", "Programs", "DeepSeek Harness", "DeepSeek Harness.exe"),
  ];
  const batCandidate = "D:\\DeepSeek-Harness\\Start-DeepSeek-Harness.bat"; let launchedVia = "";
  if (await isDshHostIpcAlive()) {
    execFile(
      "powershell.exe",
      [
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "$p = Get-Process 'DeepSeek Harness' -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1; if ($p) { (New-Object -ComObject WScript.Shell).AppActivate($p.Id) | Out-Null }",
      ],
      { windowsHide: true },
      () => {}
    );
    return { ok: true, already_running: true, host_ipc_ready: true, message: "DeepSeek Harness 已在运行并已唤醒窗口" };
  }
  for (const exePath of exeCandidates) {
    if (exePath && fs.existsSync(exePath)) {
      try {
        const child = spawn(exePath, [], {
          detached: true, stdio: "ignore", cwd: path.dirname(exePath),
          windowsHide: false,
        });
        child.unref();
        launchedVia = exePath;
        break;
      } catch {}
    }
  }
  if (!launchedVia && fs.existsSync(batCandidate)) {
    try {
      const child = spawn("cmd.exe", ["/c", batCandidate], {
        detached: true, stdio: "ignore", cwd: path.dirname(batCandidate),
        windowsHide: true,
      });
      child.unref();
      launchedVia = batCandidate;
    } catch {}
  }
  if (!launchedVia) {
    return { ok: false, error: "未找到 DeepSeek Harness 启动路径 (D:\\dsh\\DeepSeek Harness.exe)" };
  }
  if (!waitForHostIpc) {
    return { ok: true, launched: true, path: launchedVia, message: `已自动拉起 DeepSeek Harness (${launchedVia})` };
  }
  const deadline = Date.now() + maxWaitMs;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 700));
    if (await isDshHostIpcAlive()) {
      return { ok: true, launched: true, host_ipc_ready: true, path: launchedVia };
    }
  }
  return { ok: true, launched: true, host_ipc_ready: false, path: launchedVia, message: "已启动 DeepSeek Harness，正在初始化内部会话服务…" };
}
export async function dispatchToDshSession(ctx, args = {}) {
  const targetCtx = ctx || runtimeState.activeCtx;
  const prompt = String(args?.message || args?.prompt || args?.text || "").trim();
  if (!prompt) return { ok: false, error: "指令不能为空" };
  if (!targetCtx) {
    let ipcRes = await proxyToDshHostIpc("/local/infinite-chat/dispatch_session", args, 24000);
    if (ipcRes && typeof ipcRes === "object") return ipcRes;
    const launchRes = await launchDshDesktopApp(true, 18000);
    if (!launchRes.ok) return launchRes;
    await new Promise((r) => setTimeout(r, 1200));
    ipcRes = await proxyToDshHostIpc("/local/infinite-chat/dispatch_session", args, 24000);
    if (ipcRes && typeof ipcRes === "object") return ipcRes;
    return {
      ok: false, launched_dsh: true, error: "小启动器已自动拉起 DeepSeek Harness 客户端，但会话引擎仍在加载中，请稍候几秒再试。",
    };
  }
  const sessionController = getService(targetCtx, "sessionController");
  const agentsSvc = getService(targetCtx, "agents"); const sessionsSvc = getService(targetCtx, "sessions");
  const rawTarget = String(args?.session_id || args?.sessionId || args?.query || "").trim();
  let targetSession = null; let targetSid = "";
  if ((rawTarget === "new" || rawTarget === "create") && sessionController && typeof sessionController.createSession === "function") {
    try {
      const created = await sessionController.createSession({ cwd: args?.cwd || getDefaultPcWorkspaceDir() });
      targetSid = String(created?.sessionId || created?.id || "");
      if (targetSid) targetSession = { id: targetSid, title: "新建会话", status: "idle" };
    } catch {}
  }
  if (!targetSid) {
    const panorama = getDshPanorama(targetCtx, true);
    const resolved = resolveDshSessionFromPanorama(panorama, rawTarget);
    if (!resolved.ok) return resolved;
    targetSession = resolved.session; targetSid = targetSession?.id || "";
  }
  if (!targetSid) return { ok: false, error: "未找到匹配的本机 DSH 会话" };
  let agent = agentsSvc?.get?.(targetSid) || null;
  if (!agent && sessionController) {
    try {
      if (typeof sessionController.resolveAgent === "function") {
        const r = await sessionController.resolveAgent(targetSid);
        if (r?.error) return { ok: false, error: `唤醒会话 ${targetSid} 失败: ${r.error.message || JSON.stringify(r.error)}` };
        agent = r?.agent || (typeof r?.followup === "function" ? r : null);
      } else if (typeof sessionController.agents?.resolveAgent === "function") {
        const r = await sessionController.agents.resolveAgent(targetSid);
        agent = r?.agent || (typeof r?.followup === "function" ? r : null);
      }
    } catch (err) { return { ok: false, error: `解析会话 ${targetSid} 异常: ${String(err?.message || err)}` }; }
  }
  if (!agent && (!sessionController || typeof sessionController.prompt !== "function")) {
    return { ok: false, error: `会话 ${targetSid} 尚未就绪` };
  }
  let session = agent?.session || sessionsSvc?.get?.(targetSid) || null;
  const firstSeq = Number(session?.seq || 0);
  const isAgentBusy = Boolean(agent?.status && agent.status !== "idle") || targetSession?.status === "running";
  const mode = args?.mode ? (args.mode === "steer" ? "steer" : "followup") : (isAgentBusy ? "steer" : "followup");
  const shouldWait = args?.wait === true;
  try {
    if (sessionController && typeof sessionController.prompt === "function") {
      const pTask = sessionController.prompt({
        sessionId: targetSid, requestId: crypto.randomUUID(),
        content: [{ type: "text", text: prompt }], mode,
      });
      if (!shouldWait) {
        pTask.catch(() => {});
        await Promise.race([pTask.then(() => true).catch(() => false), new Promise((r) => setTimeout(r, 90))]);
      } else {
        await Promise.race([pTask, new Promise((_, rej) => setTimeout(() => rej(new Error("prompt ack timeout")), 15000))]);
      }
      if (!agent) agent = agentsSvc?.get?.(targetSid) || null;
      if (!session) session = agent?.session || sessionsSvc?.get?.(targetSid) || null;
    } else if (agent && typeof agent.followup === "function") {
      const msg = deepFreeze({
        id: crypto.randomUUID(), role: "user",
        content: [{ type: "text", text: prompt }], source: { kind: "user" },
      });
      if (mode === "steer" && typeof agent.steer === "function") agent.steer(msg);
      else agent.followup(msg);
    } else { return { ok: false, error: `会话 ${targetSid} 尚未就绪` }; }
  } catch (err) { return { ok: false, error: `向会话 ${targetSid} 投递指令失败: ${String(err?.message || err)}` }; }
  if (!shouldWait) {
    return {
      ok: true, pc_online: true, dispatched: true, mode_used: mode, preempted: Boolean(isAgentBusy && mode === "steer"),
      status: "running", session_id: targetSid, short_id: targetSid.replace(/^session-/, "").slice(0, 8),
      title: targetSession?.title || targetSid.slice(0, 12),
      assistant_reply: `✅ 已${isAgentBusy && mode === "steer" ? "即时打断插话 (steer)" : "异步派发"}至会话「${targetSession?.title || targetSid.slice(0, 12)}」：「${prompt.slice(0, 60)}」`,
    };
  }
  const reqTimeoutMs = Number(args?.timeout_ms) || (Number(args?.timeout_sec) ? Number(args.timeout_sec) * 1000 : 8500);
  const waitMs = Math.min(8500, Math.max(2000, reqTimeoutMs > 12000 ? 8500 : reqTimeoutMs));
  const deadline = Date.now() + waitMs;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 350));
    session = agent?.session || sessionsSvc?.get?.(targetSid) || session;
    const curSeqNow = Number(session?.seq || 0);
    if (agent && agent.status === "idle" && curSeqNow > firstSeq + 1) { break; }
  }
  session = agent?.session || sessionsSvc?.get?.(targetSid) || session;
  let replyText = ""; let userEchoConfirmed = false;
  const curSeq = Number(session?.seq || 0);
  for (let sq = firstSeq; sq < curSeq; sq++) {
    try {
      const ev = session.eventAt(sq);
      if (ev?.type === "user/message") {
        userEchoConfirmed = true;
      } else if (ev?.type === "assistant/message") {
        const txt = (ev.data?.message?.content || []).filter((b) => b?.type === "text").map((b) => b.text).join("").trim();
        if (txt) replyText = txt;
      }
    } catch {}
  }
  const stillRunning = !agent || agent.status !== "idle" || (!replyText && curSeq <= firstSeq + 1);
  return {
    ok: true, pc_online: true, dispatched: true, mode_used: mode, preempted: Boolean(isAgentBusy && mode === "steer"),
    user_message_recorded: userEchoConfirmed || curSeq > firstSeq, seq_before: firstSeq, seq_after: curSeq,
    status: stillRunning ? "running" : "completed", session_id: targetSid,
    short_id: targetSid.replace(/^session-/, "").slice(0, 8),
    title: targetSession?.title || targetSid.slice(0, 12),
    assistant_reply: replyText || (stillRunning
      ? `✅ 指令已投递至会话「${targetSession?.title || targetSid.slice(0, 12)}」(seq: ${firstSeq} -> ${curSeq}) 并正在后台执行中，可稍后用 inspect_session 查看回复。`
      : "已在会话中执行完毕。"),
  };
}
let cachedGitBashPath;
export function detectSystemGitBash() {
  if (cachedGitBashPath !== undefined) return cachedGitBashPath;
  const candidates = [
    "C:\\Program Files\\Git\\bin\\bash.exe",
    "C:\\Program Files\\Git\\usr\\bin\\bash.exe",
    process.env.PROGRAMFILES ? path.join(process.env.PROGRAMFILES, "Git", "bin", "bash.exe") : "",
    process.env["PROGRAMFILES(X86)"] ? path.join(process.env["PROGRAMFILES(X86)"], "Git", "bin", "bash.exe") : "",
    process.env.LOCALAPPDATA ? path.join(process.env.LOCALAPPDATA, "Programs", "Git", "bin", "bash.exe") : "",
  ].filter(Boolean);
  for (const c of candidates) {
    if (/\\system32\\bash\.exe$/i.test(c)) continue;
    try { if (fs.existsSync(c)) { cachedGitBashPath = c; return c; } } catch {}
  }
  cachedGitBashPath = "";
  return "";
}
export function getDefaultPcWorkspaceDir() {
  const dshWs = path.join(os.homedir(), "Documents", "deepseek-harness", "default-workspace");
  try {
    if (fs.existsSync(dshWs)) return dshWs;
  } catch {}
  return os.homedir();
}
export function resolvePcPath(rawPath) {
  const p = String(rawPath || "").trim();
  if (!p) return getDefaultPcWorkspaceDir();
  if (path.isAbsolute(p)) return p;
  return path.resolve(getDefaultPcWorkspaceDir(), p);
}
export async function executeInteractiveCuaTool(action, args = {}, botName = "主助理") {
  const localRes = await callCuaTool(action, args, botName).catch((e) => ({ ok: false, text: String(e?.message || e) }));
  if (localRes?.ok) return localRes;
  const errStr = String(localRes?.text || localRes?.error || "");
  if (!runtimeState.activeCtx && /EnumWindows|0x00000000/i.test(errStr) && (await isDshHostIpcAlive())) {
    const proxied = await proxyToDshHostIpc("/local/cua", { ...args, action, bot_name: botName }, 65000);
    if (proxied && typeof proxied === "object") return proxied;
  }
  return localRes;
}
export function runLocalPcShellCommand(args = {}) {
  const cmdStr = String(args.command || args.cmd || args.text || "").trim();
  if (!cmdStr) return Promise.resolve({ ok: false, error: "command 不能为空", hostname: HOSTNAME });
  const timeoutMs = Math.max(1000, Math.min(120000, Number(args.timeout || args.timeout_sec || 20) * 1000));
  const cwd = args.cwd ? resolvePcPath(args.cwd) : getDefaultPcWorkspaceDir();
  const reqShell = String(args.shell || "auto").trim().toLowerCase();
  const gitBash = (reqShell === "powershell" || reqShell === "pwsh") ? "" : detectSystemGitBash();
  const useBash = Boolean(gitBash);
  const exe = useBash ? gitBash : "powershell.exe";
  const exeArgs = useBash
    ? ["-lc", cmdStr]
    : ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", `[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; ${cmdStr}`];
  const env = useBash
    ? { ...process.env, MSYS_NO_PATHCONV: "1", MSYS2_ARG_CONV_EXCL: "*", LANG: "C.UTF-8", LC_ALL: "C.UTF-8" }
    : process.env;
  const shellUsed = useBash ? "git-bash" : "powershell";
  if (args.background === true) {
    try {
      const logDir = path.join(cwd, ".bg_logs");
      fs.mkdirSync(logDir, { recursive: true });
      const tid = `pc-bg-${crypto.randomBytes(4).toString("hex")}`;
      const logPath = path.join(logDir, `${tid}.log`);
      const outFd = fs.openSync(logPath, "a");
      const bgChild = spawn(exe, exeArgs, { cwd, env, detached: true, stdio: ["ignore", outFd, outFd], windowsHide: true });
      bgChild.unref();
      try { fs.closeSync(outFd); } catch {}
      return Promise.resolve({ ok: true, background: true, task_id: tid, pid: bgChild.pid, log_file: logPath, cwd, shell_used: shellUsed, hostname: HOSTNAME });
    } catch (e) {
      return Promise.resolve({ ok: false, error: String(e?.message || e), cwd, hostname: HOSTNAME });
    }
  }
  return new Promise((resolve) => {
    let settled = false;
    const child = execFile(
      exe,
      exeArgs,
      { cwd, env, maxBuffer: 4 * 1024 * 1024, windowsHide: true },
      (err, stdout, stderr) => {
        if (settled) return;
        settled = true;
        clearTimeout(killTimer);
        const exitCode = err && typeof err.code === "number" ? err.code : (err ? 1 : 0);
        resolve({
          ok: !err && exitCode === 0, hostname: HOSTNAME, device_id: PC_DEVICE_ID,
          cwd, shell_used: shellUsed,
          exit_code: exitCode, stdout: String(stdout || "").trim().slice(0, 16000),
          stderr: String(stderr || "").trim().slice(0, 4000),
          error: err ? String(err.message || err).slice(0, 500) : undefined,
        });
      }
    );
    const killTimer = setTimeout(() => {
      if (settled) return;
      settled = true;
      try {
        if (child.pid) {
          execFile("taskkill.exe", ["/T", "/F", "/PID", String(child.pid)], { windowsHide: true }, () => {});
        } else { child.kill("SIGKILL"); }
      } catch {}
      resolve({
        ok: false, hostname: HOSTNAME, device_id: PC_DEVICE_ID,
        cwd, shell_used: shellUsed,
        exit_code: -1, stdout: "", stderr: "",
        error: `Command timed out after ${Math.round(timeoutMs / 1000)}s`,
      });
    }, timeoutMs);
    try { child.stdin?.end(); } catch {}
  });
}
export function walkPcDir(rootDir, maxDepth = 3, maxEntries = 250) {
  const results = [];
  function visit(currDir, depth) {
    if (results.length >= maxEntries || depth > maxDepth) return;
    let entries = [];
    try { entries = fs.readdirSync(currDir, { withFileTypes: true }); } catch { return; }
    for (const ent of entries) {
      if (results.length >= maxEntries) break;
      if (ent.name === ".git" || ent.name === "node_modules" || ent.name === "__pycache__") continue;
      const full = path.join(currDir, ent.name); const rel = path.relative(rootDir, full).replace(/\\/g, "/");
      results.push({
        name: rel || ent.name, path: full, type: ent.isDirectory() ? "dir" : "file",
      });
      if (ent.isDirectory() && depth < maxDepth) { visit(full, depth + 1); }
    }
  }
  visit(rootDir, 1);
  return results;
}
export function globToRegExp(globPat) {
  const norm = String(globPat || "*").replace(/\\/g, "/"); let reStr = "^";
  for (let i = 0; i < norm.length; i++) {
    const c = norm[i];
    if (c === "*") {
      if (norm[i + 1] === "*") {
        if (norm[i + 2] === "/") {
          reStr += "(?:.*/)?";
          i += 2;
        } else {
          reStr += ".*";
          i += 1;
        }
      } else { reStr += "[^/]*"; }
    } else if (c === "?") {
      reStr += "[^/]";
    } else if ("+()^$.{}[]|\\".includes(c)) {
      reStr += "\\" + c;
    } else { reStr += c; }
  }
  reStr += "$";
  return new RegExp(reStr, "i");
}
export async function runLocalPcFsOperation(args = {}) {
  const fsOp = String(args.fs_op || args.op || "read_file").toLowerCase();
  const targetPath = resolvePcPath(args.path || args.file_path || args.filename || "");
  try {
    if (fsOp === "read_file" || fsOp === "read") {
      if (!fs.existsSync(targetPath)) {
        return { ok: false, error: `文件不存在: ${targetPath}`, path: targetPath, hostname: HOSTNAME };
      }
      const stat = fs.statSync(targetPath);
      if (stat.isDirectory()) {
        const items = fs.readdirSync(targetPath);
        return { ok: true, path: targetPath, is_directory: true, entries: items.slice(0, 200), hostname: HOSTNAME };
      }
      const ext = path.extname(targetPath).toLowerCase();
      const imageMimes = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
      };
      if (imageMimes[ext]) {
        const buf = fs.readFileSync(targetPath);
        return {
          ok: true, path: targetPath, size_bytes: buf.length,
          _multimodal_b64: buf.toString("base64"), _multimodal_mime: imageMimes[ext], hostname: HOSTNAME,
        };
      }
      const raw = fs.readFileSync(targetPath, "utf8"); const lines = raw.split(/\r?\n/);
      const rawStart = args.start_line !== undefined && args.start_line !== null ? Number(args.start_line) : 1;
      const rawEnd = args.end_line !== undefined && args.end_line !== null ? Number(args.end_line) : rawStart + 250;
      if (rawStart < 1 || rawEnd < rawStart) {
        return {
          ok: false,
          error: `无效的行号范围: start_line=${rawStart}, end_line=${rawEnd}（要求 1 <= start_line <= end_line）`,
          path: targetPath, total_lines: lines.length, hostname: HOSTNAME,
        };
      }
      if (lines.length > 0 && rawStart > lines.length) {
        return {
          ok: false,
          error: `起始行号越界: start_line=${rawStart} 超出文件总行数 (${lines.length} 行)`,
          path: targetPath, total_lines: lines.length, hostname: HOSTNAME,
        };
      }
      const start = Math.max(1, rawStart); const end = Math.min(lines.length, rawEnd);
      const content = lines.slice(start - 1, end).join("\n").slice(0, 24000);
      return { ok: true, path: targetPath, start_line: start, end_line: end, total_lines: lines.length, content, hostname: HOSTNAME };
    }
    const dedupAdjacentLongLines = (txt) => {
      const lines = String(txt || "").split("\n"); const out = [];
      let dedupCount = 0;
      for (const ln of lines) {
        const s = ln.trim();
        if (s.length >= 12 && out.length > 0 && out[out.length - 1].trim() === s) {
          dedupCount++;
          continue;
        }
        out.push(ln);
      }
      return { text: out.join("\n"), dedupCount };
    };
    if (fsOp === "write_file" || fsOp === "write") {
      const content = String(args.content ?? ""); const append = Boolean(args.append);
      fs.mkdirSync(path.dirname(targetPath), { recursive: true });
      let dedupCount = 0;
      if (append && fs.existsSync(targetPath)) {
        const existing = fs.readFileSync(targetPath, "utf8");
        const sep = existing && !existing.endsWith("\n") && !content.startsWith("\n") ? "\n" : "";
        const merged = dedupAdjacentLongLines(existing + sep + content);
        dedupCount = merged.dedupCount;
        fs.writeFileSync(targetPath, merged.text, "utf8");
      } else { fs.writeFileSync(targetPath, content, "utf8"); }
      const resObj = { ok: true, path: targetPath, append, bytes_written: Buffer.byteLength(content, "utf8"), hostname: HOSTNAME };
      if (dedupCount > 0) resObj.deduplicated_adjacent_lines = dedupCount;
      return resObj;
    }
    if (fsOp === "edit_file" || fsOp === "edit") {
      if (!fs.existsSync(targetPath)) {
        return { ok: false, error: `文件不存在: ${targetPath}`, path: targetPath, hostname: HOSTNAME };
      }
      const raw = fs.readFileSync(targetPath, "utf8");
      const oldText = String(args.old ?? args.old_text ?? args.target ?? "");
      const newText = String(args.new ?? args.new_text ?? args.replacement ?? "");
      if (!oldText || !raw.includes(oldText)) {
        return { ok: false, error: `未在文件中找到待替换内容 (${targetPath})`, path: targetPath, hostname: HOSTNAME };
      }
      const replaceAll = Boolean(args.replace_all);
      const rawUpdated = replaceAll ? raw.split(oldText).join(newText) : raw.replace(oldText, () => newText);
      const merged = dedupAdjacentLongLines(rawUpdated);
      fs.writeFileSync(targetPath, merged.text, "utf8");
      const resObj = { ok: true, path: targetPath, updated: true, hostname: HOSTNAME };
      if (merged.dedupCount > 0) resObj.deduplicated_adjacent_lines = merged.dedupCount;
      return resObj;
    }
    if (fsOp === "list_dir" || fsOp === "ls") {
      if (!fs.existsSync(targetPath)) {
        return { ok: false, error: `目录不存在: ${targetPath}`, path: targetPath, hostname: HOSTNAME };
      }
      const maxDepth = Math.max(1, Math.min(5, Number(args.depth || 1)));
      const entries = walkPcDir(targetPath, maxDepth, 250);
      return { ok: true, path: targetPath, count: entries.length, entries, hostname: HOSTNAME };
    }
    if (fsOp === "glob_files" || fsOp === "glob") {
      if (!fs.existsSync(targetPath)) {
        return { ok: false, error: `目录不存在: ${targetPath}`, path: targetPath, hostname: HOSTNAME };
      }
      const pattern = String(args.pattern || "**/*").trim(); const rx = globToRegExp(pattern);
      const allEntries = walkPcDir(targetPath, 6, 1500); const matches = [];
      for (const item of allEntries) {
        if (item.type === "file" && (rx.test(item.name) || rx.test(path.basename(item.name)))) {
          matches.push(item.path);
          if (matches.length >= 100) break;
        }
      }
      return { ok: true, path: targetPath, pattern, count: matches.length, matches, hostname: HOSTNAME };
    }
    if (fsOp === "grep_search" || fsOp === "grep") {
      const query = String(args.query || args.pattern || "").trim();
      const grepPath = resolvePcPath(args.path || args.file_path || args.target || args.dir || args.filename || "");
      if (!query) return { ok: false, error: "query 不能为空", path: grepPath, hostname: HOSTNAME };
      if (!fs.existsSync(grepPath)) {
        return { ok: false, error: `路径不存在: ${grepPath}`, path: grepPath, hostname: HOSTNAME };
      }
      let rx = null;
      try { rx = new RegExp(query, "i"); } catch { rx = new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i"); }
      const skipExts = new Set([".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".ico", ".pdf", ".zip", ".tar", ".gz", ".exe", ".dll", ".so", ".pyc", ".db", ".sqlite", ".woff", ".woff2", ".ttf", ".mp3", ".mp4", ".wav"]);
      const stat = fs.statSync(grepPath);
      const filesToScan = stat.isFile()
        ? [grepPath]
        : walkPcDir(grepPath, 5, 800).filter((e) => e.type === "file").map((e) => e.path);
      const matches = [];
      for (const fp of filesToScan) {
        if (matches.length >= 80) break;
        if (skipExts.has(path.extname(fp).toLowerCase())) continue;
        try {
          const fstat = fs.statSync(fp);
          if (fstat.size > 1024 * 1024 || fstat.size === 0) continue;
          const rawBuf = fs.readFileSync(fp);
          if (rawBuf.subarray(0, 4096).includes(0)) continue;
          const lines = rawBuf.toString("utf8").split(/\r?\n/);
          for (let i = 0; i < lines.length; i++) {
            if (rx.test(lines[i])) {
              matches.push(`${fp}:${i + 1}: ${lines[i].slice(0, 200)}`);
              if (matches.length >= 80) break;
            }
          }
        } catch {}
      }
      return { ok: true, path: grepPath, query, count: matches.length, matches, hostname: HOSTNAME };
    }
    return { ok: false, error: `未知本地文件操作: ${fsOp}`, hostname: HOSTNAME };
  } catch (e) { return { ok: false, path: targetPath, error: String(e?.message || e), hostname: HOSTNAME }; }
}
export function registerAllDshTools(toolsSvc) {
  const outSchema = {
    schema: { type: "object", properties: { ok: { type: "boolean" }, summary: { type: "string" }, data: {} }, required: ["ok", "summary"] },
    render: (_a, v) => [{ type: "text", text: v.summary }],
  };
  toolsSvc.register({
    name: "blackboard",
    description: "跨端多 Bot 统一协作黑板（秒回不阻塞）：支持 @mention 唤醒 (@Bot名 / @self / @everyone)、看板认领与交接 (claim/complete/artifact_path)、按需审查门禁 (review_by)、定时闹钟 (at_time/delay_minutes/daily_time/exec_action) 与 0-Token 事件观察哨 (watch)。",
    timeoutMs: 30000,
    parameters: {
      type: "object", additionalProperties: false,
      properties: {
        action: { type: "string", enum: ["post", "reply", "read", "list", "claim", "complete", "review", "cancel", "write", "delete", "set_todos"] },
        topic_id: { type: "string" }, title: { type: "string" }, content: { type: "string" },
        mentions: { type: "array", items: { type: "string" } }, status_filter: { type: "string" },
        artifact_path: { type: "string" }, next_action: { type: "string" },
        review_by: { type: "string" }, require_review: { type: "boolean" }, verdict: { type: "string", enum: ["approved", "rejected"] },
        at_time: { type: "string" }, delay_minutes: { type: "number" }, delay_seconds: { type: "number" }, daily_time: { type: "string" },
        exec_action: { type: "string", enum: ["wake_bot", "ring_alarm", "notify_user"] },
        watch: { type: "object" }, key: { type: "string" }, limit: { type: "number" },
      },
      required: ["action"],
    },
    output: outSchema, isConcurrencySafe: (args) => args?.action === "read" || args?.action === "list",
    async execute(args) {
      const res = await cloudFetch("/api/blackboard", "POST", { ...args, from_bot_id: "pc_engineer", from_bot_name: "电脑端 DSH", from_source: "pc" }, 20000);
      return { ok: Boolean(res && !res.error), summary: JSON.stringify(res || {}, null, 2).slice(0, 3200), data: res || {} };
    },
  });
  toolsSvc.register({
    name: "peek_bots",
    description: "只读快速窥视工具：秒级读取所有或指定 Bot（手机端与电脑端）的当前运行状态、正在调用的工具及最近消息摘要，了解各 Bot 干到哪了。",
    timeoutMs: 15000,
    parameters: {
      type: "object", additionalProperties: false,
      properties: { target: { type: "string" }, limit: { type: "number" } },
    },
    output: outSchema, isConcurrencySafe: () => true,
    async execute(args) {
      const target = encodeURIComponent(String(args?.target || "all").trim());
      const limit = Math.max(1, Math.min(15, Number(args?.limit) || 5));
      const res = await cloudFetch(`/api/peek-bots?target=${target}&limit=${limit}`, "GET", null, 15000);
      return { ok: Boolean(res?.ok !== false), summary: JSON.stringify(res || {}, null, 2).slice(0, 3200), data: res || {} };
    },
  });
  toolsSvc.register({
    name: "cloud_desktop",
    description: "直接操控 24 小时常驻 Linux 云电脑（无头/虚拟桌面 + Chromium 浏览器 + bash 终端）：支持 exec、screenshot、open_url、browser_snapshot、browser_click、browser_type、browser_eval、click、type、key、scroll、status。",
    timeoutMs: 90000,
    parameters: {
      type: "object", additionalProperties: false,
      properties: {
        op: { type: "string", enum: ["status", "screenshot", "bash", "exec", "open", "navigate", "click", "type", "key", "scroll", "files", "pull_file", "push_file", "send_screenshot"] },
        action: { type: "string", enum: ["status", "screenshot", "exec", "bash", "open_url", "browser_snapshot", "browser_click", "browser_type", "browser_eval", "click", "type", "key", "scroll"] },
        command: { type: "string" }, url: { type: "string" }, target: { type: "string" }, path: { type: "string" },
        selector: { type: "string" }, ref: { type: "string" }, nx: { type: "number" }, ny: { type: "number" },
        text: { type: "string" }, script: { type: "string" }, x: { type: "number" }, y: { type: "number" },
        key: { type: "string" }, direction: { type: "string" }, submit: { type: "boolean" }, timeout: { type: "number" },
      },
    },
    output: outSchema, isConcurrencySafe: (args) => (args?.op || args?.action) === "status" || (args?.op || args?.action) === "browser_snapshot",
    async execute(args) {
      const effOp = String(args?.op || args?.action || "status").trim();
      const res = await cloudFetch("/api/computer/command", "POST", { ...(args || {}), op: effOp }, 85000);
      const ok = Boolean(res?.ok !== false && !res?.error);
      const txt = res?.stdout || res?.output || res?.last_bash_output || res?.snapshot || res?.result || res?.page_text || res?.message || JSON.stringify(res || {}).slice(0, 2400);
      return { ok, summary: `[云电脑 · ${effOp}]\n${String(txt).slice(0, 2800)}`, data: res || {} };
    },
  });
  toolsSvc.register({
    name: "message_phone_bot",
    description: "跨端兼容工具：查看手机/平板端最近对话 (op='status'|'peek_dialogs')，或在共享黑板异步 @ 手机助理 (op='send_message'，毫秒级返回不阻塞)。",
    timeoutMs: 30000,
    parameters: {
      type: "object", additionalProperties: false,
      properties: {
        op: { type: "string", enum: ["status", "peek_dialogs", "list_bots", "send_message"] },
        limit: { type: "number" }, source: { type: "string", enum: ["phone", "pc"] },
        bot_id: { type: "string" }, message: { type: "string" },
      },
      required: ["op"],
    },
    output: outSchema, isConcurrencySafe: (args) => args?.op !== "send_message",
    async execute(args) {
      const op = args?.op || "send_message";
      if (op !== "send_message") {
        const limit = Math.max(1, Math.min(50, Number(args?.limit) || 5));
        const targetSource = args?.source === "pc" ? "pc" : "phone";
        const st = await cloudFetch(`/api/dsh/status?limit=${limit}&source=${targetSource}`, "GET");
        const dialogs = st?.dialogs || st?.phone_dialogs || st?.phone_last_3_dialogs || [];
        const lines = dialogs.map((d, i) => `${i + 1}. U: ${d.user || "(空)"} -> A(${d.bot_name || "主助理"}): ${d.assistant || "(待回复)"}`);
        return { ok: true, summary: `手机/平板状态: ${st.phone_online ? "在线" : "后台待命"} | 最近 ${dialogs.length} 条对话:\n${lines.join("\n") || "（暂无）"}`, data: st };
      }
      const msg = String(args?.message || "").trim();
      if (!msg) return { ok: false, summary: "message 不能为空", data: {} };
      const targetBot = String(args?.bot_id || "companion").trim();
      const res = await cloudFetch("/api/blackboard", "POST", {
        action: "post", title: msg.slice(0, 48), content: `@${targetBot} ${msg}`,
        mentions: [targetBot], from_bot_id: "pc_engineer", from_bot_name: "电脑端 DSH", from_source: "pc",
      }, 15000);
      return { ok: Boolean(res?.ok), summary: res?.ok ? `✅ 已在黑板发布并异步唤醒 @${targetBot} (topic_id=${res.topic?.id || ""})，稍后可用 peek_bots 查看进展。` : `黑板投递失败: ${res?.error}`, data: res || {} };
    },
  });
  toolsSvc.register({
    name: "cua_desktop", description: "本机 Windows 桌面操控工具 (`ds-claw-cua`)：枚举窗口、获取 UIA 组件树、点击、输入、按键、滚动。",
    timeoutMs: 90000,
    parameters: {
      type: "object", additionalProperties: false,
      properties: {
        action: { type: "string", enum: ["list_windows", "list_apps", "get_window_state", "click", "type_text", "set_value", "press_key", "scroll", "scroll_element", "drag", "launch_app", "activate_window", "close_window", "wait_for"] },
        bot_name: { type: "string" },
        window_id: { type: "number" },
        element_index: { type: "number" },
        x: { type: "number" },
        y: { type: "number" },
        text: { type: "string" },
        value: { type: "string" },
        clear: { type: "boolean" },
        submit: { type: "boolean" },
        key: { type: "string" },
        direction: { type: "string", enum: ["up", "down", "left", "right"] },
        scrollY: { type: "number" },
        app: { type: "string" },
        filter: { type: "string" },
        include_screenshot: { type: "boolean" },
      },
      required: ["action"],
    },
    output: outSchema,
    isConcurrencySafe: (args) => args?.action === "list_windows" || args?.action === "list_apps",
    async execute(args) {
      const action = args?.action || "list_windows";
      try {
        const res = await executeInteractiveCuaTool(action, args, args?.bot_name || "主助理");
        return { ok: res.ok, summary: `${res.banner} · [${action}]\n${res.text || "(ok)"}`, data: res };
      } catch (err) { return { ok: false, summary: `桌面操控失败: ${String(err?.message || err)}`, data: {} }; }
    },
  });
  toolsSvc.register({
    name: "shared_memory", description: "手机↔平板↔电脑跨端统一共享长期记忆工具 (`list` | `remember` | `forget` | `search`)。",
    timeoutMs: 30000,
    parameters: {
      type: "object", additionalProperties: false,
      properties: {
        op: { type: "string", enum: ["list", "remember", "forget", "search", "update_impression", "update_traits"] },
        text: { type: "string" },
        pin_id: { type: "string" },
      },
      required: ["op"],
    },
    output: outSchema, isConcurrencySafe: (args) => args?.op === "list" || args?.op === "search",
    async execute(args) {
      const op = String(args?.op || "list").trim().toLowerCase();
      const res = await cloudFetch("/api/dsh/shared-memory", "POST", { op, source: "pc", text: args?.text || "", query: args?.text || "", content: args?.text || "", pin_id: args?.pin_id || args?.text || "" });
      if (!res?.ok) return { ok: false, summary: `共享记忆操作失败: ${res?.error}`, data: res || {} };
      return { ok: true, summary: `✅ 共享记忆 (${op}) 已同步，当前共 ${(res?.shared_memories?.pins || []).length} 条置顶记忆。`, data: res };
    },
  });
}
runtimeState.getDshPanorama = getDshPanorama;
