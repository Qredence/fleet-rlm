import { truncateToWidth, type Component } from "@earendil-works/pi-tui";

import { MessageRenderCache, renderMessage } from "./message-renderer.js";
import { terminalSafeLine } from "./terminal-text.js";
import type { ConversationStore, Message, State } from "./store.js";
import { theme } from "./theme.js";

type MessageRenderer = (message: Message, width: number, cache: MessageRenderCache) => string[];

type CachedMessage = {
  message: Message;
  width: number;
  lines: string[];
};

export class TranscriptComponent implements Component {
  private readonly cache = new Map<string, CachedMessage>();
  private readonly renderCache = new MessageRenderCache();
  // Flat render fast path: the store only replaces the messages array on
  // actual message changes, so frames that touch no message (keystrokes,
  // loader ticks, status heartbeats) return the previous render O(1) instead
  // of re-concatenating the whole transcript every frame.
  private cachedLines: string[] = [];
  private cachedWidth = 0;
  private cachedMessages: readonly Message[] | null = null;
  private cachedSession: State["session"] = null;

  constructor(
    private readonly store: ConversationStore,
    private readonly renderer: MessageRenderer = renderMessage,
  ) {}

  invalidate(): void {
    this.cache.clear();
    this.renderCache.clear();
    this.cachedMessages = null;
  }

  render(width: number): string[] {
    const safeWidth = Math.max(1, width);
    const state = this.store.getState();
    if (
      safeWidth === this.cachedWidth &&
      state.messages === this.cachedMessages &&
      state.session === this.cachedSession
    ) {
      return this.cachedLines;
    }
    const lines = this.buildLines(state, safeWidth);
    this.cachedWidth = safeWidth;
    this.cachedMessages = state.messages;
    this.cachedSession = state.session;
    this.cachedLines = lines;
    return lines;
  }

  private buildLines(state: State, safeWidth: number): string[] {
    const session = state.session;
    const lines = [
      truncateToWidth(
        `${theme.fg("accent", theme.bold("FLEET"))}${dim("  /  RLM OPERATOR")}`,
        safeWidth,
        "",
      ),
    ];
    lines.push(
      truncateToWidth(
        session
          ? `${theme.fg("dim", "SESSION")}  ${theme.fg("text", terminalSafeLine(session.title))}${dim(`  ·  ${session.resumed ? "resumed" : "new"}  ·  ${session.status}`)}`
          : `${theme.fg("dim", "SESSION")}  ${dim("unavailable")}`,
        safeWidth,
        "",
      ),
    );
    lines.push("");
    if (state.messages.length === 0) {
      lines.push(
        truncateToWidth(`${theme.fg("accent", "›")} ${theme.bold("Start a Turn")}`, safeWidth, "…"),
      );
      lines.push(
        truncateToWidth(
          dim("  Investigate a question, analyze workspace files, or create a report."),
          safeWidth,
          "…",
        ),
      );
      lines.push(
        truncateToWidth(
          `  ${theme.fg("mdCode", "/skills")} ${dim("expertise")}   ${theme.fg("mdCode", "/attach")} ${dim("files")}   ${theme.fg("mdCode", "/help")} ${dim("commands")}`,
          safeWidth,
          "…",
        ),
      );
      this.cache.clear();
      this.renderCache.clear();
      return lines;
    }

    const retained = new Set<string>();
    let previousRunId: string | undefined;
    let trajectoryIndex = 0;
    state.messages.forEach((message, index) => {
      retained.add(message.id);
      if (index > 0) lines.push("");
      const runId = messageRunId(message);
      if (runId && runId !== previousRunId) {
        trajectoryIndex += 1;
        lines.push(trajectoryDivider(trajectoryIndex, safeWidth));
        previousRunId = runId;
      }
      lines.push(...this.renderCached(message, safeWidth));
    });
    for (const id of this.cache.keys()) {
      if (!retained.has(id)) this.cache.delete(id);
    }
    this.renderCache.retain(retained);
    return lines;
  }

  private renderCached(message: Message, width: number): string[] {
    // Every message — including streaming/tool/running ones — is keyed on
    // object identity + width: a render without either change costs nothing.
    // Streaming/text-tool dispatches create a new message object via spread,
    // so their cache entry busts exactly when content actually changes.
    const cached = this.cache.get(message.id);
    if (cached?.message === message && cached.width === width) return cached.lines;
    const lines = this.renderer(message, width, this.renderCache);
    this.cache.set(message.id, { message, width, lines });
    return lines;
  }
}

function dim(value: string): string {
  return theme.fg("dim", value);
}

/**
 * Gets the internal execution identifier associated with a message.
 *
 * @returns The message's execution identifier, or `undefined` when none is present.
 */
function messageRunId(message: Message): string | undefined {
  return "runId" in message ? message.runId : undefined;
}

/**
 * Formats a numbered trajectory divider for the transcript.
 *
 * @param index - The trajectory number to display
 * @param width - The maximum display width
 * @returns The width-truncated trajectory divider
 */
function trajectoryDivider(index: number, width: number): string {
  const label = `${theme.fg("accent", theme.bold("◇ TRAJECTORY"))}${dim(`  turn ${index}`)}`;
  return truncateToWidth(label, width, "");
}
