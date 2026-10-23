import { createServer } from "node:http";
import { timingSafeEqual } from "node:crypto";
import { hostHeaderValidation, NodeStreamableHTTPServerTransport } from "@modelcontextprotocol/node";
import type { Config } from "./config.js";
import { LingxingClient } from "./lingxing.js";
import { createMcpServer } from "./server.js";

export function createHttpServer(config: Config, client = new LingxingClient(config)) {
  const validateHost = hostHeaderValidation(config.allowedHosts);
  return createServer(async (request, response) => {
    if (!validateHost(request, response)) return;
    if (request.headers.origin && !config.allowedOrigins.includes(request.headers.origin)) {
      response.writeHead(403).end(); return;
    }
    const path = (request.url || "").split("?")[0];
    if (path === "/health" && request.method === "GET") {
      response.writeHead(200, { "Content-Type": "application/json" }).end('{"status":"ok"}'); return;
    }
    if (path !== "/mcp") { response.writeHead(404).end(); return; }
    const supplied = Buffer.from(request.headers.authorization || "", "utf8");
    const expected = Buffer.from(`Bearer ${config.bearerToken}`, "utf8");
    if (!config.bearerToken || supplied.length !== expected.length || !timingSafeEqual(supplied, expected)) {
      response.writeHead(401, { "WWW-Authenticate": "Bearer" }).end(); return;
    }
    if (request.method !== "POST") {
      response.writeHead(405, { Allow: "POST" }).end(); return;
    }
    const server = createMcpServer(config, client);
    const transport = new NodeStreamableHTTPServerTransport({
      sessionIdGenerator: undefined, enableJsonResponse: true,
    });
    response.once("close", () => { void server.close().catch(() => undefined); });
    try {
      await server.connect(transport);
      await transport.handleRequest(request, response);
    } catch {
      if (!response.headersSent) response.writeHead(500).end();
      else if (!response.writableEnded) response.end();
    }
  });
}
