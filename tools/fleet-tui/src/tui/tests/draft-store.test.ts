import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { DraftStore, type DraftState } from "../draft-store.js";

const tempDirs: string[] = [];

async function makeStore(): Promise<{ store: DraftStore; dir: string }> {
  const dir = await mkdtemp(join(tmpdir(), "fleet-draft-"));
  tempDirs.push(dir);
  return { store: new DraftStore({ dir, debounceMs: 20 }), dir };
}

afterEach(async () => {
  await Promise.all(tempDirs.splice(0).map((dir) => rm(dir, { recursive: true, force: true })));
});

const state: DraftState = {
  draft: "half typed prompt",
  pendingSkills: [{ id: "sk-1", expectedVersion: "2.0.0", displayName: "long-context" }],
  pendingAttachments: [{ id: "at-1", filename: "f.txt", bytes: 3 }],
  lastPrompt: "previous prompt",
};

describe("DraftStore", () => {
  it("loads nothing for an unknown Session", async () => {
    const { store } = await makeStore();
    expect(await store.load("missing-session")).toBeNull();
  });

  it("round-trips a draft through a debounced atomic write", async () => {
    const { store } = await makeStore();
    store.schedule("session-1", state);
    await store.flush();
    expect(await store.load("session-1")).toEqual(state);
  });

  it("writes one file per Session and tolerates corrupt state files", async () => {
    const { store, dir } = await makeStore();
    store.schedule("session-1", state);
    await store.flush();
    store.schedule("session-2", { ...state, draft: "other" });
    await store.flush();

    const files = (await import("node:fs/promises")).readdir(dir);
    expect((await files).sort()).toEqual(["session-1.json", "session-2.json"]);

    // Corrupt file: load must fall back to null, never throw.
    const { writeFile } = await import("node:fs/promises");
    await writeFile(join(dir, "session-1.json"), "{not json", "utf8");
    expect(await store.load("session-1")).toBeNull();
  });

  it("collapses rapid schedules into one write", async () => {
    const { store, dir } = await makeStore();
    store.schedule("session-1", { ...state, draft: "a" });
    store.schedule("session-1", { ...state, draft: "b" });
    store.schedule("session-1", { ...state, draft: "c" });
    await store.flush();
    const raw = await readFile(join(dir, "session-1.json"), "utf8");
    expect((JSON.parse(raw) as DraftState).draft).toBe("c");
  });
});
