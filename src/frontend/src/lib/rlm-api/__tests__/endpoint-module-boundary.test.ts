import { promises as fs } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vite-plus/test";

/**
 * The low-level `rlmApiClient` transport must only be consumed by endpoint
 * modules under `lib/rlm-api/`. Feature code, hooks, and components must call a
 * typed endpoint module (e.g. `sessionsEndpoints`, `volumesEndpoints`) so the
 * FastAPI backend contract stays the single source of truth and URLs/types are
 * not hand-rolled at call sites.
 */
const CLIENT_IMPORT = "@/lib/rlm-api/client";

async function collectSourceFiles(dir: string): Promise<string[]> {
  const entries = await fs.readdir(dir, { withFileTypes: true });
  const files: string[] = [];

  for (const entry of entries) {
    const absPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "__tests__" || entry.name === "node_modules") continue;
      files.push(...(await collectSourceFiles(absPath)));
      continue;
    }
    if (!entry.isFile()) continue;
    if (!/\.(ts|tsx)$/.test(entry.name)) continue;
    if (entry.name.includes(".test.") || entry.name.includes(".spec.")) continue;
    files.push(absPath);
  }

  return files;
}

describe("rlm-api endpoint module boundary", () => {
  it("only allows lib/rlm-api modules to import the raw rlmApiClient", async () => {
    const thisDir = path.dirname(fileURLToPath(import.meta.url));
    const srcRoot = path.resolve(thisDir, "../../../");
    const apiRoot = path.resolve(thisDir, "..");
    const files = await collectSourceFiles(srcRoot);
    const offenders: string[] = [];

    for (const file of files) {
      if (file.startsWith(apiRoot)) continue;
      const content = await fs.readFile(file, "utf8");
      if (content.includes(CLIENT_IMPORT)) {
        offenders.push(path.relative(srcRoot, file));
      }
    }

    expect(offenders).toEqual([]);
  });
});
