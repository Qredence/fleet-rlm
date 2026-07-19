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
import type { Message, Role } from "./store.js";
import { highlightCode } from "./syntax-highlight.js";
import { markdownTheme, theme, type ThemeBackground } from "./theme.js";

export function renderMessage(message: Message, width: number): string[] {
  const safeWidth = Math.max(1, width);
  switch (message.kind) {
    case "text":
      return renderText(message.role, message.text, message.streaming, safeWidth);
    case "error":
      return wrapStyled(`Error: ${message.text}`, safeWidth, (text) => theme.fg("error", text));
    case "reasoning":
      return card(
        "REASONING",
        `step ${message.step}`,
        markdown(message.text || "(reasoning in progress…)", safeWidth - 4, markdownTheme, {
          color: (text) => theme.fg("thinkingText", text),
          italic: true,
        }),
        safeWidth,
        "muted",
      );
    case "tool":
      return renderTool(message, safeWidth);
    case "code":
      return card(
        "CODE",
        `step ${message.step}`,
        highlightedLines(message.code, safeWidth - 4),
        safeWidth,
      );
    case "output":
      return card(
        "OUTPUT",
        `step ${message.step}`,
        codeLines(message.output, safeWidth - 4).map((line) => theme.fg("toolOutput", line)),
        safeWidth,
      );
    case "result":
      return renderResult(message, safeWidth);
    case "skill":
      return wrappedLine(
        `  ${theme.fg("accent", theme.bold(`· SKILL ${message.phase.toUpperCase()}`))}  ${theme.bold(message.name)}  ${muted(
          [`v${message.version}`, message.trust].filter(Boolean).join(" · "),
        )}`,
        safeWidth,
      );
    case "attachment":
      return wrappedLine(
        `  ${theme.fg("accent", theme.bold("· FILE"))}  ${message.filename}  ${muted(`${formatBytes(message.bytes)} · ${shortId(message.attachmentId)}`)}`,
        safeWidth,
      );
    case "artifact":
      return wrappedLine(
        `  ${theme.fg("success", theme.bold("✓ ARTIFACT"))}  ${theme.bold(message.name)}  ${muted(`${message.artifactKind} · ${formatBytes(message.bytes)} · ${shortId(message.artifactId)}`)}`,
        safeWidth,
      );
    case "usage":
      return wrappedLine(
        `  ${theme.fg("accent", theme.bold("USAGE"))}  ${muted(`${message.iterations} iterations · prompt ${formatTokens(message.prompt)} · completion ${formatTokens(message.completion)} · ${formatDuration(message.durationMs)}`)}`,
        safeWidth,
      );
    case "warning":
      return wrapStyled(
        `! WARNING  ${message.code}: ${message.message}`,
        safeWidth,
        (text) => theme.fg("warning", text),
        2,
      );
  }
}

function renderText(role: Role, text: string, streaming: boolean, width: number): string[] {
  if (role === "user") {
    const body = markdown(text || "(empty)", Math.max(1, width - 2), markdownTheme, {
      color: (value) => theme.fg("userMessageText", value),
    }).map((line) => ` ${line}`);
    return surface(["", ...body, ""], width, "userMessageBg");
  }

  if (role === "assistant") {
    const body = markdown(text || "(empty)", Math.max(1, width - 2)).map((line) => ` ${line}`);
    if (streaming && body.length > 0) {
      const last = body.length - 1;
      body[last] = `${body[last]} ${theme.fg("accent", "█")}`;
    }
    return body.map((line) => truncateToWidth(line, width, ""));
  }

  const body = markdown(text || "(empty)", Math.max(1, width - 4));
  return card("SYSTEM", "", body, width, "accent");
}

function renderTool(message: Extract<Message, { kind: "tool" }>, width: number): string[] {
  const statuses = {
    pending: ["· pending", "toolPendingBg"],
    running: ["… running", "toolPendingBg"],
    success: ["✓ success", "toolSuccessBg"],
    error: ["× error", "toolErrorBg"],
  } as const satisfies Record<typeof message.status, readonly [string, ThemeBackground]>;
  const elapsed = message.endedAt
    ? formatDuration(message.endedAt - message.startedAt)
    : formatDuration(Date.now() - message.startedAt);
  const output =
    message.status === "error"
      ? (message.error ?? "Tool failed")
      : (message.output ?? "(running…)");
  const [status, background] = statuses[message.status];
  const body = [
    `${theme.fg("toolTitle", theme.bold(message.name))}  ${muted(`${status} · ${elapsed}`)}`,
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

function renderResult(message: Extract<Message, { kind: "result" }>, width: number): string[] {
  const display = formatStructuredResult(message.value);
  const rows: string[] = [];
  if (display.prominent !== null) rows.push(theme.bold(display.prominent));
  const labelWidth = Math.min(
    24,
    Math.max(1, ...display.rows.map(([label]) => visibleWidth(label))),
  );
  for (const [label, value] of display.rows) {
    const prefix = `${muted(label.padEnd(labelWidth))}  `;
    const available = Math.max(1, width - 4 - labelWidth - 2);
    const wrapped = wrapTextWithAnsi(value, available);
    rows.push(`${prefix}${wrapped[0] ?? ""}`);
    rows.push(...wrapped.slice(1).map((line) => `${" ".repeat(labelWidth + 2)}${line}`));
  }
  if (message.narrative) rows.push("", ...markdown(message.narrative, width - 4));
  return card(
    "RESULT",
    [message.schemaId, message.schemaVersion].filter(Boolean).join(" · "),
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
): string[] {
  return new Markdown(text, 0, 0, markdownStyle, defaultTextStyle, {
    preserveOrderedListMarkers: true,
    preserveBackslashEscapes: true,
  }).render(Math.max(1, width));
}

function highlightedLines(value: string, width: number): string[] {
  return highlightCode(value, undefined, theme).flatMap((line) =>
    wrapTextWithAnsi(line || " ", Math.max(1, width)),
  );
}

function codeLines(value: string, width: number): string[] {
  return value.split("\n").flatMap((line) => wrapTextWithAnsi(line || " ", Math.max(1, width)));
}

function jsonLines(value: unknown, width: number): string[] {
  let serialized: string;
  try {
    serialized = JSON.stringify(redact(value), null, 2);
  } catch {
    serialized = String(value);
  }
  return codeLines(serialized, width);
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
