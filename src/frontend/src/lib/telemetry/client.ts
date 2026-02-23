import posthog from "posthog-js";

export const TELEMETRY_PII_DENYLIST = [
  "email",
  "name",
  "full_name",
  "first_name",
  "last_name",
] as const;

const TELEMETRY_PII_KEYS = new Set<string>(
  TELEMETRY_PII_DENYLIST.map((key) => key.toLowerCase()),
);

type TelemetryProperties = Record<string, unknown>;

type PostHogLike = {
  capture?: (event: string, properties?: TelemetryProperties) => void;
  captureException?: (error: unknown, properties?: TelemetryProperties) => void;
  reset?: () => void;
  opt_in_capturing?: () => void;
  opt_out_capturing?: () => void;
  has_opted_out_capturing?: () => boolean;
};

export function sanitizeTelemetryProperties(
  properties?: TelemetryProperties,
): TelemetryProperties | undefined {
  if (!properties) return undefined;

  const sanitized: TelemetryProperties = {};
  for (const [key, value] of Object.entries(properties)) {
    if (TELEMETRY_PII_KEYS.has(key.toLowerCase())) {
      continue;
    }
    sanitized[key] = value;
  }
  return sanitized;
}

export function captureTelemetryEvent(
  client: PostHogLike | null | undefined,
  event: string,
  properties?: TelemetryProperties,
): void {
  try {
    client?.capture?.(event, sanitizeTelemetryProperties(properties));
  } catch {
    // No-op when PostHog is unavailable or disabled in local/test contexts.
  }
}

export function captureTelemetryException(
  client: PostHogLike | null | undefined,
  error: unknown,
  properties?: TelemetryProperties,
): void {
  const sanitized = sanitizeTelemetryProperties(properties);

  try {
    if (typeof client?.captureException === "function") {
      client.captureException(error, sanitized);
      return;
    }
    client?.capture?.("telemetry_exception", {
      ...sanitized,
      error_message: error instanceof Error ? error.message : String(error),
    });
  } catch {
    // No-op when PostHog is unavailable or disabled in local/test contexts.
  }
}

export function resetTelemetry(client: PostHogLike | null | undefined): void {
  try {
    client?.reset?.();
  } catch {
    // No-op when PostHog is unavailable or disabled in local/test contexts.
  }
}

export function isAnonymousTelemetryEnabled(
  client: PostHogLike | null | undefined = posthog,
): boolean {
  try {
    return !(client?.has_opted_out_capturing?.() ?? false);
  } catch {
    return true;
  }
}

export function setAnonymousTelemetryEnabled(
  enabled: boolean,
  client: PostHogLike | null | undefined = posthog,
): void {
  try {
    if (enabled) {
      client?.opt_in_capturing?.();
    } else {
      client?.opt_out_capturing?.();
    }
  } catch {
    // No-op when PostHog is unavailable or disabled in local/test contexts.
  }
}

export const telemetryClient = {
  capture(event: string, properties?: TelemetryProperties) {
    captureTelemetryEvent(posthog, event, properties);
  },
  captureException(error: unknown, properties?: TelemetryProperties) {
    captureTelemetryException(posthog, error, properties);
  },
  reset() {
    resetTelemetry(posthog);
  },
  isAnonymousTelemetryEnabled() {
    return isAnonymousTelemetryEnabled(posthog);
  },
  setAnonymousTelemetryEnabled(enabled: boolean) {
    setAnonymousTelemetryEnabled(enabled, posthog);
  },
  sanitizeProperties: sanitizeTelemetryProperties,
};

export type { PostHogLike, TelemetryProperties };
