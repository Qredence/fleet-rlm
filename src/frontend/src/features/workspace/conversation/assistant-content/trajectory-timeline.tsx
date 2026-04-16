import {
  ChainOfThought,
  ChainOfThoughtContent,
  ChainOfThoughtHeader,
} from "@/features/workspace/conversation/render-primitives";
import { TimelineStep } from "@/components/product/timeline";
import { Reasoning, ReasoningContent, ReasoningTrigger } from "@/components/ai-elements/reasoning";
import { Streamdown } from "@/components/ui/streamdown";
import { mapTaskStatus } from "@/lib/utils/prompt-kit-state";
import type { AssistantContentModel } from "@/features/workspace/conversation/assistant-content/model";
import { inspectorStyles } from "@/features/workspace/inspection/inspector-styles";

function CompactTrajectory({
  overviewText,
  body,
  runtimeBadges,
}: {
  overviewText?: string;
  body?: string;
  runtimeBadges: string[];
}) {
  const content = [overviewText, body].filter(Boolean).join("\n\n");
  if (!content) return null;

  const hasBadges = runtimeBadges.length > 0;
  const isDaytonaReasoning = runtimeBadges.includes("runtime daytona_pilot");

  return (
    <div className="flex flex-col gap-2" data-slot="trajectory-compact">
      <Reasoning
        isStreaming={false}
        autoClose={false}
        defaultOpen={!isDaytonaReasoning}
        className="w-full"
      >
        <ReasoningTrigger
          getThinkingMessage={() => (
            <span className="font-medium text-foreground">
              {isDaytonaReasoning ? "Advanced reasoning steps" : "Planning"}
            </span>
          )}
        />
        <ReasoningContent>{content}</ReasoningContent>
      </Reasoning>
      {hasBadges ? (
        <div className={inspectorStyles.runtime.inline}>{runtimeBadges.join(" · ")}</div>
      ) : null}
    </div>
  );
}

export function TrajectoryTimeline({
  trajectory,
}: {
  trajectory: AssistantContentModel["trajectory"];
}) {
  if (!trajectory.hasContent || trajectory.displayMode === "hidden") return null;

  const compactSummary = trajectory.items.length === 1 ? trajectory.items[0]?.body : undefined;
  const compactBadges = [
    ...(trajectory.overview?.runtimeBadges ?? []),
    ...(trajectory.items.length === 1 ? (trajectory.items[0]?.runtimeBadges ?? []) : []),
  ];
  const isDaytonaReasoning = [
    ...(trajectory.overview?.runtimeBadges ?? []),
    ...trajectory.items.flatMap((item) => item.runtimeBadges),
  ].includes("runtime daytona_pilot");
  const sectionTitle = isDaytonaReasoning ? "Advanced reasoning steps" : "Reasoning";
  const chainTitle = isDaytonaReasoning ? "Advanced reasoning steps" : "Trajectory";

  return (
    <section className="flex flex-col gap-3" data-slot="assistant-trajectory">
      <div className={inspectorStyles.heading.section}>{sectionTitle}</div>

      {trajectory.displayMode === "compact" ? (
        <CompactTrajectory
          overviewText={trajectory.overview?.text}
          body={compactSummary}
          runtimeBadges={compactBadges}
        />
      ) : (
        <div className="flex flex-col gap-3">
          {trajectory.overview ? (
            <div data-slot="trajectory-overview">
              <Reasoning
                isStreaming={trajectory.overview.isStreaming}
                autoClose={false}
                defaultOpen={!isDaytonaReasoning}
                duration={trajectory.overview.duration}
                className="w-full"
              >
                <ReasoningTrigger
                  getThinkingMessage={(isStreaming) => (
                    <span className="font-medium text-foreground">
                      {isDaytonaReasoning
                        ? isStreaming
                          ? "Advanced reasoning steps..."
                          : "Advanced reasoning steps"
                        : isStreaming
                          ? "Planning..."
                          : "Planning"}
                    </span>
                  )}
                />
                <ReasoningContent>{trajectory.overview.text}</ReasoningContent>
              </Reasoning>
              <div className="mt-2">
                {trajectory.overview.runtimeBadges.length ? (
                  <div className={inspectorStyles.runtime.inline}>
                    {trajectory.overview.runtimeBadges.join(" · ")}
                  </div>
                ) : null}
              </div>
            </div>
          ) : null}

          <ChainOfThought defaultOpen={!isDaytonaReasoning} className="w-full">
            <ChainOfThoughtHeader>{chainTitle}</ChainOfThoughtHeader>
            <ChainOfThoughtContent>
              <div className="divide-y divide-border-subtle">
                {trajectory.items.map((item) => (
                  <TimelineStep
                    key={item.id}
                    label={item.title}
                    status={mapTaskStatus(
                      item.status === "running"
                        ? "in_progress"
                        : item.status === "failed"
                          ? "error"
                          : item.status,
                    )}
                  >
                    <div className="flex flex-col gap-1">
                      {item.body ? (
                        <Streamdown content={item.body} streaming={item.status === "running"} />
                      ) : null}
                      {!item.body && item.details?.length ? (
                        <div className="flex flex-col gap-1 text-xs text-muted-foreground">
                          {item.details.map((detail, idx) => (
                            <div key={`${item.id}-detail-${idx}`}>{detail}</div>
                          ))}
                        </div>
                      ) : null}
                      {item.runtimeBadges.length ? (
                        <div className={inspectorStyles.runtime.inline}>
                          {item.runtimeBadges.join(" · ")}
                        </div>
                      ) : null}
                    </div>
                  </TimelineStep>
                ))}
              </div>
            </ChainOfThoughtContent>
          </ChainOfThought>
        </div>
      )}
    </section>
  );
}
