import { beforeEach, describe, expect, it, vi } from "vite-plus/test";

describe("useNavigationStore", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  it("keeps default canvas handlers functional before registration", async () => {
    const { useNavigationStore } = await import("@/stores/navigationStore");

    useNavigationStore.getState().openCanvas();
    expect(useNavigationStore.getState().isCanvasOpen).toBe(true);

    useNavigationStore.getState().toggleCanvas();
    expect(useNavigationStore.getState().isCanvasOpen).toBe(false);
  });

  it("syncs already-open canvas state into newly registered handlers", async () => {
    const { useNavigationStore } = await import("@/stores/navigationStore");

    useNavigationStore.getState().openCanvas();

    const open = vi.fn(() => {
      useNavigationStore.setState({ isCanvasOpen: true });
    });
    const close = vi.fn(() => {
      useNavigationStore.setState({ isCanvasOpen: false });
    });

    useNavigationStore.getState().registerCanvasHandlers({ open, close });

    expect(open).toHaveBeenCalledOnce();
    expect(close).not.toHaveBeenCalled();
    expect(useNavigationStore.getState().isCanvasOpen).toBe(true);
  });
});
