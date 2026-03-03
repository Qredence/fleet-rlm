import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import { FileDetail } from "@/features/artifacts/FileDetail";
import type { FsNode } from "@/lib/data/types";

let mockMode = false;
let contentState: {
  content: string;
  isLoading: boolean;
  error: Error | null;
};

vi.mock("@/lib/api/config", () => ({
  isMockMode: () => mockMode,
}));

vi.mock("@/hooks/useFilesystem", () => ({
  useFileContent: () => contentState,
}));

vi.mock("@/components/ui/use-mobile", () => ({
  useIsMobile: () => false,
}));

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
  },
}));

describe("FileDetail markdown rendering", () => {
  beforeEach(() => {
    mockMode = false;
    contentState = {
      content: "",
      isLoading: false,
      error: null,
    };
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

    const html = renderToStaticMarkup(<FileDetail file={file} />);

    expect(html).toContain("<h1");
    expect(html).toContain("Release Notes");
    expect(html).toContain("Added feature");
    expect(html).not.toContain("<pre");
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

    const html = renderToStaticMarkup(<FileDetail file={file} />);

    expect(html).toContain("<pre");
    expect(html).toContain("plain text preview");
  });
});
