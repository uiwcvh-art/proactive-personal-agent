/**
 * DeepSeek Claw Permanent Background Sentinel Daemon (`sentinel.mjs` v3.0)
 * - Exclusively owns `127.0.0.1:18197` (single-instance control port) AND `127.0.0.1:18195` (permanent local gateway).
 * - Never yields `18195`, eliminating Keep-Alive port-handoff deadlocks completely.
 * - Includes built-in self-heal loop verifying `18195` stays bound and logging structured diagnostics to `~/.dsh/sentinel.log`.
 */
import http from "node:http";
import { logSentinel } from "./lib-sentinel-net.mjs";

process.env.DS_CLAW_SENTINEL_ONLY = "1";
process.title = "DeepSeek-Claw-Sentinel";

process.on("uncaughtException", (err) => {
  logSentinel("ERROR", `UncaughtException: ${err?.stack || err?.message || err}`);
});
process.on("unhandledRejection", (reason) => {
  logSentinel("WARN", `UnhandledRejection: ${reason?.stack || reason?.message || reason}`);
});

let gatewayModule = null;

const singleInstanceLock = http.createServer((req, res) => {
  res.setHeader("Connection", "close");
  if (req.url === "/shutdown" && req.method === "POST") {
    logSentinel("INFO", `收到 /shutdown 指令，准备优雅退出 (PID ${process.pid})`);
    res.writeHead(200, { "Content-Type": "application/json; charset=utf-8" });
    res.end(JSON.stringify({ ok: true, shutting_down: true, pid: process.pid }));
    setTimeout(() => process.exit(0), 120);
    return;
  }
  const gwListening = Boolean(gatewayModule?.isPrimaryGatewayListening?.());
  res.writeHead(200, { "Content-Type": "application/json; charset=utf-8" });
  res.end(JSON.stringify({
    ok: true,
    sentinel: true,
    version: "3.0.0",
    pid: process.pid,
    port18195_listening: gwListening,
  }));
});

singleInstanceLock.keepAliveTimeout = 1000;
singleInstanceLock.on("error", () => {
  process.exit(0);
});

singleInstanceLock.listen(18197, "127.0.0.1", async () => {
  logSentinel("INFO", `Sentinel 守护内核启动成功 (PID ${process.pid}, 控制端口 127.0.0.1:18197)`);
  try {
    gatewayModule = await import("./index.js");
    gatewayModule.ensureLocalLoopbackServer?.();
    setInterval(() => {
      try {
        if (gatewayModule && !gatewayModule.isPrimaryGatewayListening?.()) {
          logSentinel("WARN", "自检发现 18195 未处于监听态，立即触发自愈重绑");
          gatewayModule.ensureLocalLoopbackServer?.();
        }
      } catch {}
    }, 5000).unref();
  } catch (err) {
    logSentinel("ERROR", `加载 index.js 失败: ${err?.stack || err?.message || err}`);
    process.exit(1);
  }
});
