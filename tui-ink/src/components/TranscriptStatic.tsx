import { Box } from "ink";
import type React from "react";

import type { TranscriptLine } from "../types.js";
import { Text } from "./Text.js";
import { inkColors } from "./colors.js";

interface TranscriptStaticProps {
  lines: TranscriptLine[];
}

function lineStyle(role: TranscriptLine["role"]): {
  color: string;
  indicator: string;
  label: string;
} {
  switch (role) {
    case "user":
      return { color: inkColors.default, indicator: "›", label: "You" };
    case "assistant":
      return { color: inkColors.accentBright, indicator: "›", label: "Assistant" };
    case "tool":
      return { color: inkColors.accentBright, indicator: "→", label: "Tool" };
    case "status":
      return { color: inkColors.dim, indicator: "◆", label: "Status" };
    case "error":
      return { color: inkColors.error, indicator: "✗", label: "Error" };
    default:
      return { color: inkColors.dim, indicator: "●", label: "" };
  }
}

/**
 * Displays completed transcript lines with minimalistic indicators.
 * Inspired by Letta Code CommandMessage layout.
 */
export function TranscriptStatic({ lines }: TranscriptStaticProps): React.JSX.Element {
  return (
    <Box flexDirection="column" paddingX={1}>
      {lines.map((line, index) => {
        const style = lineStyle(line.role);
        const showSeparator = index > 0 && lines[index - 1].role !== line.role;

        return (
          <Box key={line.id} flexDirection="column">
            {showSeparator && (
              <Box marginY={0}>
                <Text color={inkColors.dim}>{"\n"}</Text>
              </Box>
            )}
            <Box flexDirection="row" gap={1}>
              <Text color={style.color}>{style.indicator}</Text>
              <Box flexDirection="column" flexGrow={1}>
                <Text color={style.color} bold>
                  {style.label}
                </Text>
                <Text>{line.text}</Text>
              </Box>
            </Box>
          </Box>
        );
      })}
    </Box>
  );
}
