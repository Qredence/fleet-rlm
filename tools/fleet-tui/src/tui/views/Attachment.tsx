/** Attachment read line. */

import { Box, Text } from "ink";
import type { FC } from "react";

import { formatBytes, shortId } from "../format.js";
import type { Message } from "../store.js";
import { theme } from "../theme.js";

export const Attachment: FC<{ message: Extract<Message, { kind: "attachment" }> }> = ({ message }) => (
  <Box marginLeft={2}>
    <Text color={theme.paper} bold>{"· FILE "}</Text>
    <Text color={theme.ink}>{` ${message.filename} `}</Text>
    <Text dimColor>{`(${formatBytes(message.bytes)} · ${shortId(message.attachmentId)})`}</Text>
  </Box>
);
