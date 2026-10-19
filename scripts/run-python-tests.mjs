#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = fileURLToPath(new URL("..", import.meta.url));

function localEnv() {
  const result = {};
  for (const filename of [".env", ".env.local"]) {
    const envPath = path.join(projectRoot, filename);
    if (!fs.existsSync(envPath)) continue;
    for (const rawLine of fs.readFileSync(envPath, "utf8").split(/\r?\n/)) {
      const line = rawLine.trim();
      if (!line || line.startsWith("#")) continue;
      const index = line.indexOf("=");
      if (index <= 0) continue;
      result[line.slice(0, index).trim()] = line.slice(index + 1).trim().replace(/^['"]|['"]$/g, "");
    }
  }
  return result;
}

const configured = { ...localEnv(), ...process.env };
const localVenvPython = process.platform === "win32"
  ? path.join(projectRoot, ".venv", "Scripts", "python.exe")
  : path.join(projectRoot, ".venv", "bin", "python");
const python = configured.ATLAS_TEST_PYTHON_PATH
  || (fs.existsSync(localVenvPython) ? localVenvPython : undefined)
  || configured.ATLAS_PYTHON_PATH
  || configured.MT5_PYTHON_PATH
  || "python";
for (const suite of ["tests", "backend/tests"]) {
  const run = spawnSync(python, ["-m", "unittest", "discover", "-s", suite, "-p", "test_*.py"], {
    cwd: projectRoot,
    env: process.env,
    stdio: "inherit",
  });
  if (run.error) {
    console.error(`[python-tests] Could not start ${python}: ${run.error.message}`);
    process.exit(1);
  }
  if ((run.status ?? 1) !== 0) process.exit(run.status ?? 1);
}
process.exit(0);
