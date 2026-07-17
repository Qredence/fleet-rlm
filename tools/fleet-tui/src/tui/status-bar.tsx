/** Quiet bottom metadata line. Transient Run activity lives above the prompt. */

import { Box, Text } from "ink";
import type { FC } from "react";

import { formatTokens } from "./format.js";
import type { Run } from "./store.js";
import { ansi } from "./theme.js";

export const StatusBar: FC<{ run: Run; promptTokens: number; completionTokens: number; width: number }> = ({
  run,
  promptTokens,
  completionTokens,
  width,
}) => {
  const totalTokens = promptTokens + completionTokens;
  const line =
    `${ansi.dim}model ${ansi.reset}${run.model ?? "—"}` +
    `  ${ansi.dim}tokens ${ansi.reset}${formatTokens(totalTokens)}` +
    `  ${ansi.dim}steps ${ansi.reset}${run.completedSteps}` +
    `  ${ansi.dim}tools ${ansi.reset}${run.toolCount}`;

  return (
    <Box flexDirection="column" marginTop={1}>
      <Text wrap="truncate">{line}</Text>
      {width < 80 ? null : <Text dimColor>{"─".repeat(Math.min(width, 120))}</Text>}
    </Box>
  );
};
