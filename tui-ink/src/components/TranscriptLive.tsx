import { Box } from "ink";
import Spinner from "ink-spinner";
import type React from "react";

import { Text } from "./Text.js";
import { inkColors } from "./colors.js";

interface TranscriptLiveProps {
  streamingText: string;
}

/**
 * Shows the streaming assistant response in real time.
 * Minimalistic with spinner and proper formatting.
 */
export function TranscriptLive({ streamingText }: TranscriptLiveProps): React.JSX.Element | null {
  if (!streamingText.trim()) {
    return null;
  }
  return (
    <Box paddingX={1} flexDirection="column">
      <Box marginTop={1}>
        <Text color={inkColors.dim}>{"\n"}</Text>
      </Box>
      <Box flexDirection="row" gap={1}>
        <Text color={inkColors.accentBright}>
          <Spinner type="dots" />
        </Text>
        <Box flexDirection="column" flexGrow={1}>
          <Text color={inkColors.accentBright} bold>
            Assistant:
          </Text>
          <Text>{streamingText}</Text>
        </Box>
      </Box>
    </Box>
  );
}
