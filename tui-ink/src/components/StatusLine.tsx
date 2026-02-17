import { Box } from "ink";
import type React from "react";

import type { OverlayView, WorkingPhase } from "../types.js";
import { Text } from "./Text.js";
import { inkColors } from "./colors.js";

interface StatusLineProps {
  overlayView: OverlayView;
  workingPhase: WorkingPhase;
}

function getPhaseLabel(phase: WorkingPhase): { label: string; color: string } {
  if (phase === "idle") return { label: "idle", color: inkColors.success };
  if (phase === "tool") return { label: "running", color: inkColors.warning };
  return { label: "thinking", color: inkColors.warning };
}

/**
 * Minimalistic status line footer showing available shortcuts and phase.
 */
export function StatusLine({ overlayView, workingPhase }: StatusLineProps): React.JSX.Element {
  const base =
    overlayView === "none"
      ? workingPhase === "idle"
        ? "Enter send • / commands • @ mention files • Ctrl+L clear • Ctrl+C exit"
        : "Ctrl+D cancel • Ctrl+L clear • Ctrl+C exit"
      : "↑/↓ navigate • Enter select • Tab complete • Esc back";

  const phaseInfo = getPhaseLabel(workingPhase);

  return (
    <Box marginTop={1} paddingX={1}>
      <Text color={inkColors.dim}>{base}</Text>
      <Text> </Text>
      <Text color={inkColors.dim}>•</Text>
      <Text> </Text>
      <Text color={phaseInfo.color}>{phaseInfo.label}</Text>
    </Box>
  );
}
