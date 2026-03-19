import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";

import { VolumesBrowser } from "@/screens/volumes/volumes-browser";
import type { VolumeProvider } from "@/screens/volumes/model/volumes-types";

const useFilesystemMock = vi.fn();
const clearSelectedFile = vi.fn();
const selectFile = vi.fn();
const openCanvas = vi.fn();

vi.mock("@/screens/volumes/hooks/use-volumes-filesystem", () => ({
  useFilesystem: (provider: VolumeProvider) => useFilesystemMock(provider),
}));

vi.mock("@/stores/navigationStore", () => ({
  useNavigationStore: (selector: (state: { openCanvas: () => void }) => unknown) =>
    selector({ openCanvas }),
}));

vi.mock("@/screens/volumes/model/volumes-selection-store", () => ({
  useVolumesSelectionStore: (
    selector: (state: {
      selectFile: (node: unknown) => void;
      clearSelectedFile: () => void;
    }) => unknown,
  ) =>
    selector({
      selectFile,
      clearSelectedFile,
    }),
}));

vi.mock("@/hooks/useIsMobile", () => ({
  useIsMobile: () => false,
}));

vi.mock("@/hooks/useRuntimeStatus", () => ({
  useRuntimeStatus: () => ({
    data: {
      sandbox_provider: "modal",
    },
  }),
}));

vi.mock("motion/react", () => ({
  useReducedMotion: () => true,
}));

vi.mock("@/screens/volumes/volumes-browser-sections", () => ({
  FsItem: ({ node }: { node: { name: string; provider?: string } }) => (
    <div data-testid="fs-item">{`${node.name}:${node.provider ?? "unknown"}`}</div>
  ),
}));

(
  globalThis as typeof globalThis & {
    IS_REACT_ACT_ENVIRONMENT?: boolean;
  }
).IS_REACT_ACT_ENVIRONMENT = true;

describe("VolumesBrowser", () => {
  beforeEach(() => {
    useFilesystemMock.mockImplementation((provider: VolumeProvider) => ({
      volumes: [
        {
          id: `${provider}-volume`,
          name: `${provider}-volume`,
          path: `/${provider}`,
          provider,
          type: "volume",
          children: [],
        },
      ],
      dataSource: "api",
      degradedReason: undefined,
      isLoading: false,
      isFetching: false,
      error: null,
      refetch: vi.fn(),
    }));
    useFilesystemMock.mockClear();
    clearSelectedFile.mockClear();
    selectFile.mockClear();
    openCanvas.mockClear();
  });

  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("defaults to the active runtime provider and switches provider-scoped queries locally", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    act(() => {
      root.render(<VolumesBrowser />);
    });

    expect(useFilesystemMock).toHaveBeenCalled();
    expect(useFilesystemMock.mock.calls.at(-1)?.[0]).toBe("modal");
    expect(container.textContent).toContain("Browse the modal runtime volume");
    expect(container.textContent).toContain("modal-volume:modal");

    const daytonaToggle = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent?.trim() === "Daytona",
    );
    expect(daytonaToggle).toBeTruthy();

    act(() => {
      daytonaToggle?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(useFilesystemMock.mock.calls.at(-1)?.[0]).toBe("daytona");
    expect(clearSelectedFile).toHaveBeenCalledOnce();
    expect(container.textContent).toContain("Browse the daytona runtime volume");
    expect(container.textContent).toContain("daytona-volume:daytona");

    act(() => {
      root.unmount();
    });
  });
});
