/** Skill activation line. */

import { Box, Text } from "ink";
import type { FC } from "react";

import type { Message } from "../store.js";
import { theme } from "../theme.js";

export const Skill: FC<{ message: Extract<Message, { kind: "skill" }> }> = ({ message }) => (
  <Box marginLeft={2}>
    <Text color={theme.paper} bold>{"· SKILL "}</Text>
    <Text color={theme.ink} bold>{` ${message.name} `}</Text>
    <Text dimColor>{`v${message.version} · ${message.trust}`}</Text>
  </Box>
);
