import {
  Box,
  isFocusable,
  Loader,
  ScrollView,
  Text,
  truncateToWidth,
  VStack,
  visibleWidth,
  type Component,
  type Editor,
  type Terminal,
  type TUI,
} from "@earendil-works/pi-tui";

import { formatBytes, formatDuration, formatObservedTokens, shortTraceId } from "./format.js";
import { keyHint } from "./keybinding-hints.js";
import { summarizeExecution, type ExecutionSummary } from "./execution-summary.js";
import { isCompact, railPadding } from "./layout.js";
import { MARKS } from "./marks.js";
import { terminalSafeLine } from "./terminal-text.js";
import {
  isBusy,
  type ConversationStore,
  type Message,
  type Phase,
  type Run,
  type State,
} from "./store.js";
import { statusGlyph, theme } from "./theme.js";
import { TranscriptComponent } from "./transcript.js";
import { committedTokenCounts, type ObservedTokenCounts } from "./usage-summary.js";
import { WORKING_ICON_FRAMES } from "./working-icon.js";

/**
 * Screen layout for the alternate-screen viewport TUI:
 *
 *   ─ header bar (session title · status · model) ─
 *   ─ transcript ScrollView (follows end; PgUp/PgDn scrolls; Ctrl+Home/End jumps) ─
 *   ─ activity strip (bordered loader + pulse) ─
 *   ─ pending next-Turn context (when selected) ─
 *   ─ editor dock (labeled prompt + input) ─
 *   ─ footer ─
 *
 * The transcript viewport grows into every free row; the header, activity strip,
 * context rail, editor, and footer are pinned with shrink 0 so they never compress.
 * Home/End move the editor cursor (1.0.3 default); Ctrl+Home/End navigate the transcript.
 */
export class FleetScreen extends VStack {
  readonly transcriptView: ScrollView;
  private readonly header: HeaderComponent;
  private readonly dock: OperatorDockComponent;

  constructor(store: ConversationStore, editor: Editor, terminal: Terminal, ui: TUI) {
    super();
    this.header = new HeaderComponent(store);
    this.addChild(this.header, { shrink: 0 });
    this.transcriptView = new ScrollView(new TranscriptComponent(store), {
      follow: "end",
      primary: true,
      scrollbar: "auto",
      scrollbarTrackStyle: (text) => theme.surface("toolPanelBg")(text),
      scrollbarThumbStyle: (text) => theme.fg("accent", theme.surface("toolPanelBg")(text)),
    });
    this.addChild(this.transcriptView, { grow: 1, shrink: 1, minSize: 1 });
    this.dock = new OperatorDockComponent(store, editor, terminal, ui);
    this.addChild(this.dock, { shrink: 0 });
  }

  invalidate(): void {
    super.invalidate();
    this.header.invalidate();
    this.transcriptView.invalidate();
  }

  dispose(): void {
    this.dock.dispose();
  }
}

/** Two-line header: session identity, then a full-width rule into the transcript. */
class HeaderComponent implements Component {
  private readonly content = new Text("", 0, 0);

  constructor(private readonly store: ConversationStore) {}

  invalidate(): void {
    this.content.invalidate();
  }

  render(width: number): string[] {
    const state = this.store.getState();
    const session = state.session;
    const safeWidth = Math.max(1, width);
    const rule = theme.fg("borderMuted", "─".repeat(safeWidth));
    if (!session) return [rule];

    const title = theme.style(terminalSafeLine(session.title) || "(untitled)", {
      color: "accent",
      bold: true,
    });
    const status = sessionStatusBadge(session.status);
    const model = state.model ? theme.fg("muted", terminalSafeLine(state.model)) : "";
    const sep = theme.fg("borderMuted", ` ${MARKS.trajectory} `);
    const parts = [title, status, model].filter(Boolean).join(sep);
    this.content.setText(truncateToWidth(`  ${parts}`, safeWidth, ""));
    return [...this.content.render(safeWidth), rule];
  }
}

function sessionStatusBadge(status: string): string {
  if (status === "archived")
    return theme.fg("warning", `${statusGlyph.warning} ${terminalSafeLine(status)}`);
  if (status === "active") return theme.fg("success", `${statusGlyph.success} active`);
  return theme.fg("muted", terminalSafeLine(status));
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
  private readonly cue = new Text("", 0, 0);

  constructor(private readonly editor: Component) {
    super(0, 0, (text) => theme.surface("userMessageBg")(text));
    this.addChild(this.cue);
    this.addChild(editor);
  }

  invalidate(): void {
    this.cue.invalidate();
    this.editor.invalidate();
    super.invalidate();
  }

  render(width: number): string[] {
    const safeWidth = Math.max(1, width);
    const focused = isFocusable(this.editor) && this.editor.focused;
    const mark = theme.style(MARKS.submit, { color: focused ? "accent" : "dim", bold: focused });
    const label = focused
      ? theme.fg("text", theme.bold("Ask Fleet"))
      : theme.fg("muted", "Ask Fleet");
    this.cue.setText(truncateToWidth(`${mark} ${label}`, safeWidth, "…"));
    return super.render(safeWidth);
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

    const paddingX = railPadding(width);
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

/** Phase-specific spinner frame sets. */
type SpinnerPhase = "preparation" | "running" | "cancelling";
const SPINNER_FRAMES: Record<SpinnerPhase, readonly string[]> = {
  preparation: ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"],
  running: WORKING_ICON_FRAMES,
  cancelling: ["×"],
};

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
    const frames = phaseSpinnerFrames(run);
    const pulse = frames[(this.frame >> 2) % frames.length] ?? "";
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

function phaseSpinnerFrames(run: Run): readonly string[] {
  if (run.phase === "cancelling") return SPINNER_FRAMES.cancelling;
  const phase = run.statusPhase?.toLowerCase();
  if (phase === "preparation" || run.phase === "submitting") return SPINNER_FRAMES.preparation;
  return SPINNER_FRAMES.running;
}

/** Contextual keybinding hints for the right zone of the footer. */
export function footerHints(phase: Phase): string {
  switch (phase) {
    case "submitting":
      return dim("preparing…  ·  Esc cancel");
    case "running":
      return dim(`${keyHint("fleet.interrupt", "cancel")}  ·  Ctrl+O fold`);
    case "cancelling":
      return dim("cancelling…");
    case "error":
      return dim("Enter retry · /redo · /help");
    case "completed":
      return dim("Enter new Turn · /help");
    case "idle":
      return dim("Enter send · / commands");
  }
}

/**
 * Builds the footer metrics cell string. Zero-valued optional counts (sub-LM,
 * host, interpreter errors) are omitted so the common case stays short enough
 * to fit beside the hints on an 80-column terminal.
 */
export function footerMetrics(execution: ExecutionSummary): string {
  const cells: string[] = [];
  if (execution.iterations !== null) cells.push(`${execution.iterations} iter`);
  if (execution.subLmCalls !== null && execution.subLmCalls > 0)
    cells.push(`${execution.subLmCalls} sub-LM`);
  if (execution.hostCapabilityCalls !== null && execution.hostCapabilityCalls > 0)
    cells.push(`${execution.hostCapabilityCalls} host`);
  if (execution.interpreterErrors !== null && execution.interpreterErrors > 0)
    cells.push(`${execution.interpreterErrors} errors`);
  if (execution.durationMs !== null) cells.push(formatDuration(execution.durationMs));
  return cells.length > 0 ? `  ·  ${cells.join(" · ")}` : "";
}

/**
 * Lays out the two-zone footer. The hint zone is sized to its actual text (not
 * a fixed reservation), and both zones truncate with an ellipsis so a narrow
 * terminal never renders a misleading partial token (e.g. cutting "0 sub-LM"
 * down to "0 s").
 */
export function formatFooterZones(leftZone: string, hints: string, width: number): string[] {
  const safeWidth = Math.max(1, width);
  const hintsWidth = Math.min(visibleWidth(hints), Math.max(1, Math.floor(safeWidth / 2)));
  const leftWidth = Math.max(1, safeWidth - hintsWidth - 2);
  const left = truncateToWidth(leftZone, leftWidth, "…");
  const right = truncateToWidth(hints, hintsWidth, "…");
  // Never emit a line wider than the viewport: at degenerate widths drop the
  // hint zone rather than forcing a one-cell gap that overflows.
  if (visibleWidth(left) + visibleWidth(right) >= safeWidth) {
    return [truncateToWidth(left, safeWidth, "…")];
  }
  const gap = safeWidth - visibleWidth(left) - visibleWidth(right);
  return [`${left}${" ".repeat(gap)}${right}`];
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
    const compact = isCompact(this.terminal.rows, width);
    const run = state.run;

    const metrics = footerMetrics(execution);
    const outcomeStyle = run.outcome ? OUTCOME_STYLE[run.outcome] : null;
    const outcome = outcomeStyle
      ? `  ·  ${theme.fg(outcomeStyle.color, `${outcomeStyle.glyph} ${run.outcome}`)}`
      : "";
    const replay = run.delivery === "replay" ? "  ·  replay" : "";

    // Left zone: token counts + execution metrics + outcome.
    const leftZone = `${theme.fg("dim", theme.bold("TOKENS"))}  ${theme.fg("muted", `↑ ${formatObservedTokens(usage.input)}  ↓ ${formatObservedTokens(usage.output)}`)}${dim(metrics)}${outcome}${dim(replay)}`;

    if (compact) {
      return [truncateToWidth(leftZone, width, "…")];
    }

    // Two-zone footer: left=metrics, right=contextual hints.
    return formatFooterZones(leftZone, footerHints(run.phase), width);
  }
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

function terminalSafeStatus(value: string): string {
  return terminalSafeLine(value).replaceAll(/[_-]+/g, " ");
}

/** Outcome → semantic color and status glyph, resolved once per render. */
const OUTCOME_STYLE = {
  completed: { color: "success", glyph: statusGlyph.success },
  cancelled: { color: "warning", glyph: statusGlyph.warning },
  failed: { color: "error", glyph: statusGlyph.error },
  interrupted: { color: "error", glyph: statusGlyph.error },
} as const satisfies Record<
  NonNullable<Run["outcome"]>,
  { color: "success" | "warning" | "error"; glyph: string }
>;
