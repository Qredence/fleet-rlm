import { Box } from "ink";
import type React from "react";

export function ShellFrame({ children }: { children: React.ReactNode }): React.JSX.Element {
  return (
    <Box flexDirection="column" paddingX={1}>
      {children}
    </Box>
  );
}
