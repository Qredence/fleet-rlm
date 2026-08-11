import {
  Container,
  Loader,
  truncateToWidth,
  type Component,
  type Editor,
  type Terminal,
  type TUI,
} from "@earendil-works/pi-tui";

import { formatDuration, formatTokens, shortTraceId } from "./format.js";
import { summarizeExecution, type ExecutionSummary } from "./execution-summary.js";
import { terminalSafeLine } from "./terminal-text.js";
import type { ConversationStore, Run, State } from "./store.js";
import { theme } from "./theme.js";
import { TranscriptComponent } from "./transcript.js";
import { committedTokenCounts, type ObservedTokenCounts } from "./usage-summary.js";

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
  private readonly loader: Loader;
  private active = false;
  private message = "";

  constructor(
    private readonly store: ConversationStore,
    ui: TUI,
  ) {
    this.loader = new Loader(
      ui,
      (frame) => theme.fg("accent", frame),
      (text) => theme.fg("accent", text),
      "Preparing Turn",
    );
    // Loader starts from its constructor; Fleet activates it only for a live Run.
    this.loader.stop();
  }

  invalidate(): void {
    this.loader.invalidate();
  }

  render(width: number): string[] {
    const state = this.store.getState();
    const run = state.run;
    if (!isBusy(run)) {
      this.stopLoader();
      return [];
    }

    const elapsed = run.startedAt ? formatDuration(Date.now() - run.startedAt) : "0:00";
    const message = `${activityAction(state)} ${dim(`· ${elapsed}`)}`;
    if (message !== this.message) {
      this.message = message;
      this.loader.setMessage(message);
    }
    if (!this.active) {
      this.active = true;
      this.loader.start();
    }

    const secondaryParts = [
      `${run.completedSteps}/${run.startedSteps} steps`,
      run.toolCount > 0 ? `${run.toolCount} ${run.toolCount === 1 ? "tool" : "tools"}` : null,
      run.traceId ? shortTraceId(run.traceId) : null,
      "Esc cancel",
    ]
      .filter((part): part is string => part !== null)
      .join(" · ");
    return [
      ...this.loader.render(width),
      truncateToWidth(`${theme.fg("borderMuted", "│")} ${dim(secondaryParts)}`, width, ""),
    ];
  }

  dispose(): void {
    this.stopLoader();
  }

  private stopLoader(): void {
    if (!this.active) return;
    this.loader.stop();
    this.active = false;
    this.message = "";
  }
}

class FooterComponent implements Component {
  // Footer metrics scan every message (token sums + execution summary). Memoize
  // them on the messages array reference and run id: the store creates a new
  // array on every message change and leaves it untouched for status/heartbeat
  // dispatches, so keystrokes and loader ticks cost O(1) instead of O(n).
  private metricsMessages: readonly import("./store.js").Message[] | null = null;
  private metricsRunId: string | null = null;
  private metricsUsage: ObservedTokenCounts = { input: null, output: null };
  private metricsExecution: ExecutionSummary = {
    iterations: null,
    subLmCalls: null,
    hostCapabilityCalls: null,
    interpreterErrors: null,
    durationMs: null,
  };

  constructor(
    private readonly store: ConversationStore,
    private readonly terminal: Terminal,
  ) {}
  invalidate(): void {}
  render(width: number): string[] {
    const state = this.store.getState();
    if (state.messages !== this.metricsMessages || state.run.id !== this.metricsRunId) {
      this.metricsMessages = state.messages;
      this.metricsRunId = state.run.id;
      this.metricsUsage = committedTokenCounts(state.messages);
      this.metricsExecution = summarizeExecution(state.messages, state.run.id);
    }
    const usage = this.metricsUsage;
    const execution = this.metricsExecution;
    const compact = this.terminal.rows < 14 || width < 60;
    const lines = compact
      ? []
      : [
          dim(
            isBusy(state.run)
              ? "Esc cancel · draft stays unsent · Ctrl+D exits only when empty"
              : "Enter send · Shift+Enter newline · / commands · Ctrl+D exit when empty",
          ),
        ];
    const run = state.run;

    const metricsParts: string[] = [];
    if (execution.iterations !== null) metricsParts.push(`${execution.iterations} iter`);
    if (execution.subLmCalls !== null) metricsParts.push(`${execution.subLmCalls} sub-LM`);
    if (execution.hostCapabilityCalls !== null)
      metricsParts.push(`${execution.hostCapabilityCalls} host`);
    if (execution.interpreterErrors !== null)
      metricsParts.push(`${execution.interpreterErrors} errors`);
    if (execution.durationMs !== null) metricsParts.push(formatDuration(execution.durationMs));

    const metrics = metricsParts.length > 0 ? ` · ${metricsParts.join(" · ")}` : "";
    const outcome = run.outcome ? ` · ${theme.fg(outcomeColor(run.outcome), run.outcome)}` : "";
    const replay = run.delivery === "replay" ? " · replay" : "";
    lines.push(
      truncateToWidth(
        `↑ ${formatObservedTokens(usage.input)} ↓ ${formatObservedTokens(usage.output)}${metrics}${outcome}${replay}`,
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
  return theme.fg("dim", value);
}

function activityAction(state: State): string {
  const run = state.run;
  if (run.phase === "submitting") return "Preparing Turn";
  if (run.phase === "cancelling") return "Cancelling Run";

  for (let index = state.messages.length - 1; index >= 0; index -= 1) {
    const message = state.messages[index];
    if (message?.kind === "tool" && message.status === "running" && message.runId === run.id) {
      return `Running Tool ${terminalSafeLine(message.name)}`;
    }
  }

  const detail = run.statusDetail?.trim();
  if (detail && detail.toLowerCase() !== "running") return terminalSafeStatus(detail);
  if (run.delivery === "replay") return "Replaying committed Turn";
  if (run.startedSteps > run.completedSteps) return `Executing RLM step ${run.startedSteps}`;

  const phase = run.statusPhase?.trim();
  return phase ? `Running ${terminalSafeStatus(phase)}` : "Running RLM";
}

function terminalSafeStatus(value: string): string {
  return terminalSafeLine(value).replaceAll(/[_-]+/g, " ");
}

function formatObservedTokens(value: number | null): string {
  return value === null ? "—" : formatTokens(value);
}

function outcomeColor(outcome: NonNullable<Run["outcome"]>): "success" | "warning" | "error" {
  if (outcome === "completed") return "success";
  if (outcome === "cancelled") return "warning";
  return "error";
}
