import { memo } from "react";

import { ToolRenderer } from "@/components/agent-elements/tools/tool-renderer";
import type { AgentToolPart } from "@/lib/workspace/agent-tool-parts";
import {
  chatRenderPartToAgentToolPart,
  mapToolState,
} from "@/lib/workspace/agent-tool-parts";
import type { ExecutionSection } from "@/features/workspace/conversation/assistant-content/model/types";
import {
  executionSectionState,
  renderBadges,
  renderExecutionSectionDetails,
} from "@/features/workspace/inspection/inspector-ui";
import { inspectorStyles } from "@/features/workspace/inspection/inspector-styles";
import { cn } from "@/lib/utils";

function sectionAgentState(section: ExecutionSection): AgentToolPart["state"] {
  const state = executionSectionState(section);
  if (state === "failed") return "output-error";
  if (state === "running") return "call";
  if (state === "pending") return "input-streaming";
  return "output-available";
}

function executionSectionToToolParts(
  section: ExecutionSection,
  messageId: string,
): { part: AgentToolPart; nestedTools?: AgentToolPart[] } | null {
  if (section.kind === "tool_session") {
    const parentId = section.id;
    const nestedTools = section.session.items.flatMap((item, index) => {
      const nested = chatRenderPartToAgentToolPart(item.part, messageId, index, {
        parentId,
      });
      return nested ? [nested] : [];
    });
    const parentState = sectionAgentState(section);
    return {
      part: {
        type: "tool-Agent",
        toolCallId: parentId,
        state: parentState,
        input: {
          description: section.summary,
          subagent_type: section.label,
        },
        output: parentState === "output-available" ? { status: "completed" } : undefined,
      },
      nestedTools,
    };
  }

  const agentPart = chatRenderPartToAgentToolPart(section.part, messageId, 0, {
    startedAt: section.part.kind === "tool" && section.part.state === "running" ? Date.now() : undefined,
  });
  if (!agentPart) return null;

  if (section.part.kind === "tool" || section.part.kind === "sandbox") {
    return {
      part: {
        ...agentPart,
        state: mapToolState(section.part.state),
      },
    };
  }

  return { part: { ...agentPart, state: sectionAgentState(section) } };
}

function showInspectorDetails(section: ExecutionSection) {
  return (
    section.kind === "environment_variables" ||
    section.kind === "tool" ||
    section.kind === "sandbox" ||
    section.kind === "tool_session"
  );
}

export const ExecutionInspectorRow = memo(function ExecutionInspectorRow({
  section,
  messageId,
}: {
  section: ExecutionSection;
  messageId: string;
}) {
  const mapped = executionSectionToToolParts(section, messageId);
  if (!mapped) return null;

  const failed = executionSectionState(section) === "failed";

  return (
    <div className={cn(inspectorStyles.stack.compact, failed && "text-destructive")}>
      <ToolRenderer part={mapped.part} nestedTools={mapped.nestedTools} />
      {showInspectorDetails(section) ? renderExecutionSectionDetails(section) : null}
      {section.runtimeBadges.length > 0 ? renderBadges(section.runtimeBadges) : null}
    </div>
  );
});
