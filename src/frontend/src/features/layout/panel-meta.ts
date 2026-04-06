import type { NavItem } from "@/stores/navigation-types";

export type ShellPanelMeta = {
  title: string;
  description: string;
  toggleLabel: string;
  toggleDescription: string;
  emptyTitle: string;
  emptyDescription: string;
};

export function getShellPanelMeta(activeNav: NavItem): ShellPanelMeta {
  switch (activeNav) {
    case "volumes":
      return {
        title: "Preview",
        description: "Inspect the selected file without leaving the mounted storage browser.",
        toggleLabel: "Preview",
        toggleDescription: "Inspect selected files",
        emptyTitle: "Nothing selected yet",
        emptyDescription:
          "Choose a file in Volumes to preview it here and keep your browsing context intact.",
      };
    case "settings":
      return {
        title: "Details",
        description: "Keep contextual help and future previews adjacent to the settings workspace.",
        toggleLabel: "Details",
        toggleDescription: "Reference panel",
        emptyTitle: "Settings stay focused in the dialog",
        emptyDescription:
          "Use the settings dialog for appearance, telemetry, LiteLLM, and runtime controls. This side panel is reserved for supporting context as those workflows expand.",
      };
    case "optimization":
      return {
        title: "Details",
        description:
          "Keep optimization guidance and future run context adjacent to the optimization workspace.",
        toggleLabel: "Details",
        toggleDescription: "Reference panel",
        emptyTitle: "Optimization stays focused in the main surface",
        emptyDescription:
          "Use the optimization page to configure and run GEPA prompt optimization. This side panel is reserved for supporting context as that workflow expands.",
      };
    default:
      return {
        title: "Inspector",
        description:
          "Inspect the selected turn or track run activity without losing the conversation.",
        toggleLabel: "Inspector",
        toggleDescription: "Inspect turns and runs",
        emptyTitle: "Nothing selected yet",
        emptyDescription:
          "Choose an assistant turn to inspect it, or start a run to open the workbench.",
      };
  }
}

export { getShellPanelMeta as getLayoutPanelMeta };
