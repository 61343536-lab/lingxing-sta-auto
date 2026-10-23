import { ServiceError } from "./errors.js";

export function currentMonthRange(timezone: string, now = new Date()) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(now);
  const part = (type: string) => parts.find((p) => p.type === type)!.value;
  const today = `${part("year")}-${part("month")}-${part("day")}`;
  return {
    timezone, resolvedAt: now.toISOString(), today,
    startDate: `${part("year")}-${part("month")}-01`,
    endDate: today,
    boundary: "inclusive" as const,
  };
}

export function isCalendarDate(value: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(`${value}T00:00:00.000Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

export function validateRange(startDate: string, endDate: string) {
  if (!isCalendarDate(startDate) || !isCalendarDate(endDate) || startDate > endDate) {
    throw new ServiceError("INVALID_DATE_RANGE", "必须提供有效日期，且开始日期不得晚于结束日期。");
  }
}
