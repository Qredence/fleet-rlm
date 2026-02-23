import { describe, expect, it, vi } from "vitest";
import {
  captureTelemetryEvent,
  sanitizeTelemetryProperties,
  setAnonymousTelemetryEnabled,
  type PostHogLike,
} from "@/lib/telemetry/client";

describe("telemetry client helpers", () => {
  it("sanitizes shallow PII keys from event properties", () => {
    expect(
      sanitizeTelemetryProperties({
        email: "alex@example.com",
        name: "Alex",
        source: "login_page",
        nested: { email: "kept-by-design-in-phase2" },
      }),
    ).toEqual({
      source: "login_page",
      nested: { email: "kept-by-design-in-phase2" },
    });
  });

  it("captures sanitized telemetry properties", () => {
    const capture = vi.fn();
    const client: PostHogLike = { capture };

    captureTelemetryEvent(client, "user_logged_in", {
      email: "alex@example.com",
      source: "login_page",
    });

    expect(capture).toHaveBeenCalledWith("user_logged_in", {
      source: "login_page",
    });
  });

  it("toggles anonymous telemetry safely", () => {
    const optIn = vi.fn();
    const optOut = vi.fn();
    const client: PostHogLike = {
      opt_in_capturing: optIn,
      opt_out_capturing: optOut,
    };

    setAnonymousTelemetryEnabled(true, client);
    setAnonymousTelemetryEnabled(false, client);

    expect(optIn).toHaveBeenCalledTimes(1);
    expect(optOut).toHaveBeenCalledTimes(1);
  });

  it("no-ops cleanly when PostHog client is unavailable", () => {
    expect(() =>
      captureTelemetryEvent(undefined, "anonymous_event", {
        email: "should-be-ignored",
      }),
    ).not.toThrow();
    expect(() => setAnonymousTelemetryEnabled(true, undefined)).not.toThrow();
  });
});
