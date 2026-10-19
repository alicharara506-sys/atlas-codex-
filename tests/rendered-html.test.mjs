import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import test from "node:test";

async function waitFor(url, timeoutMs = 20_000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    try {
      const response = await fetch(url);
      if (response.ok) return response;
    } catch { /* server is still starting */ }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`Timed out waiting for ${url}`);
}

test("Atlas delivery renders the canonical V4 terminal without browser signal authority", async () => {
  const port = "39127";
  const child = spawn(process.execPath, ["scripts/serve.mjs"], {
    cwd: process.cwd(),
    env: {
      ...process.env,
      PORT: port,
      HOST: "127.0.0.1",
      MT5_ENABLED: "false",
      LOCAL_LLM_ENABLED: "false",
      ATLAS_INTELLIGENCE_ENABLED: "false",
    },
    stdio: "ignore",
  });
  try {
    const page = await waitFor(`http://127.0.0.1:${port}/`);
    const html = await page.text();
    assert.match(html, /ATLAS/);
    assert.match(html, /QUANT V4/);
    assert.match(html, /Command Center/);
    assert.match(html, /GENERATE SIGNAL/);
    assert.match(html, /CONNECTING TO CANONICAL V4/);
    assert.match(html, /ORDER SUBMISSION DISABLED/);
    assert.match(html, /Loading persisted Atlas evidence/);
    assert.doesNotMatch(html, /404 - Page not found/);
    const consolePage = await waitFor(`http://127.0.0.1:${port}/console`);
    const consoleHtml = await consolePage.text();
    assert.match(consoleHtml, /Agentic Hedge Fund OS/);
    assert.match(consoleHtml, /Execution disabled at every layer/);
    assert.match(consoleHtml, /LIVE DECISION TRACE/);
    assert.match(consoleHtml, /Mandate leaderboard/);
  } finally {
    child.kill();
  }
});
