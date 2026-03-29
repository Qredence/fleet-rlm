import { describe, expect, it } from "vite-plus/test";

import {
  capFilesToCapacity,
  filterAcceptedFiles,
  filterFilesBySize,
  matchesAccept,
} from "@/components/ai-elements/prompt-input/prompt-input.utilities";

describe("prompt-input utilities", () => {
  it("matches mime wildcard accepts", () => {
    const image = new File(["binary"], "photo.png", { type: "image/png" });

    expect(matchesAccept(image, "image/*")).toBe(true);
    expect(matchesAccept(image, "application/pdf")).toBe(false);
  });

  it("filters files by accepted types", () => {
    const image = new File(["image"], "photo.png", { type: "image/png" });
    const pdf = new File(["pdf"], "doc.pdf", { type: "application/pdf" });

    expect(filterAcceptedFiles([image, pdf], "image/*")).toEqual([image]);
  });

  it("filters files by size", () => {
    const small = new File(["small"], "small.txt", { type: "text/plain" });
    const large = new File([new Uint8Array(16)], "large.txt", {
      type: "text/plain",
    });

    expect(filterFilesBySize([small, large], 10)).toEqual([small]);
  });

  it("caps files to remaining capacity", () => {
    const files = [
      new File(["1"], "a.txt", { type: "text/plain" }),
      new File(["2"], "b.txt", { type: "text/plain" }),
      new File(["3"], "c.txt", { type: "text/plain" }),
    ];

    expect(capFilesToCapacity(files, 1, 3)).toEqual(files.slice(0, 2));
  });
});
