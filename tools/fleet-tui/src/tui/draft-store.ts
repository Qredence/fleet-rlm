/** Debounced per-Session draft persistence for the Fleet TUI. */

import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";

import type { PendingAttachment, PendingSkillSelection } from "./store.js";

export type DraftState = {
  draft: string;
  pendingSkills: PendingSkillSelection[];
  pendingAttachments: PendingAttachment[];
  lastPrompt: string | null;
};

export type DraftStoreOptions = {
  /** Directory holding one `<session-id>.json` state file per Session. */
  dir?: string;
  /** Debounce delay in ms before writing (default 500). */
  debounceMs?: number;
};

/**
 * Best-effort local persistence of the editor draft, pinned Skills and
 * Attachments, and the last prompt. Writes are debounced and atomic; a failed
 * write never crashes the TUI. Keyed by Session id so resuming a Session
 * restores exactly what that Session had.
 */
export class DraftStore {
  private readonly dir: string;
  private readonly debounceMs: number;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private pending: { sessionId: string; state: DraftState } | null = null;

  constructor(options: DraftStoreOptions = {}) {
    this.dir =
      options.dir ?? process.env.FLEET_TUI_STATE_DIR ?? join(homedir(), ".local/share/fleet/tui");
    this.debounceMs = options.debounceMs ?? 500;
  }

  pathFor(sessionId: string): string {
    return join(this.dir, `${sessionId}.json`);
  }

  async load(sessionId: string): Promise<DraftState | null> {
    try {
      const raw = await readFile(this.pathFor(sessionId), "utf8");
      const parsed = JSON.parse(raw) as Partial<DraftState>;
      if (typeof parsed.draft !== "string") return null;
      return {
        draft: parsed.draft,
        pendingSkills: Array.isArray(parsed.pendingSkills) ? parsed.pendingSkills : [],
        pendingAttachments: Array.isArray(parsed.pendingAttachments)
          ? parsed.pendingAttachments
          : [],
        lastPrompt: typeof parsed.lastPrompt === "string" ? parsed.lastPrompt : null,
      };
    } catch {
      return null;
    }
  }

  schedule(sessionId: string, state: DraftState): void {
    this.pending = { sessionId, state };
    if (this.timer) return;
    this.timer = setTimeout(() => {
      this.timer = undefined;
      const current = this.pending;
      this.pending = null;
      if (current) void this.save(current.sessionId, current.state);
    }, this.debounceMs);
  }

  async flush(): Promise<void> {
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = undefined;
    }
    const current = this.pending;
    this.pending = null;
    if (current) await this.save(current.sessionId, current.state);
  }

  private async save(sessionId: string, state: DraftState): Promise<void> {
    try {
      await mkdir(this.dir, { recursive: true });
      const target = this.pathFor(sessionId);
      const temporaryPath = `${target}.${process.pid}.part`;
      await writeFile(temporaryPath, JSON.stringify(state, null, 2), "utf8");
      await rename(temporaryPath, target);
    } catch {
      // Best-effort persistence: never crash the TUI over a draft write.
    }
  }
}
