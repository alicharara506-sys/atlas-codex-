#!/usr/bin/env node
// scripts/serve.mjs
//
// Starts Atlas's production server (`vinext start`) and, when
// MT5_ENABLED=true, the read-only MT5 bridge (scripts/mt5_bridge.py)
// alongside it — matching DATA_PROVIDERS.md: "scripts/serve.mjs starts
// and stops it with the local Atlas server when MT5_ENABLED=true." Both
// processes are torn down together on SIGINT/SIGTERM, or if either one
// exits unexpectedly.
//
// This script does NOT build the app — `npm run dev` and `npm run start`
// both run `npm run build` (or expect a prior build) before invoking this
// file. See package.json.
//
// Env is loaded the same way vinext's own CLI loads it for `start`
// (process.env > .env.local > .env), via vinext's own exported dotenv
// loader, so MT5_ENABLED and friends are read from the same source of
// truth the app itself uses — this script and the app can't disagree
// about whether MT5 is enabled.

import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs";

const projectRoot = fileURLToPath(new URL("..", import.meta.url));

// See scripts/run-vinext.mjs for why this reads node_modules/vinext's
// package.json via fs directly instead of require.resolve: vinext's own
// package.json doesn't expose a "./package.json" export, and its "."
// export has no CJS "require" condition — both confirmed by actually
// running this against the installed package, not assumed.
function findVinextPackageJson(startDir) {
  let dir = startDir;
  while (true) {
    const candidate = path.join(dir, "node_modules", "vinext", "package.json");
    if (fs.existsSync(candidate)) return candidate;
    const parent = path.dirname(dir);
    if (parent === dir) return null;
    dir = parent;
  }
}

function resolveVinextCli() {
  const pkgPath = findVinextPackageJson(projectRoot);
  if (!pkgPath) {
    throw new Error(
      "could not find node_modules/vinext/package.json anywhere above " + projectRoot
    );
  }
  const pkg = JSON.parse(fs.readFileSync(pkgPath, "utf8"));
  const binRelative = typeof pkg.bin === "string" ? pkg.bin : pkg.bin?.vinext;
  if (!binRelative) throw new Error('vinext package.json has no "bin" entry for "vinext"');
  return path.join(path.dirname(pkgPath), binRelative);
}

async function loadEnv() {
  try {
    const { loadDotenv } = await import("vinext/internal/config/dotenv");
    const result = loadDotenv({
      root: projectRoot,
      mode: process.env.NODE_ENV || "production",
      processEnv: process.env,
    });
    if (result.loadedFiles.length > 0) {
      console.log(`[serve] loaded env from: ${result.loadedFiles.join(", ")}`);
    }
  } catch (err) {
    console.warn(
      "[serve] could not load vinext's dotenv helper (non-fatal, falling back to process.env as-is):",
      err instanceof Error ? err.message : err
    );
  }
}

function truthyEnv(name) {
  return (process.env[name] ?? "").trim().toLowerCase() === "true";
}

function resolveProjectPath(value, fallback) {
  const selected = (value ?? "").trim() || fallback;
  return path.isAbsolute(selected) ? selected : path.join(projectRoot, selected);
}

const children = [];
let shuttingDown = false;

function spawnManaged(label, command, args, options = {}) {
  console.log(`[serve] starting ${label}: ${command} ${args.join(" ")}`);
  const child = spawn(command, args, {
    stdio: "inherit",
    cwd: projectRoot,
    env: process.env,
    ...options,
  });
  children.push({ label, child });
  child.on("exit", (code, signal) => {
    console.log(`[serve] ${label} exited (code=${code ?? "null"} signal=${signal ?? "none"})`);
    shutdownAll(code && code !== 0 ? code : 0);
  });
  child.on("error", (err) => {
    console.error(`[serve] ${label} failed to start:`, err);
    shutdownAll(1);
  });
  return child;
}

function shutdownAll(exitCode) {
  if (shuttingDown) return;
  shuttingDown = true;
  for (const { label, child } of children) {
    if (!child.killed) {
      console.log(`[serve] stopping ${label}...`);
      child.kill("SIGTERM");
    }
  }
  process.exitCode = exitCode ?? 0;
  setTimeout(() => process.exit(exitCode ?? 0), 500).unref();
}

process.on("SIGINT", () => shutdownAll(0));
process.on("SIGTERM", () => shutdownAll(0));

async function main() {
  await loadEnv();

  if (truthyEnv("ATLAS_INTELLIGENCE_ENABLED")) {
    const pythonPath =
      process.env.ATLAS_PYTHON_PATH || process.env.MT5_PYTHON_PATH || "python";
    const intelligenceScript = path.join(projectRoot, "scripts", "intelligence_bridge.py");
    if (!fs.existsSync(intelligenceScript)) {
      console.error(
        `[serve] ATLAS_INTELLIGENCE_ENABLED=true but ${intelligenceScript} is missing.`
      );
      process.exit(1);
    }
    spawnManaged("intelligence-bridge", pythonPath, [intelligenceScript]);
  } else {
    console.log(
      "[serve] ATLAS_INTELLIGENCE_ENABLED is not true — Python order-flow and routed LLM review remain optional/offline."
    );
  }

  if (truthyEnv("MT5_ENABLED")) {
    const pythonPath = process.env.MT5_PYTHON_PATH || "python";
    const bridgeMode = (process.env.MT5_BRIDGE_MODE || "python").trim().toLowerCase();
    if (!["python", "file"].includes(bridgeMode)) {
      console.error(`[serve] MT5_BRIDGE_MODE must be "python" or "file", got "${bridgeMode}".`);
      process.exit(1);
    }
    const bridgeFilename = bridgeMode === "file" ? "mt5_file_bridge.py" : "mt5_bridge.py";
    const bridgeScript = path.join(projectRoot, "scripts", bridgeFilename);
    if (!fs.existsSync(bridgeScript)) {
      console.error(`[serve] MT5_ENABLED=true but ${bridgeScript} is missing.`);
      process.exit(1);
    }
    spawnManaged(`mt5-${bridgeMode}-bridge`, pythonPath, [bridgeScript]);
  } else {
    console.log(
      "[serve] MT5_ENABLED is not true — skipping the MT5 bridge. " +
        "Market data will use DEMO mode or another configured provider."
    );
  }

  if (truthyEnv("LOCAL_LLM_ENABLED")) {
    if (truthyEnv("OLLAMA_MANAGE_SERVERS")) {
      const ollamaExecutable = resolveProjectPath(
        process.env.OLLAMA_EXECUTABLE,
        path.join(".atlas-local-llm", "runtime", "ollama.exe")
      );
      const modelsDirectory = resolveProjectPath(
        process.env.OLLAMA_MODELS_DIR,
        path.join(".atlas-local-llm", "models")
      );
      if (!fs.existsSync(ollamaExecutable)) {
        console.error(`[serve] OLLAMA_MANAGE_SERVERS=true but ${ollamaExecutable} is missing.`);
        process.exit(1);
      }
      fs.mkdirSync(modelsDirectory, { recursive: true });
      for (const port of [11434, 11435, 11436, 11437]) {
        spawnManaged(`ollama-${port}`, ollamaExecutable, ["serve"], {
          env: {
            ...process.env,
            OLLAMA_HOST: `127.0.0.1:${port}`,
            OLLAMA_MODELS: modelsDirectory,
            OLLAMA_KEEP_ALIVE: "-1",
            OLLAMA_MAX_LOADED_MODELS: "1",
            OLLAMA_NUM_PARALLEL: "1",
            OLLAMA_NOHISTORY: "true",
          },
        });
      }
    } else {
      const relayScript = path.join(projectRoot, "scripts", "ollama-relay.mjs");
      spawnManaged("ollama-relays", process.execPath, [relayScript]);
    }
  } else {
    console.log("[serve] LOCAL_LLM_ENABLED is not true — local Ollama committee remains optional/offline.");
  }

  let cliPath;
  try {
    cliPath = resolveVinextCli();
  } catch (err) {
    console.error(
      "[serve] Could not resolve the installed `vinext` package. Run `npm ci` first.\n" +
        String(err)
    );
    shutdownAll(1);
    return;
  }

  spawnManaged("atlas-server", process.execPath, [cliPath, "start"]);
}

main();
