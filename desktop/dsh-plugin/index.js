/**
 * [Module Responsibility]
 * DSH DSB 插件主入口、本机桥接与本地路由网关 (`index.js`)。
 * 负责：
 * 1. 插件元信息导出 (`name`, `version`, `description`) 与生命周期挂载 (`apply`)。
 * 2. 原生 PC 端心跳/收件箱轮询桥接 (`startNativePcBridge`)。
 * 3. 本地 HTTP API 路由 (`handleLocalRequest`, `createBridgeHttpHandler`)、Host IPC 服务 (`ensureDshHostIpcServer`) 与 Loopback 礼让守护 (`ensureLocalLoopbackServer`)。
 * 4. 伴侣侧边栏前端路由注册 (`registerUiRoutes`)。
 *
 * [Public Interface]
 * - `name`, `version`, `description`
 * - `apply(api, context)`: DSH 插件挂载入口
 * - `default`: `{ name, version, description, apply }`
 *
 * [Dependencies]
 * - `./lib-chat-cache.mjs`: 本地聊天缓存、Bot 花名册与云端轮次执行引擎
 * - `./lib-dsh-sessions.mjs`: DSH 会话全景扫描、跨会话调度与本机 Shell/FS 处理器
 */
import fs from "node:fs";
import path from "node:path";
import http from "node:http";
import crypto from "node:crypto";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import {
  HOSTNAME,
  PC_DEVICE_ID,
  PC_DEVICE_NAME,
  DSH_HOST_IPC_PORT,
  DEFAULT_BOTS_ROSTER,
  runtimeState,
  loadLocalConfig,
  getService,
  dedupeAndNormalizeBots,
  loadBotsRosterFromDisk,
  saveBotsRosterToDisk,
  findBotByIdOrName,
  resolveCloudBotId,
  sanitizeMessagesList,
  mergeCloudPcMessagesIntoCache,
  loadLocalChatCache,
  saveLocalChatCache,
  cloudFetch,
  beginBotRequest,
  isBotRequestAborted,
  waitForCloudBotReply,
  executeCloudChatTurn,
  handleBotArchiveLocalRoute,
} from "./lib-chat-cache.mjs";
import { callCuaTool } from "./cua-bridge.mjs";
import {
  netTelemetry,
  logSentinel,
  getRecentSentinelLogs,
  getSentinelLogPath,
  registerInboxAbortController,
  triggerInstantReconnect,
} from "./lib-sentinel-net.mjs";
import {
  getDshPanorama,
  executeInteractiveCuaTool,
  searchDshSessions,
  inspectDshSessionDetail,
  dispatchToDshSession,
  proxyToDshHostIpc,
  isDshHostIpcAlive,
  launchDshDesktopApp,
  runLocalPcShellCommand,
  runLocalPcFsOperation,
  registerAllDshTools,
} from "./lib-dsh-sessions.mjs";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

export const name = "dsh-deepseek-claw";
export const version = "2.0.0";
export const description = "DSB · Multi-Bot Companion Dock, Cloud Sync & DSH Session Bridge";
const API_ROUTES = ["/api/baike-plugin", "/api/dsh-baike-plugin", "/api/dsh-deepseek-claw"];

function startNativePcBridge(ctx) {
  try {
    if (ctx) {
      runtimeState.activeCtx = ctx;
      if (!runtimeState.rootCtx) runtimeState.rootCtx = ctx;
    }
  } catch {}
  if (runtimeState.bridgeRunning) return;
  runtimeState.bridgeRunning = true;
  const gen = ++runtimeState.bridgeGeneration;
  logSentinel("INFO", `启动 PC 云端长轮询桥接 (gen=${gen}, device=${runtimeState.deviceId})`);

  (async () => {
    let lastStatusPushAt = 0;
    let lastStatusFp = "";
    let cachedHostAlive = false;
    let lastHostCheckAt = 0;
    while (gen === runtimeState.bridgeGeneration) {
      let hadCommands = false;
      let abortedForReconnect = false;
      const inboxCtrl = new AbortController();
      registerInboxAbortController(inboxCtrl);
      try {
        const nowMs = Date.now();
        if (nowMs - lastHostCheckAt > 4000) {
          cachedHostAlive = runtimeState.activeCtx ? true : await isDshHostIpcAlive();
          runtimeState.dshHostAlive = cachedHostAlive;
          lastHostCheckAt = nowMs;
        }
        if (!runtimeState.lastPanoramaCache || nowMs - (runtimeState.lastPanoramaAt || 0) > 15000) {
          if (!runtimeState.activeCtx && cachedHostAlive) {
            const hostPan = await proxyToDshHostIpc("/local/sessions", {}, 3500);
            runtimeState.lastPanoramaCache = hostPan?.panorama || getDshPanorama(null, false);
          } else {
            runtimeState.lastPanoramaCache = getDshPanorama(runtimeState.activeCtx, false);
          }
          runtimeState.lastPanoramaAt = nowMs;
        }
        const p = runtimeState.lastPanoramaCache;
        const topSid = p?.sessions?.[0]?.id || "";
        const topTurns = p?.sessions?.[0]?.turns_count || 0;
        const curFp = `${p?.sessions_count || 0}:${p?.running_count || 0}:${topSid}:${topTurns}`;
        const needFullStatus = !runtimeState.vpsOnline || (nowMs - lastStatusPushAt > 60000) || (curFp !== lastStatusFp);
        const fastHandshake = !runtimeState.vpsOnline || lastStatusPushAt === 0;
        const reqBody = {
          peer_id: runtimeState.deviceId,
          device_id: runtimeState.deviceId,
          device_name: runtimeState.deviceName,
          device_type: "pc",
          bridge_id: runtimeState.bridgeId,
          slim: !needFullStatus,
          wait_sec: fastHandshake ? 0 : 12,
          drain: true,
        };
        if (needFullStatus) {
          reqBody.status = p;
          lastStatusPushAt = nowMs;
          lastStatusFp = curFp;
        }
        const resp = await cloudFetch("/api/dsh/inbox?drain=1", "POST", reqBody, 22000, inboxCtrl.signal);
        runtimeState.bytesSent = (runtimeState.bytesSent || 0) + JSON.stringify(reqBody).length;
        if (resp?.ok) {
          const wasOffline = !runtimeState.vpsOnline;
          runtimeState.vpsOnline = true;
          runtimeState.heartbeatCount = (runtimeState.heartbeatCount || 0) + 1;
          runtimeState.lastHeartbeatOkMs = Date.now();
          runtimeState.lastHeartbeatAt = new Date().toLocaleTimeString("zh-CN", { hour12: false });
          runtimeState.bytesReceived = (runtimeState.bytesReceived || 0) + JSON.stringify(resp).length;
          if (wasOffline) {
            logSentinel("INFO", `已成功连接云端中枢 (route=${netTelemetry.activeRoute}, rtt=${netTelemetry.lastRttMs}ms)`);
          }
          if (typeof resp.phone_online === "boolean") runtimeState.phoneOnline = resp.phone_online;
          if (typeof resp.phone_last_active_time === "string") runtimeState.phoneLastActiveTime = resp.phone_last_active_time;
          if (Array.isArray(resp.phone_last_3_dialogs)) runtimeState.phoneLast3Dialogs = resp.phone_last_3_dialogs;
          if (Array.isArray(resp.dsh_peers)) runtimeState.dshPeers = resp.dsh_peers;
          if (Array.isArray(resp.connected_devices)) runtimeState.connectedDevices = resp.connected_devices;
          if (resp.shared_memories) runtimeState.sharedMemories = { ...runtimeState.sharedMemories, ...resp.shared_memories };
          const cmdList = resp.commands || resp.inbox || [];
          hadCommands = cmdList.length > 0;
          for (const cmd of cmdList) {
            const reqId = cmd?.request_id || cmd?.req_id;
            if (!reqId) continue;
            const op = String(cmd.op || "status").toLowerCase();
            const args = cmd.args || {};
            (async () => {
              let data;
              try {
                data =
                  op === "status" || op === "list_sessions" ? (
                    (!runtimeState.activeCtx && (await isDshHostIpcAlive()))
                      ? ((await proxyToDshHostIpc("/local/sessions", {}, 10000))?.panorama || getDshPanorama(runtimeState.activeCtx, true))
                      : getDshPanorama(runtimeState.activeCtx, true)
                  ) :
                  op === "search_sessions" ? searchDshSessions(runtimeState.activeCtx, args.query, args.status_filter) :
                  op === "inspect_session" ? await inspectDshSessionDetail(runtimeState.activeCtx, args.session_id || args.query) :
                  op === "wake_gui" || op === "wake" || op === "open_gui" || op === "launch_gui" ? await launchDshDesktopApp(Boolean(args.wait), 20000) :
                  op === "dispatch_session" || op === "send_to_session" || op === "continue_session" || op === "new_session" || op === "send_message" ? await dispatchToDshSession(runtimeState.activeCtx, args) :
                  op === "pc_shell" || op === "shell" || op === "run_bash" || op === "exec" ? await runLocalPcShellCommand(args) :
                  op === "pc_fs" || op === "read_file" || op === "write_file" || op === "edit_file" || op === "list_dir" || op === "glob_files" || op === "grep_search" ? await runLocalPcFsOperation({ fs_op: args.fs_op || op, ...args }) :
                  op === "cua_desktop" || op === "desktop" ? await executeInteractiveCuaTool(args.action || args.tool || "list_windows", { ...(typeof args.args === "object" && args.args ? args.args : {}), ...args }, args.bot_name || "主助理") :
                  { ok: false, error: `不支持的桌面端操作 (${op})` };
              } catch (cmdErr) {
                data = { ok: false, error: String(cmdErr?.message || cmdErr || "PC bridge command failed") };
              }
              await cloudFetch("/api/dsh/result", "POST", { request_id: reqId, peer_id: runtimeState.deviceId, result: data, status: getDshPanorama(runtimeState.activeCtx) }, 15000).catch(() => {});
            })();
          }
        } else {
          runtimeState.vpsOnline = false;
        }
      } catch (err) {
        if (String(err?.message || err || "").includes("instant_reconnect")) {
          abortedForReconnect = true;
          lastStatusPushAt = 0;
        } else {
          runtimeState.vpsOnline = false;
        }
      } finally {
        registerInboxAbortController(null);
      }
      if (!abortedForReconnect) {
        await new Promise((r) => setTimeout(r, hadCommands ? 60 : (runtimeState.vpsOnline ? 500 : 1800)));
      }
    }
  })();
}

async function handleLocalRequest(localPath, method = "GET", body = null) {
  const clean = String(localPath || "").split("?")[0];

  if (clean === "/local/ui-assets" || clean === "/local/whalechan-avatar") {
    let css = "";
    let renderJs = "";
    let dataUrl = "";
    try { css = fs.readFileSync(path.join(__dirname, "assets", "dsb-ui.css"), "utf8"); } catch {}
    try { renderJs = fs.readFileSync(path.join(__dirname, "assets", "dsb-render.js"), "utf8"); } catch {}
    try {
      const jpgP = path.join(__dirname, "assets", "whale_avatar.jpg");
      const pngP = path.join(__dirname, "assets", "whalechan_avatar.png");
      if (fs.existsSync(jpgP)) dataUrl = `data:image/jpeg;base64,${fs.readFileSync(jpgP).toString("base64")}`;
      else if (fs.existsSync(pngP)) dataUrl = `data:image/png;base64,${fs.readFileSync(pngP).toString("base64")}`;
    } catch {}
    return { ok: true, css, render_js: renderJs, data_url: dataUrl };
  }

  if (clean === "/local/panorama" || clean === "/local/bot-sessions" || clean.startsWith("/local/bots")) {
    const subOp = clean.startsWith("/local/bots/") ? clean.slice("/local/bots/".length) : "";
    const op = String(subOp || body?.op || body?.action || "list").trim().toLowerCase();
    if (op === "export" || op === "import") return await handleBotArchiveLocalRoute(op, body || {});
    let roster = loadBotsRosterFromDisk();
    if ((op === "switch" || op === "select") && body?.bot_id) {
      const target = findBotByIdOrName(body.bot_id);
      if (target) { runtimeState.activeBotId = target.id; saveBotsRosterToDisk(); }
    } else if ((op === "delete" || op === "delete_bot" || op === "remove") && body?.bot_id) {
      const target = findBotByIdOrName(body.bot_id);
      if (!target || target.primary || target.id === "pc_engineer") return { ok: false, error: "不能删除电脑端主控助理" };
      const cloudBotId = resolveCloudBotId(target.id);
      runtimeState.botsRoster = roster.filter((b) => b.id !== target.id && b.id !== String(body.bot_id).trim());
      if (runtimeState.activeBotId === target.id || !runtimeState.botsRoster.some((b) => b.id === runtimeState.activeBotId)) {
        runtimeState.activeBotId = runtimeState.botsRoster[0]?.id || "pc_engineer";
      }
      saveBotsRosterToDisk();
      const delRes = await cloudFetch(`/api/bots/${encodeURIComponent(cloudBotId)}?source=pc`, "DELETE", null, 15000).catch(() => null);
      if (Array.isArray(delRes?.bots) && delRes.bots.length > 0) {
        runtimeState.botsRoster = dedupeAndNormalizeBots(delRes.bots);
        saveBotsRosterToDisk();
      }
    } else if ((op === "create_bot" || op === "create") && body?.name) {
      const rawName = String(body.name).trim();
      const existing = roster.find((b) => b.name.toLowerCase() === rawName.toLowerCase());
      if (existing) {
        runtimeState.activeBotId = existing.id; saveBotsRosterToDisk();
      } else {
        const personaStr = String(body.persona || body.instructions || body.system_prompt || `你是「${rawName}」，认真协助主人完成任务。`).trim();
        const descStr = String(body.description || `${rawName} 电脑端助手`).trim();
        const colorStr = String(body.color || body.avatar_color || "#0A84FF").trim();
        const shapeStr = String(body.shape || "circle").trim();
        const modelStr = String(body.model || "").trim();
        const curiosityVal = Boolean(body.curiosity_enabled);
        const cloudCreated = await cloudFetch("/api/bots", "POST", {
          name: rawName, title: "", description: descStr, instructions: personaStr, color: colorStr,
          shape: shapeStr, model: modelStr, curiosity_enabled: curiosityVal, engine: "big", notify: true, source: "pc", home_node: "pc",
        }, 15000).catch(() => null);
        const createdId = String(cloudCreated?.id || body.id || "").trim().replace(/[^a-zA-Z0-9_-]/g, "") || `bot_${Date.now().toString(36)}`;
        const newBot = {
          id: createdId, name: rawName, description: descStr, color: colorStr, shape: shapeStr,
          model: modelStr, curiosity_enabled: curiosityVal, primary: false, home_node: "pc", persona: personaStr, instructions: personaStr,
        };
        roster.push(newBot);
        runtimeState.botsRoster = dedupeAndNormalizeBots(roster);
        runtimeState.activeBotId = newBot.id; saveBotsRosterToDisk();
      }
    } else if (op === "update" || op === "update_bot" || op === "edit") {
      const targetId = String(body?.bot_id || body?.id || runtimeState.activeBotId || "pc_engineer").trim();
      const target = findBotByIdOrName(targetId);
      if (target) {
        const nextName = String(body?.name || target.name || target.id).trim();
        const nextDesc = body?.description !== undefined ? String(body.description).trim() : String(target.description || `${nextName} 跨端助手`);
        const nextColor = String(body?.color || target.color || "#0A84FF").trim();
        const nextShape = String(body?.shape || target.shape || "flower").trim();
        const nextPersona = String(body?.persona ?? body?.instructions ?? target.persona ?? target.instructions ?? "").trim();
        const nextModel = body?.model !== undefined ? String(body.model).trim() : String(target.model || "");
        const nextCuriosity = body?.curiosity_enabled !== undefined ? Boolean(body.curiosity_enabled) : Boolean(target.curiosity_enabled);
        const nextNotify = body?.notify !== undefined ? Boolean(body.notify) : Boolean(target.notify ?? true);
        target.name = nextName;
        target.description = nextDesc;
        target.color = nextColor;
        target.shape = nextShape;
        target.model = nextModel;
        target.curiosity_enabled = nextCuriosity;
        target.notify = nextNotify;
        if (nextPersona) {
          target.persona = nextPersona;
          target.instructions = nextPersona;
        }
        runtimeState.botsRoster = dedupeAndNormalizeBots(roster);
        saveBotsRosterToDisk();
        const cloudBotId = resolveCloudBotId(target.id);
        const putPayload = {
          id: cloudBotId,
          name: nextName,
          description: nextDesc,
          instructions: target.instructions || nextPersona || `你是「${nextName}」`,
          color: nextColor,
          shape: nextShape,
          model: String(target.model || ""),
          curiosity_enabled: Boolean(target.curiosity_enabled),
          notify: Boolean(target.notify ?? true),
          source: "pc",
        };
        await Promise.all([
          cloudFetch(`/api/bots/${encodeURIComponent(cloudBotId)}`, "PUT", putPayload, 12000).catch(() => null),
          cloudFetch(`/api/bots/${encodeURIComponent(cloudBotId)}/identity`, "PATCH", {
            name: nextName,
            color: nextColor,
            shape: nextShape,
            persona: target.instructions || nextPersona,
            instructions: target.instructions || nextPersona,
            source: "pc",
          }, 12000).catch(() => null),
        ]);
      }
    }
    const panorama = getDshPanorama(runtimeState.activeCtx, Boolean(body?.force));
    const currentRoster = loadBotsRosterFromDisk();
    return {
      ok: true,
      bots: currentRoster,
      bot: findBotByIdOrName(runtimeState.activeBotId),
      active_bot_id: runtimeState.activeBotId,
      messages_epoch: runtimeState.messagesEpoch,
      panorama,
      phone_online: runtimeState.phoneOnline,
      phone_last_3_dialogs: runtimeState.phoneLast3Dialogs || [],
      shared_memories: runtimeState.sharedMemories,
    };
  }

  if (clean.startsWith("/local/infinite-chat")) {
    const op = String(clean.replace(/^\/local\/infinite-chat\/?/, "") || body?.op || "sync").trim().toLowerCase();

    if (op === "sync" || op === "history") {
      const nowMs = Date.now();
      const hasInflight = runtimeState.inflightControllersByBot.size > 0;
      if (Boolean(body?.force) || !runtimeState.lastPanoramaCache || (nowMs - (runtimeState.lastPanoramaAt || 0) > 12000 && !hasInflight)) {
        runtimeState.lastPanoramaCache = getDshPanorama(runtimeState.activeCtx, Boolean(body?.force));
        runtimeState.lastPanoramaAt = nowMs;
      }
      const syncIntervalMs = hasInflight ? 800 : 2500;
      if (Boolean(body?.force) || nowMs - (runtimeState.lastBgCloudSyncAt || 0) > syncIntervalMs) {
        runtimeState.lastBgCloudSyncAt = nowMs;
        Promise.all([
          cloudFetch("/api/sync?since_seq=0&source=pc", "GET", null, 8000).catch(() => null),
          cloudFetch("/api/dsh/status", "GET", null, 8000).catch(() => null),
        ]).then(([syncRes, dshSt]) => {
          if (dshSt?.ok && typeof dshSt.phone_online === "boolean") runtimeState.phoneOnline = dshSt.phone_online;
          if (Array.isArray(syncRes?.bots) && syncRes.bots.length > 0) {
            runtimeState.botsRoster = dedupeAndNormalizeBots(syncRes.bots);
            if (!runtimeState.botsRoster.some((b) => b.id === runtimeState.activeBotId)) {
              runtimeState.activeBotId = runtimeState.botsRoster[0]?.id || "pc_engineer";
            }
            saveBotsRosterToDisk();
          }
          if (Array.isArray(syncRes?.messages)) {
            const cloudEp = Number(syncRes?.messages_epoch || 0);
            if (cloudEp > 0 && runtimeState.cloudMessagesEpoch !== cloudEp && syncRes?.delta === false) {
              runtimeState.cloudMessagesEpoch = cloudEp;
              saveLocalChatCache(sanitizeMessagesList(syncRes.messages), true);
            } else if (syncRes.messages.length > 0) {
              if (cloudEp > 0) runtimeState.cloudMessagesEpoch = cloudEp;
              mergeCloudPcMessagesIntoCache(syncRes.messages);
            }
          }
          const ctxMap = syncRes?.context_by_bot || syncRes?.bot_context_by_id;
          if (ctxMap && typeof ctxMap === "object") {
            runtimeState.botContextById = { ...runtimeState.botContextById, ...ctxMap };
          }
          const incomingPins = syncRes?.pinned_memories || syncRes?.pins;
          if (Array.isArray(incomingPins)) {
            runtimeState.pinnedMemories = incomingPins;
          }
          if (syncRes?.streams_by_bot && typeof syncRes.streams_by_bot === "object") {
            const activeCloudBotIds = new Set();
            for (const [k, stVal] of Object.entries(syncRes.streams_by_bot)) {
              if (stVal && typeof stVal === "object") {
                const localB = findBotByIdOrName(k);
                if (localB) {
                  activeCloudBotIds.add(localB.id);
                  const exVal = syncRes.executions_by_bot?.[k] || syncRes.executions_by_bot?.[localB.id] || null;
                  runtimeState.liveStreamsByBot[localB.id] = {
                    text: String(stVal.text || ""),
                    thought: String(stVal.thought || ""),
                    tool: String(stVal.tool || exVal?.tool || ""),
                    trace: Array.isArray(stVal.trace) && stVal.trace.length ? stVal.trace : (Array.isArray(exVal?.trace) ? exVal.trace : []),
                    speaker_id: String(stVal.speaker_id || localB.id),
                    speaker_name: String(stVal.speaker_name || localB.name),
                    speaker_node: String(stVal.speaker_node || "pc"),
                  };
                }
              }
            }
            for (const existingBotId of Object.keys(runtimeState.liveStreamsByBot)) {
              const hasCloudExec = Boolean(syncRes.executions_by_bot?.[existingBotId]);
              if (!activeCloudBotIds.has(existingBotId) && !hasCloudExec) {
                delete runtimeState.liveStreamsByBot[existingBotId];
              }
            }
          }
        }).catch(() => {});
      }
      return {
        ok: true,
        messages: loadLocalChatCache(),
        messages_epoch: runtimeState.messagesEpoch,
        streams_by_bot: runtimeState.liveStreamsByBot,
        bot_context_by_id: runtimeState.botContextById || {},
        panorama: runtimeState.lastPanoramaCache,
        bots: loadBotsRosterFromDisk(),
        active_bot_id: runtimeState.activeBotId,
        vps_online: Boolean(runtimeState.vpsOnline),
        rtt_ms: Number(netTelemetry.lastRttMs || 0),
        route: String(netTelemetry.activeRoute || "direct"),
        phone_online: runtimeState.phoneOnline,
        phone_last_3_dialogs: runtimeState.phoneLast3Dialogs || [],
        pinned_memories: Array.isArray(runtimeState.pinnedMemories) ? runtimeState.pinnedMemories : [],
        pins: Array.isArray(runtimeState.pinnedMemories) ? runtimeState.pinnedMemories : [],
        shared_memories: runtimeState.sharedMemories,
      };
    }

    if (op === "compact") {
      const bot = findBotByIdOrName(body?.bot_id || runtimeState.activeBotId || "pc_engineer");
      const cloudBotId = resolveCloudBotId(bot.id);
      const compRes = await cloudFetch("/api/context/compact", "POST", { bot_id: cloudBotId, source: "pc" }, 90000).catch((e) => ({ ok: false, error: String(e?.message || e) }));
      if (compRes?.context_by_bot && typeof compRes.context_by_bot === "object") runtimeState.botContextById = { ...runtimeState.botContextById, ...compRes.context_by_bot };
      if (compRes?.marker && typeof compRes.marker === "object") mergeCloudPcMessagesIntoCache([compRes.marker]);
      return { ok: Boolean(compRes?.ok ?? true), ...compRes, messages: loadLocalChatCache(), bot_context_by_id: runtimeState.botContextById || {} };
    }

    if (op === "send" || op === "edit" || op === "reroll") return await executeCloudChatTurn(op, body || {});

    if (op === "stop") {
      const bot = findBotByIdOrName(body?.bot_id || runtimeState.activeBotId || "pc_engineer");
      const cloudBotId = resolveCloudBotId(bot.id);
      try { runtimeState.inflightControllersByBot.get(bot.id)?.abort(); } catch {}
      runtimeState.inflightControllersByBot.delete(bot.id);
      runtimeState.abortedEpochByBot.set(bot.id, (runtimeState.abortedEpochByBot.get(bot.id) || 0) + 1);
      const stopRes = await cloudFetch("/api/stop", "POST", { bot_id: cloudBotId, source: "pc" }, 10000).catch(() => null);
      saveLocalChatCache(loadLocalChatCache(), true);
      return { ok: true, stopped: true, bot_id: bot.id, messages: loadLocalChatCache(), messages_epoch: runtimeState.messagesEpoch, cloud_stop: stopRes };
    }

    if (op === "pin") {
      const pinRes = await cloudFetch("/api/memories/pins", "POST", {
        action: body?.action || "add", text: body?.text || "", pin_id: body?.pin_id || null, category: body?.category || "general", source: "pc",
      }, 15000).catch((e) => ({ ok: false, error: String(e?.message || e) }));
      return { ok: Boolean(pinRes?.ok || pinRes?.pins), ...pinRes };
    }

    if (op === "confirm") {
      const confRes = await cloudFetch("/api/confirm", "POST", { id: String(body?.id || "").trim(), accepted: Boolean(body?.accepted) }, 15000).catch((e) => ({ ok: false, error: String(e?.message || e) }));
      return { ok: Boolean(confRes?.ok), ...confRes };
    }

    if (op === "search_sessions") return searchDshSessions(runtimeState.activeCtx, body?.query, body?.status_filter);
    if (op === "inspect_session") return await inspectDshSessionDetail(runtimeState.activeCtx, body?.session_id || body?.query);
    if (op === "dispatch_session") return await dispatchToDshSession(runtimeState.activeCtx, body || {});
    if (op === "pc_shell") return await runLocalPcShellCommand(body || {});
    if (op === "pc_fs") return await runLocalPcFsOperation(body || {});

    if (op === "peek_phone") {
      const limit = Math.max(1, Math.min(50, Number(body?.limit) || 5));
      const st = await cloudFetch(`/api/dsh/status?limit=${limit}&source=phone`, "GET", null, 15000).catch(() => null);
      const dialogs = st?.dialogs || st?.phone_dialogs || st?.phone_last_3_dialogs || runtimeState.phoneLast3Dialogs || [];
      if (typeof st?.phone_online === "boolean") runtimeState.phoneOnline = st.phone_online;
      return { ok: Boolean(st?.ok), phone_online: runtimeState.phoneOnline, dialogs };
    }

    if (op === "message_phone") {
      const message = String(body?.message || body?.text || "").trim();
      if (!message) return { ok: false, error: "发送给手机/平板端的消息不能为空" };
      const targetBot = String(body?.bot_id || "companion").trim();
      const res = await cloudFetch("/api/blackboard", "POST", {
        action: "post", title: message.slice(0, 48), content: `@${targetBot} ${message}`,
        mentions: [targetBot], from_bot_id: "pc_engineer", from_bot_name: "电脑端 DSH", from_source: "pc",
      }, 15000).catch((e) => ({ ok: false, error: String(e?.message || e) }));
      return { ok: Boolean(res?.ok), reply: res?.ok ? `已在黑板异步 @${targetBot}` : "", error: res?.error || null };
    }
  }

  if (clean === "/local/speech-to-text") {
    const stt = getService(runtimeState.activeCtx, "speechToText") || getService(runtimeState.rootCtx, "speechToText");
    if (!stt) {
      if (!runtimeState.activeCtx) {
        const proxied = await proxyToDshHostIpc("/local/speech-to-text", body || {}, 35000);
        if (proxied && typeof proxied === "object") return proxied;
      }
      return { ok: false, error: "官方 SenseVoice 语音识别服务未加载，请重启 DeepSeek Harness 以激活语音插件" };
    }
    const op = String(body?.op || "transcribe").trim().toLowerCase();
    const snap = typeof stt.snapshot === "function" ? stt.snapshot() : {};
    const prov = Array.isArray(snap?.providers) ? snap.providers[0] : null;
    const providerId = prov?.id || snap?.selection?.providerId || "sensevoice-local";
    if (op === "status" || op === "prepare") {
      if (op === "prepare" && prov?.preparation?.phase !== "ready" && typeof stt.prepare === "function") {
        try { stt.prepare(providerId, {}); } catch {}
      }
      return { ok: true, providerId, phase: prov?.preparation?.phase || "ready", snapshot: snap };
    }
    if (prov?.preparation?.phase && prov.preparation.phase !== "ready") {
      if (typeof stt.prepare === "function") {
        try { stt.prepare(providerId, {}); } catch {}
      }
      return { ok: false, preparing: true, phase: prov.preparation.phase, error: `官方 SenseVoice 语音模型正在准备中 (${prov.preparation.phase})，请稍候几秒再试` };
    }
    const audioBase64 = String(body?.audioBase64 || body?.audio_b64 || "").trim();
    if (!audioBase64) return { ok: false, error: "未收到录音数据" };
    try {
      const audioBuf = Buffer.from(audioBase64, "base64");
      const spec = stt.resolve({ audio: audioBuf, providerId, language: body?.language || "auto" });
      const result = await stt.transcribe(spec, AbortSignal.timeout(30000));
      return { ok: true, text: String(result?.text || result || "").trim(), result };
    } catch (err) {
      return { ok: false, error: String(err?.message || err) };
    }
  }

  if (clean === "/local/vault") {
    const op = String(body?.op || body?.action || (method === "PUT" ? "write" : "list")).trim().toLowerCase();
    if (op === "read" && body?.path) {
      const rel = String(body.path).replace(/^memory_vault\//, "").replace(/^\/+/, "");
      const r = await cloudFetch(`/api/vault?path=${encodeURIComponent(rel)}`, "GET", null, 15000).catch((e) => ({ ok: false, error: String(e?.message || e) }));
      return { ok: Boolean(r && typeof r.content === "string"), path: rel, content: r?.content ?? "", error: r?.error || r?.detail };
    }
    if ((op === "write" || op === "save") && body?.path) {
      const rel = String(body.path).replace(/^memory_vault\//, "").replace(/^\/+/, "");
      const r = await cloudFetch("/api/vault", "PUT", { path: rel, content: String(body.content ?? "") }, 15000).catch((e) => ({ ok: false, error: String(e?.message || e) }));
      return { ok: Boolean(r?.saved || r?.ok), saved: Boolean(r?.saved || r?.ok), path: rel, error: r?.error || r?.detail };
    }
    if (op === "pin" || op === "unpin" || op === "add_pin" || op === "remove_pin") {
      const act = (op === "unpin" || op === "remove_pin") ? "unpin" : "pin";
      const pinId = String(body?.pin_id || body?.id || "").trim();
      const r = await cloudFetch("/api/memories/pins", "POST", { action: act, text: body?.text || "", pin_id: pinId, source: "pc" }, 15000).catch((e) => ({ ok: false, error: String(e?.message || e) }));
      const nextPins = Array.isArray(r?.pins) ? r.pins : [];
      if (Array.isArray(r?.pins)) runtimeState.pinnedMemories = nextPins;
      return { ok: Boolean(r?.ok || Array.isArray(r?.pins)), pins: nextPins, pinned_memories: nextPins, error: r?.error || r?.detail };
    }
    const [vRes, pRes] = await Promise.all([
      cloudFetch("/api/vault", "GET", null, 15000).catch(() => null),
      cloudFetch("/api/memories/pins", "GET", null, 10000).catch(() => null),
    ]);
    const listPins = Array.isArray(pRes?.pins) ? pRes.pins : (Array.isArray(runtimeState.pinnedMemories) ? runtimeState.pinnedMemories : []);
    runtimeState.pinnedMemories = listPins;
    return {
      ok: true,
      files: Array.isArray(vRes?.files) ? vRes.files : [],
      pins: listPins,
      pinned_memories: listPins,
    };
  }

  if (clean === "/local/upload") {
    try {
      const rawName = String(body?.name || "attachment.bin").replace(/[^a-zA-Z0-9._\u4e00-\u9fa5-]/g, "_").replace(/^\.+/, "") || "file.bin";
      const safeName = rawName.slice(0, 120);
      const rawB64 = String(body?.data_b64 || body?.data || "").replace(/^data:[^;]+;base64,/, "").trim();
      if (!rawB64) return { ok: false, error: "上传内容为空" };
      const buf = Buffer.from(rawB64, "base64");
      if (!buf.length || buf.length > 25 * 1024 * 1024) return { ok: false, error: "文件大小需在 1B ~ 25MB 之间" };
      const sha256 = crypto.createHash("sha256").update(buf).digest("hex");
      const startRes = await cloudFetch("/api/files/uploads", "POST", { name: safeName, size: buf.length, sha256 }, 20000);
      if (!startRes?.id) return { ok: false, error: startRes?.detail || startRes?.error || "创建上传会话失败" };
      const chunkSize = 360 * 1024;
      for (let off = 0; off < buf.length; off += chunkSize) {
        const slice = buf.subarray(off, Math.min(buf.length, off + chunkSize));
        const cRes = await cloudFetch(`/api/files/uploads/${encodeURIComponent(startRes.id)}`, "PUT", { offset: off, data: slice.toString("base64") }, 30000);
        if (typeof cRes?.offset !== "number") {
          await cloudFetch(`/api/files/uploads/${encodeURIComponent(startRes.id)}`, "DELETE", null, 8000).catch(() => {});
          return { ok: false, error: cRes?.detail || cRes?.error || "上传分块失败" };
        }
      }
      const finRes = await cloudFetch(`/api/files/uploads/${encodeURIComponent(startRes.id)}/finish`, "POST", {}, 20000);
      if (!finRes?.path) return { ok: false, error: finRes?.detail || finRes?.error || "合并上传文件失败" };
      return { ok: true, path: finRes.path, name: safeName, bytes: buf.length, sha256 };
    } catch (err) {
      return { ok: false, error: String(err?.message || err) };
    }
  }

  if (clean === "/local/models") {
    const r = await cloudFetch("/api/models", "POST", body || {}, 15000).catch(() => null);
    return { ok: true, models: Array.isArray(r?.models) ? r.models : [], base_url: r?.base_url || "" };
  }

  if (clean === "/local/sessions" || clean === "/sessions") {
    const p = getDshPanorama(runtimeState.activeCtx, true);
    return { ok: true, sessions: p.sessions, panorama: p };
  }

  if (clean === "/local/pc-shell") {
    return await runLocalPcShellCommand(body || {});
  }

  if (clean === "/local/pc-fs") {
    return await runLocalPcFsOperation(body || {});
  }

  if (clean === "/local/cua") {
    return await callCuaTool(body?.action || body?.tool || "list_windows", body || {}, body?.bot_name || "主助理");
  }

  if (clean === "/cloud/shared-memory") {
    const res = await cloudFetch("/api/dsh/shared-memory", method || "POST", { source: "pc", ...(body || {}) }, 25000);
    if (res?.shared_memories) runtimeState.sharedMemories = { ...runtimeState.sharedMemories, ...res.shared_memories };
    return res;
  }

  return null;
}

const HOST_ONLY_LOCAL_PATHS = new Set([
  "/local/speech-to-text",
  "/local/cua",
  "/local/sessions",
  "/sessions",
  "/local/infinite-chat/inspect_session",
  "/local/infinite-chat/dispatch_session",
  "/local/infinite-chat/search_sessions",
]);

async function forwardRequest(localPath, cloudPath, method = "GET", body = null) {
  const cleanLocal = String(localPath || "").split("?")[0];
  // When called inside DSH Host (`activeCtx != null`), delegate shared chat/bot/vault requests to the
  // permanent 18195 Sentinel Gateway so there is NEVER a split-brain cache between Host and Sentinel.
  if (runtimeState.activeCtx && !localHttpServer && (!cleanLocal || !HOST_ONLY_LOCAL_PATHS.has(cleanLocal))) {
    try {
      const port = Number(loadLocalConfig().local_port || 18195);
      const gwRes = await fetch(`http://127.0.0.1:${port}/api/dsh-deepseek-claw`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Connection: "close" },
        body: JSON.stringify({ localPath, cloudPath, method, body }),
        signal: AbortSignal.timeout(175000),
      });
      if (gwRes.ok) return await gwRes.json();
    } catch {}
  }
  if (localPath) {
    const handled = await handleLocalRequest(localPath, method, body);
    if (handled !== null) return handled;
  }
  if (!cloudPath) return { ok: false, error: "未指定请求路径" };
  try { return await cloudFetch(cloudPath, method, body, 175000); } catch (err) { return { ok: false, error: String(err?.message || err) }; }
}

let connectionRouteRegistered = false;
let localHttpServer = null;
let dshHostIpcServer = null;
const PROCESS_STARTED_AT = Date.now();

export function isPrimaryGatewayListening() {
  return Boolean(localHttpServer && localHttpServer.listening);
}

function createBridgeHttpHandler() {
  return (req, res) => {
    res.setHeader("Access-Control-Allow-Origin", "*");
    res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
    res.setHeader("Access-Control-Allow-Headers", "Content-Type, Authorization");
    res.setHeader("Connection", "close");
    if (req.method === "OPTIONS") { res.writeHead(204); res.end(); return; }
    if (req.method === "GET" && (req.url === "/health" || req.url === "/status")) {
      const cfg = loadLocalConfig();
      const hbAgeSec = runtimeState.lastHeartbeatOkMs > 0
        ? Math.max(0, Math.round((Date.now() - runtimeState.lastHeartbeatOkMs) / 1000))
        : -1;
      const effectiveVpsOnline = Boolean(runtimeState.vpsOnline && hbAgeSec >= 0 && hbAgeSec <= 45);
      const pan = runtimeState.lastPanoramaCache || {};
      res.writeHead(200, { "Content-Type": "application/json; charset=utf-8" });
      res.end(JSON.stringify({
        ok: true,
        pid: process.pid,
        uptime_sec: Math.round((Date.now() - PROCESS_STARTED_AT) / 1000),
        device_id: PC_DEVICE_ID,
        device_name: runtimeState.deviceName || "Windows一",
        server_url: cfg.server_url || "",
        active_route: netTelemetry.activeRoute,
        proxy_alive: Boolean(netTelemetry.proxyAlive),
        last_rtt_ms: Number(netTelemetry.lastRttMs || 0),
        last_error: String(netTelemetry.lastError || ""),
        has_dsh_ctx: Boolean(runtimeState.activeCtx),
        dsh_host_alive: Boolean(runtimeState.activeCtx || runtimeState.dshHostAlive),
        sessions_count: Number(pan.sessions_count ?? pan.total_sessions ?? 0),
        running_sessions_count: Number(pan.running_count || 0),
        bridge_running: Boolean(runtimeState.bridgeRunning),
        vps_online: effectiveVpsOnline,
        last_heartbeat_ok_age_sec: hbAgeSec,
        phone_online: Boolean(runtimeState.phoneOnline),
        heartbeat_count: Number(runtimeState.heartbeatCount || 0),
        last_heartbeat_at: String(runtimeState.lastHeartbeatAt || "等待握手"),
        bytes_sent: Number(runtimeState.bytesSent || 0),
        bytes_received: Number(runtimeState.bytesReceived || 0),
        log_path: getSentinelLogPath(),
      }));
      return;
    }
    if (req.url === "/local/logs") {
      res.writeHead(200, { "Content-Type": "application/json; charset=utf-8" });
      res.end(JSON.stringify({ ok: true, log_path: getSentinelLogPath(), logs: getRecentSentinelLogs(40) }));
      return;
    }
    if (req.url === "/local/reconnect") {
      const seq = triggerInstantReconnect("tray_manual_reconnect");
      if (!runtimeState.bridgeRunning) {
        startNativePcBridge(runtimeState.activeCtx);
      }
      res.writeHead(200, { "Content-Type": "application/json; charset=utf-8" });
      res.end(JSON.stringify({ ok: true, reconnecting: true, reconnect_seq: seq }));
      return;
    }
    if (req.method === "GET" && req.url && req.url.startsWith("/proxy-media?")) {
      try {
        const u = new URL(req.url, "http://127.0.0.1");
        const mediaPath = String(u.searchParams.get("path") || "").trim();
        const cfg = loadLocalConfig();
        if (!mediaPath || !cfg.server_url) {
          res.writeHead(404);
          res.end();
          return;
        }
        const fullUrl = mediaPath.startsWith("http")
          ? mediaPath
          : `${cfg.server_url.replace(/\/$/, "")}/${mediaPath.replace(/^\//, "")}`;
        const headers = {};
        if (cfg.token) headers.Authorization = `Bearer ${cfg.token}`;
        fetch(fullUrl, { method: "GET", headers, signal: AbortSignal.timeout(20000) })
          .then(async (upRes) => {
            if (!upRes.ok) {
              res.writeHead(upRes.status || 404);
              res.end();
              return;
            }
            const ct = upRes.headers.get("content-type") || "image/png";
            const buf = Buffer.from(await upRes.arrayBuffer());
            res.writeHead(200, {
              "Content-Type": ct,
              "Cache-Control": "public, max-age=3600",
            });
            res.end(buf);
          })
          .catch(() => {
            res.writeHead(502);
            res.end();
          });
      } catch {
        res.writeHead(400);
        res.end();
      }
      return;
    }
    let rawBody = "";
    req.on("data", (c) => { rawBody += c.toString("utf8"); });
    req.on("end", async () => {
      try {
        const payload = rawBody ? JSON.parse(rawBody) : {};
        const data = await forwardRequest(payload.localPath || null, payload.cloudPath || null, payload.method || req.method || "GET", payload.body ?? null);
        res.writeHead(200, { "Content-Type": "application/json; charset=utf-8" });
        res.end(JSON.stringify(data));
      } catch (err) {
        res.writeHead(200, { "Content-Type": "application/json; charset=utf-8" });
        res.end(JSON.stringify({ ok: false, error: String(err?.message || err) }));
      }
    });
  };
}

function ensureDshHostIpcServer() {
  if (dshHostIpcServer || !runtimeState.activeCtx) return;
  try {
    const srv = http.createServer(createBridgeHttpHandler());
    srv.on("error", () => {});
    srv.listen(DSH_HOST_IPC_PORT, "127.0.0.1", () => {
      dshHostIpcServer = srv;
    });
  } catch {}
}

let localLoopbackRetryTimer = null;
let localHttpServerBinding = false;

export function ensureLocalLoopbackServer() {
  if ((localHttpServer && localHttpServer.listening) || localHttpServerBinding) return;
  localHttpServerBinding = true;
  try {
    const port = Number(loadLocalConfig().local_port || 18195);
    const srv = http.createServer(createBridgeHttpHandler());
    srv.keepAliveTimeout = 1000;
    srv.on("error", (err) => {
      localHttpServerBinding = false;
      logSentinel("WARN", `监听本机网关端口 ${port} 失败 (${err?.code || err?.message || err})，将在 2.5s 后重试`);
      try { srv.close(); } catch {}
      if (!localHttpServer && !localLoopbackRetryTimer) {
        localLoopbackRetryTimer = setTimeout(() => {
          localLoopbackRetryTimer = null;
          ensureLocalLoopbackServer();
        }, 2500);
      }
    });
    srv.listen(port, "127.0.0.1", () => {
      localHttpServerBinding = false;
      if (localLoopbackRetryTimer) {
        clearTimeout(localLoopbackRetryTimer);
        localLoopbackRetryTimer = null;
      }
      localHttpServer = srv;
      logSentinel("INFO", `本机常驻网关已绑定 127.0.0.1:${port} (PID ${process.pid})`);
      srv.on("close", () => {
        if (localHttpServer === srv) localHttpServer = null;
        if (!localLoopbackRetryTimer) {
          localLoopbackRetryTimer = setTimeout(() => {
            localLoopbackRetryTimer = null;
            ensureLocalLoopbackServer();
          }, 1500);
        }
      });
      startNativePcBridge(runtimeState.activeCtx);
    });
  } catch (e) {
    localHttpServerBinding = false;
    logSentinel("ERROR", `ensureLocalLoopbackServer 异常: ${e?.message || e}`);
  }
}

async function ensureSentinelRunningFromDshHost() {
  const port = Number(loadLocalConfig().local_port || 18195);
  try {
    const r = await fetch(`http://127.0.0.1:${port}/status`, {
      method: "GET",
      headers: { Connection: "close" },
      signal: AbortSignal.timeout(1200),
    });
    if (r.ok) return;
  } catch {}
  // Background Sentinel is not up yet -> spawn minimized tray guardian (or sentinel.mjs directly)
  const sentinelRoot = fs.existsSync("D:\\dsh-baike-plugin\\sentinel.mjs") ? "D:\\dsh-baike-plugin" : __dirname;
  const trayExe = path.join(sentinelRoot, "DeepSeekClawSentinel.exe");
  const sentinelMjs = path.join(sentinelRoot, "sentinel.mjs");
  try {
    if (fs.existsSync(trayExe)) {
      const child = spawn(trayExe, ["--minimized"], { detached: true, stdio: "ignore", cwd: sentinelRoot, windowsHide: true });
      child.unref();
      return;
    }
    if (fs.existsSync(sentinelMjs)) {
      const child = spawn(process.execPath, [sentinelMjs], { detached: true, stdio: "ignore", cwd: sentinelRoot, windowsHide: true });
      child.unref();
      return;
    }
  } catch {}
  // Fallback if standalone files are absent: bind 18195 in-process
  ensureLocalLoopbackServer();
}

export function registerUiRoutes(ctx) {
  if (ctx) {
    runtimeState.activeCtx = ctx;
    runtimeState.rootCtx = ctx;
  }
  ensureDshHostIpcServer();
  ensureSentinelRunningFromDshHost().catch(() => {});
  const registerOnConnection = (conn) => {
    if (connectionRouteRegistered || typeof conn?.fetch?.register !== "function") return;
    try {
      for (const rPath of API_ROUTES) {
        conn.fetch.register({
          path: rPath,
          methods: ["POST", "GET"],
          requestBody: "buffered",
          fetch: async (request) => {
            try {
              const payload = request.method === "POST" ? await request.json().catch(() => ({})) : {};
              const data = await forwardRequest(payload.localPath || null, payload.cloudPath || null, payload.method || "GET", payload.body ?? null);
              return new Response(JSON.stringify(data), { status: 200, headers: { "content-type": "application/json" } });
            } catch (err) {
              return new Response(JSON.stringify({ ok: false, error: String(err?.message || err) }), { status: 200, headers: { "content-type": "application/json" } });
            }
          },
        });
      }
      connectionRouteRegistered = true;
    } catch {}
  };
  let hasInject = false;
  try { hasInject = typeof ctx?.inject === "function"; } catch {}
  if (hasInject) {
    try {
      ctx.inject(["connection"], (cCtx) => {
        try {
          if (!runtimeState.activeCtx) runtimeState.activeCtx = cCtx;
          registerOnConnection(getService(cCtx, "connection"));
        } catch {}
      });
    } catch {}
    try {
      ctx.inject(["sessionController", "agents", "sessions"], (sCtx) => {
        try {
          runtimeState.rootCtx = sCtx;
          runtimeState.activeCtx = sCtx;
          const sc = getService(sCtx, "sessionController");
          const ag = getService(sCtx, "agents");
          const ss = getService(sCtx, "sessions");
          if (sc) runtimeState.services.sessionController = sc;
          if (ag) runtimeState.services.agents = ag;
          if (ss) runtimeState.services.sessions = ss;
        } catch {}
      });
    } catch {}
    try {
      ctx.inject(["speechToText"], (spCtx) => {
        try {
          const stt = getService(spCtx, "speechToText");
          if (stt) runtimeState.services.speechToText = stt;
        } catch {}
      });
    } catch {}
  }
}

export function apply(ctx, config = {}) {
  if (config?.uiOnly === true) {
    registerUiRoutes(ctx);
    return;
  }
  // When loaded inside an Agent Preset (`deepseek-claw-tools`), NEVER overwrite `runtimeState.activeCtx`
  // with the temporary preset validation context, and NEVER call `registerUiRoutes` here!
  const reg = (tCtx) => {
    try {
      const toolsSvc = getService(tCtx, "tools");
      if (!toolsSvc || typeof toolsSvc.register !== "function") return false;
      registerAllDshTools(toolsSvc);
      return true;
    } catch {
      return false;
    }
  };
  let hasInject = false;
  try { hasInject = typeof ctx?.inject === "function"; } catch {}
  if (!reg(ctx) && hasInject) {
    try {
      ctx.inject(["tools"], reg);
    } catch {}
  }
}

if (process.env.DS_CLAW_SENTINEL_ONLY === "1") {
  ensureLocalLoopbackServer();
}

export default { name, version, description, apply };

