import test from "node:test";
import assert from "node:assert/strict";
import { loadConfig } from "../src/config.js";
import { LingxingClient, PACKING_DETAIL, PACKING_LIST } from "../src/lingxing.js";
import { signParameters } from "../src/signature.js";
import { publicError } from "../src/errors.js";

const config = loadConfig({
  LINGXING_APP_ID: "0123456789abcdef", LINGXING_APP_SECRET: "test-only-secret",
  LINGXING_ALLOWED_SIDS: "54",
});
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
const tokenData = (access = "test-only-access", refresh = "test-only-refresh") => ({
  code: "200", data: { access_token: access, refresh_token: refresh, expires_in: 120 },
});

test("credentials stay in multipart auth, business parameters are signed, and token acquisition is single-flight", async () => {
  let authCount = 0;
  let active = 0;
  let maxActive = 0;
  const bodies: unknown[] = [];
  const client = new LingxingClient(config, async (input, init) => {
    const url = new URL(String(input));
    assert.equal(url.origin, "https://openapi.lingxing.com");
    if (url.pathname.includes("oauth")) {
      authCount++;
      assert.ok(init?.body instanceof FormData);
      assert.equal(init.body.get("appId"), config.appId);
      assert.equal(init.body.get("appSecret"), config.appSecret);
      return json(tokenData());
    }
    active++;
    maxActive = Math.max(active, maxActive);
    const body = JSON.parse(String(init?.body));
    bodies.push(body);
    const common = {
      access_token: "test-only-access", app_key: config.appId, timestamp: "1792713600",
    };
    assert.equal(url.searchParams.get("sign"), signParameters({ ...common, ...body }, config.appId));
    assert.equal(url.searchParams.has("appSecret"), false);
    await new Promise<void>((resolve) => setImmediate(resolve));
    active--;
    return json({ code: 0, data: { list: [], total: 0 } });
  }, () => 1792713600000);
  const input = { offset: 0, length: 100, relateSnType: ["2"], startDate: "2026-10-01", endDate: "2026-10-23" };
  await Promise.all([client.read(PACKING_LIST, input), client.read(PACKING_LIST, input)]);
  assert.equal(authCount, 1);
  assert.equal(maxActive, 1);
  assert.deepEqual(bodies, [input, input]);
  await assert.rejects(client.read("/dangerous/write", {}), /只开放/);
});

test("near-expiry concurrent calls use the single-use refresh token once and rotate it", async () => {
  let time = 0;
  let refreshCount = 0;
  const client = new LingxingClient(config, async (input, init) => {
    const path = new URL(String(input)).pathname;
    if (path.endsWith("access-token")) return json(tokenData());
    if (path.endsWith("refresh")) {
      refreshCount++;
      assert.ok(init?.body instanceof FormData);
      assert.equal(init.body.get("refreshToken"), "test-only-refresh");
      return json(tokenData("test-only-access-2", "test-only-refresh-2"));
    }
    return json({ code: 0, data: { list: [], total: 0 } });
  }, () => time);
  await client.read(PACKING_LIST, {});
  time = 100_000;
  await Promise.all([client.read(PACKING_LIST, {}), client.read(PACKING_DETAIL, {})]);
  assert.equal(refreshCount, 1);
});

test("errors do not turn into empty success and never echo secrets from the upstream body or network exception", async () => {
  for (const failure of ["rate", "network", "malformed"] as const) {
    const client = new LingxingClient(config, async (input) => {
      if (String(input).includes("oauth")) return json(tokenData());
      if (failure === "network") throw new Error(`https://host/?access_token=${config.appSecret}`);
      if (failure === "malformed") return new Response("not-json");
      return json({ code: 3001008, message: config.appSecret });
    });
    try {
      await client.read(PACKING_LIST, {});
      assert.fail("expected error");
    } catch (error) {
      const safe = publicError(error);
      assert.equal(JSON.stringify(safe).includes(config.appSecret), false);
      assert.equal(safe.retryable, failure !== "malformed");
    }
  }
});

test("missing credentials produce an actionable configuration error without fetching", async () => {
  const client = new LingxingClient(loadConfig({}), async () => { throw new Error("must not fetch"); });
  await assert.rejects(client.read(PACKING_LIST, {}), /LINGXING_APP_ID/);
});
