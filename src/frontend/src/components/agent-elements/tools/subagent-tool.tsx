import { memo } from "react";

import { ToolGroup } from "./tool-group";

export type SubagentToolProps = {
  part: any;
  nestedTools?: any[];
  chatStatus?: string;
};

function subagentLabel(part: any): string {
  const raw =
    part?.input?.subagent_type ??
    part?.input?.agent_type ??
    part?.input?.name ??
    (part?.type === "tool-Agent" ? "Agent" : "Task");
  const label = String(raw || "").trim();
  return label || "Agent";
}

export const SubagentTool = memo(function SubagentTool({
  part,
  nestedTools,
  chatStatus,
}: SubagentToolProps) {
  const label = subagentLabel(part);

  return (
    <ToolGroup
      part={part}
      nestedTools={nestedTools}
      chatStatus={chatStatus}
      completeLabel={`${label} completed`}
      shimmerLabel={`Running ${label.toLowerCase()}`}
      interruptedLabel={`${label} interrupted`}
      defaultOpen={false}
    />
  );
});
