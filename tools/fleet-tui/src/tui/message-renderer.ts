import {
  Markdown,
  truncateToWidth,
  visibleWidth,
  wrapTextWithAnsi,
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
import { ansi, markdownTheme } from "./theme.js";

export function renderMessage(message: Message, width: number): string[] {
  const safeWidth = Math.max(1, width);
  switch (message.kind) {
    case "text":
      return renderText(message.role, message.text, message.ts, message.streaming, safeWidth);
    case "error":
      return renderText("error", message.text, message.ts, false, safeWidth);
    case "reasoning":
      return card(
        "REASONING",
        `step ${message.step}`,
        markdown(message.text || "(reasoning in progress…)", safeWidth - 4, {
          ...markdownTheme,
          italic: dimItalic,
        }),
        safeWidth,
      );
    case "tool":
      return renderTool(message, safeWidth);
    case "code":
      return card(
        "CODE",
        `step ${message.step}`,
        codeLines(message.code, safeWidth - 4),
        safeWidth,
      );
    case "output":
      return card(
        "OUTPUT",
        `step ${message.step}`,
        codeLines(message.output, safeWidth - 4),
        safeWidth,
      );
    case "result":
      return renderResult(message, safeWidth);
    case "skill":
      return wrappedLine(
        `  ${bold(`· SKILL ${message.phase.toUpperCase()}`)}  ${bold(message.name)}  ${dim(
          [`v${message.version}`, message.trust].filter(Boolean).join(" · "),
        )}`,
        safeWidth,
      );
    case "attachment":
      return wrappedLine(
        `  ${bold("· FILE")}  ${message.filename}  ${dim(`${formatBytes(message.bytes)} · ${shortId(message.attachmentId)}`)}`,
        safeWidth,
      );
    case "artifact":
      return wrappedLine(
        `  ${bold("✓ ARTIFACT")}  ${bold(message.name)}  ${dim(`${message.artifactKind} · ${formatBytes(message.bytes)} · ${shortId(message.artifactId)}`)}`,
        safeWidth,
      );
    case "usage":
      return wrappedLine(
        `  ${bold("USAGE")}  ${dim(`${message.iterations} iterations · prompt ${formatTokens(message.prompt)} · completion ${formatTokens(message.completion)} · ${formatDuration(message.durationMs)}`)}`,
        safeWidth,
      );
    case "warning":
      return wrapIndented(
        `${bold("! WARNING")}  ${message.code}: ${message.message}`,
        safeWidth,
        2,
      );
  }
}

function renderText(
  role: Role | "error",
  text: string,
  ts: number,
  streaming: boolean,
  width: number,
): string[] {
  const badges: Record<Role | "error", [string, string]> = {
    user: ["│", "YOU"],
    assistant: ["·", "FLEET"],
    system: ["·", "SYSTEM"],
    error: ["×", "ERROR"],
  };
  const [marker, label] = badges[role];
  const stamp = new Date(ts).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  const header = truncateToWidth(
    `${bold(`${marker} ${label}`)} ${dim(stamp)}${streaming ? ` ${ansi.white}█${ansi.reset}` : ""}`,
    width,
    "",
  );
  const body = markdown(text || "(empty)", Math.max(1, width - 2)).map((line) => `  ${line}`);
  return [header, ...body.map((line) => truncateToWidth(line, width, ""))];
}

function renderTool(message: Extract<Message, { kind: "tool" }>, width: number): string[] {
  const statuses = {
    pending: "· pending",
    running: "… running",
    success: "✓ success",
    error: "× error",
  } as const;
  const elapsed = message.endedAt
    ? formatDuration(message.endedAt - message.startedAt)
    : formatDuration(Date.now() - message.startedAt);
  const output =
    message.status === "error"
      ? (message.error ?? "Tool failed")
      : (message.output ?? "(running…)");
  return card(
    `TOOL  ${message.name}`,
    `${statuses[message.status]} · ${elapsed}`,
    [
      dim("input"),
      ...jsonLines(message.input ?? "(no input)", width - 4),
      "",
      dim("output"),
      ...jsonLines(output, width - 4),
    ],
    width,
  );
}

function renderResult(message: Extract<Message, { kind: "result" }>, width: number): string[] {
  const display = formatStructuredResult(message.value);
  const rows: string[] = [];
  if (display.prominent !== null) rows.push(bold(display.prominent));
  const labelWidth = Math.min(
    24,
    Math.max(1, ...display.rows.map(([label]) => visibleWidth(label))),
  );
  for (const [label, value] of display.rows) {
    const prefix = `${dim(label.padEnd(labelWidth))}  `;
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
  );
}

function card(label: string, detail: string, body: string[], width: number): string[] {
  const header = `${bold(label)}${detail ? `  ${dim(detail)}` : ""}`;
  return [
    truncateToWidth(`${ansi.gray}│${ansi.reset} ${header}`, width, ""),
    ...body.map((line) => truncateToWidth(`${ansi.gray}│${ansi.reset}   ${line}`, width, "")),
  ].map((line) => truncateToWidth(line, Math.max(1, width), ""));
}

function markdown(text: string, width: number, theme: MarkdownTheme = markdownTheme): string[] {
  const renderer = new Markdown(
    text,
    0,
    0,
    theme,
    { color: (value) => value },
    {
      preserveOrderedListMarkers: true,
      preserveBackslashEscapes: true,
    },
  );
  return renderer.render(Math.max(1, width));
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

function wrapIndented(value: string, width: number, indent: number): string[] {
  const prefix = " ".repeat(indent);
  return wrapTextWithAnsi(value, Math.max(1, width - indent)).map((line) => `${prefix}${line}`);
}

function wrappedLine(value: string, width: number): string[] {
  return wrapTextWithAnsi(value, Math.max(1, width));
}

function bold(value: string): string {
  return `${ansi.bold}${value}${ansi.boldOff}`;
}

function dim(value: string): string {
  return `${ansi.dim}${value}${ansi.dimOff}`;
}

function dimItalic(value: string): string {
  return `${ansi.dim}${ansi.italic}${value}${ansi.italicOff}${ansi.dimOff}`;
}
