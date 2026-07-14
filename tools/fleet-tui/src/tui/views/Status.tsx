/** Status indicator line. */

import { Box, Text } from "ink";
import type { FC } from "react";

import type { Message } from "../store.js";
import { theme } from "../theme.js";

export const Status: FC<{ message: Extract<Message, { kind: "status" }> }> = ({ message }) => (
  <Box marginLeft={2}>
    <Text backgroundColor={theme.paper} color={theme.background} bold>
      {" STATUS "}
    </Text>
    <Text dimColor>{` ${message.phase}${message.detail ? ` · ${message.detail}` : ""}`}</Text>
  </Box>
);
