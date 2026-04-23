import { act } from "react";
import type { HTMLAttributes, ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";

import { VolumeFileDetail } from "@/features/volumes/components/file-detail";
import { VolumesBrowser } from "@/features/volumes/volumes-screen";
import type { FsNode, VolumeProvider } from "@/features/volumes/use-volumes";

let contentState: {
  content: string;
  isLoading: boolean;
  error: Error | null;
};
let fileContentCalls: Array<{ path: string | null; provider: string }> = [];

const useFilesystemMock = vi.fn();
const clearSelectedFile = vi.fn();
const selectFile = vi.fn();
const openCanvas = vi.fn();

vi.mock("@/features/volumes/use-volumes", async () => {
  const actual = await vi.importActual<typeof import("@/features/volumes/use-volumes")>(
    "@/features/volumes/use-volumes",
  );
  return {
    ...actual,
    useFileContent: (path: string | null, provider: string, _volumeName?: string | null) => {
      fileContentCalls.push({ path, provider });
      return contentState;
    },
    useFilesystem: (provider: VolumeProvider) => useFilesystemMock(provider),
    useVolumesList: () => ({
      volumes: [],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    }),
    useVolumesSelectionStore: (
      selector?: (state: {
        selectFile: (node: unknown) => void;
        clearSelectedFile: () => void;
        selectedVolumeName: string | null;
        selectVolume: (name: string | null) => void;
      }) => unknown,
    ) =>
      selector
        ? selector({ selectFile, clearSelectedFile, selectedVolumeName: null, selectVolume: vi.fn() })
        : null,
  };
});

vi.mock("@/stores/navigation-store", () => ({
  useNavigationStore: (selector: (state: { openCanvas: () => void }) => unknown) =>
    selector({ openCanvas }),
}));

vi.mock("@/hooks/use-is-mobile", () => ({
  useIsMobile: () => false,
}));

vi.mock("@/hooks/use-runtime-status", () => ({
  useRuntimeStatus: () => ({
    data: {
      sandbox_provider: "daytona",
    },
  }),
}));

vi.mock("motion/react", () => ({
  AnimatePresence: ({ children }: { children: ReactNode }) => children,
  motion: {
    div: ({ children, ...props }: HTMLAttributes<HTMLDivElement>) => (
      <div {...props}>{children}</div>
    ),
  },
  useReducedMotion: () => true,
}));

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
  },
}));

(
  globalThis as typeof globalThis & {
    IS_REACT_ACT_ENVIRONMENT?: boolean;
  }
).IS_REACT_ACT_ENVIRONMENT = true;

describe("VolumeFileDetail markdown rendering", () => {
  beforeEach(() => {
    contentState = {
      content: "",
      isLoading: false,
      error: null,
    };
    fileContentCalls = [];
  });

  it("renders markdown files with formatted markdown output", () => {
    contentState = {
      content: "# Release Notes\n\n- Added feature",
      isLoading: false,
      error: null,
    };

    const file: FsNode = {
      id: "md-1",
      name: "release-notes.md",
      path: "/docs/release-notes.md",
      type: "file",
      children: [],
      size: 42,
      modifiedAt: "2026-03-03T00:00:00Z",
    };

    const html = renderToStaticMarkup(<VolumeFileDetail file={file} />);

    expect(html).toContain("<h1");
    expect(html).toContain("Release Notes");
    expect(html).toContain("Added feature");
    expect(html).not.toContain("<pre");
    expect(fileContentCalls[0]).toEqual({
      path: "/docs/release-notes.md",
      provider: "daytona",
    });
  });

  it("renders non-markdown text files in preformatted mode", () => {
    contentState = {
      content: "plain text preview",
      isLoading: false,
      error: null,
    };

    const file: FsNode = {
      id: "txt-1",
      name: "notes.txt",
      path: "/docs/notes.txt",
      type: "file",
      children: [],
      size: 16,
      modifiedAt: "2026-03-03T00:00:00Z",
    };

    const html = renderToStaticMarkup(<VolumeFileDetail file={file} />);

    expect(html).toContain("<pre");
    expect(html).toContain("plain text preview");
  });

  it("requests API content using the file provider when present", () => {
    contentState = {
      content: "daytona preview",
      isLoading: false,
      error: null,
    };

    const file: FsNode = {
      id: "py-1",
      name: "notes.py",
      path: "/workspace/notes.py",
      provider: "daytona",
      type: "file",
      children: [],
      size: 16,
      modifiedAt: "2026-03-03T00:00:00Z",
    };

    renderToStaticMarkup(<VolumeFileDetail file={file} />);

    expect(fileContentCalls[0]).toEqual({
      path: "/workspace/notes.py",
      provider: "daytona",
    });
  });
});

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

  it("uses the Daytona durable volume view by default", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    act(() => {
      root.render(<VolumesBrowser />);
    });

    expect(useFilesystemMock).toHaveBeenCalled();
    expect(useFilesystemMock.mock.calls.at(-1)?.[0]).toBe("daytona");
    expect(container.textContent).toContain("Browse the daytona mounted durable volume");
    expect(container.textContent).toContain("/daytona");
    expect(clearSelectedFile).not.toHaveBeenCalled();

    act(() => {
      root.unmount();
    });
  });
});
