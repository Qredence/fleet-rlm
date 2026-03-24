import { describe, expect, it, vi } from "vite-plus/test";

import {
  capFilesToCapacity,
  filterAcceptedFiles,
  filterFilesBySize,
} from "@/components/ui/prompt-input.utilities";

const createFile = (name: string, type: string, size = 4) =>
  new File([new Uint8Array(size)], name, { type });

describe("prompt-input utilities", () => {
  it("filters files by accepted mime patterns", () => {
    const onError = vi.fn();
    const files = [
      createFile("photo.png", "image/png"),
      createFile("notes.txt", "text/plain"),
    ];

    const accepted = filterAcceptedFiles(files, "image/*", onError);

    expect(accepted).toHaveLength(1);
    expect(accepted[0]?.name).toBe("photo.png");
    expect(onError).not.toHaveBeenCalled();
  });

  it("reports when no files match the accepted types", () => {
    const onError = vi.fn();
    const files = [createFile("notes.txt", "text/plain")];

    const accepted = filterAcceptedFiles(files, "image/*", onError);

    expect(accepted).toEqual([]);
    expect(onError).toHaveBeenCalledWith({
      code: "accept",
      message: "No files match the accepted types.",
    });
  });

  it("filters files by max file size", () => {
    const onError = vi.fn();
    const files = [
      createFile("small.png", "image/png", 4),
      createFile("large.png", "image/png", 12),
    ];

    const sized = filterFilesBySize(files, 8, onError);

    expect(sized).toHaveLength(1);
    expect(sized[0]?.name).toBe("small.png");
    expect(onError).not.toHaveBeenCalled();
  });

  it("caps files to the remaining attachment capacity", () => {
    const onError = vi.fn();
    const files = [
      createFile("a.png", "image/png"),
      createFile("b.png", "image/png"),
      createFile("c.png", "image/png"),
    ];

    const capped = capFilesToCapacity(files, 1, 3, onError);

    expect(capped).toHaveLength(2);
    expect(capped.map((file) => file.name)).toEqual(["a.png", "b.png"]);
    expect(onError).toHaveBeenCalledWith({
      code: "max_files",
      message: "Too many files. Some were not added.",
    });
  });
});
