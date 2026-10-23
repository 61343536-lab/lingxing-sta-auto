import type { Config } from "./config.js";
import { ServiceError } from "./errors.js";
import { signParameters } from "./signature.js";

export const PACKING_LIST = "/basicOpen/packingTask/list";
export const PACKING_DETAIL = "/basicOpen/packingTask/taskDetail";
const API_ORIGIN = "https://openapi.lingxing.com";
type Envelope = Record<string, unknown> & { data?: unknown; code?: string | number };
type Fetch = typeof fetch;

export function parseApiJson(text: string): Envelope {
  // Node 22+ exposes the original numeric source: Lingxing IDs exceed 2^53.
  return JSON.parse(text, (_key: string, value: unknown, context?: { source?: string }) => {
    if (typeof value === "number" && Number.isInteger(value) && !Number.isSafeInteger(value)) {
      if (!context?.source) throw new Error("Node runtime must support JSON source context");
      return context.source;
    }
    return value;
  }) as Envelope;
}

export class LingxingClient {
  private token?: { access: string; refresh: string; expiresAt: number };
  private renewing?: Promise<string>;
  private lanes = new Map<string, Promise<unknown>>();

  constructor(
    private readonly config: Config,
    private readonly fetcher: Fetch = fetch,
    private readonly now: () => number = Date.now,
  ) {}

  private async request(path: string, init: RequestInit, query?: URLSearchParams): Promise<Envelope> {
    const url = new URL(path, API_ORIGIN);
    if (query) url.search = query.toString();
    let response: Response;
    try {
      response = await this.fetcher(url, {
        ...init, redirect: "error", signal: AbortSignal.timeout(this.config.timeoutMs),
      });
    } catch {
      throw new ServiceError("UPSTREAM_UNAVAILABLE", "领星连接失败或超时，可重试本次只读查询。", true);
    }
    if (!response.ok) {
      throw new ServiceError("UPSTREAM_HTTP_ERROR", "领星请求未成功，请检查连接、授权或服务状态。",
        response.status === 429 || response.status >= 500, String(response.status));
    }
    try {
      const text = await response.text();
      if (text.length > 8_000_000) throw new Error("oversize");
      const envelope = parseApiJson(text);
      if (!envelope || typeof envelope !== "object" || Array.isArray(envelope)) throw new Error("shape");
      return envelope;
    } catch {
      throw new ServiceError("INVALID_UPSTREAM_RESPONSE", "领星响应格式异常，不能视为查询成功。");
    }
  }

  private async authorize(refresh?: string) {
    const body = new FormData();
    body.set("appId", this.config.appId);
    body.set(refresh ? "refreshToken" : "appSecret", refresh || this.config.appSecret);
    const result = await this.request(
      refresh ? "/api/auth-server/oauth/refresh" : "/api/auth-server/oauth/access-token",
      { method: "POST", body },
    );
    if (String(result.code) !== "200") {
      throw new ServiceError("LINGXING_AUTH_ERROR", "领星鉴权失败，请检查应用凭据、授权及 IP 白名单。",
        false, safeCode(result.code));
    }
    const data = result.data as Record<string, unknown> | undefined;
    const expires = Number(data?.expires_in);
    if (typeof data?.access_token !== "string" || !data.access_token ||
        typeof data.refresh_token !== "string" || !data.refresh_token ||
        !Number.isFinite(expires) || expires <= 0) {
      throw new ServiceError("INVALID_TOKEN_RESPONSE", "领星未返回有效的令牌信息。");
    }
    this.token = {
      access: data.access_token, refresh: data.refresh_token,
      expiresAt: this.now() + expires * 1000,
    };
    return this.token.access;
  }

  private async accessToken() {
    if (!this.config.appId || !this.config.appSecret) {
      throw new ServiceError("MISSING_CREDENTIALS", "请在服务端配置 LINGXING_APP_ID 和 LINGXING_APP_SECRET。");
    }
    if (this.token && this.token.expiresAt > this.now() + 30_000) return this.token.access;
    if (this.renewing) return this.renewing;
    this.renewing = (async () => {
      if (this.token && this.token.expiresAt > this.now()) {
        try { return await this.authorize(this.token.refresh); }
        catch (error) {
          if (!(error instanceof ServiceError) || error.code !== "LINGXING_AUTH_ERROR") throw error;
        }
      }
      return this.authorize();
    })();
    try { return await this.renewing; }
    finally { this.renewing = undefined; }
  }

  async read(path: string, body: Record<string, unknown>): Promise<Envelope> {
    if (![PACKING_LIST, PACKING_DETAIL].includes(path)) {
      throw new ServiceError("UNSUPPORTED_API", "当前服务只开放装箱任务列表和详情查询。");
    }
    // Serialize each API path in this single service instance, matching its bucket capacity.
    const previous = this.lanes.get(path) || Promise.resolve();
    const pending = previous.catch(() => undefined).then(async () => {
      const access = await this.accessToken();
      const common = {
        access_token: access, app_key: this.config.appId,
        timestamp: String(Math.floor(this.now() / 1000)),
      };
      const query = new URLSearchParams({
        ...common, sign: signParameters({ ...common, ...body }, this.config.appId),
      });
      const result = await this.request(path, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      }, query);
      if (String(result.code) !== "0") {
        throw new ServiceError("LINGXING_API_ERROR",
          "领星业务查询失败；请依据错误码检查接口权限、白名单、参数或令牌状态。",
          String(result.code) === "3001008", safeCode(result.code));
      }
      return result;
    });
    this.lanes.set(path, pending);
    try { return await pending; }
    finally { if (this.lanes.get(path) === pending) this.lanes.delete(path); }
  }
}

function safeCode(value: unknown) {
  const code = String(value);
  return /^[A-Za-z0-9_-]{1,32}$/.test(code) ? code : "UNKNOWN";
}
