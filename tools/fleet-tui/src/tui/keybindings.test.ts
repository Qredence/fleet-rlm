import { describe, expect, it } from "vitest";

import { fleetKeybindings } from "./keybindings.js";

describe("Fleet keybindings", () => {
  it("uses Pi-style interrupt, clear/exit, empty-editor exit, and suspend actions", () => {
    expect(fleetKeybindings.matches("\x1b", "fleet.interrupt")).toBe(true);
    expect(fleetKeybindings.matches("\x03", "fleet.clearOrExit")).toBe(true);
    expect(fleetKeybindings.matches("\x04", "fleet.exit")).toBe(true);
    expect(fleetKeybindings.matches("\x1a", "fleet.suspend")).toBe(true);
    expect(fleetKeybindings.getConflicts()).toEqual([]);
  });
});
