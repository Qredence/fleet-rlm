/**
 * Command history storage for persistent command history.
 * Saves to ~/.fleet-rlm/command-history.json
 */

const HISTORY_FILE = `${process.env.HOME}/.fleet-rlm/command-history.json`;
const MAX_HISTORY = 100;

export interface CommandHistory {
  commands: string[];
}

async function ensureHistoryDir(): Promise<void> {
  const dir = HISTORY_FILE.replace(/\/command-history\.json$/, "");
  const fs = await import("fs/promises");
  try {
    await fs.access(dir);
  } catch {
    await fs.mkdir(dir, { recursive: true });
  }
}

export async function loadHistory(): Promise<string[]> {
  try {
    const file = Bun.file(HISTORY_FILE);
    if (await file.exists()) {
      const content = await file.text();
      const data: CommandHistory = JSON.parse(content);
      return data.commands || [];
    }
  } catch (error) {
    console.error("Failed to load command history:", error);
  }
  return [];
}

export async function saveHistory(commands: string[]): Promise<void> {
  try {
    await ensureHistoryDir();
    const data: CommandHistory = {
      commands: commands.slice(-MAX_HISTORY),
    };
    await Bun.write(HISTORY_FILE, JSON.stringify(data, null, 2));
  } catch (error) {
    console.error("Failed to save command history:", error);
  }
}

export async function addToHistory(
  command: string,
  existingHistory: string[]
): Promise<string[]> {
  const trimmed = command.trim();
  if (!trimmed) return existingHistory;

  const newHistory = existingHistory.filter((c) => c !== trimmed);
  newHistory.push(trimmed);

  const limited = newHistory.slice(-MAX_HISTORY);
  await saveHistory(limited);

  return limited;
}
