/** Bordered tool call panel. */

import { Box, Text } from "ink";
import type { FC } from "react";

import { formatDuration, previewJson } from "../format.js";
import type { Message } from "../store.js";
import { statusGlyph, theme } from "../theme.js";
import { OperatorCard } from "./OperatorCard.js";

const STATUS_LABEL = {
  pending: `${statusGlyph.idle} pending`,
  running: `${statusGlyph.running} running`,
  success: `${statusGlyph.success} success`,
  error: `${statusGlyph.error} error`,
} as const;

export const ToolCall: FC<{
  message: Extract<Message, { kind: "tool" }>;
  expanded: boolean;
  focused?: boolean;
}> = ({ message, expanded, focused = false }) => {
  const elapsed = message.endedAt
    ? formatDuration(message.endedAt - message.startedAt)
    : formatDuration(Date.now() - message.startedAt);
  const inputPreview = message.input !== undefined ? previewJson(message.input, 120) : "";
  const outputPreview =
    message.status === "error"
      ? message.error ?? "Tool failed"
      : message.output !== undefined
        ? previewJson(message.output, 200)
        : "(running…)";

  return (
    <OperatorCard
      label={`TOOL  ${message.name}`}
      detail={`${STATUS_LABEL[message.status]} · ${elapsed}`}
      expanded={expanded}
      focused={focused}
    >
      <Box flexDirection="column">
          <Text>
            <Text color={theme.muted}>input</Text>
            {"\n"}
            {inputPreview || "(no input)"}
          </Text>
          <Text>
            {"\n"}
            <Text color={theme.muted}>output</Text>
            {"\n"}
            {outputPreview}
          </Text>
      </Box>
    </OperatorCard>
  );
};
