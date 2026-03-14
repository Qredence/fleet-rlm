import {
  ChainOfThought,
  ChainOfThoughtContent,
  ChainOfThoughtHeader,
  ChainOfThoughtStep,
} from "@/components/ai-elements/chain-of-thought";
import {
  Reasoning,
  ReasoningContent,
  ReasoningTrigger,
} from "@/components/ai-elements/reasoning";
import { Streamdown } from "@/components/ui/streamdown";
import { mapTaskStatus } from "@/lib/utils/ai-elements-state";
import type { AssistantContentModel } from "@/features/rlm-workspace/assistant-content/types";
import { inspectorStyles } from "@/features/rlm-workspace/shared/inspector-styles";

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

  return (
    <div className="space-y-2" data-slot="trajectory-compact">
      <Reasoning
        isStreaming={false}
        autoClose={false}
        defaultOpen
        className="w-full"
      >
        <ReasoningTrigger
          getThinkingMessage={() => (
            <span className="font-medium text-foreground">Planning</span>
          )}
        />
        <ReasoningContent>{content}</ReasoningContent>
      </Reasoning>
      {hasBadges ? (
        <div className={inspectorStyles.runtime.inline}>
          {runtimeBadges.join(" · ")}
        </div>
      ) : null}
    </div>
  );
}

export function TrajectoryTimeline({
  trajectory,
}: {
  trajectory: AssistantContentModel["trajectory"];
}) {
  if (!trajectory.hasContent || trajectory.displayMode === "hidden")
    return null;

  const compactSummary =
    trajectory.items.length === 1 ? trajectory.items[0]?.body : undefined;
  const compactBadges = [
    ...(trajectory.overview?.runtimeBadges ?? []),
    ...(trajectory.items.length === 1
      ? (trajectory.items[0]?.runtimeBadges ?? [])
      : []),
  ];

  return (
    <section className="space-y-3" data-slot="assistant-trajectory">
      <div className={inspectorStyles.heading.section}>Reasoning</div>

      {trajectory.displayMode === "compact" ? (
        <CompactTrajectory
          overviewText={trajectory.overview?.text}
          body={compactSummary}
          runtimeBadges={compactBadges}
        />
      ) : (
        <div className="space-y-3">
          {trajectory.overview ? (
            <div data-slot="trajectory-overview">
              <Reasoning
                isStreaming={trajectory.overview.isStreaming}
                autoClose={false}
                defaultOpen
                duration={trajectory.overview.duration}
                className="w-full"
              >
                <ReasoningTrigger
                  getThinkingMessage={(isStreaming) => (
                    <span className="font-medium text-foreground">
                      {isStreaming ? "Planning..." : "Planning"}
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

          <ChainOfThought defaultOpen className="w-full">
            <ChainOfThoughtHeader>Trajectory</ChainOfThoughtHeader>
            <ChainOfThoughtContent>
              <div className="divide-y divide-border-subtle">
                {trajectory.items.map((item) => (
                  <ChainOfThoughtStep
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
                    <div className="space-y-1">
                      {item.body ? (
                        <Streamdown
                          content={item.body}
                          streaming={item.status === "running"}
                        />
                      ) : null}
                      {item.details?.length ? (
                        <div className="space-y-1 text-xs text-muted-foreground">
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
                  </ChainOfThoughtStep>
                ))}
              </div>
            </ChainOfThoughtContent>
          </ChainOfThought>
        </div>
      )}
    </section>
  );
}
