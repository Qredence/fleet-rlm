import { KeybindingsManager } from "@earendil-works/pi-tui";

declare module "@earendil-works/pi-tui" {
  interface Keybindings {
    "fleet.interrupt": true;
    "fleet.clearOrExit": true;
    "fleet.exit": true;
    "fleet.suspend": true;
  }
}

export const fleetKeybindings = new KeybindingsManager({
  "fleet.interrupt": { defaultKeys: "escape", description: "Cancel the active Run" },
  "fleet.clearOrExit": {
    defaultKeys: "ctrl+c",
    description: "Clear the editor; press twice while empty to exit",
  },
  "fleet.exit": { defaultKeys: "ctrl+d", description: "Exit Fleet when the editor is empty" },
  "fleet.suspend": { defaultKeys: "ctrl+z", description: "Suspend Fleet" },
});
