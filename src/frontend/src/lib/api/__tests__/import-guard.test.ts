import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";

import { describe, expect, it } from "vitest";

const FRONTEND_SRC_DIR = resolve(process.cwd(), "src");
const LEGACY_API_DIR = resolve(process.cwd(), "src/lib/api");

function walkFiles(dir: string, collected: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const absPath = join(dir, entry);
    const stat = statSync(absPath);
    if (stat.isDirectory()) {
      walkFiles(absPath, collected);
      continue;
    }
    if (/\.(ts|tsx)$/.test(entry)) {
      collected.push(absPath);
    }
  }
  return collected;
}

describe("frontend import guard", () => {
  it("prevents feature code from importing legacy auth/chat endpoints", () => {
    const files = walkFiles(FRONTEND_SRC_DIR).filter(
      (file) => !file.startsWith(LEGACY_API_DIR),
    );
    const violations: string[] = [];
    const disallowedImportPattern =
      /import\s*\{[\s\S]*?\b(?:chatEndpoints|authEndpoints)\b[\s\S]*?\}\s*from\s*["']@\/lib\/api(?:\/endpoints)?["']/g;

    for (const file of files) {
      const content = readFileSync(file, "utf-8");
      if (disallowedImportPattern.test(content)) {
        violations.push(relative(process.cwd(), file));
      }
      disallowedImportPattern.lastIndex = 0;
    }

    expect(violations).toEqual([]);
  });
});
