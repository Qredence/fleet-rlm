import { Box } from "ink";
import type React from "react";

import type { EventFeedMode } from "../event-feed.js";
import type { TranscriptLine } from "../types.js";
import { Text } from "./Text.js";
import { inkColors } from "./colors.js";

interface EventFeedPanelProps {
  mode: EventFeedMode;
  lines: TranscriptLine[];
}

/**
 * Displays events inline in a minimalistic format.
 * Uses dot indicators and dim text like Letta Code's reasoning messages.
 * Events appear as they happen, no box border.
 */
export function EventFeedPanel({ mode: _mode, lines }: EventFeedPanelProps): React.JSX.Element {
  if (lines.length === 0) {
    return <Box />;
  }

  return (
    <Box flexDirection="column" marginY={0}>
      {lines.map((line) => {
        // Extract event type from the line text for special formatting
        const eventMatch = line.text.match(/^\[(\w+)\]/);
        const eventType = eventMatch ? eventMatch[1] : "event";
        const content = eventMatch ? line.text.slice(eventMatch[0].length).trim() : line.text;

        // Use dot indicator style based on event type
        let indicator = "●";
        let color: string = inkColors.dim;

        if (eventType === "tool_call") {
          indicator = "→";
          color = inkColors.accentBright;
        } else if (eventType === "tool_result") {
          indicator = "⎿";
          color = inkColors.dim;
        } else if (eventType === "reasoning_step") {
          indicator = "◆";
          color = inkColors.dim;
        } else if (eventType === "trajectory_step") {
          indicator = "◇";
          color = inkColors.dim;
        } else if (eventType === "final") {
          indicator = "✓";
          color = inkColors.success;
        }

        return (
          <Box key={line.id} flexDirection="row" gap={1}>
            <Text color={color}>{indicator}</Text>
            <Text color={color} dimColor>
              {content}
            </Text>
          </Box>
        );
      })}
    </Box>
  );
}
