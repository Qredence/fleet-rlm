import {
  Markdown,
  truncateToWidth,
  visibleWidth,
  wrapTextWithAnsi,
  type DefaultTextStyle,
  type MarkdownTheme,
} from "@earendil-works/pi-tui";

import {
  formatBytes,
  formatDuration,
  formatObservedTokens,
  formatStructuredResult,
  redact,
  shortId,
} from "./format.js";
import { formatExecutionMetric } from "./execution-summary.js";
import { keyText } from "./keybinding-hints.js";
import type { Message, Role } from "./store.js";
import { highlightCode } from "./syntax-highlight.js";
import { hasMultipleLines, terminalSafeText } from "./terminal-text.js";
import { markdownTheme, statusGlyph, theme, type ThemeBackground } from "./theme.js";

export class MessageRenderCache {
  private readonly markdownComponents = new Map<
    string,
    { id: string; component: Markdown; text: string }
  >();

  clear(): void {
    this.markdownComponents.clear();
  }

  retain(ids: ReadonlySet<string>): void {
    for (const [key, entry] of this.markdownComponents) {
      if (!ids.has(entry.id)) this.markdownComponents.delete(key);
    }
  }

  renderMarkdown(
    key: string,
    id: string,
    text: string,
    width: number,
    markdownStyle: MarkdownTheme,
    defaultTextStyle: DefaultTextStyle,
  ): string[] {
    const entry = this.markdownComponents.get(key);
    const component = entry?.component;
    if (!component) {
      const next = new Markdown(text, 0, 0, markdownStyle, defaultTextStyle, {
        preserveOrderedListMarkers: true,
        preserveBackslashEscapes: true,
      });
      this.markdownComponents.set(key, { id, component: next, text });
      return next.render(Math.max(1, width));
    }
    if (entry.text !== text) {
      component.setText(text);
      entry.text = text;
    }
    return component.render(Math.max(1, width));
  }
}

export function renderMessage(
  message: Message,
  width: number,
  cache = new MessageRenderCache(),
): string[] {
  const safeWidth = Math.max(1, width);
  switch (message.kind) {
    case "text":
      return renderText(
        message.id,
        message.role,
        message.text,
        message.streaming,
        safeWidth,
        cache,
      );
    case "error":
      return wrapStyled(`Error: ${terminalSafeText(message.text)}`, safeWidth, (text) =>
        theme.fg("error", text),
      );
    case "reasoning":
      return card(
        "REASONING",
        `step ${message.step}`,
        markdown(
          message.text ? terminalSafeText(message.text) : "(reasoning in progress…)",
          safeWidth - 4,
          markdownTheme,
          {
            color: (text) => theme.fg("thinkingText", text),
            italic: true,
          },
          cache,
          `reasoning:${message.id}`,
          message.id,
        ),
        safeWidth,
        "muted",
      );
    case "tool":
      return renderTool(message, safeWidth);
    case "code":
      return renderCode(message, safeWidth);
    case "output":
      return renderOutput(message, safeWidth);
    case "result":
      return renderResult(message, safeWidth, cache);
    case "skill":
      return wrappedLine(
        `  ${theme.fg("accent", theme.bold(`· SKILL ${message.phase.toUpperCase()}`))}  ${theme.bold(terminalSafeText(message.name))}  ${muted(
          [
            `v${message.version}`,
            message.trust ? terminalSafeText(message.trust) : null,
            message.affordances?.length
              ? `can use ${message.affordances.map(terminalSafeText).join(", ")}`
              : null,
          ]
            .filter((value): value is string => value !== null)
            .join(" · "),
        )}`,
        safeWidth,
      );
    case "attachment":
      return wrappedLine(
        `  ${theme.fg("accent", theme.bold("· FILE"))}  ${terminalSafeText(message.filename)}  ${muted(`${formatBytes(message.bytes)} · ${shortId(message.attachmentId)}`)}`,
        safeWidth,
      );
    case "artifact":
      return wrappedLine(
        `  ${theme.fg("success", theme.bold("✓ ARTIFACT"))}  ${theme.bold(terminalSafeText(message.name))}  ${muted(`${terminalSafeText(message.artifactKind)} · ${formatBytes(message.bytes)} · ${shortId(message.artifactId)}`)}`,
        safeWidth,
      );
    case "usage": {
      const summary = message.executionSummary ?? {
        iterations: message.iterations,
        subLmCalls: null,
        hostCapabilityCalls: null,
        interpreterErrors: null,
        durationMs: message.durationMs,
      };
      return wrappedLine(
        `  ${theme.fg("accent", theme.bold("USAGE"))}  ${muted(`${formatExecutionMetric(summary.iterations)} iterations · ${formatExecutionMetric(summary.subLmCalls)} sub-LM · ${formatExecutionMetric(summary.hostCapabilityCalls)} host · ${formatExecutionMetric(summary.interpreterErrors)} errors · ↑ input ${formatObservedTokens(message.inputTokens)} · ↓ output ${formatObservedTokens(message.outputTokens)} · ${summary.durationMs === null ? "—" : formatDuration(summary.durationMs)}`)}`,
        safeWidth,
      );
    }
    case "warning":
      return wrapStyled(
        `! WARNING  ${terminalSafeText(message.code)}: ${terminalSafeText(message.message)}`,
        safeWidth,
        (text) => theme.fg("warning", text),
        2,
      );
  }
}

function renderText(
  id: string,
  role: Role,
  text: string,
  streaming: boolean,
  width: number,
  cache: MessageRenderCache,
): string[] {
  const safeText = terminalSafeText(text);
  if (role === "user") {
    const body = markdown(
      safeText || "(empty)",
      Math.max(1, width - 2),
      markdownTheme,
      { color: (value) => theme.fg("userMessageText", value) },
      cache,
      `text:${id}:user`,
      id,
    ).map((line) => ` ${line}`);
    return surface(["", ...body, ""], width, theme.userMessageBackground());
  }

  if (role === "assistant") {
    const markdownWidth = Math.max(1, width - (streaming ? 3 : 1));
    const body = markdown(
      safeText || "(empty)",
      markdownWidth,
      markdownTheme,
      { color: (value) => theme.fg("text", value) },
      cache,
      `text:${id}:assistant`,
      id,
    ).map((line) => ` ${line}`);
    if (streaming && body.length > 0) {
      const last = body.length - 1;
      body[last] = appendStreamingCursor(body[last] ?? "", width);
    }
    return body.map((line) => truncateToWidth(line, width, ""));
  }

  const body = markdown(
    safeText || "(empty)",
    Math.max(1, width - 4),
    markdownTheme,
    { color: (value) => theme.fg("text", value) },
    cache,
    `text:${id}:system`,
    id,
  );
  return card("SYSTEM", "", body, width, "accent");
}

const TOOL_STATUS = {
  pending: { glyph: statusGlyph.idle, color: "dim", label: "pending" },
  running: { glyph: statusGlyph.running, color: "accent", label: "running" },
  success: { glyph: statusGlyph.success, color: "success", label: "success" },
  error: { glyph: statusGlyph.error, color: "error", label: "error" },
} as const;

/**
 * Renders a tool message with its status, elapsed time, input, and output or error details.
 *
 * @param message - The tool message to render
 * @param width - The available rendering width
 * @returns The rendered tool panel lines
 */
function renderTool(message: Extract<Message, { kind: "tool" }>, width: number): string[] {
  const status = TOOL_STATUS[message.status];
  // Running tools tick at 5s granularity: a mid-transcript card whose line
  // content changes every render drags the differential renderer's
  // firstChanged upward and rewrites everything below it per frame. The
  // activity strip already shows the live elapsed for the current action.
  const elapsed = message.endedAt
    ? formatDuration(message.endedAt - message.startedAt)
    : formatDuration(Math.floor((Date.now() - message.startedAt) / 5000) * 5000);
  const statusText = theme.fg(status.color, `${status.glyph} ${status.label} · ${elapsed}`);
  const header = `${theme.fg("accent", theme.bold("◆"))} ${theme.fg("toolTitle", theme.bold(terminalSafeText(message.name)))}  ${muted(statusText)}`;

  const errorDetails = message.status === "error" ? (message.error ?? "Tool failed") : null;
  // Multi-line tool errors collapse to their summary by default; everything
  // else stays expanded until the operator folds it with ctrl+o.
  const collapsed =
    message.collapsed === true ||
    (message.collapsed === undefined && errorDetails !== null && hasMultipleLines(errorDetails));
  if (collapsed) {
    const summary = errorDetails !== null ? summarizeErrorDetails(errorDetails) : "";
    const suffix = summary ? `  ${theme.fg("error", terminalSafeText(summary))}` : "";
    return panel(
      [` ${header}${suffix}  ${dim(`${keyText("fleet.toggleFold")} to expand`)}`],
      width,
    );
  }

  const body = [
    ` ${header}`,
    "",
    ` ${muted("input")}`,
    ...jsonLines(message.input ?? "(no input)", width - 4),
    "",
  ];
  if (errorDetails !== null) {
    body.push(` ${muted("error")}`);
    body.push(...jsonLines(errorDetails, width - 4).map((line) => theme.fg("error", line)));
  } else {
    body.push(` ${muted("output")}`);
    body.push(
      ...jsonLines(message.output ?? "(running…)", width - 4).map((line) =>
        theme.fg("toolOutput", line),
      ),
    );
  }
  return panel(body, width);
}

function renderCode(message: Extract<Message, { kind: "code" }>, width: number): string[] {
  const detail = `step ${message.step}${message.streaming ? " · streaming" : ""}`;
  if (message.collapsed) {
    const count = message.code.split("\n").length;
    return card(
      "CODE",
      detail,
      [dim(`${count} line${count === 1 ? "" : "s"} · ${keyText("fleet.toggleFold")} to expand`)],
      width,
    );
  }
  const lines = highlightedLines(
    terminalSafeText(message.code),
    width - 4,
    message.language ?? "python",
    message.streaming === true,
  );
  return card("CODE", detail, message.collapsed === false ? lines : capLinesHead(lines), width);
}

function renderOutput(message: Extract<Message, { kind: "output" }>, width: number): string[] {
  const detail = `step ${message.step}${message.streaming ? " · streaming" : ""}`;
  if (message.collapsed) {
    const count = message.output.split("\n").length;
    return card(
      "OUTPUT",
      detail,
      [dim(`${count} line${count === 1 ? "" : "s"} · ${keyText("fleet.toggleFold")} to expand`)],
      width,
    );
  }
  const lines = codeLines(terminalSafeText(message.output), width - 4).map((line) =>
    theme.fg("toolOutput", line),
  );
  return card("OUTPUT", detail, message.collapsed === false ? lines : capLinesTail(lines), width);
}

function renderResult(
  message: Extract<Message, { kind: "result" }>,
  width: number,
  cache: MessageRenderCache,
): string[] {
  const display = formatStructuredResult(message.value);
  const rows: string[] = [];
  if (display.prominent !== null) rows.push(theme.bold(terminalSafeText(display.prominent)));
  const safeRows = display.rows.map(
    ([label, value]) => [terminalSafeText(label), terminalSafeText(value)] as const,
  );
  const labelWidth = Math.min(24, Math.max(1, ...safeRows.map(([label]) => visibleWidth(label))));
  for (const [label, value] of safeRows) {
    const prefix = `${muted(label.padEnd(labelWidth))}  `;
    const available = Math.max(1, width - 4 - labelWidth - 2);
    const wrapped = wrapTextWithAnsi(value, available);
    rows.push(`${prefix}${wrapped[0] ?? ""}`);
    rows.push(...wrapped.slice(1).map((line) => `${" ".repeat(labelWidth + 2)}${line}`));
  }
  if (message.narrative) {
    rows.push(
      "",
      ...markdown(
        terminalSafeText(message.narrative),
        width - 4,
        markdownTheme,
        { color: (value) => theme.fg("text", value) },
        cache,
        `result:${message.id}:narrative`,
        message.id,
      ),
    );
  }
  return card(
    "RESULT",
    [terminalSafeText(message.schemaId), terminalSafeText(message.schemaVersion)]
      .filter(Boolean)
      .join(" · "),
    rows,
    width,
    "success",
  );
}

function card(
  label: string,
  detail: string,
  body: string[],
  width: number,
  color: "border" | "muted" | "accent" | "success" = "border",
): string[] {
  const borderColor = color === "muted" ? "borderMuted" : color;
  const header = `${theme.fg(color, theme.bold(label))}${detail ? `  ${muted(detail)}` : ""}`;
  return [
    truncateToWidth(`${theme.fg(borderColor, "│")} ${header}`, width, ""),
    ...body.map((line) => truncateToWidth(`${theme.fg(borderColor, "│")}   ${line}`, width, "")),
  ];
}

function markdown(
  text: string,
  width: number,
  markdownStyle: MarkdownTheme = markdownTheme,
  defaultTextStyle: DefaultTextStyle = { color: (value) => theme.fg("text", value) },
  cache?: MessageRenderCache,
  key?: string,
  id?: string,
): string[] {
  if (cache && key && id) {
    return cache.renderMarkdown(key, id, text, width, markdownStyle, defaultTextStyle);
  }
  return new Markdown(text, 0, 0, markdownStyle, defaultTextStyle, {
    preserveOrderedListMarkers: true,
    preserveBackslashEscapes: true,
  }).render(Math.max(1, width));
}

function highlightedLines(
  value: string,
  width: number,
  language: string | undefined,
  streaming: boolean,
): string[] {
  const lines = streaming
    ? value.split("\n").map((line) => theme.fg("mdCodeBlock", line))
    : highlightCode(value, language, theme);
  return lines.flatMap((line) => wrapTextWithAnsi(line || " ", Math.max(1, width)));
}

function codeLines(value: string, width: number): string[] {
  return value.split("\n").flatMap((line) => wrapTextWithAnsi(line || " ", Math.max(1, width)));
}

/** Bounded tool-payload preview: large JSON never floods scrollback or re-wrap cost. */
const TOOL_PAYLOAD_PREVIEW_CHARS = 4_000;

function jsonLines(value: unknown, width: number): string[] {
  let serialized: string;
  try {
    serialized = JSON.stringify(redact(value), null, 2);
  } catch {
    serialized = String(value);
  }
  if (serialized.length > TOOL_PAYLOAD_PREVIEW_CHARS) {
    serialized = `${serialized.slice(0, TOOL_PAYLOAD_PREVIEW_CHARS)}\n… ${formatBytes(serialized.length - TOOL_PAYLOAD_PREVIEW_CHARS)} more`;
  }
  return codeLines(terminalSafeText(serialized), width);
}

function surface(
  lines: string[],
  width: number,
  background: ThemeBackground | ((text: string) => string),
): string[] {
  const bg =
    typeof background === "function" ? background : (text: string) => theme.bg(background, text);
  return lines.map((line) => {
    const clipped = truncateToWidth(line, width, "");
    const padded = `${clipped}${" ".repeat(Math.max(0, width - visibleWidth(clipped)))}`;
    return bg(padded);
  });
}

/** One full-width tool panel block: every line on the panel background. */
function panel(lines: string[], width: number): string[] {
  const safeWidth = Math.max(1, width);
  const bg = theme.surface("toolPanelBg");
  return lines.map((line) => {
    const clipped = truncateToWidth(line, safeWidth - 2, "");
    const padded = `${clipped}${" ".repeat(Math.max(0, safeWidth - 2 - visibleWidth(clipped)))}`;
    return bg(` ${padded} `);
  });
}

/** Keep the first N wrapped lines and mark the skipped tail. */
function capLinesHead(lines: readonly string[]): string[] {
  const cap = 200;
  if (lines.length <= cap) return [...lines];
  const skipped = lines.length - cap;
  return [
    ...lines.slice(0, cap),
    theme.fg(
      "dim",
      `… ${skipped} more line${skipped === 1 ? "" : "s"} · ${keyText("fleet.toggleFold")} to expand`,
    ),
  ];
}

/** Keep the last N wrapped lines and mark the skipped head. */
function capLinesTail(lines: readonly string[]): string[] {
  const cap = 200;
  if (lines.length <= cap) return [...lines];
  const skipped = lines.length - cap;
  return [
    theme.fg(
      "dim",
      `… ${skipped} more line${skipped === 1 ? "" : "s"} above · ${keyText("fleet.toggleFold")} to expand`,
    ),
    ...lines.slice(-cap),
  ];
}

function errorDetailLines(text: string): Array<{ raw: string; trimmed: string }> {
  return text
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n")
    .trimEnd()
    .split("\n")
    .map((raw) => ({ raw, trimmed: raw.trim() }))
    .filter((line) => line.trimmed.length > 0);
}

function startsStackContext(line: { trimmed: string }): boolean {
  return (
    line.trimmed.startsWith("Traceback ") ||
    (line.trimmed.startsWith("File ") && line.trimmed.includes(", line ")) ||
    (line.trimmed.startsWith("Cell In[") && line.trimmed.includes(", line ")) ||
    line.trimmed.startsWith("---->")
  );
}

function isStackContextLine(line: { raw: string; trimmed: string }): boolean {
  if (startsStackContext(line)) return true;
  return line.raw.startsWith(" ") || line.raw.startsWith("\t");
}

/**
 * Extracts a concise summary from an error message or stack trace.
 *
 * @returns The last meaningful line of a stack trace, the first meaningful line of other text, or `Error` when no usable detail exists.
 */
export function summarizeErrorDetails(text: string): string {
  const lines = errorDetailLines(text);
  if (lines.length === 0) return "Error";
  if (lines.length > 1 && startsStackContext(lines[0] ?? { trimmed: "" })) {
    for (let index = lines.length - 1; index >= 0; index -= 1) {
      const line = lines[index];
      if (line && !isStackContextLine(line)) return line.trimmed;
    }
    return "Error";
  }
  return lines[0]?.trimmed ?? "Error";
}

/**
 * Wraps text to the available width and applies a style to each resulting line.
 *
 * @param value - The text to wrap and style
 * @param width - The maximum total line width
 * @param style - The function used to style each wrapped line
 * @param indent - The number of spaces to prepend to each line
 * @returns The indented, styled lines
 */
function wrapStyled(
  value: string,
  width: number,
  style: (text: string) => string,
  indent = 0,
): string[] {
  const prefix = " ".repeat(indent);
  return wrapTextWithAnsi(value, Math.max(1, width - indent)).map(
    (line) => `${prefix}${style(line)}`,
  );
}

function wrappedLine(value: string, width: number): string[] {
  return wrapTextWithAnsi(value, Math.max(1, width));
}

function muted(value: string): string {
  return theme.fg("muted", value);
}

function dim(value: string): string {
  return theme.fg("dim", value);
}

/**
 * Adds a streaming cursor to a line while keeping the result within the specified width.
 *
 * @param line - The text to display before the cursor
 * @param width - The maximum rendered width
 * @returns The truncated line with a streaming cursor, or only the cursor when the width is one
 */
function appendStreamingCursor(line: string, width: number): string {
  const cursor = theme.fg("accent", "█");
  if (width <= 1) return truncateToWidth(cursor, width, "");
  return `${truncateToWidth(line, width - 2, "")} ${cursor}`;
}
