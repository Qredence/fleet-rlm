import { describe, expect, it } from "vite-plus/test";

import { getMockFilesystem } from "@/features/volumes/use-volumes";

describe("getMockFilesystem", () => {
  it("uses the canonical durable roots for daytona", () => {
    const filesystem = getMockFilesystem("daytona");

    expect(filesystem.map((node) => node.path)).toEqual([
      "/home/daytona/memory/memory",
      "/home/daytona/memory/artifacts",
      "/home/daytona/memory/buffers",
      "/home/daytona/memory/meta",
    ]);
  });
});
