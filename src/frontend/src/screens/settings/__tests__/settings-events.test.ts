import { describe, expect, it, vi } from "vite-plus/test";

import {
  OPEN_SETTINGS_EVENT,
  requestSettingsDialogOpen,
  type OpenSettingsEventDetail,
} from "@/screens/settings/settings-events";
import {
  getSettingsSectionDescription,
  getSettingsSectionTitle,
  resolveSettingsSection,
} from "@/screens/settings/settings-screen";

describe("settings section helpers", () => {
  it("resolves supported sections and ignores unknown values", () => {
    expect(resolveSettingsSection("runtime")).toBe("runtime");
    expect(resolveSettingsSection("telemetry")).toBe("telemetry");
    expect(resolveSettingsSection("general")).toBeUndefined();
    expect(resolveSettingsSection(undefined)).toBeUndefined();
  });

  it("returns route and dialog copy for overview and section-specific states", () => {
    expect(getSettingsSectionTitle(undefined)).toBe("Settings");
    expect(getSettingsSectionDescription(undefined)).toContain(
      "Configure theme, telemetry, LM integration",
    );
    expect(getSettingsSectionTitle("runtime")).toBe("Runtime");
    expect(getSettingsSectionDescription("runtime")).toContain(
      "runtime credentials",
    );
  });
});

describe("requestSettingsDialogOpen", () => {
  it("returns false when no listener handles the event", () => {
    expect(requestSettingsDialogOpen({ section: "runtime" })).toBe(false);
  });

  it("returns true when a listener prevents the default action", () => {
    const returnFocusTarget = document.createElement("button");
    const handler = vi.fn((event: Event) => {
      const customEvent = event as CustomEvent<OpenSettingsEventDetail>;
      expect(customEvent.detail.section).toBe("runtime");
      expect(customEvent.detail.returnFocusTarget).toBe(returnFocusTarget);
      customEvent.preventDefault();
    });

    document.addEventListener(OPEN_SETTINGS_EVENT, handler as EventListener, {
      once: true,
    });

    expect(
      requestSettingsDialogOpen({
        section: "runtime",
        returnFocusTarget,
      }),
    ).toBe(true);
    expect(handler).toHaveBeenCalledTimes(1);
  });
});
