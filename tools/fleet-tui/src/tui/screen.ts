import {
  Container,
  truncateToWidth,
  type Component,
  type Editor,
  type Terminal,
  type TUI,
} from "@earendil-works/pi-tui";

import { formatDuration, formatTokens } from "./format.js";
import type { ConversationStore, Run } from "./store.js";
import { ansi } from "./theme.js";
import { TranscriptComponent } from "./transcript.js";

export class FleetScreen extends Container {
  private readonly activity: ActivityComponent;

  constructor(store: ConversationStore, editor: Editor, terminal: Terminal, ui: TUI) {
    super();
    this.activity = new ActivityComponent(store, ui);
    this.addChild(new TranscriptComponent(store));
    this.addChild(this.activity);
    this.addChild(editor);
    this.addChild(new FooterComponent(store, terminal));
  }

  dispose(): void {
    this.activity.dispose();
  }
}

class ActivityComponent implements Component {
  private timer?: NodeJS.Timeout;

  constructor(
    private readonly store: ConversationStore,
    private readonly ui: TUI,
  ) {}
  invalidate(): void {}
  render(width: number): string[] {
    const run = this.store.getState().run;
    if (!isBusy(run)) {
      this.dispose();
      return [];
    }
    this.timer ??= setInterval(() => this.ui.requestRender(), 250);
    const localPhase = run.phase === "submitting" ? "preparing" : run.phase;
    const phase = run.phase === "cancelling" ? run.phase : run.statusPhase?.trim() || localPhase;
    const elapsed = run.startedAt ? formatDuration(Date.now() - run.startedAt) : "0:00";
    const detail = run.statusDetail?.trim();
    const primary = `${ansi.white}… ${phase.replaceAll(/[_-]+/g, " ").toUpperCase()}${ansi.reset}${detail ? `  ${dim(detail)}` : ""} · ${elapsed}`;
    const secondary = `${run.completedSteps}/${run.startedSteps} steps complete · ${run.toolCount} ${run.toolCount === 1 ? "tool" : "tools"} · Ctrl+C cancel`;
    return [
      "",
      truncateToWidth(`${ansi.gray}│${ansi.reset} ${primary}`, width, ""),
      truncateToWidth(`${ansi.gray}│${ansi.reset} ${dim(secondary)}`, width, ""),
    ];
  }

  dispose(): void {
    if (!this.timer) return;
    clearInterval(this.timer);
    this.timer = undefined;
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
    const run = state.run;
    const outcome = run.outcome ? `  ${dim("outcome")} ${run.outcome}` : "";
    const replay = run.delivery === "replay" ? " · replay" : "";
    lines.push(
      truncateToWidth(
        `${dim("session tokens")} ${formatTokens(prompt + completion)}  ${dim("turn steps")} ${run.completedSteps}/${run.startedSteps}  ${dim("turn tools")} ${run.toolCount}${outcome}${replay}`,
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

function dim(value: string): string {
  return `${ansi.dim}${value}${ansi.dimOff}`;
}
