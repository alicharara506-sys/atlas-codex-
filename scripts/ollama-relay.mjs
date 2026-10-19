#!/usr/bin/env node

import http from "node:http";

const targetPort = Number(process.env.OLLAMA_PRIMARY_PORT || 11434);
const relayPorts = (process.env.OLLAMA_RELAY_PORTS || "11435,11436,11437")
  .split(",")
  .map((value) => Number(value.trim()))
  .filter((value) => Number.isInteger(value) && value > 0 && value < 65_536);

function isLoopback(address) {
  return address === "127.0.0.1" || address === "::1" || address === "::ffff:127.0.0.1";
}

function createRelay(port) {
  const server = http.createServer((request, response) => {
    if (!isLoopback(request.socket.remoteAddress)) {
      response.writeHead(403, { "content-type": "application/json" });
      response.end(JSON.stringify({ error: "Loopback access only." }));
      return;
    }

    const upstream = http.request({
      hostname: "127.0.0.1",
      port: targetPort,
      path: request.url,
      method: request.method,
      headers: { ...request.headers, host: `127.0.0.1:${targetPort}` },
    }, (upstreamResponse) => {
      response.writeHead(upstreamResponse.statusCode ?? 502, upstreamResponse.headers);
      upstreamResponse.pipe(response);
    });

    upstream.setTimeout(600_000, () => upstream.destroy(new Error("Local Ollama request timed out.")));
    upstream.on("error", (error) => {
      if (response.headersSent) return response.destroy(error);
      response.writeHead(502, { "content-type": "application/json" });
      response.end(JSON.stringify({ error: "Primary local Ollama service unavailable." }));
    });
    request.pipe(upstream);
  });

  server.listen(port, "127.0.0.1", () => {
    console.log(`[ollama-relay] 127.0.0.1:${port} -> 127.0.0.1:${targetPort}`);
  });
  return server;
}

const servers = relayPorts.map(createRelay);
function shutdown() {
  for (const server of servers) server.close();
}
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
