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
  return new URL(path, rlmApiConfig.baseUrl).toString();
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

  try {
    const parsed = (await response.json()) as { detail?: unknown };
    if (typeof parsed.detail === "string" && parsed.detail.trim()) {
      detail = parsed.detail;
    }
  } catch {
    const text = await response.text().catch(() => "");
    if (text.trim()) detail = text;
  }

  throw new RlmApiError(response.status, detail);
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
  const timeoutController = new AbortController();
  const timeoutId = setTimeout(
    () => timeoutController.abort(),
    options?.timeoutMs ?? rlmApiConfig.timeoutMs,
  );

  const signal = options?.signal
    ? anySignal([options.signal, timeoutController.signal])
    : timeoutController.signal;

  try {
    const accessToken = getAccessToken();
    const response = await fetch(buildUrl(path), {
      method,
      signal,
      headers: {
        Accept: "application/json",
        ...(options?.body ? { "Content-Type": "application/json" } : {}),
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
        ...options?.headers,
      },
      ...(options?.body && method !== "GET" ? { body: JSON.stringify(options.body) } : {}),
    });

    if (!response.ok) {
      return await parseError(response);
    }

    if (response.status === 204) {
      return undefined as T;
    }

    return (await response.json()) as T;
  } finally {
    clearTimeout(timeoutId);
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
  const timeoutController = new AbortController();
  const timeoutId = setTimeout(
    () => timeoutController.abort(),
    options?.timeoutMs ?? rlmApiConfig.timeoutMs,
  );

  const signal = options?.signal
    ? anySignal([options.signal, timeoutController.signal])
    : timeoutController.signal;

  try {
    const accessToken = getAccessToken();
    const response = await fetch(buildUrl(path), {
      method: "POST",
      signal,
      headers: {
        Accept: "application/json",
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      },
      body: formData,
    });

    if (!response.ok) {
      return await parseError(response);
    }

    if (response.status === 204) {
      return undefined as T;
    }

    return (await response.json()) as T;
  } finally {
    clearTimeout(timeoutId);
  }
}

export const rlmApiClient = {
  get<T>(path: string, signal?: AbortSignal): Promise<T> {
    return requestJson<T>("GET", path, { signal });
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
