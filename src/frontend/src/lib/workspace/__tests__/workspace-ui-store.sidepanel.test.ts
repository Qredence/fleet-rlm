import { beforeEach, describe, expect, it } from "vite-plus/test";

import { useNavigationStore } from "@/stores/navigation-store";
import { useWorkspaceUiStore } from "@/lib/workspace/workspace-ui-store";

describe("workspace sidepanel state", () => {
  beforeEach(() => {
    localStorage.clear();
    useNavigationStore.setState({
      isCanvasOpen: false,
      canvasPanel: "workspace",
    });
    useWorkspaceUiStore.setState({
      selectedAssistantTurnId: null,
      activeInspectorTab: "trajectories",
      activeSidepanelTab: "trajectories",
      sidebarOpen: false,
      sidebarTab: "trajectories",
    });
  });

  it("starts closed and opens locally when an assistant turn is selected", () => {
    expect(useWorkspaceUiStore.getState().sidebarOpen).toBe(false);

    useWorkspaceUiStore.getState().selectInspectorTurn("assistant-1", "graph");

    expect(useWorkspaceUiStore.getState().selectedAssistantTurnId).toBe("assistant-1");
    expect(useWorkspaceUiStore.getState().sidebarOpen).toBe(true);
    expect(useWorkspaceUiStore.getState().activeSidepanelTab).toBe("graph");
    expect(useWorkspaceUiStore.getState().activeInspectorTab).toBe("graph");
    expect(useNavigationStore.getState().isCanvasOpen).toBe(false);
  });

  it("maps legacy inspector tabs to the Trajectories sidepanel tab", () => {
    useWorkspaceUiStore.getState().selectInspectorTurn("assistant-1", "trace");

    expect(useWorkspaceUiStore.getState().sidebarOpen).toBe(true);
    expect(useWorkspaceUiStore.getState().activeSidepanelTab).toBe("trajectories");
    expect(useWorkspaceUiStore.getState().activeInspectorTab).toBe("trace");
  });

  it("keeps open and closed state local to the current runtime session", () => {
    useWorkspaceUiStore.getState().openSidepanel("volume");

    expect(useWorkspaceUiStore.getState().sidebarOpen).toBe(true);
    expect(useWorkspaceUiStore.getState().activeSidepanelTab).toBe("volume");
    expect(localStorage.getItem("workspace.sidebarOpen")).toBeNull();
    expect(useNavigationStore.getState().isCanvasOpen).toBe(false);

    useWorkspaceUiStore.getState().closeSidepanel();

    expect(useWorkspaceUiStore.getState().sidebarOpen).toBe(false);
    expect(useWorkspaceUiStore.getState().activeSidepanelTab).toBe("volume");
    expect(localStorage.getItem("workspace.sidebarOpen")).toBeNull();
    expect(useNavigationStore.getState().isCanvasOpen).toBe(false);
  });

  it("closes without clearing selected turn or active tab context", () => {
    useWorkspaceUiStore.getState().selectInspectorTurn("assistant-1", "graph");

    useWorkspaceUiStore.getState().closeSidepanel();

    expect(useWorkspaceUiStore.getState().sidebarOpen).toBe(false);
    expect(useWorkspaceUiStore.getState().selectedAssistantTurnId).toBe("assistant-1");
    expect(useWorkspaceUiStore.getState().activeSidepanelTab).toBe("graph");
    expect(useWorkspaceUiStore.getState().activeInspectorTab).toBe("graph");
  });
});
