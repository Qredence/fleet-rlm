/** Bottom status line: session, run, model, tokens, phase, elapsed. */

import { Box, Text } from "ink";
import type { FC } from "react";

import { formatDuration, formatTokens, shortId } from "./format.js";
import type { Run, Session, Phase } from "./store.js";
import { ansi, statusGlyph, theme } from "./theme.js";

const PHASE_COLORS: Record<Phase, string> = {
  idle: ansi.dim,
  submitting: ansi.white,
  running: ansi.white,
  cancelling: ansi.white,
  completed: ansi.white,
  error: ansi.white,
};

export const StatusBar: FC<{ session: Session | null; run: Run; promptTokens: number; completionTokens: number; width: number }> = ({
  session,
  run,
  promptTokens,
  completionTokens,
  width,
}) => {
  const phaseColor = PHASE_COLORS[run.phase];
  const elapsed = run.startedAt ? formatDuration((run.endedAt ?? Date.now()) - run.startedAt) : "—";
  const sessionLabel = session ? shortId(session.id) : "no-session";
  const runLabel = run.id ? shortId(run.id) : "—";
  const totalTokens = promptTokens + completionTokens;
  const line =
    `${ansi.dim}session ${ansi.reset}${sessionLabel}` +
    `  ${ansi.dim}run ${ansi.reset}${runLabel}` +
    `  ${ansi.dim}model ${ansi.reset}${run.model ?? "—"}` +
    `  ${ansi.dim}tokens ${ansi.reset}${formatTokens(totalTokens)}` +
    `  ${ansi.dim}steps ${ansi.reset}${run.completedSteps}` +
    `  ${ansi.dim}tools ${ansi.reset}${run.toolCount}` +
    `  ${phaseColor}${run.phase === "completed" ? statusGlyph.success : run.phase === "error" ? statusGlyph.error : run.phase === "running" ? statusGlyph.running : statusGlyph.idle} ${run.phase}${ansi.reset}` +
    `  ${ansi.dim}elapsed ${ansi.reset}${elapsed}`;

  return (
    <Box flexDirection="column" marginTop={1}>
      <Text wrap="truncate">{line}</Text>
      {run.error ? <Text color={theme.paper}>{`${statusGlyph.error} ${run.error}`}</Text> : null}
      {width < 80 ? null : <Text dimColor>{"─".repeat(Math.min(width, 120))}</Text>}
    </Box>
  );
};
