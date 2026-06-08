import { memo } from "react";
import { ChevronDown } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import type { AssistantContentModel } from "@/features/workspace/conversation/assistant-content/model";
import { ExecutionInspectorRow } from "@/features/workspace/inspection/execution-inspector-rows";
import { inspectorStyles } from "@/features/workspace/inspection/inspector-styles";
import { sectionGroups } from "../inspector-ui";
import { InspectorTabPanel } from "../inspector-tab-panel";

export const ExecutionInspectorTab = memo(function ExecutionInspectorTab({
  model,
}: {
  model: AssistantContentModel;
}) {
  const groups = sectionGroups(model.execution.sections);
  const messageId = model.item.turnId;

  return (
    <InspectorTabPanel value="execution">
      {groups.map((group) => (
        <Collapsible key={group.key} defaultOpen className="group/collapsible">
          <CollapsibleTrigger className="flex w-full items-center gap-2 rounded-md py-2 text-left text-sm font-medium text-foreground hover:text-accent">
            <ChevronDown className="size-4 shrink-0 text-muted-foreground transition-transform group-data-[state=open]/collapsible:rotate-180" />
            <span className="flex items-center gap-2">
              {group.label}
              <Badge variant="secondary" className={inspectorStyles.badge.meta}>
                {group.sections.length}
              </Badge>
            </span>
          </CollapsibleTrigger>

          <CollapsibleContent className="flex flex-col gap-3 pb-2 pl-6">
            {group.sections.map((section) => (
              <ExecutionInspectorRow key={section.id} section={section} messageId={messageId} />
            ))}
          </CollapsibleContent>
        </Collapsible>
      ))}
    </InspectorTabPanel>
  );
});
