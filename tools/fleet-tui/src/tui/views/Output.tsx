import { Text } from "ink";
import type { FC } from "react";
import { renderMarkdown } from "../markdown.js";
import type { Message } from "../store.js";
import { theme } from "../theme.js";
import { OperatorCard } from "./OperatorCard.js";

export const Output: FC<{ message: Extract<Message,{kind:"output"}>; width:number; expanded:boolean; focused?:boolean }> = ({message,width,expanded,focused}) => (
  <OperatorCard label="OUTPUT" detail={`step ${message.step}`} expanded={expanded} focused={focused}>
    <Text color={theme.ink}>{renderMarkdown(`\`\`\`text\n${message.output}\n\`\`\``,Math.max(20,width-8))}</Text>
  </OperatorCard>
);
