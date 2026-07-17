import {
  Container,
  truncateToWidth,
  type Component,
  type Editor,
  type Terminal,
} from "@earendil-works/pi-tui";

import { formatDuration, formatTokens } from "./format.js";
import { renderMessage } from "./message-renderer.js";
import type { ConversationStore, Run } from "./store.js";
import { ansi } from "./theme.js";

export class FleetScreen extends Container {
  constructor(store: ConversationStore, editor: Editor, terminal: Terminal) {
    super();
    this.addChild(new TranscriptComponent(store));
    this.addChild(new ActivityComponent(store));
    this.addChild(editor);
    this.addChild(new FooterComponent(store, terminal));
  }
}

class TranscriptComponent implements Component {
  constructor(private readonly store: ConversationStore) {}
  invalidate(): void {}
  render(width: number): string[] {
    const state = this.store.getState();
    const session = state.session;
    const lines = [
      `${bold("FLEET")}`,
      dim(
        session
          ? `session ${session.id.slice(0, 8)}… · ${session.resumed ? "resumed" : "new"}`
          : "session unavailable",
      ),
    ];
    if (state.pendingSkillSelections.length > 0) {
      lines.push(
        dim(
          `next Turn Skills · ${state.pendingSkillSelections.map((item) => `${item.displayName}@${item.expectedVersion}`).join(", ")}`,
        ),
      );
    }
    lines.push("");
    if (state.messages.length === 0) {
      lines.push(dim("(empty conversation — type a prompt or /help)"));
      return lines;
    }
    state.messages.forEach((message, index) => {
      if (index > 0) lines.push("");
      lines.push(...renderMessage(message, width));
    });
    return lines;
  }
}

class ActivityComponent implements Component {
  constructor(private readonly store: ConversationStore) {}
  invalidate(): void {}
  render(width: number): string[] {
    const run = this.store.getState().run;
    if (!isBusy(run)) return [];
    const phase = run.phase === "cancelling" ? run.phase : run.statusPhase?.trim() || run.phase;
    const elapsed = run.startedAt ? formatDuration(Date.now() - run.startedAt) : "0:00";
    const detail = run.statusDetail?.trim();
    const primary = `${ansi.white}… ${phase.replaceAll(/[_-]+/g, " ").toUpperCase()}${ansi.reset}${detail ? `  ${dim(detail)}` : ""} · ${elapsed}`;
    const secondary = `${run.completedSteps} ${run.completedSteps === 1 ? "step" : "steps"} · ${run.toolCount} ${run.toolCount === 1 ? "tool" : "tools"} · Ctrl+C cancel`;
    return [
      "",
      truncateToWidth(`${ansi.gray}│${ansi.reset} ${primary}`, width, ""),
      truncateToWidth(`${ansi.gray}│${ansi.reset} ${dim(secondary)}`, width, ""),
    ];
  }
}

class FooterComponent implements Component {
  constructor(
    private readonly store: ConversationStore,
    private readonly terminal: Terminal,
  ) {}
  invalidate(): void {}
  render(width: number): string[] {
    const state = this.store.getState();
    const prompt = state.messages
      .filter((m) => m.kind === "usage")
      .reduce((sum, m) => sum + m.prompt, 0);
    const completion = state.messages
      .filter((m) => m.kind === "usage")
      .reduce((sum, m) => sum + m.completion, 0);
    const compact = this.terminal.rows < 14 || width < 60;
    const lines = compact
      ? []
      : [
          dim(
            isBusy(state.run)
              ? "Ctrl+C cancel · Ctrl+D cancel and exit"
              : "Enter send · modified Enter newline · / commands · Ctrl+D exit",
          ),
        ];
    lines.push(
      truncateToWidth(
        `${dim("model")} ${state.run.model ?? "—"}  ${dim("tokens")} ${formatTokens(prompt + completion)}  ${dim("steps")} ${state.run.completedSteps}  ${dim("tools")} ${state.run.toolCount}`,
        width,
        "",
      ),
    );
    return lines;
  }
}

export function isBusy(run: Run): boolean {
  return run.phase === "submitting" || run.phase === "running" || run.phase === "cancelling";
}

function bold(value: string): string {
  return `${ansi.bold}${ansi.white}${value}${ansi.reset}`;
}
function dim(value: string): string {
  return `${ansi.dim}${value}${ansi.dimOff}`;
}
