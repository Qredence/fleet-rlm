import { KeybindingsManager } from "@earendil-works/pi-tui";

declare module "@earendil-works/pi-tui" {
  interface Keybindings {
    "fleet.cancel": true;
    "fleet.exit": true;
    "fleet.suspend": true;
  }
}

export const fleetKeybindings = new KeybindingsManager({
  "fleet.cancel": { defaultKeys: "ctrl+c", description: "Cancel the active Run" },
  "fleet.exit": { defaultKeys: "ctrl+d", description: "Exit Fleet" },
  "fleet.suspend": { defaultKeys: "ctrl+z", description: "Suspend Fleet" },
});
