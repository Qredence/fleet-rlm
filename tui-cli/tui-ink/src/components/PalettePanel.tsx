import { Box } from "ink";
import { ScrollList } from "ink-scroll-list";
import type React from "react";

import type { PaletteItem } from "../palette.js";
import { Text } from "./Text.js";
import { inkColors } from "./colors.js";

interface PalettePanelProps {
  title: string;
  breadcrumb: string;
  items: PaletteItem[];
  selectedIndex: number;
  emptyLabel: string;
}

/**
 * Minimalistic command palette, inspired by Letta Code.
 * No emojis, clean layout with descriptions in gray.
 */
export function PalettePanel({
  title: _title,
  breadcrumb,
  items,
  selectedIndex,
  emptyLabel,
}: PalettePanelProps): React.JSX.Element {
  if (items.length === 0) {
    return (
      <Box flexDirection="column" marginTop={1}>
        <Text color={inkColors.dim}>{emptyLabel}</Text>
      </Box>
    );
  }

  // Determine visible height (min 5, max 14, or items.length + 2)
  const visibleHeight = Math.min(14, Math.max(5, items.length + 2));

  return (
    <Box flexDirection="column" marginTop={1}>
      <Text color={inkColors.accentBright}>{breadcrumb}</Text>
      <Box height={visibleHeight} marginTop={0} flexDirection="column">
        <ScrollList selectedIndex={selectedIndex} scrollAlignment="auto">
          {items.map((item, index) => {
            const isSelected = index === selectedIndex;
            const labelPadded = item.label.padEnd(25);
            return (
              <Box key={item.id} flexDirection="row" gap={1}>
                <Text color={isSelected ? inkColors.accentBright : inkColors.dim}>
                  {labelPadded}
                </Text>
                <Text color={inkColors.dim}>{item.description}</Text>
              </Box>
            );
          })}
        </ScrollList>
      </Box>
    </Box>
  );
}
