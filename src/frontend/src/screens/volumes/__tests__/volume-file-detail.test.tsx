import { beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { renderToStaticMarkup } from "react-dom/server";

import { VolumeFileDetail } from "@/screens/volumes/volumes-canvas-panel";
import type { FsNode } from "@/screens/volumes/use-volumes";

let contentState: {
  content: string;
  isLoading: boolean;
  error: Error | null;
};
let fileContentCalls: Array<{ path: string | null; provider: string }> = [];

vi.mock("@/screens/volumes/use-volumes", () => ({
  useFileContent: (path: string | null, provider: string) => {
    fileContentCalls.push({ path, provider });
    return contentState;
  },
}));

vi.mock("@/hooks/useIsMobile", () => ({
  useIsMobile: () => false,
}));

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
  },
}));

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
      provider: "modal",
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
