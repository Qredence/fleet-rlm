import { promises as fs } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const CHAT_HELPER_PATTERN = /\brlmCoreEndpoints\.chat\s*\(/;

async function collectSourceFiles(dir: string): Promise<string[]> {
  const entries = await fs.readdir(dir, { withFileTypes: true });
  const files: string[] = [];

  for (const entry of entries) {
    const absPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "__tests__") continue;
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

describe("ws-first chat guard", () => {
  it("prevents non-test usage of rlmCoreEndpoints.chat()", async () => {
    const thisDir = path.dirname(fileURLToPath(import.meta.url));
    const srcRoot = path.resolve(thisDir, "../../../");
    const files = await collectSourceFiles(srcRoot);
    const offenders: string[] = [];

    for (const file of files) {
      const relPath = path.relative(srcRoot, file);
      if (relPath === "lib/rlm-api/endpoints.ts") continue;

      const content = await fs.readFile(file, "utf8");
      if (CHAT_HELPER_PATTERN.test(content)) {
        offenders.push(relPath);
      }
    }

    expect(offenders).toEqual([]);
  });
});
