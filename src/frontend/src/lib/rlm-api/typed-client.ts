import createClient from "openapi-fetch";

import { getAccessToken } from "@/lib/auth/token-store";
import { rlmApiConfig } from "@/lib/rlm-api/config";
import { RlmApiError } from "@/lib/rlm-api/client";
import type { paths } from "@/lib/rlm-api/generated/openapi";

// ── Base URL resolution ───────────────────────────────────────────────

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

function resolveBaseUrl(): string {
  if (!rlmApiConfig.baseUrl) return "";
  if (shouldUseSameOriginDevProxy(rlmApiConfig.baseUrl)) return "";
  return rlmApiConfig.baseUrl.replace(/\/$/, "");
}

// ── Typed client (openapi-fetch) ──────────────────────────────────────

export const typedClient = createClient<paths>({
  baseUrl: resolveBaseUrl(),
  headers: { Accept: "application/json" },
});

typedClient.use({
  async onRequest({ request }) {
    const token = getAccessToken();
    if (token) {
      request.headers.set("Authorization", `Bearer ${token}`);
    }
    return request;
  },
});

// ── Helpers ───────────────────────────────────────────────────────────

/** Combine an external AbortSignal with a timeout. */
export function withTimeout(signal?: AbortSignal, timeoutMs?: number): AbortSignal | undefined {
  const timeout = timeoutMs ?? rlmApiConfig.timeoutMs;
  const timeoutSignal = AbortSignal.timeout(timeout);
  return signal ? AbortSignal.any([signal, timeoutSignal]) : timeoutSignal;
}

type DataOf<R> = R extends { data?: infer D } ? D : never;

function extractErrorDetail(error: unknown, status: number): string {
  const errorBody = error as Record<string, unknown> | undefined;
  if (errorBody) {
    if (typeof errorBody.message === "string" && errorBody.message.trim()) {
      return errorBody.message;
    }
    if (typeof errorBody.detail === "string" && errorBody.detail.trim()) {
      return errorBody.detail;
    }
  }
  return `HTTP ${status}`;
}

/**
 * Unwrap an openapi-fetch response promise:
 * return the typed data on success, throw {@link RlmApiError} on failure.
 *
 * For 204 No Content responses, returns an empty object cast to the
 * expected type — safe because callers of 204 endpoints (e.g. delete)
 * do not destructure the return value.
 */
export async function unwrap<R extends { data?: unknown; error?: unknown; response: Response }>(
  promise: Promise<R>,
): Promise<NonNullable<DataOf<R>>> {
  const result = await promise;
  if (result.error !== undefined) {
    throw new RlmApiError(
      result.response.status,
      extractErrorDetail(result.error, result.response.status),
    );
  }
  if (result.response.status === 204) {
    return {} as NonNullable<DataOf<R>>;
  }
  if (!result.response.ok) {
    throw new RlmApiError(result.response.status, `HTTP ${result.response.status}`);
  }
  return result.data as NonNullable<DataOf<R>>;
}
