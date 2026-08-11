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
  formatStructuredResult,
  formatTokens,
  redact,
  shortId,
} from "./format.js";
import { formatExecutionMetric } from "./execution-summary.js";
import type { Message, Role } from "./store.js";
import { highlightCode } from "./syntax-highlight.js";
import { terminalSafeText } from "./terminal-text.js";
import { markdownTheme, theme, type ThemeBackground } from "./theme.js";

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
      return card(
        "CODE",
        `step ${message.step}${message.streaming ? " · streaming" : ""}`,
        highlightedLines(
          terminalSafeText(message.code),
          safeWidth - 4,
          message.language ?? "python",
          message.streaming === true,
        ),
        safeWidth,
      );
    case "output":
      return card(
        "OUTPUT",
        `step ${message.step}${message.streaming ? " · streaming" : ""}`,
        codeLines(terminalSafeText(message.output), safeWidth - 4).map((line) =>
          theme.fg("toolOutput", line),
        ),
        safeWidth,
      );
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
    return surface(["", ...body, ""], width, "userMessageBg");
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

function renderTool(message: Extract<Message, { kind: "tool" }>, width: number): string[] {
  const statuses = {
    pending: ["· pending", "toolPendingBg"],
    running: ["… running", "toolPendingBg"],
    success: ["✓ success", "toolSuccessBg"],
    error: ["× error", "toolErrorBg"],
  } as const satisfies Record<typeof message.status, readonly [string, ThemeBackground]>;
  // Running tools tick at 5s granularity: a mid-transcript card whose line
  // content changes every render drags the differential renderer's
  // firstChanged upward and rewrites everything below it per frame. The
  // activity strip already shows the live elapsed for the current action.
  const elapsed = message.endedAt
    ? formatDuration(message.endedAt - message.startedAt)
    : formatDuration(Math.floor((Date.now() - message.startedAt) / 5000) * 5000);
  const output =
    message.status === "error"
      ? terminalSafeText(message.error ?? "Tool failed")
      : (message.output ?? "(running…)");
  const [status, background] = statuses[message.status];
  const body = [
    `${theme.fg("toolTitle", theme.bold(terminalSafeText(message.name)))}  ${muted(`${status} · ${elapsed}`)}`,
    "",
    muted("input"),
    ...jsonLines(message.input ?? "(no input)", width - 4),
    "",
    muted("output"),
    ...jsonLines(output, width - 4).map((line) =>
      message.status === "error" ? theme.fg("error", line) : theme.fg("toolOutput", line),
    ),
  ].map((line) => ` ${line}`);
  return surface(["", ...body, ""], width, background);
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

function surface(lines: string[], width: number, background: ThemeBackground): string[] {
  return lines.map((line) => {
    const clipped = truncateToWidth(line, width, "");
    const padded = `${clipped}${" ".repeat(Math.max(0, width - visibleWidth(clipped)))}`;
    return theme.bg(background, padded);
  });
}

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

function appendStreamingCursor(line: string, width: number): string {
  const cursor = theme.fg("accent", "█");
  if (width <= 1) return truncateToWidth(cursor, width, "");
  return `${truncateToWidth(line, width - 2, "")} ${cursor}`;
}

function formatObservedTokens(value: number | null): string {
  return value === null ? "—" : formatTokens(value);
}
