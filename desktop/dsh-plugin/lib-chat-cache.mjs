/**
 * [Module Responsibility]
 * 本地聊天缓存、Bot 花名册与云端对话轮次引擎 (`lib-chat-cache.mjs`)。
 * 负责：
 * 1. 维护默认与自定义 Bot 花名册 (`DEFAULT_BOTS_ROSTER`) 及本地磁盘持久化 (`bots-roster.json`)。
 * 2. 本地消息缓存 (`chat-history-cache.json`) 的清洗、去重与云端/PC 双端消息合并。
 * 3. 共享运行时状态 (`runtimeState`) 与本地配置文件 (`config.json`) 读取。
 * 4. 云端 HTTP 通信封装 (`cloudFetch`) 与单 Bot 聊天轮次流式等待 (`executeCloudChatTurn`)。
 *
 * [Public Interface]
 * - `runtimeState`: 共享运行时状态单例
 * - `DEFAULT_BOTS_ROSTER`, `dedupeAndNormalizeBots(rawList)`, `loadBotsRosterFromDisk()`, `saveBotsRosterToDisk(list)`
 * - `findBotByIdOrName(target)`, `resolveCloudBotId(bot)`
 * - `sanitizeMessagesList(list)`, `mergeCloudPcMessagesIntoCache(cloudMsgs, pcMsgs)`
 * - `loadLocalChatCache()`, `saveLocalChatCache(immediate)`
 * - `loadLocalConfig()`, `getService(context)`, `cloudFetch(urlPath, options)`
 * - `beginBotRequest(botId)`, `isBotRequestAborted(botId, reqId)`, `waitForCloudBotReply(...)`, `executeCloudChatTurn(...)`
 *
 * [Dependencies]
 * - Node.js 标准库: `node:fs`, `node:path`, `node:os`
 */
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { smartCloudFetchJson } from "./lib-sentinel-net.mjs";

const __dirname = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"));

export const HOSTNAME = os.hostname() || "Windows-PC";
export const PC_DEVICE_ID = `pc-${HOSTNAME.toLowerCase().replace(/[^a-z0-9_-]/g, "-")}`;
export const PC_DEVICE_NAME = `${HOSTNAME} (电脑端 DSH)`;
export const DSH_HOST_IPC_PORT = Number(process.env.DS_CLAW_HOST_IPC_PORT || 18196);

// Keep localhost IPC strictly direct; cloud requests use `smartCloudFetchJson` dual-route (Direct <-> Proxy)
if (!process.env.NO_PROXY && !process.env.no_proxy) {
  process.env.NO_PROXY = "localhost,127.0.0.1,::1";
} else {
  const curNoProxy = process.env.NO_PROXY || process.env.no_proxy || "";
  for (const host of ["localhost", "127.0.0.1", "::1"]) {
    if (!curNoProxy.includes(host)) {
      process.env.NO_PROXY = curNoProxy ? `${curNoProxy},${host}` : host;
    }
  }
}

let cachedWhaleDataUri = null;
export function getWhalechanAvatarUri() {
  if (cachedWhaleDataUri !== null) return cachedWhaleDataUri;
  try {
    const p = path.join(__dirname, "assets", "whalechan-eyeless.png");
    if (fs.existsSync(p)) {
      cachedWhaleDataUri = `data:image/png;base64,${fs.readFileSync(p).toString("base64")}`;
      return cachedWhaleDataUri;
    }
  } catch {}
  cachedWhaleDataUri = "";
  return "";
}

export const DEFAULT_BOTS_ROSTER = [
  { id: "pc_engineer", name: "司工", color: "#0A84FF", shape: "square", primary: true, description: "电脑端主控总工程师，负责代码架构、Git Bash/终端操控、DSH 会话编排与云电脑协同", persona: "你是电脑端专属主控总工程师「司工」，沉稳干练、动手极快，擅长代码工程、Git Bash 自动化、DSH 会话调度与云电脑操控。跨助理协作统一使用 blackboard，查看其他助理进度使用 peek_bots。" },
  { id: "pc_research", name: "探微", color: "#3478F6", shape: "circle", primary: false, description: "电脑端深度技术调研、文献检索与源码审计专家", persona: "你是电脑端调研专家「探微」，负责深度技术调研、文档考据、源码审计与事实核验，结论严谨有据。" },
  { id: "pc_reviewer", name: "审校", color: "#FF8A3D", shape: "diamond", primary: false, description: "电脑端独立审查员，专攻代码缺陷、边界条件与方案验收", persona: "你是电脑端独立审查员「审校」，专门对代码改动、架构方案与产出物做严格复核，挑出隐患与未测边界。" },
  { id: "pc_companion", name: "云汐", color: "#2EC4B6", shape: "flower", primary: false, description: "电脑端轻快灵感搭档，负责头脑风暴、文稿润色与桌面日常协理", persona: "你是电脑端灵感搭档「云汐」，温柔利落、富有创造力，擅长文稿打磨、灵感碰撞与日常事务协理。" },
];

const LEGACY_PHONE_ONLY_IDS = new Set(["companion", "research", "contrarian", "whalechan"]);

export const runtimeState = {
  deviceId: PC_DEVICE_ID,
  deviceName: PC_DEVICE_NAME,
  deviceType: "pc",
  vpsOnline: false,
  lastHeartbeatAt: "未连接",
  lastHeartbeatOkMs: 0,
  phoneOnline: false,
  phoneLastActiveTime: "",
  phoneLast3Dialogs: [],
  connectedDevices: [],
  dshPeers: [],
  sharedMemories: { pins: [], pinned_memories_md: "", user_traits: "", personal_impression: "" },
  bridgeId: `dsh-bridge-${PC_DEVICE_ID}`,
  bridgeGeneration: 0,
  bridgeRunning: false,
  activeCtx: null,
  rootCtx: null,
  services: {},
  lastAgentErrorBySid: new Map(),
  activeBotId: "pc_engineer",
  botsRoster: null,
  cachedPanorama: null,
  cachedPanoramaAt: 0,
  sessionDetailById: new Map(),
  localMessagesCache: null,
  messagesEpoch: 1,
  removedMessageIds: new Set(),
  inflightControllersByBot: new Map(),
  abortedEpochByBot: new Map(),
  cloudBotIdByLocalId: new Map(),
  liveStreamsByBot: {},
  botContextById: {},
};

export function loadLocalConfig() {
  const cfgPath = path.join(__dirname, "bridge_config.json");
  let cfg = {
    server_url: process.env.DS_CLAW_SERVER_URL || "http://216.23.82.196/proactive-agent",
    token: process.env.DS_CLAW_TOKEN || "",
    local_port: Number(process.env.DS_CLAW_LOCAL_PORT || 18195),
    dsh_home: path.join(os.homedir(), ".dsh"),
  };
  try {
    if (fs.existsSync(cfgPath)) cfg = { ...cfg, ...JSON.parse(fs.readFileSync(cfgPath, "utf8")) };
  } catch {}
  if (!cfg.token) {
    for (const c of [
      path.join(os.homedir(), ".dsh", "ds-claw-token"),
      "D:\\bubuki\\proactive-personal-agent\\private\\device-token",
      "D:\\bubuki\\proactive-personal-agent\\server\\data\\device-token",
    ]) {
      try {
        if (fs.existsSync(c)) {
          cfg.token = fs.readFileSync(c, "utf8").trim();
          if (cfg.token) break;
        }
      } catch {}
    }
  }
  cfg.server_url = String(cfg.server_url || "http://216.23.82.196/proactive-agent").replace(/\/$/, "");
  return cfg;
}

export function getService(ctx, key) {
  try {
    if (runtimeState.services?.[key]) return runtimeState.services[key];
    for (const target of [ctx, runtimeState.rootCtx, runtimeState.activeCtx]) {
      if (!target || typeof target !== "object") continue;
      // IMPORTANT: Never access `target[key]` directly on a Cordis Context (`ctx`)!
      // Cordis registers properties via `ctx.mixin` / Context getters that throw
      // `Error: cannot get property "parent" without inject` when accessed outside an `inject` scope,
      // and in DSH Desktop Host some internal getters emit an async rejection or host error if touched blindly.
      // Only use `target.get(key)` when `get` is a function on `target`.
      try {
        const getDesc = Object.getOwnPropertyDescriptor(target, "get") || Object.getOwnPropertyDescriptor(Object.getPrototypeOf(target) || {}, "get");
        const getFn = typeof getDesc?.value === "function" ? getDesc.value : null;
        if (getFn) {
          const v = getFn.call(target, key);
          if (v) {
            runtimeState.services[key] = v;
            return v;
          }
        }
      } catch {}
    }
  } catch {}
  return null;
}

export function dedupeAndNormalizeBots(rawItems = []) {
  const byId = new Map();
  const nameToId = new Map();
  const whaleUri = getWhalechanAvatarUri();
  const defaultById = new Map();
  const defaultByName = new Map();
  for (const def of DEFAULT_BOTS_ROSTER) {
    const defWithAvatar = { ...def, avatar_url: def.id === "whalechan" ? whaleUri : "" };
    defaultById.set(def.id, defWithAvatar);
    defaultByName.set(def.name.toLowerCase(), defWithAvatar);
  }
  const hasPcPrimary = Array.isArray(rawItems) && rawItems.some((x) => String(x?.id || "").trim() === "pc_engineer");
  const isLegacyPhoneOnlyCache = Array.isArray(rawItems) && rawItems.length > 0 && !hasPcPrimary &&
    rawItems.every((x) => LEGACY_PHONE_ONLY_IDS.has(String(x?.id || "").trim()));
  const sourceItems = Array.isArray(rawItems) && rawItems.length > 0 && !isLegacyPhoneOnlyCache ? rawItems : DEFAULT_BOTS_ROSTER;
  for (const item of sourceItems) {
    if (!item) continue;
    const rawId = String(item.id || "").trim();
    const rawName = String(item.name || "").trim();
    if (!rawId && !rawName) continue;
    const nameKey = rawName.toLowerCase();
    const matchedDef = defaultById.get(rawId) || defaultByName.get(nameKey) || null;
    const canonicalId = matchedDef ? matchedDef.id : (rawId || nameToId.get(nameKey) || "");
    if (!canonicalId) continue;
    if (matchedDef && rawId && rawId !== canonicalId) {
      runtimeState.cloudBotIdByLocalId.set(canonicalId, rawId);
    }
    const prev = byId.get(canonicalId) || matchedDef || {};
    const finalName = rawName || prev.name || canonicalId;
    const pcNodeCfg = (item.node_settings && typeof item.node_settings.pc === "object") ? item.node_settings.pc : null;
    nameToId.set(finalName.toLowerCase(), canonicalId);
    byId.set(canonicalId, {
      ...prev,
      id: canonicalId,
      name: finalName,
      color: String(item.color || item.avatar_color || prev.color || "#0A84FF"),
      shape: String(item.shape || prev.shape || "square"),
      primary: canonicalId === "pc_engineer" ? true : Boolean(item.primary ?? prev.primary ?? false),
      home_node: String(item.home_node || prev.home_node || "pc"),
      description: String(item.description || prev.description || ""),
      model: String((pcNodeCfg && pcNodeCfg.model !== undefined) ? pcNodeCfg.model : (item.model || prev.model || "")),
      curiosity_enabled: Boolean((pcNodeCfg && pcNodeCfg.curiosity_enabled !== undefined) ? pcNodeCfg.curiosity_enabled : (item.curiosity_enabled ?? prev.curiosity_enabled ?? (canonicalId === "pc_engineer"))),
      notify: Boolean((pcNodeCfg && pcNodeCfg.notify !== undefined) ? pcNodeCfg.notify : (item.notify ?? prev.notify ?? true)),
      persona: String(item.instructions || item.persona || prev.persona || ""),
      instructions: String(item.instructions || item.persona || prev.persona || ""),
      avatar_url: (canonicalId === "whalechan" || item.shape === "whalechan") ? whaleUri : String(item.avatar_url || prev.avatar_url || ""),
    });
  }
  if (!byId.has("pc_engineer") && defaultById.has("pc_engineer")) {
    const comp = defaultById.get("pc_engineer");
    return [comp, ...Array.from(byId.values())];
  }
  return Array.from(byId.values());
}

export function getBotsRosterFilePath() {
  const cfg = loadLocalConfig();
  return path.join(cfg.dsh_home || path.join(os.homedir(), ".dsh"), "baike-bots-roster.json");
}

export function loadBotsRosterFromDisk() {
  if (Array.isArray(runtimeState.botsRoster) && runtimeState.botsRoster.length > 0) {
    runtimeState.botsRoster = dedupeAndNormalizeBots(runtimeState.botsRoster);
    return runtimeState.botsRoster;
  }
  let saved = [], activeId = "pc_engineer";
  try {
    const p = getBotsRosterFilePath();
    if (fs.existsSync(p)) {
      const parsed = JSON.parse(fs.readFileSync(p, "utf8"));
      if (Array.isArray(parsed?.bots)) saved = parsed.bots;
      if (parsed?.active_bot_id) activeId = String(parsed.active_bot_id);
    }
  } catch {}
  runtimeState.botsRoster = dedupeAndNormalizeBots(saved);
  runtimeState.activeBotId = runtimeState.botsRoster.some((b) => b.id === activeId) ? activeId : (runtimeState.botsRoster[0]?.id || "pc_engineer");
  return runtimeState.botsRoster;
}

export function saveBotsRosterToDisk() {
  try {
    const p = getBotsRosterFilePath();
    fs.mkdirSync(path.dirname(p), { recursive: true });
    runtimeState.botsRoster = dedupeAndNormalizeBots(runtimeState.botsRoster || DEFAULT_BOTS_ROSTER);
    fs.writeFileSync(p, JSON.stringify({ active_bot_id: runtimeState.activeBotId, bots: runtimeState.botsRoster, updated_at: Date.now() }, null, 2), "utf8");
  } catch {}
}

export function findBotByIdOrName(botIdOrName) {
  const roster = loadBotsRosterFromDisk();
  const q = String(botIdOrName || "pc_engineer").trim().toLowerCase();
  for (const [localId, cloudId] of runtimeState.cloudBotIdByLocalId.entries()) {
    if (cloudId.toLowerCase() === q) return roster.find((b) => b.id === localId) || roster[0];
  }
  return roster.find((b) => b.id.toLowerCase() === q || b.name.toLowerCase() === q) || roster[0];
}

export function resolveCloudBotId(localBotId) {
  return runtimeState.cloudBotIdByLocalId.get(localBotId) || localBotId || "pc_engineer";
}

export function getLocalChatCachePath() {
  const cfg = loadLocalConfig();
  return path.join(cfg.dsh_home || path.join(os.homedir(), ".dsh"), "baike-infinite-chat-cache.json");
}

export function normMsgKind(k) {
  const s = String(k || "").trim();
  return !s || s === "chat" ? "chat" : s;
}

export function sanitizeMessagesList(rawMessages) {
  if (!Array.isArray(rawMessages)) return [];
  const out = [];
  for (const m of rawMessages) {
    if (!m || typeof m !== "object") continue;
    const text = String(m.text || "").trim();
    const sticker = String(m.sticker || "").trim();
    const rawId = String(m.id || "");
    if ((!text && !sticker) || m.source !== "pc") continue;
    const nKind = normMsgKind(m.kind);
    const role = (m.role === "user" && nKind !== "bot_message") ? "user" : "assistant";
    const botId = findBotByIdOrName(m.bot_id || "pc_engineer")?.id || "pc_engineer";
    const fromResolvedId = m.from_bot_id ? (findBotByIdOrName(m.from_bot_id)?.id || m.from_bot_id) : "";
    if (nKind === "bot_message" && m.direction === "outbound") continue;
    if (role === "assistant" && fromResolvedId && fromResolvedId !== botId && nKind !== "bot_message") continue;
    const incomingTrace = nKind === "bot_message"
      ? []
      : (Array.isArray(m.tool_trace) && m.tool_trace.length
          ? m.tool_trace
          : (Array.isArray(m.trace) ? m.trace : []));
    const mMetrics = m.metrics && typeof m.metrics === "object" ? m.metrics : {};
    const createdMs = Number(m.created || (m.created_at ? Math.round(Number(m.created_at) * 1000) : 0) || 0);
    const fmtTime = m.time || (createdMs > 0 ? new Date(createdMs).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false }) : new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false }));
    const norm = {
      ...m,
      id: rawId || `${role === "user" ? "pc" : "reply"}-${Date.now()}-${out.length}`,
      role,
      kind: nKind,
      text,
      thought: nKind === "bot_message" ? "" : String(m.thought || "").trim(),
      bot_id: botId,
      from_bot_id: m.from_bot_id || "",
      from_bot_name: m.from_bot_name || "",
      from_node: m.from_node || "",
      to_bot_id: m.to_bot_id || "",
      to_bot_name: m.to_bot_name || "",
      to_node: m.to_node || "",
      direction: m.direction || "",
      sticker,
      choices: Array.isArray(m.choices) ? m.choices : [],
      decision: m.decision || "",
      ttft_ms: Number(m.ttft_ms || mMetrics.ttft_ms || 0),
      tps: Number(m.tps || mMetrics.tps || 0),
      cache_hit_pct: Number(m.cache_hit_pct || mMetrics.cache_hit_pct || 0),
      api_calls: Number(m.api_calls || mMetrics.api_calls || 0),
      created: createdMs,
      source: "pc",
      pc_local: true,
      tool_trace: incomingTrace,
      time: fmtTime,
    };
    const prev = out[out.length - 1];
    const sameAssistantReply =
      prev &&
      !prev.is_sub_turn &&
      !norm.is_sub_turn &&
      prev.bot_id === norm.bot_id &&
      prev.role === norm.role &&
      prev.text === norm.text &&
      prev.sticker === norm.sticker &&
      (normMsgKind(prev.kind) === norm.kind ||
        (prev.role === "assistant" && normMsgKind(prev.kind) !== "bot_message" && norm.kind !== "bot_message"));
    if (prev && prev.bot_id === norm.bot_id && (prev.id === norm.id || prev.id === `reply-${norm.id}` || `reply-${prev.id}` === norm.id || sameAssistantReply)) {
      const mergedTrace = norm.tool_trace.length > (prev.tool_trace?.length || 0) ? norm.tool_trace : (prev.tool_trace || []);
      const bestKind = norm.kind === "bot_reply" || normMsgKind(prev.kind) === "bot_reply" ? "bot_reply" : (norm.kind || prev.kind || "chat");
      out[out.length - 1] = {
        ...prev,
        ...norm,
        id: prev.id.startsWith("reply-") && !norm.id.startsWith("reply-") ? norm.id : prev.id,
        kind: bestKind,
        from_bot_id: norm.from_bot_id || prev.from_bot_id || "",
        from_bot_name: (bestKind === "bot_reply" && (norm.from_bot_name || prev.from_bot_name)) ? (norm.from_bot_name?.includes("·") ? norm.from_bot_name : (prev.from_bot_name || norm.from_bot_name)) : (norm.from_bot_name || prev.from_bot_name || ""),
        from_node: norm.from_node || prev.from_node || "",
        to_bot_id: norm.to_bot_id || prev.to_bot_id || "",
        to_bot_name: norm.to_bot_name || prev.to_bot_name || "",
        to_node: norm.to_node || prev.to_node || "",
        direction: norm.direction || prev.direction || "",
        sticker: String(norm.sticker || prev.sticker || "").trim(),
        thought: String(norm.thought || prev.thought || "").trim(),
        tool_trace: mergedTrace,
      };
      continue;
    }
    out.push(norm);
  }
  return out.slice(-160);
}

export function mergeCloudPcMessagesIntoCache(cloudMessages) {
  if (!Array.isArray(cloudMessages) || cloudMessages.length === 0) return;
  const cached = [...loadLocalChatCache()];
  let changed = false;
  for (const cm of cloudMessages) {
    if (!cm || cm.source !== "pc") continue;
    const cid = String(cm.id || "");
    const replyCid = `reply-${cid}`;
    const pcCid = `pc-${cid}`;
    if (
      cid &&
      (runtimeState.removedMessageIds.has(cid) ||
        runtimeState.removedMessageIds.has(replyCid) ||
        runtimeState.removedMessageIds.has(pcCid) ||
        (cm.client_msg_id && runtimeState.removedMessageIds.has(String(cm.client_msg_id))))
    ) {
      continue;
    }
    const text = String(cm.text || "").trim();
    const sticker = String(cm.sticker || "").trim();
    if (!text && !sticker) continue;
    const cmKind = normMsgKind(cm.kind);
    const role = (cm.role === "user" && cmKind !== "bot_message") ? "user" : "assistant";
    const botId = findBotByIdOrName(cm.bot_id || "companion")?.id || "companion";
    const fromResolvedId = cm.from_bot_id ? (findBotByIdOrName(cm.from_bot_id)?.id || cm.from_bot_id) : "";
    if (cmKind === "bot_message" && cm.direction === "outbound") continue;
    if (role === "assistant" && fromResolvedId && fromResolvedId !== botId && cmKind !== "bot_message") continue;
    const cmMetrics = cm.metrics && typeof cm.metrics === "object" ? cm.metrics : {};
    const cmCreatedMs = Number(cm.created || (cm.created_at ? Math.round(Number(cm.created_at) * 1000) : 0) || 0);
    let existingIdx = -1;
    for (let i = cached.length - 1; i >= 0; i--) {
      const x = cached[i];
      if (!x) continue;
      if (
        (cid && (x.id === cid || x.id === replyCid || x.id === pcCid || x.client_msg_id === cid || x.id === cm.client_msg_id)) ||
        (!cm.is_sub_turn && !x.is_sub_turn && x.bot_id === botId && x.role === role && x.text === text && (x.sticker || "") === sticker && (normMsgKind(x.kind) === cmKind || (role === "assistant" && normMsgKind(x.kind) !== "bot_message" && cmKind !== "bot_message")))
      ) {
        existingIdx = i;
        break;
      }
    }
    if (existingIdx >= 0) {
      const prev = cached[existingIdx];
      const cmTrace = cmKind === "bot_message" ? [] : (Array.isArray(cm.trace) && cm.trace.length ? cm.trace : (Array.isArray(cm.tool_trace) ? cm.tool_trace : []));
      const traceArr = cmTrace.length > (prev.tool_trace?.length || 0) ? cmTrace : (prev.tool_trace || []);
      const nextTtft = Number(cm.ttft_ms || cmMetrics.ttft_ms || prev.ttft_ms || 0);
      const nextTps = Number(cm.tps || cmMetrics.tps || prev.tps || 0);
      const nextCachePct = Number(cm.cache_hit_pct || cmMetrics.cache_hit_pct || prev.cache_hit_pct || 0);
      const nextApiCalls = Number(cm.api_calls || cmMetrics.api_calls || prev.api_calls || 0);
      if (
        (cm.thought && !prev.thought && cmKind !== "bot_message") ||
        (cm.sticker && !prev.sticker) ||
        (traceArr.length > (prev.tool_trace?.length || 0)) ||
        (cmKind && normMsgKind(prev.kind) !== cmKind) ||
        (nextTtft > 0 && !prev.ttft_ms) ||
        (nextTps > 0 && !prev.tps) ||
        (cm.from_bot_name && !prev.from_bot_name) ||
        (cm.to_bot_name && !prev.to_bot_name) ||
        (cm.from_node && !prev.from_node) ||
        (cm.direction && !prev.direction)
      ) {
        cached[existingIdx] = {
          ...prev,
          kind: cmKind,
          from_bot_id: cm.from_bot_id || prev.from_bot_id || "",
          from_bot_name: cm.from_bot_name || prev.from_bot_name || "",
          from_node: cm.from_node || prev.from_node || "",
          to_bot_id: cm.to_bot_id || prev.to_bot_id || "",
          to_bot_name: cm.to_bot_name || prev.to_bot_name || "",
          to_node: cm.to_node || prev.to_node || "",
          direction: cm.direction || prev.direction || "",
          sticker: String(cm.sticker || prev.sticker || "").trim(),
          thought: cmKind === "bot_message" ? "" : String(cm.thought || prev.thought || "").trim(),
          choices: Array.isArray(cm.choices) && cm.choices.length ? cm.choices : (prev.choices || []),
          decision: cm.decision || prev.decision || "",
          ttft_ms: nextTtft,
          tps: nextTps,
          cache_hit_pct: nextCachePct,
          api_calls: nextApiCalls,
          created: cmCreatedMs || Number(prev.created || 0),
          tool_trace: traceArr,
        };
        changed = true;
      }
      continue;
    }
    cached.push({
      id: cid || `${role === "user" ? "pc" : "reply"}-${Date.now()}`,
      role,
      kind: cmKind,
      text,
      thought: cmKind === "bot_message" ? "" : String(cm.thought || "").trim(),
      bot_id: botId,
      from_bot_id: cm.from_bot_id || "",
      from_bot_name: cm.from_bot_name || "",
      from_node: cm.from_node || "",
      to_bot_id: cm.to_bot_id || "",
      to_bot_name: cm.to_bot_name || "",
      to_node: cm.to_node || "",
      direction: cm.direction || "",
      sticker: String(cm.sticker || "").trim(),
      choices: Array.isArray(cm.choices) ? cm.choices : [],
      decision: cm.decision || "",
      ttft_ms: Number(cm.ttft_ms || cmMetrics.ttft_ms || 0),
      tps: Number(cm.tps || cmMetrics.tps || 0),
      cache_hit_pct: Number(cm.cache_hit_pct || cmMetrics.cache_hit_pct || 0),
      api_calls: Number(cm.api_calls || cmMetrics.api_calls || 0),
      created: cmCreatedMs,
      source: "pc",
      pc_local: true,
      tool_trace: cmKind === "bot_message" ? [] : (Array.isArray(cm.trace) && cm.trace.length ? cm.trace : (Array.isArray(cm.tool_trace) ? cm.tool_trace : [])),
      time: cm.time || (cmCreatedMs > 0 ? new Date(cmCreatedMs).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false }) : new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false })),
    });
    changed = true;
  }
  if (changed) {
    saveLocalChatCache(cached, true);
  }
}

export function loadLocalChatCache() {
  if (Array.isArray(runtimeState.localMessagesCache)) return runtimeState.localMessagesCache;
  try {
    const p = getLocalChatCachePath();
    if (fs.existsSync(p)) {
      const parsed = JSON.parse(fs.readFileSync(p, "utf8"));
      if (Array.isArray(parsed?.messages)) {
        runtimeState.localMessagesCache = sanitizeMessagesList(parsed.messages);
        if (parsed?.messages_epoch) runtimeState.messagesEpoch = Number(parsed.messages_epoch) || 1;
        return runtimeState.localMessagesCache;
      }
    }
  } catch {}
  runtimeState.localMessagesCache = [];
  return runtimeState.localMessagesCache;
}

let chatCacheWriteTimer = null;
export function saveLocalChatCache(messages, bumpEpoch = false) {
  try {
    if (bumpEpoch) runtimeState.messagesEpoch = (Number(runtimeState.messagesEpoch) || 1) + 1;
    runtimeState.localMessagesCache = sanitizeMessagesList(messages);
    if (chatCacheWriteTimer) clearTimeout(chatCacheWriteTimer);
    chatCacheWriteTimer = setTimeout(() => {
      chatCacheWriteTimer = null;
      try {
        const p = getLocalChatCachePath();
        fs.mkdirSync(path.dirname(p), { recursive: true });
        const payload = JSON.stringify({ updated_at: Date.now(), messages_epoch: runtimeState.messagesEpoch, messages: runtimeState.localMessagesCache });
        fs.promises.writeFile(p, payload, "utf8").catch(() => {});
      } catch {}
    }, 350);
  } catch {}
}

export async function cloudFetch(cloudPath, method = "GET", body = null, timeoutMs = 45000, externalSignal = null) {
  const cfg = loadLocalConfig();
  if (!cfg.server_url) return { ok: false, error: "未配置云端地址" };
  const headers = { "Content-Type": "application/json" };
  if (cfg.token) headers.Authorization = `Bearer ${cfg.token}`;
  return await smartCloudFetchJson(`${cfg.server_url}${cloudPath}`, method, headers, body, timeoutMs, externalSignal);
}

export function beginBotRequest(botId) {
  try { runtimeState.inflightControllersByBot.get(botId)?.abort(); } catch {}
  const ctrl = new AbortController();
  runtimeState.inflightControllersByBot.set(botId, ctrl);
  const epoch = (runtimeState.abortedEpochByBot.get(botId) || 0) + 1;
  runtimeState.abortedEpochByBot.set(botId, epoch);
  return { ctrl, epoch };
}

export function isBotRequestAborted(botId, epoch, ctrl) {
  return Boolean(ctrl?.signal?.aborted) || (runtimeState.abortedEpochByBot.get(botId) || 0) !== epoch;
}

export async function waitForCloudBotReply(localBot, cloudBotId, epoch, ctrl, maxWaitMs = 150000, preTurnIds = null, expectedTurnId = "") {
  const deadline = Date.now() + maxWaitMs;
  let lastAssistant = null;
  let cursorSeq = 0;
  const knownBefore = preTurnIds instanceof Set ? preTurnIds : new Set();
  while (Date.now() < deadline) {
    if (isBotRequestAborted(localBot.id, epoch, ctrl)) {
      delete runtimeState.liveStreamsByBot[localBot.id];
      return { aborted: true, assistantMsg: lastAssistant };
    }
    await new Promise((r) => setTimeout(r, 180));
    if (isBotRequestAborted(localBot.id, epoch, ctrl)) {
      delete runtimeState.liveStreamsByBot[localBot.id];
      return { aborted: true, assistantMsg: lastAssistant };
    }
    const syncRes = await cloudFetch(`/api/sync?since_seq=${cursorSeq}&source=pc`, "GET", null, 8000, ctrl.signal).catch(() => null);
    if (!syncRes?.ok) continue;
    const incomingMsgs = Array.isArray(syncRes.messages) ? syncRes.messages : [];
    const recentTail = Array.isArray(syncRes.recent_tail) ? syncRes.recent_tail : [];
    const batchMsgs = incomingMsgs.length > 0 ? incomingMsgs : recentTail;
    if (batchMsgs.length > 0) {
      mergeCloudPcMessagesIntoCache(batchMsgs);
      for (const m of batchMsgs) {
        const s = Number(m?.seq || 0);
        if (s > cursorSeq) cursorSeq = s;
      }
    }
    const liveSt = syncRes.streams_by_bot?.[cloudBotId] || syncRes.streams_by_bot?.[localBot.id] || null;
    const liveEx = syncRes.executions_by_bot?.[cloudBotId] || syncRes.executions_by_bot?.[localBot.id] || null;
    if (liveSt || liveEx) {
      runtimeState.liveStreamsByBot[localBot.id] = {
        text: String(liveSt?.text || ""),
        thought: String(liveSt?.thought || ""),
        tool: String(liveSt?.tool || liveEx?.tool || ""),
        trace: Array.isArray(liveSt?.trace) && liveSt.trace.length ? liveSt.trace : (Array.isArray(liveEx?.trace) ? liveEx.trace : []),
        speaker_id: String(liveSt?.speaker_id || localBot.id),
        speaker_name: String(liveSt?.speaker_name || localBot.name),
        speaker_node: String(liveSt?.speaker_node || "pc"),
      };
    } else {
      delete runtimeState.liveStreamsByBot[localBot.id];
    }
    const botMsgs = batchMsgs.filter(
      (m) => m.source === "pc" && (m.bot_id === cloudBotId || m.bot_id === localBot.id)
    );
    const tail = botMsgs[botMsgs.length - 1];
    if (
      tail &&
      tail.role === "assistant" &&
      !tail.is_sub_turn &&
      normMsgKind(tail.kind) !== "bot_message" &&
      String(tail.text || "").trim() &&
      (!tail.id || (!knownBefore.has(String(tail.id)) && !knownBefore.has(`reply-${tail.id}`))) &&
      (!expectedTurnId || !tail.turn_id || tail.turn_id === expectedTurnId)
    ) {
      const tid = String(tail.id || `reply-${Date.now()}`);
      const kindNorm = normMsgKind(tail.kind);
      lastAssistant = {
        id: tid,
        role: "assistant",
        kind: kindNorm,
        text: String(tail.text).trim(),
        thought: String(tail.thought || (kindNorm === "bot_reply" ? "" : runtimeState.liveStreamsByBot[localBot.id]?.thought) || "").trim(),
        bot_id: localBot.id,
        from_bot_id: tail.from_bot_id || localBot.id,
        from_bot_name: tail.from_bot_name || localBot.name,
        from_node: tail.from_node || "",
        to_bot_id: tail.to_bot_id || "",
        to_bot_name: tail.to_bot_name || "",
        to_node: tail.to_node || "",
        direction: tail.direction || "",
        sticker: String(tail.sticker || "").trim(),
        source: "pc",
        pc_local: true,
        tool_trace: tail.trace || tail.tool_trace || [],
        time: tail.time || new Date().toLocaleTimeString("zh-CN", { hour12: false }),
      };
    }
    if (lastAssistant) {
      delete runtimeState.liveStreamsByBot[localBot.id];
      return { aborted: false, assistantMsg: lastAssistant };
    }
  }
  delete runtimeState.liveStreamsByBot[localBot.id];
  return { aborted: false, assistantMsg: lastAssistant };
}

/**
 * Unified Cloud Chat Turn Executor for `send`, `edit`, and `reroll`
 */
export async function executeCloudChatTurn(op, body = {}) {
  const bot = findBotByIdOrName(body?.bot_id || runtimeState.activeBotId || "companion");
  const cloudBotId = resolveCloudBotId(bot.id);
  const cached = [...loadLocalChatCache()];
  let kept = [];
  let removedIds = [];
  let userMsg = null;
  let reused = false;

  if (op === "send") {
    const text = String(body?.text || body?.message || "").trim();
    if (!text) return { ok: false, error: "消息不能为空" };
    let lastRoomIdx = -1;
    for (let i = cached.length - 1; i >= 0; i--) {
      if (cached[i].bot_id === bot.id) { lastRoomIdx = i; break; }
    }
    if (lastRoomIdx >= 0 && cached[lastRoomIdx].role === "user" && cached[lastRoomIdx].text === text) {
      userMsg = cached[lastRoomIdx];
      reused = true;
      kept = cached;
    } else {
      const rawId = String(body?.message_id || body?.client_msg_id || `pc-${Date.now()}`);
      userMsg = {
        id: rawId.startsWith("pc-") ? rawId : `pc-${rawId}`,
        role: "user",
        text,
        bot_id: bot.id,
        source: "pc",
        pc_local: true,
        time: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
      };
      kept = [...cached, userMsg];
      saveLocalChatCache(kept, false);
    }
  } else if (op === "edit") {
    const messageId = String(body?.message_id || "").trim();
    const newText = String(body?.text || "").trim();
    if (!messageId || !newText) return { ok: false, error: "message_id 和新内容不能为空" };
    let targetIdx = cached.findIndex((m) => m.id === messageId && m.bot_id === bot.id);
    if (targetIdx === -1) targetIdx = cached.findIndex((m) => m.id === messageId);
    if (targetIdx === -1) {
      for (let i = cached.length - 1; i >= 0; i--) {
        if (cached[i].bot_id === bot.id && cached[i].role === "user") { targetIdx = i; break; }
      }
    }
    if (targetIdx >= 0) {
      for (let i = 0; i < cached.length; i++) {
        if (i < targetIdx || cached[i].bot_id !== bot.id) kept.push(cached[i]);
        else if (i === targetIdx) {
          userMsg = { ...cached[i], id: String(cached[i].id || "").startsWith("pc-") ? cached[i].id : `pc-${cached[i].id || Date.now()}`, role: "user", text: newText, bot_id: bot.id, source: "pc", pc_local: true };
          kept.push(userMsg);
        } else removedIds.push(cached[i].id);
      }
    } else {
      userMsg = { id: messageId.startsWith("pc-") ? messageId : `pc-${messageId}`, role: "user", text: newText, bot_id: bot.id, source: "pc", pc_local: true, time: new Date().toLocaleTimeString("zh-CN", { hour12: false }) };
      kept = [...cached, userMsg];
    }
    saveLocalChatCache(kept, true);
  } else if (op === "reroll") {
    const targetId = body?.message_id ? String(body.message_id).trim() : null;
    let userIdx = -1;
    if (targetId) {
      const hitIdx = cached.findIndex((m) => m.id === targetId && m.bot_id === bot.id);
      for (let i = hitIdx; i >= 0; i--) {
        if (cached[i].bot_id === bot.id && cached[i].role === "user") { userIdx = i; break; }
      }
    }
    if (userIdx === -1) {
      for (let i = cached.length - 1; i >= 0; i--) {
        if (cached[i].bot_id === bot.id && cached[i].role === "user") { userIdx = i; break; }
      }
    }
    if (userIdx === -1) return { ok: false, error: "当前对话没有可重发的用户消息" };
    userMsg = cached[userIdx];
    for (let i = 0; i < cached.length; i++) {
      if (i <= userIdx || cached[i].bot_id !== bot.id) kept.push(cached[i]);
      else removedIds.push(cached[i].id);
    }
    saveLocalChatCache(kept, true);
  }

  const preTurnIds = new Set(loadLocalChatCache().map((m) => String(m.id || "")));
  const { ctrl, epoch } = beginBotRequest(bot.id);
  let primaryRes = null;
  try {
    if (op === "send") {
      primaryRes = await cloudFetch("/api/chat", "POST", { text: userMsg.text, bot_id: cloudBotId, mode: body?.mode || "auto", client_msg_id: userMsg.id, source: "pc", async_ack: true }, 25000, ctrl.signal);
    } else {
      const endpoint = op === "edit" ? "/api/messages/edit" : "/api/messages/reroll";
      const reqPayload = op === "edit"
        ? { message_id: userMsg.id, text: userMsg.text, bot_id: cloudBotId, source: "pc" }
        : { bot_id: cloudBotId, message_id: userMsg.id, source: "pc" };
      primaryRes = await cloudFetch(endpoint, "POST", reqPayload, 25000, ctrl.signal).catch(() => null);
      if (!primaryRes?.ok) {
        primaryRes = await cloudFetch("/api/chat", "POST", { text: userMsg.text, bot_id: cloudBotId, mode: "auto", client_msg_id: userMsg.id, source: "pc", async_ack: true }, 25000, ctrl.signal).catch(() => null);
      }
    }

    if (isBotRequestAborted(bot.id, epoch, ctrl)) {
      return { ok: true, aborted: true, user_message: userMsg, edited_message: userMsg, removed_message_ids: removedIds, messages: loadLocalChatCache(), messages_epoch: runtimeState.messagesEpoch };
    }

    if (primaryRes?.user_message?.id) {
      preTurnIds.add(String(primaryRes.user_message.id));
    }
    const allRemoved = [
      ...removedIds,
      ...(Array.isArray(primaryRes?.removed_message_ids) ? primaryRes.removed_message_ids : []),
    ];
    if (allRemoved.length > 0) {
      for (const rid of allRemoved) {
        const s = String(rid || "").trim();
        if (!s) continue;
        runtimeState.removedMessageIds.add(s);
        runtimeState.removedMessageIds.add(s.replace(/^reply-/, ""));
        runtimeState.removedMessageIds.add(`reply-${s.replace(/^reply-/, "")}`);
      }
      const cleaned = loadLocalChatCache().filter(
        (m) => !runtimeState.removedMessageIds.has(String(m.id || ""))
      );
      saveLocalChatCache(cleaned, false);
    }
    const expectedTurnId = String(primaryRes?.turn_id || "");

    let assistantMsg = null;
    const directReply = primaryRes?.reply || primaryRes?.text || primaryRes?.message?.text || "";
    if (directReply) {
      const rid = String(primaryRes?.message?.id || `reply-${Date.now()}`);
      const mObj = primaryRes?.message || {};
      assistantMsg = {
        id: rid,
        role: "assistant",
        kind: normMsgKind(mObj.kind || primaryRes?.kind || "chat"),
        text: String(directReply).trim(),
        thought: String(primaryRes?.thought || mObj.thought || "").trim(),
        bot_id: bot.id,
        from_bot_id: mObj.from_bot_id || bot.id,
        from_bot_name: mObj.from_bot_name || bot.name,
        from_node: mObj.from_node || "",
        to_bot_id: mObj.to_bot_id || "",
        to_bot_name: mObj.to_bot_name || "",
        to_node: mObj.to_node || "",
        direction: mObj.direction || "",
        sticker: String(primaryRes?.sticker || mObj.sticker || "").trim(),
        source: "pc",
        pc_local: true,
        tool_trace: primaryRes?.tool_trace || primaryRes?.trace || mObj.trace || [],
        time: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
      };
    } else if (primaryRes?.ok || primaryRes?.queued || primaryRes?.turn_id) {
      const waited = await waitForCloudBotReply(bot, cloudBotId, epoch, ctrl, 150000, preTurnIds, expectedTurnId);
      if (waited.assistantMsg) assistantMsg = waited.assistantMsg;
    }

    if (!isBotRequestAborted(bot.id, epoch, ctrl)) {
      const postSync = await cloudFetch("/api/sync?since_seq=0&source=pc", "GET", null, 8000).catch(() => null);
      if (Array.isArray(postSync?.messages) && postSync.messages.length > 0) {
        mergeCloudPcMessagesIntoCache(postSync.messages);
      }
      if (assistantMsg) {
        const latestCache = [...loadLocalChatCache()];
        const existIdx = latestCache.findIndex(
          (x) => x.id === assistantMsg.id || (x.bot_id === assistantMsg.bot_id && x.role === "assistant" && x.text === assistantMsg.text)
        );
        if (existIdx >= 0) {
          const prev = latestCache[existIdx];
          const bestTrace = (assistantMsg.tool_trace?.length || 0) > (prev.tool_trace?.length || 0)
            ? assistantMsg.tool_trace
            : (prev.tool_trace || []);
          latestCache[existIdx] = {
            ...prev,
            ...assistantMsg,
            id: prev.id,
            thought: String(assistantMsg.thought || prev.thought || "").trim(),
            tool_trace: bestTrace,
          };
          saveLocalChatCache(latestCache, op !== "send");
        } else {
          saveLocalChatCache([...latestCache, assistantMsg], op !== "send");
        }
      }
    }
  } finally {
    if (runtimeState.inflightControllersByBot.get(bot.id) === ctrl) {
      runtimeState.inflightControllersByBot.delete(bot.id);
    }
    delete runtimeState.liveStreamsByBot[bot.id];
  }

  return {
    ok: Boolean(primaryRes?.ok || primaryRes?.queued || assistantMsg),
    reused,
    user_message: userMsg,
    edited_message: userMsg,
    removed_message_ids: removedIds,
    reply: assistantMsg?.text || primaryRes?.reply || primaryRes?.text || primaryRes?.message?.text || "",
    messages: loadLocalChatCache(),
    messages_epoch: runtimeState.messagesEpoch,
    streams_by_bot: runtimeState.liveStreamsByBot,
    panorama: runtimeState.lastPanoramaCache || (runtimeState.getDshPanorama ? await runtimeState.getDshPanorama(runtimeState.activeCtx, false) : null),
  };
}

export async function handleBotArchiveLocalRoute(op, body = {}) {
  const roster = loadBotsRosterFromDisk();
  if (op === "export") {
    const target = findBotByIdOrName(body?.bot_id || runtimeState.activeBotId || "pc_engineer");
    const cloudBotId = resolveCloudBotId(target?.id || "pc_engineer");
    const cloudRes = await cloudFetch("/api/bots/export", "POST", {
      bot_id: cloudBotId,
      include_messages: body?.include_messages !== false,
      max_messages: Number(body?.max_messages || 500),
    }, 30000).catch(() => null);
    if (cloudRes?.ok && cloudRes?.bundle) {
      return { ok: true, filename: cloudRes.filename || `${cloudBotId}.dsb-bot.json`, bundle: cloudRes.bundle };
    }
    const localMsgs = loadLocalChatCache().filter((m) => m.bot_id === target.id).slice(-500);
    const fallbackBundle = {
      format: "dsb-bot-archive",
      version: "1.0",
      exported_at: new Date().toISOString(),
      bot: { ...target },
      messages: localMsgs,
      stats: { messages_count: localMsgs.length },
    };
    return { ok: true, filename: `${target.id}.dsb-bot.json`, bundle: fallbackBundle };
  }
  const bundle = body?.bundle && typeof body.bundle === "object" ? body.bundle : body;
  const impRes = await cloudFetch("/api/bots/import", "POST", {
    bundle,
    mode: String(body?.mode || "merge"),
    target_bot_id: String(body?.target_bot_id || ""),
    home_node: "pc",
    source: "pc",
    import_messages: body?.import_messages !== false,
  }, 45000).catch((e) => ({ ok: false, error: String(e?.message || e) }));
  if (!impRes?.ok) return { ok: false, error: impRes?.error || impRes?.detail || "导入助理档案失败" };
  if (Array.isArray(impRes.bots) && impRes.bots.length > 0) {
    runtimeState.botsRoster = dedupeAndNormalizeBots(impRes.bots);
  } else if (impRes.bot && typeof impRes.bot === "object") {
    runtimeState.botsRoster = dedupeAndNormalizeBots([...roster.filter((b) => b.id !== impRes.bot.id), impRes.bot]);
  }
  if (impRes.bot_id) runtimeState.activeBotId = impRes.bot_id;
  saveBotsRosterToDisk();
  const postSync = await cloudFetch("/api/sync?since_seq=0&source=pc", "GET", null, 8000).catch(() => null);
  if (Array.isArray(postSync?.messages) && postSync.messages.length > 0) {
    mergeCloudPcMessagesIntoCache(postSync.messages);
  }
  return {
    ok: true,
    bot_id: impRes.bot_id,
    bot: findBotByIdOrName(impRes.bot_id || runtimeState.activeBotId),
    bots: loadBotsRosterFromDisk(),
    active_bot_id: runtimeState.activeBotId,
    messages: loadLocalChatCache(),
    imported_messages: Number(impRes.imported_messages || 0),
  };
}
