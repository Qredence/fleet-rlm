import { Text } from "ink";
import type { FC } from "react";
import { renderMarkdown } from "../markdown.js";
import type { Message } from "../store.js";
import { theme } from "../theme.js";
import { OperatorCard } from "./OperatorCard.js";

export const Reasoning: FC<{ message: Extract<Message, {kind:"reasoning"}>; width:number; expanded:boolean; focused?:boolean }> = ({message,width,expanded,focused}) => (
  <OperatorCard label="REASONING" detail={`step ${message.step}`} expanded={expanded} focused={focused}>
    <Text color={theme.muted} italic>{renderMarkdown(message.text || "(reasoning in progress…)", Math.max(20,width-8))}</Text>
  </OperatorCard>
);
