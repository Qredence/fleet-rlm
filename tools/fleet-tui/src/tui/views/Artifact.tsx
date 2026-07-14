/** Artifact created line with download hint. */

import { Box, Text } from "ink";
import type { FC } from "react";

import { formatBytes, shortId } from "../format.js";
import type { Message } from "../store.js";
import { theme } from "../theme.js";

export const Artifact: FC<{ message: Extract<Message, { kind: "artifact" }> }> = ({ message }) => (
  <Box marginLeft={2}>
    <Text color={theme.paper} bold>{"✓ ARTIFACT "}</Text>
    <Text color={theme.ink} bold>{` ${message.name} `}</Text>
    <Text dimColor>{`(${message.artifactKind} · ${formatBytes(message.bytes)} · ${shortId(message.artifactId)})`}</Text>
  </Box>
);
