/** Chat message bubble with role badge, timestamp, and streaming cursor. */

import { Box, Text } from "ink";
import type { FC } from "react";

import { renderMarkdown } from "../markdown.js";
import type { Message, Role } from "../store.js";
import { ansi, theme } from "../theme.js";

type DisplayRole = Role | "error";

function roleBadge(role: DisplayRole): { label: string; marker: string } {
  switch (role) {
    case "user":
      return { label: "YOU", marker: "│" };
    case "assistant":
      return { label: "FLEET", marker: "·" };
    case "system":
      return { label: "SYSTEM", marker: "·" };
    case "error":
      return { label: "ERROR", marker: "×" };
  }
}

function time(ts: number): string {
  const date = new Date(ts);
  return date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

export const ChatMessage: FC<{ message: Extract<Message, { kind: "text" }> | Extract<Message, { kind: "error" }>; width: number }> = ({
  message,
  width,
}) => {
  const role: DisplayRole = message.kind === "error" ? "error" : message.role;
  const badge = roleBadge(role);
  const ts = time(message.ts);
  const body =
    message.kind === "text" && message.text
      ? renderMarkdown(message.text, Math.max(20, width - 4))
      : message.kind === "error"
        ? message.text
        : "";

  return (
    <Box flexDirection="column">
      <Box>
        <Text>
          {ansi.white}{badge.marker} {ansi.bold}{badge.label}{ansi.reset}
          {ansi.dim} {ts} {ansi.reset}
        </Text>
        {message.kind === "text" && message.streaming ? (
          <Text color={theme.paper}>{"\u2588"}</Text>
        ) : null}
      </Box>
      <Box marginLeft={2} flexDirection="column">
        <Text>{body || `${ansi.dim}(empty)${ansi.reset}`}</Text>
      </Box>
    </Box>
  );
};
