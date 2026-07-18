import { describe, expect, it } from "vitest";

import { fleetKeybindings } from "./keybindings.js";

describe("Fleet keybindings", () => {
  it("maps only the global cancel, exit, and suspend actions", () => {
    expect(fleetKeybindings.matches("\x03", "fleet.cancel")).toBe(true);
    expect(fleetKeybindings.matches("\x04", "fleet.exit")).toBe(true);
    expect(fleetKeybindings.matches("\x1a", "fleet.suspend")).toBe(true);
    expect(fleetKeybindings.getConflicts()).toEqual([]);
  });
});
