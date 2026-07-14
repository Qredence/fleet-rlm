/** Warning line. */

import { Box, Text } from "ink";
import type { FC } from "react";

import type { Message } from "../store.js";
import { theme } from "../theme.js";

export const Warning: FC<{ message: Extract<Message, { kind: "warning" }> }> = ({ message }) => (
  <Box marginLeft={2}>
    <Text color={theme.paper} bold>{"! WARNING "}</Text>
    <Text color={theme.ink}>{` ${message.code}: ${message.message}`}</Text>
  </Box>
);
