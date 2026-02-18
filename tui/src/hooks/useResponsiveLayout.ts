/**
 * Responsive layout hook for terminal-based breakpoints.
 * Adapts UI layout based on terminal width.
 */

import { useTerminalDimensions } from "@opentui/react";
import { useMemo } from "react";

const BREAKPOINTS = {
  NARROW: 100,
  MEDIUM: 140,
} as const;

export interface ResponsiveLayout {
  width: number;
  height: number;
  isNarrow: boolean;
  isMedium: boolean;
  isWide: boolean;
  layout: "stacked" | "split";
  shouldCollapseSidebar: boolean;
  chatFlex: number;
  sidebarFlex: number;
}

export function useResponsiveLayout(): ResponsiveLayout {
  const { width, height } = useTerminalDimensions();

  return useMemo(() => {
    const isNarrow = width < BREAKPOINTS.NARROW;
    const isWide = width >= BREAKPOINTS.MEDIUM;
    const isMedium = !isNarrow && !isWide;

    return {
      width,
      height,
      isNarrow,
      isMedium,
      isWide,
      layout: isNarrow ? "stacked" : "split",
      shouldCollapseSidebar: isNarrow,
      chatFlex: isWide ? 65 : 70,
      sidebarFlex: isWide ? 35 : 30,
    };
  }, [width, height]);
}
