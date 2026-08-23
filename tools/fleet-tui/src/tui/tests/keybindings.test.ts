import { TUI_KEYBINDINGS, getKeybindings } from "@earendil-works/pi-tui";
import { describe, expect, it } from "vitest";

import { fleetKeybindings } from "../keybindings.js";

describe("Fleet keybindings", () => {
  it("uses Pi-style interrupt, clear/exit, empty-editor exit, and suspend actions", () => {
    expect(fleetKeybindings.matches("\x1b", "fleet.interrupt")).toBe(true);
    expect(fleetKeybindings.matches("\x03", "fleet.clearOrExit")).toBe(true);
    expect(fleetKeybindings.matches("\x04", "fleet.exit")).toBe(true);
    expect(fleetKeybindings.matches("\x1a", "fleet.suspend")).toBe(true);
    expect(fleetKeybindings.getConflicts()).toEqual([]);
  });
});

/**
 * pi-tui 0.84.2 adds default alt-screen keybindings for transcript search
 * (Ctrl+Shift+F) plus unbound half-page/line scrolling. Those keys are
 * consumed by Pi's viewport input listener — registered in the TuiAltScreen
 * constructor, before Fleet's application-level listener — so they can never
 * be shadowed by Fleet bindings. The only intentionally shared key is
 * Escape: Pi closes a focused search overlay with it before Fleet's
 * `fleet.interrupt` listener ever runs, and `fleet.interrupt` already
 * declines while any overlay is open.
 */
const FLEET_BINDINGS = [
  "fleet.interrupt",
  "fleet.clearOrExit",
  "fleet.exit",
  "fleet.suspend",
  "fleet.toggleFold",
] as const;

describe("pi-tui 0.84.2 alt-screen search defaults", () => {
  it("ships the transcript search defaults relied on by the Fleet UI and docs", () => {
    expect(TUI_KEYBINDINGS["tui.altScreen.search"].defaultKeys).toBe("ctrl+shift+f");
    expect(TUI_KEYBINDINGS["tui.altScreen.searchNext"].defaultKeys).toEqual(["enter", "ctrl+g"]);
    expect(TUI_KEYBINDINGS["tui.altScreen.searchPrevious"].defaultKeys).toEqual([
      "shift+enter",
      "ctrl+shift+g",
    ]);
    expect(TUI_KEYBINDINGS["tui.altScreen.searchClose"].defaultKeys).toBe("escape");
    // Half-page / single-line scrolling exists but ships unbound.
    expect(TUI_KEYBINDINGS["tui.altScreen.halfPageUp"].defaultKeys).toEqual([]);
    expect(TUI_KEYBINDINGS["tui.altScreen.halfPageDown"].defaultKeys).toEqual([]);
    expect(TUI_KEYBINDINGS["tui.altScreen.lineUp"].defaultKeys).toEqual([]);
    expect(TUI_KEYBINDINGS["tui.altScreen.lineDown"].defaultKeys).toEqual([]);
  });

  it("keeps Fleet defaults distinct from always-on viewport navigation keys", () => {
    const viewportKeys = new Set(
      [
        "tui.altScreen.pageUp",
        "tui.altScreen.pageDown",
        "tui.altScreen.previousPrompt",
        "tui.altScreen.nextPrompt",
        "tui.altScreen.top",
        "tui.altScreen.bottom",
        "tui.altScreen.search",
      ].flatMap((id) => getKeybindings().getKeys(id as never)),
    );
    for (const binding of FLEET_BINDINGS) {
      for (const key of fleetKeybindings.getKeys(binding)) {
        expect(
          viewportKeys.has(key),
          `${binding} (${key}) must not shadow a pi-tui alt-screen viewport default`,
        ).toBe(false);
      }
    }
  });

  it("documents Escape as the single intentional overlap with search close", () => {
    expect(fleetKeybindings.getKeys("fleet.interrupt")).toEqual(["escape"]);
    expect(TUI_KEYBINDINGS["tui.altScreen.searchClose"].defaultKeys).toBe("escape");
  });
});
