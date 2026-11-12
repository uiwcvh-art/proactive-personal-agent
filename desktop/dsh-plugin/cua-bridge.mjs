#!/usr/bin/env node
/**
 * DeepSeek Claw PC CUA Bridge (`cua-bridge.mjs`)
 *
 * Separation of Duties ("手机控制手机，电脑控制电脑，各自其职"):
 * - Provides programmatic & CLI access for the PC Bot in DeepSeek Harness (`dsh-plugin`)
 *   to drive the local Windows CUA engine (`dsh-plugin/cua/index.mjs` -> `ds-claw-cua`).
 * - Automatically displays the topmost click-through HUD banner:
 *   `「主助理 正在操控你的电脑」` or `「鲸鱼娘 正在操控你的电脑」` with 240Hz silky cursor & neon border glow.
 */
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const CUA_MCP_SCRIPT = path.join(__dirname, "cua", "index.mjs");

let mcpClient = null;
let reqId = 1;

function ensureCuaClient(defaultBotName = process.env.DS_CLAW_BOT_NAME || "主助理") {
  if (mcpClient && mcpClient.proc.exitCode === null && !mcpClient.proc.killed) {
    return mcpClient;
  }
  const proc = spawn(process.execPath, [CUA_MCP_SCRIPT], {
    stdio: ["pipe", "pipe", "pipe"],
    env: {
      ...process.env,
      DS_CLAW_BOT_NAME: defaultBotName,
    },
    windowsHide: true,
  });

  const client = {
    proc,
    pending: new Map(),
    buf: "",
    initialized: false,
  };

  const abortAll = (err) => {
    for (const p of client.pending.values()) {
      clearTimeout(p.timer);
      p.reject(err);
    }
    client.pending.clear();
    if (mcpClient === client) mcpClient = null;
  };

  proc.on("error", abortAll);
  proc.on("exit", (code) => abortAll(new Error(`ds-claw-cua MCP exited (${code})`)));
  proc.stdout.on("data", (chunk) => {
    client.buf += chunk.toString("utf8");
    let idx;
    while ((idx = client.buf.indexOf("\n")) >= 0) {
      const line = client.buf.slice(0, idx).trim();
      client.buf = client.buf.slice(idx + 1);
      if (!line) continue;
      let msg;
      try {
        msg = JSON.parse(line);
      } catch {
        continue;
      }
      const p = client.pending.get(msg.id);
      if (!p) continue;
      client.pending.delete(msg.id);
      clearTimeout(p.timer);
      if (msg.error) {
        p.reject(new Error(msg.error.message || JSON.stringify(msg.error)));
      } else {
        p.resolve(msg.result);
      }
    }
  });

  mcpClient = client;
  return client;
}

function sendJsonRpc(client, method, params = {}, timeoutMs = 65000) {
  const id = reqId++;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      client.pending.delete(id);
      reject(new Error(`ds-claw-cua ${method} timed out after ${timeoutMs}ms`));
    }, timeoutMs);
    client.pending.set(id, { resolve, reject, timer });
    client.proc.stdin.write(JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n");
  });
}

const ACTION_ALIAS_MAP = {
  open: "launch_app",
  open_app: "launch_app",
  start_app: "launch_app",
  inspect: "get_window_state",
  screenshot: "get_window_state",
  snapshot: "get_window_state",
  state: "get_window_state",
  tap: "click",
  left_click: "click",
  double_tap: "click",
  double_click: "click",
  right_click: "click",
  type: "type_text",
  input: "type_text",
  press: "press_key",
  key: "press_key",
  hotkey: "press_key",
  swipe: "drag",
  close: "close_window",
  close_window: "close_window",
};

export async function callCuaTool(toolName, args = {}, botName = null) {
  const rawAction = String(toolName || args?.action || args?.op || "list_windows").trim().toLowerCase();
  const mappedTool = ACTION_ALIAS_MAP[rawAction] || rawAction;
  const effectiveBot = botName || args?.bot_name || process.env.DS_CLAW_BOT_NAME || "主助理";
  const client = ensureCuaClient(effectiveBot);
  if (!client.initialized) {
    await sendJsonRpc(client, "initialize", {
      protocolVersion: "2025-06-18",
      capabilities: {},
      clientInfo: { name: "dsh-deepseek-claw-pc-bot", version: "2.1.0" },
    });
    client.initialized = true;
  }
  const finalArgs = { ...args, bot_name: effectiveBot };
  if (rawAction === "double_tap" || rawAction === "double_click") finalArgs.click_count = 2;
  if (rawAction === "right_click") finalArgs.mouse_button = "right";
  if ((mappedTool === "get_window_state") && finalArgs.include_screenshot === undefined) {
    finalArgs.include_screenshot = true;
  }
  const result = await sendJsonRpc(client, "tools/call", {
    name: mappedTool,
    arguments: finalArgs,
  });
  const isError = Boolean(result?.isError);
  const textParts = (result?.content || [])
    .filter((c) => c.type === "text")
    .map((c) => c.text)
    .join("\n");
  const images = (result?.content || []).filter((c) => c.type === "image");
  return {
    ok: !isError,
    bot_name: effectiveBot,
    banner: `「${effectiveBot} 正在操控你的电脑」`,
    tool: toolName,
    text: textParts,
    images,
    raw: result,
  };
}

export function closeCuaBridge() {
  if (mcpClient) {
    try {
      mcpClient.proc.kill();
    } catch {}
    mcpClient = null;
  }
}

// CLI mode: node cua-bridge.mjs <toolName> [jsonArgs] [botName]
if (process.argv[1] && path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url))) {
  const toolName = process.argv[2] || "list_windows";
  let parsedArgs = {};
  if (process.argv[3]) {
    try {
      parsedArgs = JSON.parse(process.argv[3]);
    } catch {}
  }
  const botName = process.argv[4] || parsedArgs.bot_name || process.env.DS_CLAW_BOT_NAME || "主助理";
  callCuaTool(toolName, parsedArgs, botName)
    .then((res) => {
      console.log(JSON.stringify(res, null, 2));
      closeCuaBridge();
      process.exit(res.ok ? 0 : 1);
    })
    .catch((err) => {
      console.error(JSON.stringify({ ok: false, error: String(err?.message || err) }));
      closeCuaBridge();
      process.exit(1);
    });
}
