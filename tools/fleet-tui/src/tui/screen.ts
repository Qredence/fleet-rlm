import {
  Box,
  Loader,
  ScrollView,
  Text,
  truncateToWidth,
  VStack,
  type Component,
  type Editor,
  type Terminal,
  type TUI,
} from "@earendil-works/pi-tui";

import { formatBytes, formatDuration, formatObservedTokens, shortTraceId } from "./format.js";
import { keyHint } from "./keybinding-hints.js";
import { summarizeExecution, type ExecutionSummary } from "./execution-summary.js";
import { terminalSafeLine } from "./terminal-text.js";
import type { ConversationStore, Message, Run, State } from "./store.js";
import { statusGlyph, theme } from "./theme.js";
import { TranscriptComponent } from "./transcript.js";
import { committedTokenCounts, type ObservedTokenCounts } from "./usage-summary.js";
import { WORKING_ICON_FRAMES } from "./working-icon.js";

/**
 * Screen layout for the alternate-screen viewport TUI:
 *
 *   ─ transcript ScrollView (follows end; PgUp/PgDn/Home/End scroll) ─
 *   ─ activity strip (bordered loader + pulse) ─
 *   ─ pending next-Turn context (when selected) ─
 *   ─ editor ─
 *   ─ footer ─
 *
 * The transcript viewport grows into every free row; the activity strip,
 * context rail, editor, and footer are pinned with shrink 0 so they never
 * compress.
 */
export class FleetScreen extends VStack {
  readonly transcriptView: ScrollView;
  private readonly dock: OperatorDockComponent;

  constructor(store: ConversationStore, editor: Editor, terminal: Terminal, ui: TUI) {
    super();
    this.transcriptView = new ScrollView(new TranscriptComponent(store), {
      follow: "end",
      primary: true,
      scrollbar: "auto",
      scrollbarStyle: (text) => theme.surface("toolPanelBg")(text),
    });
    this.addChild(this.transcriptView, { grow: 1, shrink: 1, minSize: 1 });
    this.dock = new OperatorDockComponent(store, editor, terminal, ui);
    this.addChild(this.dock, { shrink: 0 });
  }

  invalidate(): void {
    super.invalidate();
    this.transcriptView.invalidate();
  }

  dispose(): void {
    this.dock.dispose();
  }
}

/**
 * The persistent control plane below the trajectory. The transcript owns all
 * historical evidence; this component owns only live state and the next Turn.
 */
export class OperatorDockComponent implements Component {
  private readonly activity: ActivityComponent;
  private readonly context: NextTurnContextComponent;
  private readonly editorDock: EditorDockComponent;
  private readonly footer: FooterComponent;

  constructor(store: ConversationStore, editor: Editor, terminal: Terminal, ui: TUI) {
    this.activity = new ActivityComponent(store, ui);
    this.context = new NextTurnContextComponent(store);
    this.editorDock = new EditorDockComponent(editor);
    this.footer = new FooterComponent(store, terminal);
  }

  invalidate(): void {
    this.activity.invalidate();
    this.context.invalidate();
    this.editorDock.invalidate();
    this.footer.invalidate();
  }

  render(width: number): string[] {
    // A fixed ordering keeps the active action adjacent to the exact inputs it
    // affects, then leaves the editor as the always-available final control.
    return [
      ...this.activity.render(width),
      ...this.context.render(width),
      ...this.editorDock.render(width),
      ...this.footer.render(width),
    ];
  }

  dispose(): void {
    this.activity.dispose();
  }
}

/** Applies the same adaptive, quiet surface to the editor as user prompts. */
export class EditorDockComponent extends Box {
  constructor(editor: Component) {
    super(0, 0, (text) => theme.surface("userMessageBg")(text));
    this.addChild(editor);
  }
}

/**
 * Keeps persisted Skill and Attachment selections visible next to the editor.
 * These values affect the next accepted Turn, so hiding them in transcript
 * scrollback makes it too easy to submit with stale or forgotten context.
 */
export class NextTurnContextComponent implements Component {
  private readonly content = new Text("", 0, 0, (text) => theme.surface("toolPanelBg")(text));

  constructor(private readonly store: ConversationStore) {}

  invalidate(): void {
    this.content.invalidate();
  }

  render(width: number): string[] {
    const state = this.store.getState();
    const skills = state.pendingSkillSelections;
    const attachments = state.pendingAttachments;
    if (skills.length === 0 && attachments.length === 0) return [];

    const counts: string[] = [];
    if (skills.length > 0) counts.push(`${skills.length} ${plural(skills.length, "Skill")}`);
    if (attachments.length > 0) {
      const totalBytes = attachments.reduce((total, attachment) => total + attachment.bytes, 0);
      counts.push(
        `${attachments.length} ${plural(attachments.length, "Attachment")} · ${formatBytes(totalBytes)}`,
      );
    }

    const details: string[] = [];
    if (skills.length > 0) {
      details.push(
        skills
          .map((selection) =>
            terminalSafeLine(`${selection.displayName}@${selection.expectedVersion}`),
          )
          .join(", "),
      );
    }
    if (attachments.length > 0) {
      details.push(
        attachments.map((attachment) => terminalSafeLine(attachment.filename)).join(", "),
      );
    }

    let paddingX = 0;
    if (width > 4) paddingX = 2;
    else if (width > 2) paddingX = 1;
    const contentWidth = Math.max(1, width - paddingX * 2);
    const label = theme.fg("accent", theme.bold("NEXT TURN"));
    const summary = theme.fg("muted", `  ${counts.join(" · ")}`);
    const detail = theme.fg("dim", `  ${details.join(" · ")}`);
    this.content.setText(
      `${" ".repeat(paddingX)}${truncateToWidth(`${label}${summary}${detail}`, contentWidth, "…")}`,
    );
    return this.content.render(width);
  }
}

class ActivityComponent implements Component {
  private readonly loader: Loader;
  private active = false;
  private message = "";
  private frame = 0;

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
    const pulse = isBusy(run)
      ? WORKING_ICON_FRAMES[(this.frame >> 2) % WORKING_ICON_FRAMES.length]
      : "";
    const message = `${pulse} ${activityAction(state)} ${dim(`· ${elapsed}`)}`;
    if (message !== this.message) {
      this.message = message;
      this.loader.setMessage(message);
    }
    if (!this.active) {
      this.active = true;
      this.loader.start();
    }
    this.frame += 1;

    const secondaryParts = [
      `${run.completedSteps}/${run.startedSteps} steps`,
      run.toolCount > 0 ? `${run.toolCount} ${run.toolCount === 1 ? "tool" : "tools"}` : null,
      run.traceId ? shortTraceId(run.traceId) : null,
      "Esc cancel",
    ]
      .filter((part): part is string => part !== null)
      .join(" · ");
    const border = theme.fg("borderMuted", "─".repeat(Math.max(1, width)));
    return [
      border,
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
  private metricsMessages: readonly Message[] | null = null;
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
              ? `${keyHint("fleet.interrupt", "cancel")} · draft stays unsent`
              : "Enter send · Shift+Enter newline · / commands",
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

    const metrics = metricsParts.length > 0 ? `  ·  ${metricsParts.join(" · ")}` : "";
    const outcome = run.outcome
      ? `  ·  ${theme.fg(outcomeColor(run.outcome), `${statusGlyph[outcomeGlyph(run.outcome)]} ${run.outcome}`)}`
      : "";
    const replay = run.delivery === "replay" ? "  ·  replay" : "";
    lines.push(
      truncateToWidth(
        `${theme.fg("dim", theme.bold("TOKENS"))}  ${theme.fg("muted", `↑ ${formatObservedTokens(usage.input)}  ↓ ${formatObservedTokens(usage.output)}`)}${dim(metrics)}${outcome}${dim(replay)}`,
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

function plural(count: number, singular: string): string {
  return count === 1 ? singular : `${singular}s`;
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

/**
 * Sanitizes status text for terminal display.
 *
 * @param value - The status text to sanitize
 * @returns The sanitized text with terminal-unsafe characters removed and underscores or hyphens replaced with spaces.
 */
function terminalSafeStatus(value: string): string {
  return terminalSafeLine(value).replaceAll(/[_-]+/g, " ");
}

/**
 * Selects the status glyph for a run outcome.
 *
 * @param outcome - The run outcome to represent.
 * @returns `"success"` for completed runs, `"warning"` for cancelled runs, and `"error"` for other outcomes.
 */
function outcomeGlyph(outcome: NonNullable<Run["outcome"]>): keyof typeof statusGlyph {
  if (outcome === "completed") return "success";
  if (outcome === "cancelled") return "warning";
  return "error";
}

function outcomeColor(outcome: NonNullable<Run["outcome"]>): "success" | "warning" | "error" {
  if (outcome === "completed") return "success";
  if (outcome === "cancelled") return "warning";
  return "error";
}
