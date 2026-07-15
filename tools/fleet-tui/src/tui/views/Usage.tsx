/** Token usage line. */

import { Box, Text } from "ink";
import type { FC } from "react";

import { formatDuration, formatTokens } from "../format.js";
import type { Message } from "../store.js";
import { theme } from "../theme.js";

export const Usage: FC<{ message: Extract<Message, { kind: "usage" }> }> = ({ message }) => (
  <Box marginLeft={2}>
    <Text color={theme.paper} bold>{"USAGE "}</Text>
    <Text color={theme.muted}>
      {` ${message.iterations} iterations · prompt ${formatTokens(message.prompt)} · completion ${formatTokens(message.completion)} · ${formatDuration(message.durationMs)}`}
    </Text>
  </Box>
);
