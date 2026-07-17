import { Box, useBoxMetrics, type DOMElement } from "ink";
import { useEffect, useRef, type FC, type ReactNode } from "react";

export type TimelineScrollState = {
  scrollTop: number;
  maxScroll: number;
  following: boolean;
};

export type TimelineScrollAction =
  | { type: "metrics"; contentHeight: number; viewportHeight: number }
  | { type: "page-up"; viewportHeight: number }
  | { type: "page-down"; viewportHeight: number }
  | { type: "end" }
  | { type: "reset" };

export function initialTimelineScroll(): TimelineScrollState {
  return { scrollTop: 0, maxScroll: 0, following: true };
}

export function reduceTimelineScroll(
  state: TimelineScrollState,
  action: TimelineScrollAction,
): TimelineScrollState {
  switch (action.type) {
    case "metrics": {
      const maxScroll = Math.max(0, action.contentHeight - action.viewportHeight);
      const scrollTop = state.following
        ? maxScroll
        : Math.min(state.scrollTop, maxScroll);
      return { scrollTop, maxScroll, following: state.following || scrollTop === maxScroll };
    }
    case "page-up": {
      if (state.maxScroll === 0) return state;
      const scrollTop = Math.max(0, state.scrollTop - pageRows(action.viewportHeight));
      return { ...state, scrollTop, following: false };
    }
    case "page-down": {
      const scrollTop = Math.min(
        state.maxScroll,
        state.scrollTop + pageRows(action.viewportHeight),
      );
      return { ...state, scrollTop, following: scrollTop === state.maxScroll };
    }
    case "end":
      return { ...state, scrollTop: state.maxScroll, following: true };
    case "reset":
      return initialTimelineScroll();
  }
}

function pageRows(viewportHeight: number): number {
  return Math.max(1, Math.floor(viewportHeight * 0.8));
}

/** Fixed execution viewport that grows upward from the prompt. */
export const TimelineViewport: FC<{
  height: number;
  scroll?: TimelineScrollState;
  onMetrics?: (contentHeight: number, viewportHeight: number) => void;
  children?: ReactNode;
}> = ({ height, scroll = initialTimelineScroll(), onMetrics, children }) => {
  const viewportRef = useRef<DOMElement | null>(null);
  const contentRef = useRef<DOMElement | null>(null);
  const { height: contentHeight, hasMeasured } = useBoxMetrics(contentRef);
  const { height: viewportHeight, hasMeasured: hasMeasuredViewport } = useBoxMetrics(viewportRef);

  useEffect(() => {
    if (hasMeasured && hasMeasuredViewport) onMetrics?.(contentHeight, viewportHeight);
  }, [contentHeight, hasMeasured, hasMeasuredViewport, onMetrics, viewportHeight]);

  return (
    <Box
      ref={viewportRef}
      flexDirection="column"
      height={height}
      justifyContent="flex-end"
      overflow="hidden"
    >
      <Box
        ref={contentRef}
        flexDirection="column"
        flexShrink={0}
        position="relative"
        top={scroll.maxScroll - scroll.scrollTop}
        gap={1}
      >
        {children}
      </Box>
    </Box>
  );
};
