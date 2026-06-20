import { rlmApiConfig } from "@/lib/rlm-api/config";
import { getAccessToken } from "@/lib/auth/token-store";

export class RlmApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(`[${status}] ${detail}`);
    this.name = "RlmApiError";
    this.status = status;
    this.detail = detail;
  }
}

function buildUrl(path: string): string {
  if (/^https?:\/\//.test(path)) return path;
  if (!rlmApiConfig.baseUrl) return path;
  if (shouldUseSameOriginDevProxy(rlmApiConfig.baseUrl)) return path;
  return new URL(path, rlmApiConfig.baseUrl).toString();
}

function isLoopbackHost(hostname: string): boolean {
  return hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1";
}

function shouldUseSameOriginDevProxy(apiUrl: string): boolean {
  if (!import.meta.env.DEV || typeof window === "undefined") return false;

  try {
    const api = new URL(apiUrl);
    const current = window.location;
    return (
      current.port === "5173" &&
      api.port === "8000" &&
      isLoopbackHost(current.hostname) &&
      isLoopbackHost(api.hostname)
    );
  } catch {
    return false;
  }
}

function anySignal(signals: AbortSignal[]): AbortSignal {
  const controller = new AbortController();

  for (const signal of signals) {
    if (signal.aborted) {
      controller.abort(signal.reason);
      return controller.signal;
    }

    signal.addEventListener("abort", () => controller.abort(signal.reason), {
      once: true,
    });
  }

  return controller.signal;
}

async function parseError(response: Response): Promise<never> {
  let detail = `HTTP ${response.status}`;
  const text = await response.text().catch(() => "");

  if (text.trim()) {
    try {
      const parsed = JSON.parse(text) as { detail?: unknown; message?: unknown };
      if (typeof parsed.message === "string" && parsed.message.trim()) {
        detail = parsed.message;
      } else if (typeof parsed.detail === "string" && parsed.detail.trim()) {
        detail = parsed.detail;
      } else {
        detail = text;
      }
    } catch {
      detail = text;
    }
  }

  throw new RlmApiError(response.status, detail);
}

function createTimedSignal(
  signal: AbortSignal | undefined,
  timeoutMs: number | undefined,
): { cleanup: () => void; signal: AbortSignal } {
  const timeoutController = new AbortController();
  const timeoutId = setTimeout(
    () => timeoutController.abort(),
    timeoutMs ?? rlmApiConfig.timeoutMs,
  );

  return {
    cleanup: () => clearTimeout(timeoutId),
    signal: signal ? anySignal([signal, timeoutController.signal]) : timeoutController.signal,
  };
}

function getAuthHeaders(headers: Record<string, string>): Record<string, string> {
  const accessToken = getAccessToken();
  return {
    ...headers,
    ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
  };
}

async function parseJsonResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    return await parseError(response);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

async function requestJson<T>(
  method: "GET" | "POST" | "PATCH" | "DELETE",
  path: string,
  options?: {
    body?: unknown;
    signal?: AbortSignal;
    headers?: Record<string, string>;
    timeoutMs?: number;
  },
): Promise<T> {
  const { cleanup, signal } = createTimedSignal(options?.signal, options?.timeoutMs);

  try {
    const response = await fetch(buildUrl(path), {
      method,
      signal,
      headers: getAuthHeaders({
        Accept: "application/json",
        ...(options?.body ? { "Content-Type": "application/json" } : {}),
        ...options?.headers,
      }),
      ...(options?.body && method !== "GET" ? { body: JSON.stringify(options.body) } : {}),
    });

    return await parseJsonResponse(response);
  } finally {
    cleanup();
  }
}

async function requestFormData<T>(
  path: string,
  formData: FormData,
  options?: {
    signal?: AbortSignal;
    timeoutMs?: number;
  },
): Promise<T> {
  const { cleanup, signal } = createTimedSignal(options?.signal, options?.timeoutMs);

  try {
    const response = await fetch(buildUrl(path), {
      method: "POST",
      signal,
      headers: getAuthHeaders({
        Accept: "application/json",
      }),
      body: formData,
    });

    return await parseJsonResponse(response);
  } finally {
    cleanup();
  }
}

export const rlmApiClient = {
  get<T>(path: string, signal?: AbortSignal, timeoutMs?: number): Promise<T> {
    return requestJson<T>("GET", path, { signal, timeoutMs });
  },

  post<T>(path: string, body?: unknown, signal?: AbortSignal, timeoutMs?: number): Promise<T> {
    return requestJson<T>("POST", path, { body, signal, timeoutMs });
  },

  postForm<T>(
    path: string,
    formData: FormData,
    signal?: AbortSignal,
    timeoutMs?: number,
  ): Promise<T> {
    return requestFormData<T>(path, formData, { signal, timeoutMs });
  },

  patch<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
    return requestJson<T>("PATCH", path, { body, signal });
  },

  delete<T>(path: string, signal?: AbortSignal): Promise<T> {
    return requestJson<T>("DELETE", path, { signal });
  },
};
