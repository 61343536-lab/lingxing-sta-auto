import { createCipheriv, createHash } from "node:crypto";
import { ServiceError } from "./errors.js";

export function canonicalParameters(params: Record<string, unknown>): string {
  return Object.keys(params).sort()
    .filter((key) => params[key] !== "" && params[key] !== undefined)
    .map((key) => {
      const value = params[key];
      return `${key}=${value !== null && typeof value === "object" ? JSON.stringify(value) : String(value)}`;
    }).join("&");
}

export function signParameters(params: Record<string, unknown>, appId: string): string {
  const key = Buffer.from(appId, "utf8");
  if (![16, 24, 32].includes(key.length)) {
    throw new ServiceError("CONFIG_ERROR", "领星 AppID 长度不符合 AES 签名密钥要求。");
  }
  const digest = createHash("md5").update(canonicalParameters(params), "utf8").digest("hex").toUpperCase();
  const cipher = createCipheriv(`aes-${key.length * 8}-ecb`, key, null);
  return Buffer.concat([cipher.update(digest, "utf8"), cipher.final()]).toString("base64");
}
