export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

interface ErrorPayload {
  detail?: string;
}

export async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/awn/${path.replace(/^\//, "")}`, {
    ...init,
    cache: "no-store",
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });

  if (!response.ok) {
    let detail = `تعذر إكمال الطلب (${response.status})`;
    try {
      const payload = (await response.json()) as ErrorPayload;
      if (payload.detail) detail = payload.detail;
    } catch {
      // The upstream may be offline or return a non-JSON error page.
    }
    throw new ApiError(response.status, detail);
  }

  return (await response.json()) as T;
}
