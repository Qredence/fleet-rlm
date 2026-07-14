import { Box, Text } from "ink";
import type { FC } from "react";
import { formatStructuredResult } from "../format.js";
import { renderMarkdown } from "../markdown.js";
import type { Message } from "../store.js";
import { theme } from "../theme.js";
import { OperatorCard } from "./OperatorCard.js";

export const Result: FC<{message:Extract<Message,{kind:"result"}>;width:number;expanded:boolean;focused?:boolean}> = ({message,width,expanded,focused}) => {
  const display=formatStructuredResult(message.value);
  const labelWidth=Math.min(24,Math.max(1,...display.rows.map(([label])=>label.length)));
  return <OperatorCard label="RESULT" detail={[message.schemaId,message.schemaVersion].filter(Boolean).join(" · ")} expanded={expanded} focused={focused}>
    {display.prominent!==null?<Text color={theme.paper} bold>{display.prominent}</Text>:null}
    {display.rows.map(([label,value])=><Box key={label} flexDirection="column"><Text><Text color={theme.muted}>{label.padEnd(labelWidth)}</Text><Text color={theme.ink}>{`  ${value}`}</Text></Text></Box>)}
    {message.narrative?<Box marginTop={1}><Text>{renderMarkdown(message.narrative,Math.max(20,width-8))}</Text></Box>:null}
  </OperatorCard>;
};
