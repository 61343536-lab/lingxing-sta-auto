import test from "node:test";
import assert from "node:assert/strict";
import { createDecipheriv, createHash } from "node:crypto";
import { currentMonthRange, validateRange } from "../src/dates.js";
import { canonicalParameters, signParameters } from "../src/signature.js";
import { parseApiJson } from "../src/lingxing.js";
import { inScope, normalizeTask, readPage, summarizeTasks } from "../src/packing.js";

export function packingRow(overrides: Record<string, unknown> = {}) {
  return {
    taskSn: "PL261023001", ptId: "250692382241271800", relateSn: "SP261023001",
    sid: 54, taskStatus: 3, relateSnType: 2, relateShipment: [],
    itemList: [{ sid: 54, msku: "SKU-A", shipmentId: "" }],
    gmtCreate: "2026-10-23 09:00:00", ...overrides,
  };
}

test("this month uses the business timezone and includes today, including month/year boundaries", () => {
  assert.deepEqual(
    currentMonthRange("Asia/Shanghai", new Date("2026-10-22T16:30:00Z")),
    {
      timezone: "Asia/Shanghai", resolvedAt: "2026-10-22T16:30:00.000Z",
      today: "2026-10-23", startDate: "2026-10-01", endDate: "2026-10-23", boundary: "inclusive",
    },
  );
  assert.equal(currentMonthRange("Asia/Shanghai", new Date("2026-10-31T16:00:00Z")).startDate, "2026-11-01");
  assert.equal(currentMonthRange("Asia/Shanghai", new Date("2026-12-31T16:00:00Z")).endDate, "2027-01-01");
  assert.equal(currentMonthRange("America/Los_Angeles", new Date("2026-11-01T01:00:00Z")).endDate, "2026-10-31");
  assert.throws(() => validateRange("2026-02-29", "2026-03-01"));
  assert.throws(() => validateRange("2026-10-23", "2026-10-01"));
  validateRange("2028-02-01", "2028-02-29");
});

test("signature follows ASCII ordering, exact JSON values, null and empty-value rules", () => {
  const params = {
    z: "", c: { value: "中 文" }, b: ["2", "3"], n: null, a: 0, missing: undefined,
  };
  const canonical = 'a=0&b=["2","3"]&c={"value":"中 文"}&n=null';
  assert.equal(canonicalParameters(params), canonical);
  const key = "0123456789abcdef";
  const encrypted = signParameters(params, key);
  const decipher = createDecipheriv("aes-128-ecb", Buffer.from(key), null);
  const recovered = Buffer.concat([decipher.update(encrypted, "base64"), decipher.final()]).toString("utf8");
  assert.equal(recovered, createHash("md5").update(canonical).digest("hex").toUpperCase());
  assert.throws(() => signParameters(params, "invalid-key"));
});

test("unsafe integer IDs retain their original digits", () => {
  const parsed = parseApiJson('{"code":0,"data":{"ptId":250692382241271800,"sid":54}}');
  assert.deepEqual(parsed.data, { ptId: "250692382241271800", sid: 54 });
});

test("completed status, source type and shipment fields all participate in filtering", () => {
  assert.equal(normalizeTask(packingRow()).eligibility, "candidate");
  assert.equal(normalizeTask(packingRow({ taskStatus: 2, packingCon: 3 })).reason, "NOT_COMPLETED");
  assert.equal(normalizeTask(packingRow({ relateSnType: 1 })).reason, "NOT_SP_SOURCE");
  assert.equal(normalizeTask(packingRow({
    relateShipment: [{ shipmentId: " FBA123 " }],
  })).reason, "HAS_SHIPMENT");
  assert.equal(normalizeTask(packingRow({
    itemList: [{ sid: 54, shipmentId: "FBA456" }],
  })).reason, "HAS_SHIPMENT");
  assert.equal(normalizeTask(packingRow({ relateShipment: undefined })).eligibility, "needs_review");
  assert.equal(normalizeTask(packingRow({ relateShipment: null })).eligibility, "needs_review");
  assert.equal(normalizeTask(packingRow({ relateShipment: [{}] })).eligibility, "needs_review");
  assert.equal(normalizeTask(packingRow({ relateShipment: [{ shipmentId: 0 }] })).eligibility, "needs_review");
  assert.equal(normalizeTask(packingRow({ relateShipment: [{ shipmentId: "  " }] })).eligibility, "candidate");
});

test("existing STA remains visible as a no-shipment result, not permission to create again", () => {
  const task = normalizeTask(packingRow({
    relateShipment: [{ shipmentId: "", staInboundPlanId: "wf-existing" }],
  }));
  assert.equal(task.reason, "NO_SHIPMENT_EXISTING_STA");
  assert.deepEqual(summarizeTasks([task]).candidates[0]?.packingTasks[0]?.staIds, ["wf-existing"]);
});

test("detail can resolve unknown list associations; all store IDs must be authorized", () => {
  const detail = normalizeTask({
    taskInfo: packingRow({ sid: 0, itemList: undefined }),
    productItems: [{ sid: 54, shipmentId: "" }],
  });
  assert.equal(detail.sid, "54");
  assert.equal(detail.eligibility, "candidate");
  const mixed = normalizeTask(packingRow({ itemList: [{ sid: 99 }] }));
  assert.equal(inScope(mixed, new Set(["54"])), false);
  assert.equal(inScope(mixed, new Set(["54", "99"])), true);
  assert.equal(mixed.eligibility, "needs_review");
});

test("pagination uses raw records, supports both documented total locations, and fails on malformed pages", () => {
  const first = readPage({ code: 0, data: { list: [packingRow(), packingRow()], total: 3 } }, 0, 2);
  assert.equal(first.pagination.nextOffset, 2);
  const last = readPage({ code: 0, data: { list: [packingRow()] }, total: 3 }, 2, 2);
  assert.equal(last.pagination.hasMore, false);
  assert.equal(readPage({ data: { list: [] }, total: 0 }, 0, 100).pagination.hasMore, false);
  assert.equal(readPage({ data: { list: [packingRow()] } }, 0, 1).pagination.hasMore, true);
  assert.throws(() => readPage({ data: { items: [] } }, 0, 100));
  assert.throws(() => readPage({ data: { list: [], total: 10 } }, 0, 100));
  assert.throws(() => readPage({ data: { list: [], total: 0 }, total: 10 }, 0, 100));
});

test("SP grouping preserves multiple PLs and store boundaries, and does not silently resolve conflicts", () => {
  const one = normalizeTask(packingRow());
  const two = normalizeTask(packingRow({ taskSn: "PL261023002" }));
  const anotherStore = normalizeTask(packingRow({ sid: 99, itemList: [{ sid: 99 }] }));
  const summary = summarizeTasks([one, one, two, anotherStore]);
  assert.equal(summary.candidateSpCount, 2);
  assert.equal(summary.candidateTaskCount, 3);
  assert.deepEqual(summary.relatedSpNumbers, ["SP261023001"]);
  assert.equal(summary.candidates.find((c) => c.sid === "54")?.packingTasks.length, 2);
  const conflicting = { ...one, shipmentIds: ["FBA-now"], eligibility: "excluded" as const, reason: "HAS_SHIPMENT" };
  const conflictResult = summarizeTasks([one, conflicting]);
  assert.equal(conflictResult.candidateSpCount, 0);
  assert.equal(conflictResult.needsReview[0]?.reason, "CONFLICTING_SNAPSHOTS");
});
