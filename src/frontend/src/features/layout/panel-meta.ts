import type { CanvasPanel } from "@/stores/navigation-types";

export type ShellPanelMeta = {
  title: string;
  toggleLabel: string;
  toggleDescription: string;
};

export function getShellPanelMeta(panel: CanvasPanel): ShellPanelMeta {
  switch (panel) {
    case "volumes":
      return {
        title: "Volumes",
        toggleLabel: "Panel",
        toggleDescription: "Toggle the side panel",
      };
    case "graph":
      return {
        title: "Graph",
        toggleLabel: "Panel",
        toggleDescription: "Toggle the side panel",
      };
    default:
      return {
        title: "Workbench",
        toggleLabel: "Panel",
        toggleDescription: "Toggle the side panel",
      };
  }
}

export { getShellPanelMeta as getLayoutPanelMeta };
