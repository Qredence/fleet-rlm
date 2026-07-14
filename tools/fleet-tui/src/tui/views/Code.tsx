import { Text } from "ink";
import type { FC } from "react";
import { renderMarkdown } from "../markdown.js";
import type { Message } from "../store.js";
import { OperatorCard } from "./OperatorCard.js";

export const Code: FC<{ message: Extract<Message,{kind:"code"}>; width:number; expanded:boolean; focused?:boolean }> = ({message,width,expanded,focused}) => (
  <OperatorCard label="CODE" detail={`step ${message.step}`} expanded={expanded} focused={focused}>
    <Text>{renderMarkdown(`\`\`\`python\n${message.code}\n\`\`\``,Math.max(20,width-8))}</Text>
  </OperatorCard>
);
