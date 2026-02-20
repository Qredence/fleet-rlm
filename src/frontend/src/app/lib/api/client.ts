/**
 * HTTP client for the fleet-rlm API.
 *
 * Provides a thin wrapper around `fetch` with:
 *   - Automatic auth header injection (Bearer token)
 *   - Request timeout via AbortController
 *   - snake_case → camelCase response transformation
 *   - Structured error handling with `ApiClientError`
 *   - SSE streaming support for chat creation flow
 *
 * The client is stateless — auth tokens are read from the token store
 * at request time.
 *
 * @example
 * ```ts
 * import { apiClient } from '../../lib/api/client';
 *
 * const tasks = await apiClient.get<ApiTaskListResponse>('/api/v1/tasks');
 * ```
 */

import { apiConfig } from "./config";
import type { ApiError, ApiStreamEvent } from "./types";

// ── Token Store ─────────────────────────────────────────────────────
// Token store with sessionStorage persistence.
// sessionStorage is cleared on tab close, providing better security than
// localStorage while persisting across page refreshes.

const TOKEN_KEY = 'fleet-rlm:access-token';

// Initialize from storage on module load
let _accessToken: string | null = sessionStorage.getItem(TOKEN_KEY);

export function setAccessToken(token: string | null): void {
  _accessToken = token;
  if (token) {
    sessionStorage.setItem(TOKEN_KEY, token);
  } else {
    sessionStorage.removeItem(TOKEN_KEY);
  }
}

export function getAccessToken(): string | null {
  return _accessToken;
}

export function clearTokens(): void {
  _accessToken = null;
  sessionStorage.removeItem(TOKEN_KEY);
}

// ── Error Class ─────────────────────────────────────────────────────

export class ApiClientError extends Error {
  public readonly status: number;
  public readonly detail: string;
  public readonly errorType?: string;

  constructor(status: number, detail: string, errorType?: string) {
    super(`[${status}] ${detail}`);
    this.name = "ApiClientError";
    this.status = status;
    this.detail = detail;
    this.errorType = errorType;
  }
}

// ── Case Conversion ─────────────────────────────────────────────────

/** Converts a single snake_case string to camelCase. */
function snakeToCamel(str: string): string {
  return str.replace(/_([a-z0-9])/g, (_, char) => char.toUpperCase());
}

/** Converts a single camelCase string to snake_case. */
function camelToSnake(str: string): string {
  return str.replace(/[A-Z]/g, (char) => `_${char.toLowerCase()}`);
}

/** Deep-converts all object keys from snake_case to camelCase. */
export function keysToCamel<T>(obj: unknown): T {
  if (Array.isArray(obj)) {
    return obj.map((v) => keysToCamel(v)) as unknown as T;
  }
  if (obj !== null && typeof obj === "object" && !(obj instanceof Date)) {
    const entries = Object.entries(obj as Record<string, unknown>);
    const mapped = entries.map(([key, val]) => [
      snakeToCamel(key),
      keysToCamel(val),
    ]);
    return Object.fromEntries(mapped) as T;
  }
  return obj as T;
}

/** Deep-converts all object keys from camelCase to snake_case. */
export function keysToSnake<T>(obj: unknown): T {
  if (Array.isArray(obj)) {
    return obj.map((v) => keysToSnake(v)) as unknown as T;
  }
  if (obj !== null && typeof obj === "object" && !(obj instanceof Date)) {
    const entries = Object.entries(obj as Record<string, unknown>);
    const mapped = entries.map(([key, val]) => [
      camelToSnake(key),
      keysToSnake(val),
    ]);
    return Object.fromEntries(mapped) as T;
  }
  return obj as T;
}

// ── Request Helpers ─────────────────────────────────────────────────

function buildHeaders(extra?: Record<string, string>): Headers {
  const headers = new Headers({
    "Content-Type": "application/json",
    Accept: "application/json",
    ...extra,
  });

  if (_accessToken) {
    headers.set("Authorization", `Bearer ${_accessToken}`);
  }

  if (apiConfig.apiKey) {
    headers.set("X-API-Key", apiConfig.apiKey);
  }

  return headers;
}

function buildUrl(
  path: string,
  params?: Record<string, string | number | boolean | undefined>,
): string {
  const url = new URL(path, apiConfig.baseUrl);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined) {
        url.searchParams.set(camelToSnake(key), String(value));
      }
    }
  }
  return url.toString();
}

async function handleResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    let errorType: string | undefined;

    try {
      const errorBody = (await response.json()) as ApiError;
      detail = errorBody.detail || detail;
      errorType = errorBody.error_type;
    } catch {
      // If the error body isn't JSON, use the status text
      detail = response.statusText || detail;
    }

    throw new ApiClientError(response.status, detail, errorType);
  }

  // 204 No Content
  if (response.status === 204) {
    return undefined as unknown as T;
  }

  const json = await response.json();
  return keysToCamel<T>(json);
}

// ── Core Client ─────────────────────────────────────────────────────

async function request<T>(
  method: string,
  path: string,
  options?: {
    body?: unknown;
    params?: Record<string, string | number | boolean | undefined>;
    headers?: Record<string, string>;
    signal?: AbortSignal;
  },
): Promise<T> {
  const url = buildUrl(path, options?.params);
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), apiConfig.timeout);

  // Combine user signal with timeout
  const signal = options?.signal
    ? anySignal([options.signal, controller.signal])
    : controller.signal;

  try {
    const response = await fetch(url, {
      method,
      headers: buildHeaders(options?.headers),
      body: options?.body
        ? JSON.stringify(keysToSnake(options.body))
        : undefined,
      signal,
    });

    return await handleResponse<T>(response);
  } finally {
    clearTimeout(timeoutId);
  }
}

/** Combine multiple AbortSignals (first one to abort wins). */
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

// ── Public API ──────────────────────────────────────────────────────

export const apiClient = {
  get<T>(
    path: string,
    params?: Record<string, string | number | boolean | undefined>,
    signal?: AbortSignal,
  ): Promise<T> {
    return request<T>("GET", path, { params, signal });
  },

  post<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
    return request<T>("POST", path, { body, signal });
  },

  put<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
    return request<T>("PUT", path, { body, signal });
  },

  patch<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
    return request<T>("PATCH", path, { body, signal });
  },

  delete<T>(path: string, signal?: AbortSignal): Promise<T> {
    return request<T>("DELETE", path, { signal });
  },
};

// ── SSE Streaming ───────────────────────────────────────────────────

/**
 * Opens an SSE connection to the given path and yields parsed events.
 *
 * Usage:
 * ```ts
 * for await (const event of streamChat('/api/v1/chat/stream', { sessionId, message })) {
 *   if (event.event === 'content_delta') { ... }
 * }
 * ```
 */
export async function* streamSSE(
  path: string,
  body: unknown,
  signal?: AbortSignal,
): AsyncGenerator<ApiStreamEvent> {
  const url = buildUrl(path);
  const controller = new AbortController();
  const timeoutId = setTimeout(
    () => controller.abort(),
    apiConfig.timeout * 10,
  ); // longer timeout for streams

  const combinedSignal = signal
    ? anySignal([signal, controller.signal])
    : controller.signal;

  try {
    const response = await fetch(url, {
      method: "POST",
      headers: buildHeaders({ Accept: "text/event-stream" }),
      body: JSON.stringify(keysToSnake(body)),
      signal: combinedSignal,
    });

    if (!response.ok) {
      const detail = await response
        .text()
        .catch(() => `HTTP ${response.status}`);
      throw new ApiClientError(response.status, detail);
    }

    const reader = response.body?.getReader();
    if (!reader) throw new ApiClientError(0, "No response body for SSE stream");

    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";

      let currentEvent = "";
      let currentData = "";

      for (const line of lines) {
        if (line.startsWith("event: ")) {
          currentEvent = line.slice(7).trim();
        } else if (line.startsWith("data: ")) {
          currentData = line.slice(6);
        } else if (line === "" && currentData) {
          // Empty line = end of SSE message
          try {
            const parsed = JSON.parse(currentData) as Record<string, unknown>;
            yield keysToCamel<ApiStreamEvent>({
              event: currentEvent || "message",
              data: parsed,
              phase: parsed.phase as number | undefined,
            });
          } catch {
            // Skip malformed events
          }
          currentEvent = "";
          currentData = "";
        }
      }
    }
  } finally {
    clearTimeout(timeoutId);
  }
}
