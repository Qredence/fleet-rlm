import { Box } from "ink";
import type { FC, ReactNode } from "react";

/** Fixed execution viewport that grows upward from the prompt. */
export const TimelineViewport: FC<{ height: number; children?: ReactNode }> = ({
  height,
  children,
}) => (
  <Box
    flexDirection="column"
    height={height}
    justifyContent="flex-end"
    overflow="hidden"
    gap={1}
  >
    {children}
  </Box>
);
