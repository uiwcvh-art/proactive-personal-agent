/**
 * DeepSeek Claw (`DS-Claw`) One-Click Auto-Installer & Profile Mounter (`setup.mjs`)
 */
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = process.env.DS_CLAW_REPO_ROOT || "D:\\bubuki\\proactive-personal-agent";

function resolveToken() {
  if (process.env.DS_CLAW_TOKEN) return process.env.DS_CLAW_TOKEN.trim();
  try {
    const existingCfg = path.join(__dirname, "bridge_config.json");
    if (fs.existsSync(existingCfg)) {
      const parsed = JSON.parse(fs.readFileSync(existingCfg, "utf8"));
      if (parsed?.token && parsed.token !== "dsclaw-default-token") return String(parsed.token).trim();
    }
  } catch {}
  const candidates = [
    path.join(os.homedir(), ".dsh", "ds-claw-token"),
    path.join(REPO_ROOT, "private", "device-token"),
    path.join(REPO_ROOT, "server", "data", "device-token"),
  ];
  for (const c of candidates) {
    if (fs.existsSync(c)) {
      const t = fs.readFileSync(c, "utf8").trim();
      if (t) return t;
    }
  }
  return "dsclaw-default-token";
}

function copyDirRecursive(src, dest) {
  fs.mkdirSync(dest, { recursive: true });
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    if (entry.name === "node_modules" || entry.name === ".git" || entry.name === "DeepSeekClawSentinel.exe" || entry.name === "SentinelApp.cs") continue;
    const s = path.join(src, entry.name);
    const d = path.join(dest, entry.name);
    if (entry.isDirectory()) copyDirRecursive(s, d);
    else fs.copyFileSync(s, d);
  }
}

function mountIntoProfile(profileDir) {
  if (!fs.existsSync(profileDir)) return false;
  const pkgPath = path.join(profileDir, "package.json");
  if (!fs.existsSync(pkgPath)) return false;

  const pkgBak = `${pkgPath}.before-ds-claw.bak`;
  if (!fs.existsSync(pkgBak)) fs.copyFileSync(pkgPath, pkgBak);

  const targetModDir = path.join(profileDir, "node_modules", "dsh-deepseek-claw");
  let sameRealDir = false;
  try {
    if (fs.existsSync(targetModDir)) {
      sameRealDir = fs.realpathSync(targetModDir).toLowerCase() === fs.realpathSync(__dirname).toLowerCase();
    }
  } catch {}
  if (!sameRealDir) {
    copyDirRecursive(__dirname, targetModDir);
  }

  const pkg = JSON.parse(fs.readFileSync(pkgPath, "utf8"));
  pkg.dependencies = pkg.dependencies || {};
  pkg.dependencies["dsh-deepseek-claw"] = `link:${__dirname.replace(/\\/g, "/")}`;
  pkg.dsh = pkg.dsh || {};
  pkg.dsh.profile = pkg.dsh.profile || {};
  pkg.dsh.profile.bundles = Array.isArray(pkg.dsh.profile.bundles)
    ? pkg.dsh.profile.bundles
    : ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-web-app"];
  if (
    profileDir.toLowerCase().includes("desktop") &&
    !pkg.dsh.profile.bundles.includes("@deepseek-ai/dsh-experimental-voice-input-bundle")
  ) {
    pkg.dsh.profile.bundles.push("@deepseek-ai/dsh-experimental-voice-input-bundle");
  }
  if (!pkg.dsh.profile.bundles.includes("dsh-deepseek-claw")) {
    pkg.dsh.profile.bundles.push("dsh-deepseek-claw");
  }
  fs.writeFileSync(pkgPath, JSON.stringify(pkg, null, 2) + "\n", "utf8");
  return true;
}

async function main() {
  const serverUrl = (process.env.DS_CLAW_SERVER_URL || "http://216.23.82.196/proactive-agent").replace(/\/$/, "");
  const token = resolveToken();
  const localPort = Number(process.env.DS_CLAW_LOCAL_PORT || 18195);

  const bridgeCfgPath = path.join(__dirname, "bridge_config.json");
  const bridgeCfg = {
    server_url: serverUrl,
    token,
    local_port: localPort,
    dsh_home: path.join(os.homedir(), ".dsh"),
    updated_at: new Date().toISOString(),
  };
  fs.writeFileSync(bridgeCfgPath, JSON.stringify(bridgeCfg, null, 2) + "\n", "utf8");
  console.log(`[1/3] Wrote bridge_config.json -> ${bridgeCfgPath}`);

  const profiles = [
    path.join(os.homedir(), ".dsh", "profiles", "desktop"),
    "D:\\DeepSeek-Harness\\data\\profiles\\web",
  ];
  for (const p of profiles) {
    if (mountIntoProfile(p)) {
      console.log(`[2/3] Mounted dsh-deepseek-claw into DSH profile: ${p}`);
    }
  }

  // Install lightweight background Sentinel GUI mini-program in Windows Registry Run + Startup folder
  const startupDir = path.join(
    process.env.APPDATA || path.join(os.homedir(), "AppData", "Roaming"),
    "Microsoft", "Windows", "Start Menu", "Programs", "Startup"
  );
  const startupVbs = path.join(startupDir, "DeepSeek-Claw-Sentinel.vbs");
  const sentinelMjs = path.join(__dirname, "sentinel.mjs");
  const sentinelExe = path.join(__dirname, "DeepSeekClawSentinel.exe");
  const nodeExe = process.execPath;
  try {
    fs.mkdirSync(startupDir, { recursive: true });
    const vbsContent = [
      'Set WshShell = CreateObject("WScript.Shell")',
      `WshShell.CurrentDirectory = "${__dirname}"`,
      fs.existsSync(sentinelExe)
        ? `WshShell.Run """${sentinelExe}"" --minimized", 0, False`
        : `WshShell.Run """${nodeExe}"" ""${sentinelMjs}""", 0, False`,
      "",
    ].join("\r\n");
    fs.writeFileSync(startupVbs, vbsContent, "utf8");
    console.log(`[3/3] Installed background Sentinel startup launcher -> ${startupVbs}`);
    const { execFileSync } = await import("node:child_process");
    try {
      execFileSync("powershell.exe", [
        "-NoProfile",
        "-WindowStyle",
        "Hidden",
        "-Command",
        `$exe = '${sentinelExe.replace(/'/g, "''")}'; $vbs = '${startupVbs.replace(/'/g, "''")}'; Set-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run' -Name 'DeepSeekClawSentinel' -Value ('"{0}" --minimized' -f $exe) -ErrorAction SilentlyContinue; Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = ('wscript.exe //B "{0}"' -f $vbs) } | Out-Null`,
      ], { stdio: "ignore", timeout: 8000, windowsHide: true });
    } catch {}
  } catch (e) {
    console.warn("Warning: could not write startup VBS:", e?.message || e);
  }
}

main().catch((err) => {
  console.error("Setup error:", err);
  process.exit(1);
});
