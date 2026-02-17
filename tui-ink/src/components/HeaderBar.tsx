import { Box } from "ink";
import Spinner from "ink-spinner";
import type React from "react";

import type { WorkingPhase } from "../types.js";
import { Text } from "./Text.js";
import { inkColors } from "./colors.js";

interface HeaderBarProps {
  status: string;
  sessionId: string;
  workingPhase: WorkingPhase;
  userName?: string;
}

function getPhaseIndicator(phase: WorkingPhase): string {
  if (phase === "idle") return "●";
  if (phase === "tool") return "●";
  return "●";
}

function getPhaseLabel(phase: WorkingPhase): string {
  if (phase === "idle") return "ready";
  if (phase === "tool") return "running";
  return "thinking";
}

/**
 * Minimalistic header bar inspired by Letta Code.
 * No emojis, clean indicators with proper spacing.
 */
export function HeaderBar({
  status,
  sessionId,
  workingPhase,
  userName,
}: HeaderBarProps): React.JSX.Element {
  return (
    <Box borderStyle="round" borderColor={inkColors.dim} paddingX={2} paddingY={0}>
      <Text color={inkColors.accentBright} bold>
        fleet
      </Text>
      <Text> </Text>
      <Text color={inkColors.dim}>|</Text>
      <Text> </Text>
      {workingPhase === "idle" ? (
        <>
          <Text color={inkColors.success}>{getPhaseIndicator(workingPhase)}</Text>
          <Text> </Text>
          <Text color={inkColors.success}>{status || getPhaseLabel(workingPhase)}</Text>
        </>
      ) : (
        <Box>
          <Text color={inkColors.warning}>
            <Spinner type="dots" />
          </Text>
          <Text> </Text>
          <Text color={inkColors.warning}>{getPhaseLabel(workingPhase)}</Text>
        </Box>
      )}
      {userName ? (
        <>
          <Text> </Text>
          <Text color={inkColors.dim}>|</Text>
          <Text> </Text>
          <Text color={inkColors.dim}>{userName}</Text>
        </>
      ) : null}
      {sessionId ? (
        <>
          <Text> </Text>
          <Text color={inkColors.dim}>|</Text>
          <Text> </Text>
          <Text color={inkColors.dim}>session:{sessionId.slice(0, 8)}</Text>
        </>
      ) : null}
    </Box>
  );
}
