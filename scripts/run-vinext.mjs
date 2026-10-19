#!/usr/bin/env node
// scripts/run-vinext.mjs
//
// Thin wrapper around the real `vinext` CLI (package "vinext", bin ->
// dist/cli.js), invoked as `node <resolved-cli-path> <mode> [...args]`
// rather than relying on PATH-based shell resolution of the vinext/
// vinext.cmd shim. On a locked-down Windows setup, execution policy can
// block running installed .cmd shims directly; resolving and running the
// CLI's own JS entry point through `node` sidesteps that failure mode
// entirely (see the CODEX_SANDBOX comment in vite.config.ts for the
// related esbuild-optimizer issue this project already works around).
//
// The bin path is read from vinext's own package.json rather than
// hard-coded, so this wrapper keeps working if a future vinext release
// changes its internal file layout.
//
// Usage: node scripts/run-vinext.mjs <dev|build|start|check|init|lint> [args]

import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs";

const VALID_MODES = new Set(["dev", "build", "start", "check", "init", "lint"]);
const mode = process.argv[2];
const extraArgs = process.argv.slice(3);

if (!VALID_MODES.has(mode)) {
  console.error(
    `Usage: node scripts/run-vinext.mjs <${[...VALID_MODES].join("|")}> [args]`
  );
  process.exit(1);
}

const projectRoot = fileURLToPath(new URL("..", import.meta.url));

// require.resolve("vinext") / require.resolve("vinext/package.json") both
// fail under Node's strict "exports" enforcement: vinext's package.json
// only exposes an ESM "import" condition on ".", with no "./package.json"
// entry at all — confirmed by actually running this against the installed
// package, not assumed. Reading the file directly via fs sidesteps package
// "exports" entirely (those restrictions only apply to import/require
// resolution), and walking up from the project root handles both flat and
// hoisted node_modules layouts without reimplementing Node's resolver.
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
  if (!binRelative) {
    throw new Error('vinext package.json has no "bin" entry for "vinext"');
  }
  return path.join(path.dirname(pkgPath), binRelative);
}

let cliPath;
try {
  cliPath = resolveVinextCli();
} catch (err) {
  console.error(
    "[run-vinext] Could not resolve the installed `vinext` package. Run `npm ci` first.\n" +
      String(err)
  );
  process.exit(1);
}

const child = spawn(process.execPath, [cliPath, mode, ...extraArgs], {
  stdio: "inherit",
  cwd: projectRoot,
  env: process.env,
});

child.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
  } else {
    process.exit(code ?? 1);
  }
});
child.on("error", (err) => {
  console.error(`[run-vinext] Failed to start "vinext ${mode}":`, err);
  process.exit(1);
});
