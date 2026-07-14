import { Box, Text } from "ink";
import type { FC, ReactNode } from "react";

import { theme } from "../theme.js";

export const OperatorCard: FC<{
  label: string;
  detail?: string;
  expanded: boolean;
  focused?: boolean;
  children?: ReactNode;
}> = ({ label, detail, expanded, focused = false, children }) => (
  <Box flexDirection="column" paddingLeft={1} borderStyle="single" borderLeft borderRight={false} borderTop={false} borderBottom={false} borderColor={focused ? theme.paper : theme.rule}>
    <Box>
      <Text color={focused ? theme.paper : theme.ink} bold={focused}>{focused ? "› " : "  "}{label}</Text>
      {detail ? <Text color={theme.muted}>{`  ${detail}`}</Text> : null}
      <Text color={theme.faint}>{expanded ? "  ▾" : "  ▸"}</Text>
    </Box>
    {expanded ? <Box flexDirection="column" paddingLeft={2}>{children}</Box> : null}
  </Box>
);
