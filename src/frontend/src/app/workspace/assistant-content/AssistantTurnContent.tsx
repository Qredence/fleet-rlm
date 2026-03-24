import type { KeyboardEvent, MouseEvent } from "react";
import { Message, MessageContent } from "@/components/ai-elements/message";
import { Separator } from "@/components/ui/separator";
import { AssistantAnswerBlock } from "@/app/workspace/assistant-content/AssistantAnswerBlock";
import { AssistantSummaryBar } from "@/app/workspace/assistant-content/AssistantSummaryBar";
import { TrajectoryTimeline } from "@/app/workspace/assistant-content/TrajectoryTimeline";
import { ExecutionHighlightsGroup } from "@/app/workspace/assistant-content/ExecutionHighlightsGroup";
import { EvidencePreview } from "@/app/workspace/assistant-content/AssistantPreviewSections";
import type { AssistantContentModel } from "@/app/workspace/assistant-content/model";
import type { InspectorTab } from "@/screens/workspace/use-workspace";
import { cn } from "@/lib/utils";

function visibleSections(model: AssistantContentModel) {
  return [
    model.answer.hasContent || model.answer.showStreamingShell ? "answer" : null,
    model.summary.show ? "summary" : null,
    model.execution.hasChatHighlights ? "execution" : null,
    model.trajectory.hasContent ? "trajectory" : null,
    model.evidence.hasContent ? "evidence" : null,
  ].filter(Boolean);
}

function isInteractiveTarget(target: EventTarget | null, container?: HTMLElement | null) {
  if (!(target instanceof HTMLElement)) return false;
  const interactiveAncestor = target.closest(
    "a,button,input,textarea,select,summary,[role='button'],[data-no-inspector-open='true']",
  );
  return Boolean(interactiveAncestor && interactiveAncestor !== container);
}

export function AssistantTurnContent({
  model,
  selected = false,
  onOpenTab,
}: {
  model: AssistantContentModel;
  selected?: boolean;
  onOpenTab?: (tab: InspectorTab) => void;
}) {
  const sections = visibleSections(model);
  if (sections.length === 0) return null;

  const hasRichSections =
    model.trajectory.hasContent || model.execution.hasChatHighlights || model.evidence.hasContent;

  const handleOpenTrajectory = () => {
    onOpenTab?.("trajectory");
  };

  const handleContainerClick = (event: MouseEvent<HTMLDivElement>) => {
    if (!onOpenTab || isInteractiveTarget(event.target, event.currentTarget)) {
      return;
    }
    handleOpenTrajectory();
  };

  const handleContainerKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!onOpenTab) return;
    if (event.key !== "Enter" && event.key !== " ") return;
    if (isInteractiveTarget(event.target, event.currentTarget)) return;
    event.preventDefault();
    handleOpenTrajectory();
  };

  return (
    <Message from="assistant" className="mb-2.5" key={model.item.key}>
      <MessageContent className="w-full space-y-2.5">
        <div
          className={cn(
            "max-w-content rounded-bubble px-4 py-3.5 shadow-sm md:px-5 md:py-4",
            "border transition-colors",
            selected && "border-accent/20 bg-accent/5",
            !selected && "border-border-subtle/60 bg-card/60",
          )}
          data-slot="assistant-turn-content"
          role={onOpenTab ? "button" : undefined}
          tabIndex={onOpenTab ? 0 : undefined}
          onClick={handleContainerClick}
          onKeyDown={handleContainerKeyDown}
        >
          <div className="flex flex-col gap-4">
            <AssistantAnswerBlock
              text={model.answer.text}
              showStreamingShell={model.answer.showStreamingShell}
            />

            {model.summary.show ? (
              <>
                {model.answer.hasContent || model.answer.showStreamingShell ? (
                  <Separator className="bg-border-subtle/70" />
                ) : null}
                <AssistantSummaryBar summary={model.summary} onOpenTab={onOpenTab} />
              </>
            ) : null}

            {hasRichSections ? (
              <>
                {model.answer.hasContent ||
                model.answer.showStreamingShell ||
                model.summary.show ? (
                  <Separator className="bg-border-subtle/70" />
                ) : null}
                <div className="space-y-4">
                  {model.execution.hasChatHighlights ? (
                    <ExecutionHighlightsGroup
                      execution={model.execution}
                      onOpenTab={(tab) => onOpenTab?.(tab)}
                    />
                  ) : null}
                  {model.trajectory.hasContent ? (
                    <TrajectoryTimeline trajectory={model.trajectory} />
                  ) : null}
                  {model.evidence.hasContent ? (
                    <EvidencePreview model={model} onOpenTab={(tab) => onOpenTab?.(tab)} />
                  ) : null}
                </div>
              </>
            ) : null}
          </div>
        </div>
      </MessageContent>
    </Message>
  );
}
