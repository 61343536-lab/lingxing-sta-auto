import { ServiceError } from "./errors.js";

export interface Config {
  appId: string;
  appSecret: string;
  allowedSids: Set<string>;
  timezone: string;
  dateField: "create" | "done";
  timeoutMs: number;
  transport: "stdio" | "http";
  host: string;
  port: number;
  bearerToken: string;
  allowedHosts: string[];
  allowedOrigins: string[];
}

function csv(value = "") {
  return value.split(",").map((part) => part.trim()).filter(Boolean);
}

export function loadConfig(env = process.env): Config {
  const timezone = env.BUSINESS_TIMEZONE || "Asia/Shanghai";
  try { new Intl.DateTimeFormat("en", { timeZone: timezone }).format(); }
  catch { throw new ServiceError("CONFIG_ERROR", "BUSINESS_TIMEZONE 不是有效时区。"); }
  const dateField = env.PACKING_DATE_FIELD || "create";
  const transport = env.MCP_TRANSPORT || "stdio";
  const timeoutMs = Number(env.LINGXING_TIMEOUT_MS || "20000");
  const port = Number(env.MCP_PORT || "3100");
  const allowedSids = new Set(csv(env.LINGXING_ALLOWED_SIDS));
  if (!["create", "done"].includes(dateField) || !["stdio", "http"].includes(transport) ||
      !Number.isInteger(timeoutMs) || timeoutMs < 100 || timeoutMs > 25000 ||
      !Number.isInteger(port) || port < 1 || port > 65535 ||
      [...allowedSids].some((sid) => !/^[1-9]\d*$/.test(sid))) {
    throw new ServiceError("CONFIG_ERROR", "检查日期字段、传输方式、超时、端口和授权店铺配置。");
  }
  const bearerToken = env.MCP_BEARER_TOKEN || "";
  if (transport === "http" && bearerToken.length < 32) {
    throw new ServiceError("CONFIG_ERROR", "HTTP 模式需要至少 32 字符的独立 MCP_BEARER_TOKEN。");
  }
  return {
    appId: env.LINGXING_APP_ID || "",
    appSecret: env.LINGXING_APP_SECRET || "",
    allowedSids, timezone, dateField: dateField as Config["dateField"], timeoutMs,
    transport: transport as Config["transport"], host: env.MCP_HOST || "127.0.0.1",
    port, bearerToken,
    allowedHosts: csv(env.MCP_ALLOWED_HOSTS || "localhost,127.0.0.1"),
    allowedOrigins: csv(env.MCP_ALLOWED_ORIGINS),
  };
}
