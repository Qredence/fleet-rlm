import { promises as fs } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const LEGACY_HELPER_CALL = "rlmCoreEndpoints.";

function stripStringsAndComments(source: string): string {
  let output = "";
  let index = 0;
  const length = source.length;
  let state:
    | "code"
    | "line_comment"
    | "block_comment"
    | "single_quote"
    | "double_quote"
    | "template" = "code";

  while (index < length) {
    const char = source[index]!;
    const next = source[index + 1];

    if (state === "code") {
      if (char === "/" && next === "/") {
        state = "line_comment";
        output += "  ";
        index += 2;
        continue;
      }
      if (char === "/" && next === "*") {
        state = "block_comment";
        output += "  ";
        index += 2;
        continue;
      }
      if (char === "'") {
        state = "single_quote";
        output += " ";
        index += 1;
        continue;
      }
      if (char === '"') {
        state = "double_quote";
        output += " ";
        index += 1;
        continue;
      }
      if (char === "`") {
        state = "template";
        output += " ";
        index += 1;
        continue;
      }
      output += char;
      index += 1;
      continue;
    }

    if (state === "line_comment") {
      if (char === "\n") {
        state = "code";
        output += "\n";
      } else {
        output += " ";
      }
      index += 1;
      continue;
    }

    if (state === "block_comment") {
      if (char === "*" && next === "/") {
        state = "code";
        output += "  ";
        index += 2;
      } else {
        output += char === "\n" ? "\n" : " ";
        index += 1;
      }
      continue;
    }

    if (char === "\\") {
      output += "  ";
      index += 2;
      continue;
    }

    const isSingle = state === "single_quote" && char === "'";
    const isDouble = state === "double_quote" && char === '"';
    const isTemplate = state === "template" && char === "`";
    if (isSingle || isDouble || isTemplate) {
      state = "code";
      output += " ";
      index += 1;
      continue;
    }

    output += char === "\n" ? "\n" : " ";
    index += 1;
  }

  return output;
}

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
  it("prevents non-test usage of removed rlmCoreEndpoints helper", async () => {
    const thisDir = path.dirname(fileURLToPath(import.meta.url));
    const srcRoot = path.resolve(thisDir, "../../../");
    const files = await collectSourceFiles(srcRoot);
    const offenders: string[] = [];

    for (const file of files) {
      const relPath = path.relative(srcRoot, file);
      const content = await fs.readFile(file, "utf8");
      const stripped = stripStringsAndComments(content);
      if (stripped.includes(LEGACY_HELPER_CALL)) {
        offenders.push(relPath);
      }
    }

    expect(offenders).toEqual([]);
  });

  it("does not export rlmCoreEndpoints from rlm-api barrel", async () => {
    const module = await import("@/lib/rlm-api");
    expect("rlmCoreEndpoints" in module).toBe(false);
  });

  it("ensures generated OpenAPI no longer contains /api/v1/chat", async () => {
    const thisDir = path.dirname(fileURLToPath(import.meta.url));
    const srcRoot = path.resolve(thisDir, "../../../");
    const generatedPath = path.resolve(
      srcRoot,
      "lib/rlm-api/generated/openapi.ts",
    );
    const generated = await fs.readFile(generatedPath, "utf8");
    expect(generated.includes('"/api/v1/chat"')).toBe(false);
  });
});
