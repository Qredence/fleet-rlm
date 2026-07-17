/** Busy-only live Run activity rail. */

import cliSpinners from "cli-spinners";
import { Box, Text, useStdout } from "ink";
import { useEffect, useState, type FC } from "react";

import { formatDuration } from "./format.js";
import type { Run } from "./store.js";
import { theme } from "./theme.js";

const spinner = cliSpinners.dots;
const activePhases = new Set<Run["phase"]>(["submitting", "running", "cancelling"]);

export function runActivityLabel(run: Run): string {
  const phase = run.phase === "cancelling" ? run.phase : run.statusPhase?.trim() || run.phase;
  return phase.replaceAll(/[_-]+/g, " ").replaceAll(/\s+/g, " ").toUpperCase();
}

export const RunActivity: FC<{ run: Run; interactive?: boolean }> = ({
  run,
  interactive,
}) => {
  const { stdout } = useStdout();
  const active = activePhases.has(run.phase);
  const animate = interactive ?? stdout.isTTY === true;
  const [frameIndex, setFrameIndex] = useState(0);
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    if (!active || !animate) return;
    const timer = setInterval(() => {
      setFrameIndex((index) => (index + 1) % spinner.frames.length);
      setNow(Date.now());
    }, spinner.interval);
    return () => clearInterval(timer);
  }, [active, animate]);

  if (!active) return null;

  const frame = animate ? spinner.frames[frameIndex] : "…";
  const elapsed = run.startedAt ? formatDuration(now - run.startedAt) : "0:00";
  const detail = run.statusDetail?.trim();

  return (
    <Box
      flexDirection="column"
      borderStyle="single"
      borderLeft
      borderRight={false}
      borderTop={false}
      borderBottom={false}
      borderColor={theme.paper}
      paddingLeft={1}
      marginTop={1}
    >
      <Box>
        <Text color={theme.paper} bold>{`${frame} ${runActivityLabel(run)}`}</Text>
        {detail ? <Text color={theme.muted}>{`  ${detail}`}</Text> : null}
        <Text color={theme.muted}>{` · ${elapsed}`}</Text>
      </Box>
      <Text color={theme.muted}>
        {`${count(run.completedSteps, "step")} · ${count(run.toolCount, "tool")} · Ctrl+C cancel`}
      </Text>
    </Box>
  );
};

function count(value: number, label: string): string {
  return `${value} ${label}${value === 1 ? "" : "s"}`;
}
