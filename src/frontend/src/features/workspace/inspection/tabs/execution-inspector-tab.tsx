import { memo } from "react";
import { Badge } from "@/components/ui/badge";
import {
  ChainOfThought,
  ChainOfThoughtContent,
  ChainOfThoughtHeader,
  ChainOfThoughtStep,
} from "@/components/ai-elements/chain-of-thought";
import {
  ActivityIcon,
  LayersIcon,
  ListChecksIcon,
  TerminalIcon,
  VariableIcon,
  WrenchIcon,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { AssistantContentModel } from "@/features/workspace/conversation/assistant-content/model";
import type { ExecutionSection } from "@/features/workspace/conversation/assistant-content/model/types";
import { inspectorStyles } from "@/features/workspace/inspection/inspector-styles";
import {
  executionSectionState,
  renderBadges,
  renderExecutionSectionDetails,
  sectionGroups,
} from "../inspector-ui";
import { InspectorTabPanel } from "../inspector-tab-panel";

const sectionIcon: Record<ExecutionSection["kind"], LucideIcon> = {
  tool_session: WrenchIcon,
  tool: WrenchIcon,
  task: ListChecksIcon,
  queue: LayersIcon,
  sandbox: TerminalIcon,
  environment_variables: VariableIcon,
  status_note: ActivityIcon,
};

function mapStatus(
  state: "pending" | "running" | "completed" | "failed",
): "complete" | "active" | "pending" {
  if (state === "completed" || state === "failed") return "complete";
  if (state === "running") return "active";
  return "pending";
}

export const ExecutionInspectorTab = memo(function ExecutionInspectorTab({
  model,
}: {
  model: AssistantContentModel;
}) {
  const groups = sectionGroups(model.execution.sections);
  return (
    <InspectorTabPanel value="execution">
      {groups.map((group) => (
        <ChainOfThought key={group.key} defaultOpen>
          <ChainOfThoughtHeader>
            <span className="flex items-center gap-2">
              {group.label}
              <Badge variant="secondary" className={inspectorStyles.badge.meta}>
                {group.sections.length}
              </Badge>
            </span>
          </ChainOfThoughtHeader>

          <ChainOfThoughtContent>
            {group.sections.map((section) => {
              const state = executionSectionState(section);
              const usesSummaryAsLabel = section.kind === "status_note";
              return (
                <ChainOfThoughtStep
                  key={section.id}
                  icon={sectionIcon[section.kind]}
                  label={usesSummaryAsLabel ? section.summary : section.label}
                  description={usesSummaryAsLabel ? undefined : section.summary}
                  status={mapStatus(state)}
                  className={state === "failed" ? "text-destructive" : undefined}
                >
                  {section.kind !== "status_note" && renderExecutionSectionDetails(section)}
                  {section.runtimeBadges.length > 0 && renderBadges(section.runtimeBadges)}
                </ChainOfThoughtStep>
              );
            })}
          </ChainOfThoughtContent>
        </ChainOfThought>
      ))}
    </InspectorTabPanel>
  );
});
