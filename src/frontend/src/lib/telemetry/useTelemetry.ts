import { useMemo } from "react";
import { usePostHog } from "@posthog/react";
import {
  captureTelemetryEvent,
  captureTelemetryException,
  resetTelemetry,
  type TelemetryProperties,
} from "@/lib/telemetry/client";

export function useTelemetry() {
  const posthog = usePostHog();

  return useMemo(
    () => ({
      capture: (event: string, properties?: TelemetryProperties) =>
        captureTelemetryEvent(posthog, event, properties),
      captureException: (error: unknown, properties?: TelemetryProperties) =>
        captureTelemetryException(posthog, error, properties),
      reset: () => resetTelemetry(posthog),
    }),
    [posthog],
  );
}
