import { truncateToWidth, type Component } from "@earendil-works/pi-tui";

import { renderMessage } from "./message-renderer.js";
import type { ConversationStore, Message } from "./store.js";
import { theme } from "./theme.js";

type MessageRenderer = (message: Message, width: number) => string[];

type CachedMessage = {
  message: Message;
  width: number;
  lines: string[];
};

export class TranscriptComponent implements Component {
  private readonly cache = new Map<string, CachedMessage>();

  constructor(
    private readonly store: ConversationStore,
    private readonly renderer: MessageRenderer = renderMessage,
  ) {}

  invalidate(): void {
    this.cache.clear();
  }

  render(width: number): string[] {
    const safeWidth = Math.max(1, width);
    const state = this.store.getState();
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
    return lines;
  }

  private renderCached(message: Message, width: number): string[] {
    const dynamic =
      (message.kind === "text" && message.streaming) ||
      (message.kind === "tool" && message.status === "running");
    const cached = this.cache.get(message.id);
    if (!dynamic && cached?.message === message && cached.width === width) return cached.lines;
    const lines = this.renderer(message, width);
    if (!dynamic) this.cache.set(message.id, { message, width, lines });
    return lines;
  }
}

function dim(value: string): string {
  return theme.fg("dim", value);
}

function terminalSafeLine(value: string): string {
  return value
    .replaceAll(/[\p{Cc}\p{Zl}\p{Zp}]+/gu, " ")
    .replaceAll(/\s+/gu, " ")
    .trim();
}
