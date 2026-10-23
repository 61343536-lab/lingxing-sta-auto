import test from "node:test";
import assert from "node:assert/strict";
import { once } from "node:events";
import type { AddressInfo } from "node:net";
import { Client, StreamableHTTPClientTransport } from "@modelcontextprotocol/client";
import { StdioClientTransport } from "@modelcontextprotocol/client/stdio";
import { loadConfig } from "../src/config.js";
import { createHttpServer } from "../src/http.js";
import { LingxingClient, PACKING_DETAIL, PACKING_LIST } from "../src/lingxing.js";
import type { PackingTask } from "../src/packing.js";

const config = loadConfig({
  MCP_TRANSPORT: "http", MCP_BEARER_TOKEN: "test-only-mcp-bearer-000000000000000",
  LINGXING_APP_ID: "0123456789abcdef", LINGXING_APP_SECRET: "test-only-secret",
  LINGXING_ALLOWED_SIDS: "54",
});
const packing = (taskSn: string, overrides = {}) => ({
  taskSn, ptId: taskSn, relateSn: "SP261023001", sid: 54, taskStatus: 3, relateSnType: 2,
  relateShipment: [], itemList: [{ sid: 54 }], ...overrides,
});
const response = (value: unknown) => new Response(JSON.stringify(value));
function content(result: Awaited<ReturnType<Client["callTool"]>>) {
  assert.equal(result.isError, undefined, JSON.stringify(result));
  return result.structuredContent as Record<string, any>;
}

test("MCP HTTP executes date → paginated API tools → detail → SP summary with scope and provenance", async (t) => {
  const tasks = [
    packing("PL261023001"), packing("PL261023002", { taskStatus: 2 }),
    packing("PL261023003", { sid: 99, itemList: [{ sid: 99 }] }),
    packing("PL261023004", { relateShipment: undefined }),
    packing("PL261023005"),
    packing("PL261023006", { relateShipment: [{ shipmentId: "FBA-ALREADY" }] }),
    packing("PL261023007", { relateSn: "SP261023002",
      relateShipment: [{ shipmentId: "", staInboundPlanId: "wf-existing" }] }),
  ];
  const listCalls: Record<string, any>[] = [];
  const lingxing = new LingxingClient(config, async (url, init) => {
    const path = new URL(String(url)).pathname;
    if (path.includes("oauth")) return response({
      code: "200", data: { access_token: "mock-access", refresh_token: "mock-refresh", expires_in: 7199 },
    });
    const body = JSON.parse(String(init?.body));
    if (path === PACKING_LIST) {
      listCalls.push(body);
      return response({ code: 0, data: { list: tasks.slice(body.offset, body.offset + body.length), total: tasks.length } });
    }
    assert.equal(path, PACKING_DETAIL);
    assert.equal(body.taskSn, "PL261023004");
    return response({ code: 0, data: { taskInfo: packing("PL261023004") } });
  });
  const http = createHttpServer(config, lingxing);
  http.listen(0, "127.0.0.1");
  await once(http, "listening");
  const base = `http://127.0.0.1:${(http.address() as AddressInfo).port}`;
  const client = new Client({ name: "packing-integration-test", version: "1.0.0" });
  t.after(async () => { await client.close(); http.closeAllConnections(); await new Promise<void>((r) => http.close(() => r())); });

  assert.equal((await fetch(`${base}/mcp`, { method: "POST" })).status, 401);
  assert.equal((await fetch(`${base}/mcp`, { method: "POST", headers: {
    Authorization: `Bearer ${config.bearerToken}`, Origin: "https://untrusted.example",
  } })).status, 403);
  await client.connect(new StreamableHTTPClientTransport(new URL(`${base}/mcp`), {
    requestInit: { headers: { Authorization: `Bearer ${config.bearerToken}` } },
  }));
  const tools = await client.listTools();
  assert.deepEqual(tools.tools.map((tool) => tool.name).sort(), [
    "get_current_month_range", "get_packing_task", "list_packing_tasks", "summarize_sp_candidates",
  ]);
  const range = content(await client.callTool({ name: "get_current_month_range", arguments: {} }));
  assert.equal(range.searchFieldTime, "create");
  assert.equal(range.startDate.slice(0, 7), range.endDate.slice(0, 7));

  const collected: PackingTask[] = [];
  let offset: number | null = 0;
  let hidden = 0;
  while (offset !== null) {
    const page = content(await client.callTool({
      name: "list_packing_tasks",
      arguments: { startDate: "2026-10-01", endDate: "2026-10-23", offset, length: 2 },
    }));
    collected.push(...page.tasks);
    hidden += page.hiddenOrUnresolvedStoreCount;
    offset = page.pagination.nextOffset;
  }
  assert.equal(hidden, 1);
  assert.deepEqual(listCalls.map((c) => c.offset), [0, 2, 4, 6]);
  assert.ok(listCalls.every((c) => c.searchFieldTime === "create" && c.endDate === "2026-10-23"));
  assert.ok(collected.every((task) => task.sid === "54"));
  const unresolved = collected.findIndex((task) => task.taskSn === "PL261023004");
  assert.equal(collected[unresolved]?.eligibility, "needs_review");
  const detail = content(await client.callTool({ name: "get_packing_task", arguments: { taskSn: "PL261023004" } }));
  collected[unresolved] = detail.task;
  const summary = content(await client.callTool({
    name: "summarize_sp_candidates", arguments: { tasks: collected, paginationComplete: true },
  }));
  assert.deepEqual(summary.relatedSpNumbers, ["SP261023001", "SP261023002"]);
  assert.equal(summary.candidateTaskCount, 4);
  assert.equal(summary.complete, true);
  assert.equal(summary.excludedCount, 2);
  assert.equal(summary.candidates[0].packingTasks.length, 3);
  assert.deepEqual(summary.candidates[1].packingTasks[0].staIds, ["wf-existing"]);

  const partial = content(await client.callTool({
    name: "summarize_sp_candidates", arguments: { tasks: collected, paginationComplete: false },
  }));
  assert.equal(partial.complete, false);
  const invalid = await client.callTool({ name: "list_packing_tasks", arguments: {
    startDate: "2026-10-23", endDate: "2026-10-01",
  } });
  assert.equal(invalid.isError, true);
  assert.equal(listCalls.length, 4);
});

test("stdio server can start without credentials and reports missing configuration on business calls", async (t) => {
  const client = new Client({ name: "stdio-smoke", version: "1.0.0" });
  const transport = new StdioClientTransport({
    command: process.execPath, args: ["--import", "tsx", "src/main.ts"], cwd: process.cwd(),
    env: {
      MCP_TRANSPORT: "stdio", LINGXING_ALLOWED_SIDS: "54",
      LINGXING_APP_ID: "", LINGXING_APP_SECRET: "", BUSINESS_TIMEZONE: "Asia/Shanghai",
    },
    stderr: "pipe",
  });
  t.after(async () => { await client.close(); });
  await client.connect(transport);
  const date = content(await client.callTool({ name: "get_current_month_range", arguments: {} }));
  assert.equal(date.timezone, "Asia/Shanghai");
  const result = await client.callTool({ name: "list_packing_tasks", arguments: {
    startDate: "2026-10-01", endDate: "2026-10-23",
  } });
  assert.equal(result.isError, true);
  assert.equal((result.structuredContent as any).error.code, "MISSING_CREDENTIALS");
});
