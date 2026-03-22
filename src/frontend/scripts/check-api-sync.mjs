import { readFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";

const GENERATED_FILES = ["openapi/fleet-rlm.openapi.yaml", "src/lib/rlm-api/generated/openapi.ts"];

async function readSnapshot(path) {
  try {
    return await readFile(path, "utf8");
  } catch (error) {
    if (error && typeof error === "object" && "code" in error && error.code === "ENOENT") {
      return null;
    }
    throw error;
  }
}

async function main() {
  const before = new Map(
    await Promise.all(GENERATED_FILES.map(async (path) => [path, await readSnapshot(path)])),
  );

  const result = spawnSync("pnpm", ["run", "api:sync"], {
    cwd: process.cwd(),
    stdio: "inherit",
    shell: process.platform === "win32",
  });

  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }

  const changed = [];
  for (const path of GENERATED_FILES) {
    const after = await readSnapshot(path);
    if (before.get(path) !== after) {
      changed.push(path);
    }
  }

  if (changed.length > 0) {
    console.error("Generated OpenAPI artifacts changed during api:check:");
    for (const path of changed) {
      console.error(`- ${path}`);
    }
    console.error("Run `pnpm run api:sync` and keep the updated generated files in this change.");
    process.exit(1);
  }

  console.log("OpenAPI frontend artifacts are already in sync.");
}

await main();
