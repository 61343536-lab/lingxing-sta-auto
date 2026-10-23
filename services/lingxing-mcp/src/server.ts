import { McpServer } from "@modelcontextprotocol/server";
import * as z from "zod/v4";
import type { Config } from "./config.js";
import { currentMonthRange, isCalendarDate, validateRange } from "./dates.js";
import { publicError, ServiceError } from "./errors.js";
import { LingxingClient, PACKING_DETAIL, PACKING_LIST } from "./lingxing.js";
import { inScope, normalizeTask, readPage, summarizeTasks, type PackingTask } from "./packing.js";

const annotations = { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true };
const dateSchema = z.string().refine(isCalendarDate, "格式为有效的 YYYY-MM-DD 日期");
const taskSchema = z.object({
  taskSn: z.string(), ptId: z.string(), relateSn: z.string(), sid: z.string(),
  storeIds: z.array(z.string()), taskStatus: z.number().int().nullable(), relateSnType: z.number().int().nullable(),
  createdAt: z.string(), completedAt: z.string(), shipmentIds: z.array(z.string()), staIds: z.array(z.string()),
  relationKnown: z.boolean(), eligibility: z.enum(["candidate", "excluded", "needs_review"]), reason: z.string(),
});

function success(value: Record<string, unknown>) {
  const result = { ok: true, ...value };
  return { content: [{ type: "text" as const, text: JSON.stringify(result) }], structuredContent: result };
}
async function guarded(fn: () => Record<string, unknown> | Promise<Record<string, unknown>>) {
  try { return success(await fn()); }
  catch (error) {
    const result = { ok: false, error: publicError(error) };
    return { isError: true, content: [{ type: "text" as const, text: JSON.stringify(result) }], structuredContent: result };
  }
}
function requireScope(config: Config) {
  if (!config.allowedSids.size) {
    throw new ServiceError("MISSING_STORE_SCOPE", "服务端需配置 LINGXING_ALLOWED_SIDS 后才能查询业务数据。");
  }
}

export function createMcpServer(
  config: Config,
  client = new LingxingClient(config),
  now: () => Date = () => new Date(),
) {
  const server = new McpServer({ name: "lingxing-packing", version: "0.1.0" });
  server.registerTool("get_current_month_range", {
    description: "读取服务运行时当前日期，按公司业务时区返回当月1日至当天的日期范围。用户说“处理这个月的建单业务”时首先调用；不要使用模型记忆中的日期。",
    inputSchema: z.object({}), annotations: { ...annotations, openWorldHint: false },
  }, async () => guarded(() => ({
    ...currentMonthRange(config.timezone, now()), searchFieldTime: config.dateField,
    allowedSids: [...config.allowedSids],
  })));

  server.registerTool("list_packing_tasks", {
    description: "对应领星装箱任务列表 API，只查询一页。返回授权店铺的任务摘要和筛选判断。按照 nextOffset 继续；必须保留日期范围和日期字段。候选是 taskStatus=3、来源为SP且明确无货件的任务。",
    inputSchema: z.object({
      startDate: dateSchema, endDate: dateSchema,
      searchFieldTime: z.enum(["create", "done"]).default(config.dateField),
      offset: z.number().int().min(0).default(0), length: z.number().int().min(1).max(200).default(100),
      wid: z.array(z.string().regex(/^[1-9]\d*$/)).optional(),
      relateSnType: z.array(z.enum(["1", "2", "3", "4"])).min(1).default(["2"]),
    }), annotations,
  }, async (input) => guarded(async () => {
    requireScope(config);
    validateRange(input.startDate, input.endDate);
    const result = await client.read(PACKING_LIST, input);
    const page = readPage(result, input.offset, input.length);
    const all = page.sourceTasks.map(normalizeTask);
    const tasks = all.filter((task) => inScope(task, config.allowedSids));
    return {
      range: { startDate: input.startDate, endDate: input.endDate, searchFieldTime: input.searchFieldTime },
      pagination: page.pagination, tasks,
      hiddenOrUnresolvedStoreCount: all.length - tasks.length,
      pageSummary: summarizeTasks(tasks),
      note: "分页基于领星原始记录数；空的授权结果不等于分页结束。needs_review 需查询详情，不能当成货件号为空。",
    };
  }));

  server.registerTool("get_packing_task", {
    description: "对应领星装箱任务详情 API。用前一工具返回的 taskSn（PL号）核验状态和货件关联，返回同形摘要供替换列表记录；不创建或修改任务。",
    inputSchema: z.object({ taskSn: z.string().regex(/^PL[A-Za-z0-9_-]+$/).max(100) }), annotations,
  }, async ({ taskSn }) => guarded(async () => {
    requireScope(config);
    const result = await client.read(PACKING_DETAIL, { taskSn });
    const task = normalizeTask(result.data);
    if (task.taskSn !== taskSn) throw new ServiceError("MISMATCHED_TASK", "详情与请求的 PL 不一致。");
    if (!inScope(task, config.allowedSids)) {
      throw new ServiceError("STORE_SCOPE_UNRESOLVED", "该任务不在授权店铺范围内，或无法确定店铺归属。");
    }
    return { task, summary: summarizeTasks([task]) };
  }));

  server.registerTool("summarize_sp_candidates", {
    description: "确定性汇总辅助工具，不访问领星。接收所有已取页的任务摘要；用详情替换待核验记录后再调用。按店铺+SP去重，保留全部PL；不得仅传候选而省略待核验和排除记录。",
    inputSchema: z.object({
      tasks: z.array(taskSchema).max(10000),
      paginationComplete: z.boolean(),
    }), annotations: { ...annotations, openWorldHint: false },
  }, async ({ tasks, paginationComplete }) => guarded(() => {
    requireScope(config);
    if (tasks.some((task) => !inScope(task, config.allowedSids))) {
      throw new ServiceError("STORE_SCOPE_UNRESOLVED", "汇总包含未授权或店铺归属不明的任务。");
    }
    // Re-evaluate derived eligibility; caller-provided labels do not override source facts.
    const checked: PackingTask[] = tasks.map((task) => ({
      ...task, ...normalizeTask({
        taskSn: task.taskSn, ptId: task.ptId, relateSn: task.relateSn, sid: task.sid,
        itemList: [
          ...task.storeIds.map((sid) => ({ sid })),
          ...task.shipmentIds.map((shipmentId) => ({ shipmentId })),
        ],
        taskStatus: task.taskStatus, relateSnType: task.relateSnType,
        gmtCreate: task.createdAt, finishTime: task.completedAt,
        relateShipment: task.relationKnown
          ? [...task.shipmentIds.map((shipmentId) => ({ shipmentId })),
              ...task.staIds.map((staInboundPlanId) => ({ shipmentId: "", staInboundPlanId }))]
          : undefined,
      }),
      // A list response containing malformed shipment fields must stay unresolved.
      ...(task.reason === "INVALID_SHIPMENT_FIELD"
        ? { eligibility: "needs_review" as const, reason: task.reason } : {}),
    }));
    return { ...summarizeTasks(checked), complete: paginationComplete,
      source: "caller_provided_tool_results",
      limitation: "分页完整性由 Agent 提供；仅当每一页均成功且 hasMore=false 时才能报告完整结果。" };
  }));
  return server;
}
