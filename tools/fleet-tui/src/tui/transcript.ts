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
  private cachedSkills: State["pendingSkillSelections"] | null = null;

  constructor(
    private readonly store: ConversationStore,
    private readonly renderer: MessageRenderer = renderMessage,
  ) {}

  invalidate(): void {
    this.cache.clear();
    this.renderCache.clear();
    this.cachedMessages = null;
    this.cachedSkills = null;
  }

  render(width: number): string[] {
    const safeWidth = Math.max(1, width);
    const state = this.store.getState();
    if (
      safeWidth === this.cachedWidth &&
      state.messages === this.cachedMessages &&
      state.session === this.cachedSession &&
      state.pendingSkillSelections === this.cachedSkills
    ) {
      return this.cachedLines;
    }
    const lines = this.buildLines(state, safeWidth);
    this.cachedWidth = safeWidth;
    this.cachedMessages = state.messages;
    this.cachedSession = state.session;
    this.cachedSkills = state.pendingSkillSelections;
    this.cachedLines = lines;
    return lines;
  }

  private buildLines(state: State, safeWidth: number): string[] {
    const session = state.session;
    const lines = [theme.fg("accent", theme.bold("FLEET"))];
    lines.push(
      dim(
        truncateToWidth(
          session
            ? `${terminalSafeLine(session.title)} · ${session.resumed ? "resumed" : "new"} · ${session.status}`
            : "session unavailable",
          safeWidth,
          "",
        ),
      ),
    );
    if (state.pendingSkillSelections.length > 0) {
      lines.push(
        dim(
          truncateToWidth(
            `next Turn Skills · ${state.pendingSkillSelections.map((item) => `${item.displayName}@${item.expectedVersion}`).join(", ")}`,
            safeWidth,
            "",
          ),
        ),
      );
    }
    lines.push("");
    if (state.messages.length === 0) {
      lines.push(dim("(empty conversation — type a prompt or /help)"));
      this.cache.clear();
      this.renderCache.clear();
      return lines;
    }

    const retained = new Set<string>();
    state.messages.forEach((message, index) => {
      retained.add(message.id);
      if (index > 0) lines.push("");
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
