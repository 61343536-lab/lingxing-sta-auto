export class ServiceError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly retryable = false,
    public readonly upstreamCode?: string,
  ) {
    super(message);
  }
}

export function publicError(error: unknown) {
  if (error instanceof ServiceError) {
    return {
      code: error.code,
      message: error.message,
      retryable: error.retryable,
      ...(error.upstreamCode ? { upstreamCode: error.upstreamCode } : {}),
    };
  }
  // Never serialize fetch errors (they can contain a URL with access_token).
  return { code: "INTERNAL_ERROR", message: "处理失败，请联系服务管理员。", retryable: false };
}
