import { ServiceError } from "./errors.js";

type Row = Record<string, unknown>;
export interface PackingTask {
  taskSn: string;
  ptId: string;
  relateSn: string;
  sid: string;
  storeIds: string[];
  taskStatus: number | null;
  relateSnType: number | null;
  createdAt: string;
  completedAt: string;
  shipmentIds: string[];
  staIds: string[];
  relationKnown: boolean;
  eligibility: "candidate" | "excluded" | "needs_review";
  reason: string;
}

function row(value: unknown): Row {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
}
function rows(value: unknown): Row[] { return Array.isArray(value) ? value.map(row) : []; }
function text(value: unknown) { return value === null || value === undefined ? "" : String(value).trim(); }
function storeId(value: unknown) { const v = text(value); return /^[1-9]\d*$/.test(v) ? v : ""; }
function integer(value: unknown): number | null {
  return (typeof value === "number" || typeof value === "string") && text(value) !== "" &&
    Number.isSafeInteger(Number(value)) ? Number(value) : null;
}

export function normalizeTask(value: unknown): PackingTask {
  const outer = row(value);
  const task = outer.taskInfo ? row(outer.taskInfo) : outer;
  const products = [
    ...rows(outer.itemList), ...rows(outer.productItems),
    ...rows(outer.boxList).flatMap((box) => rows(box.boxProduct)),
  ];
  const relationRows = rows(task.relateShipment);
  const storeIds = [...new Set([task.sid, ...products.map((p) => p.sid)]
    .map(storeId).filter(Boolean))].sort();
  const shipmentValues = [
    task.shipmentId, ...relationRows.map((r) => r.shipmentId),
    ...products.map((p) => p.shipmentId),
  ];
  const shipmentIds = [...new Set(shipmentValues.filter((v) => typeof v === "string")
    .map(text).filter(Boolean))].sort();
  const invalidShipment = shipmentValues.some((v) => v !== null && v !== undefined && typeof v !== "string");
  const relationKnown = Array.isArray(task.relateShipment) &&
    task.relateShipment.every((r: unknown) => r !== null && typeof r === "object" &&
      !Array.isArray(r) && Object.hasOwn(r, "shipmentId"));
  const result: PackingTask = {
    taskSn: text(task.taskSn), ptId: text(task.ptId), relateSn: text(task.relateSn || outer.relateSn),
    sid: storeIds.length === 1 ? storeIds[0]! : "", storeIds,
    taskStatus: integer(task.taskStatus), relateSnType: integer(task.relateSnType),
    createdAt: text(task.gmtCreate), completedAt: text(task.finishTime),
    shipmentIds,
    staIds: [...new Set(relationRows.map((r) => text(r.staInboundPlanId)).filter(Boolean))].sort(),
    relationKnown, eligibility: "needs_review", reason: "INCOMPLETE_DATA",
  };
  if (result.taskStatus !== null && result.taskStatus !== 3) {
    result.eligibility = "excluded"; result.reason = "NOT_COMPLETED";
  } else if (shipmentIds.length) {
    result.eligibility = "excluded"; result.reason = "HAS_SHIPMENT";
  } else if (result.relateSnType !== null && result.relateSnType !== 2) {
    result.eligibility = "excluded"; result.reason = "NOT_SP_SOURCE";
  } else if (invalidShipment) {
    result.reason = "INVALID_SHIPMENT_FIELD";
  } else if (!relationKnown) {
    result.reason = "SHIPMENT_RELATION_UNKNOWN";
  } else if (!result.taskSn || !/^SP[A-Za-z0-9_-]+$/.test(result.relateSn) ||
      !result.sid || result.taskStatus !== 3 || result.relateSnType !== 2) {
    result.reason = "INVALID_SOURCE_OR_STORE";
  } else {
    result.eligibility = "candidate";
    result.reason = result.staIds.length ? "NO_SHIPMENT_EXISTING_STA" : "COMPLETED_NO_SHIPMENT";
  }
  return result;
}

export function inScope(task: PackingTask, allowedSids: Set<string>) {
  return task.storeIds.length > 0 && task.storeIds.every((sid) => allowedSids.has(sid));
}

export function readPage(envelope: Row, offset: number, length: number) {
  const data = row(envelope.data);
  if (!Array.isArray(data.list)) {
    throw new ServiceError("INVALID_PAGE", "领星未返回 data.list，不能将本页视为空结果。");
  }
  const dataTotal = data.total === undefined ? null : integer(data.total);
  const rootTotal = envelope.total === undefined ? null : integer(envelope.total);
  if ((data.total !== undefined && (dataTotal === null || dataTotal < 0)) ||
      (envelope.total !== undefined && (rootTotal === null || rootTotal < 0)) ||
      (dataTotal !== null && rootTotal !== null && dataTotal !== rootTotal)) {
    throw new ServiceError("INVALID_PAGE_TOTAL", "领星分页总数缺损或互相矛盾，请核验真实响应。");
  }
  const total = dataTotal ?? rootTotal;
  const received = data.list.length;
  if (received > length || (received === 0 && total !== null && offset < total) ||
      (total !== null && received > 0 && offset + received > total)) {
    throw new ServiceError("INCONSISTENT_PAGE", "领星分页数据与总数不一致，请重新查询本次范围。", true);
  }
  const hasMore = total === null ? received === length : offset + received < total;
  return {
    sourceTasks: data.list,
    pagination: { offset, length, received, total, hasMore, nextOffset: hasMore ? offset + received : null },
  };
}

export function summarizeTasks(tasks: PackingTask[]) {
  // The same PL may appear on multiple pages; conflicting snapshots cannot be silently merged.
  const unique = new Map<string, PackingTask>();
  const conflicts = new Set<string>();
  for (const task of tasks) {
    const key = `${task.sid}:${task.taskSn}`;
    const old = unique.get(key);
    if (old && JSON.stringify(old) !== JSON.stringify(task)) conflicts.add(key);
    else unique.set(key, task);
  }
  const review: Array<{ taskSn: string; relateSn: string; reason: string }> = [];
  const groups = new Map<string, { sid: string; shipmentSn: string; packingTasks: Array<{
    taskSn: string; ptId: string; staIds: string[];
  }> }>();
  let excludedCount = 0;
  for (const [key, task] of unique) {
    if (conflicts.has(key) || task.eligibility === "needs_review") {
      review.push({ taskSn: task.taskSn, relateSn: task.relateSn,
        reason: conflicts.has(key) ? "CONFLICTING_SNAPSHOTS" : task.reason });
      continue;
    }
    if (task.eligibility === "excluded") { excludedCount++; continue; }
    const groupKey = `${task.sid}:${task.relateSn}`;
    const group = groups.get(groupKey) || { sid: task.sid, shipmentSn: task.relateSn, packingTasks: [] };
    group.packingTasks.push({ taskSn: task.taskSn, ptId: task.ptId, staIds: task.staIds });
    groups.set(groupKey, group);
  }
  const candidates = [...groups.values()].sort((a, b) =>
    `${a.sid}:${a.shipmentSn}`.localeCompare(`${b.sid}:${b.shipmentSn}`));
  return {
    candidates,
    relatedSpNumbers: [...new Set(candidates.map((c) => c.shipmentSn))],
    candidateSpCount: candidates.length,
    candidateTaskCount: candidates.reduce((n, c) => n + c.packingTasks.length, 0),
    excludedCount, needsReview: review,
    note: "这是已完成且未发现货件的装箱任务所关联 SP，尚未核验 SP 待发货状态，也不代表允许直接创建 STA。",
  };
}
