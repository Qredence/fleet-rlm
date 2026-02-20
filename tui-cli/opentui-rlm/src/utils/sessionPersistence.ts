/**
 * Session persistence utilities for saving and restoring chat state.
 * Saves transcript and settings to ~/.fleet-rlm/sessions/
 */

import type { TranscriptEvent } from "../types/protocol";
import type { TraceMode } from "../types/protocol";

const SESSIONS_DIR = `${process.env.HOME}/.fleet-rlm/sessions`;
const MAX_SESSIONS = 10;

export interface SessionData {
  id: string;
  workspaceId: string;
  userId: string;
  transcript: TranscriptEvent[];
  traceMode: TraceMode;
  docsPath: string | null;
  savedAt: string;
}

async function ensureSessionsDir(): Promise<void> {
  const fs = await import("fs/promises");
  try {
    await fs.access(SESSIONS_DIR);
  } catch {
    await fs.mkdir(SESSIONS_DIR, { recursive: true });
  }
}

export async function saveSession(session: SessionData): Promise<void> {
  await ensureSessionsDir();

  const filePath = `${SESSIONS_DIR}/${session.id}.json`;
  const data = {
    ...session,
    savedAt: new Date().toISOString(),
  };

  await Bun.write(filePath, JSON.stringify(data, null, 2));

  await cleanupOldSessions();
}

export async function loadSession(sessionId: string): Promise<SessionData | null> {
  try {
    const filePath = `${SESSIONS_DIR}/${sessionId}.json`;
    const file = Bun.file(filePath);

    if (await file.exists()) {
      const content = await file.text();
      return JSON.parse(content) as SessionData;
    }
  } catch (error) {
    console.error("Failed to load session:", error);
  }

  return null;
}

export async function listSessions(): Promise<SessionData[]> {
  await ensureSessionsDir();

  try {
    const fs = await import("fs/promises");
    const entries = await fs.readdir(SESSIONS_DIR);

    const sessions: SessionData[] = [];

    for (const entry of entries) {
      if (entry.endsWith(".json")) {
        const filePath = `${SESSIONS_DIR}/${entry}`;
        const file = Bun.file(filePath);

        if (await file.exists()) {
          try {
            const content = await file.text();
            sessions.push(JSON.parse(content) as SessionData);
          } catch {
            // Skip invalid session files
          }
        }
      }
    }

    return sessions.sort((a, b) =>
      new Date(b.savedAt).getTime() - new Date(a.savedAt).getTime()
    );
  } catch (error) {
    console.error("Failed to list sessions:", error);
    return [];
  }
}

export async function deleteSession(sessionId: string): Promise<void> {
  try {
    const fs = await import("fs/promises");
    const filePath = `${SESSIONS_DIR}/${sessionId}.json`;
    await fs.unlink(filePath);
  } catch (error) {
    console.error("Failed to delete session:", error);
  }
}

async function cleanupOldSessions(): Promise<void> {
  const sessions = await listSessions();

  if (sessions.length > MAX_SESSIONS) {
    const toDelete = sessions.slice(MAX_SESSIONS);

    for (const session of toDelete) {
      await deleteSession(session.id);
    }
  }
}

export function generateSessionId(): string {
  const timestamp = Date.now().toString(36);
  const random = Math.random().toString(36).slice(2, 8);
  return `session-${timestamp}-${random}`;
}
