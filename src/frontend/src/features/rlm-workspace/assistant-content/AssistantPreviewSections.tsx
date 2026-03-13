import { Badge } from "@/components/ui/badge";
import { Streamdown } from "@/components/ui/streamdown";
import type { InspectorTab } from "@/lib/data/types";
import type {
  AssistantContentModel,
} from "@/features/rlm-workspace/assistant-content/types";
import { inspectorStyles, inspectorPreviewButtonClass } from "@/features/rlm-workspace/shared/inspector-styles";
import { statusTone } from "@/features/rlm-workspace/message-inspector/utils/inspector-utils";

function summarizeText(value: string, maxLength = 140) {
  const trimmed = value.trim();
  if (trimmed.length <= maxLength) return trimmed;
  return `${trimmed.slice(0, maxLength - 3)}...`;
}

function EvidencePreviewButton({
  label,
  description,
  iconLabel,
  onClick,
}: {
  label: string;
  description?: string;
  iconLabel: string;
  onClick: () => void;
}) {
  return (
    <button type="button" className={inspectorPreviewButtonClass()} onClick={onClick}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="space-y-1">
          <div className="text-sm font-medium leading-5 text-foreground">
            {label}
          </div>
          {description ? (
            <div className="text-sm leading-5 text-muted-foreground">
              {description}
            </div>
          ) : null}
        </div>
        <Badge variant="secondary" className={inspectorStyles.badge.meta}>
          {iconLabel}
        </Badge>
      </div>
    </button>
  );
}

export function TrajectoryPreview({
  model,
  onOpenTab,
}: {
  model: AssistantContentModel;
  onOpenTab: (tab: InspectorTab) => void;
}) {
  if (!model.trajectory.hasContent) return null;

  const items =
    model.trajectory.items.length > 0
      ? model.trajectory.items.slice(0, 2)
      : model.trajectory.overview
        ? [
            {
              id: model.trajectory.overview.key,
              title: "Planning",
              body: model.trajectory.overview.text,
              status: model.trajectory.overview.isStreaming
                ? ("running" as const)
                : ("completed" as const),
              runtimeBadges: model.trajectory.overview.runtimeBadges,
            },
          ]
        : [];

  return (
    <section className={inspectorStyles.stack.section} data-slot="assistant-trajectory-preview">
      <div className={inspectorStyles.heading.section}>Trajectory</div>
      <div className={inspectorStyles.stack.compact}>
        {items.map((item) => {
          const tone = statusTone(item.status);
          return (
            <button
              key={item.id}
              type="button"
              className={inspectorPreviewButtonClass()}
              onClick={() => onOpenTab("trajectory")}
            >
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="space-y-1">
                  <div className="text-sm font-medium leading-5 text-foreground">
                    {item.title}
                  </div>
                  {item.body ? (
                    <div className="text-sm leading-6 text-muted-foreground">
                      <Streamdown
                        content={item.body}
                        streaming={item.status === "running"}
                      />
                    </div>
                  ) : null}
                </div>
                <Badge variant={tone.variant} className={inspectorStyles.badge.status}>
                  {tone.label}
                </Badge>
              </div>
              {item.runtimeBadges.length ? (
                <div className={inspectorStyles.badge.row}>
                  {item.runtimeBadges.slice(0, 3).map((badge) => (
                    <Badge
                      key={`${item.id}-${badge}`}
                      variant="outline"
                      className={inspectorStyles.badge.meta}
                    >
                      {badge}
                    </Badge>
                  ))}
                </div>
              ) : null}
            </button>
          );
        })}
      </div>
    </section>
  );
}

export function ExecutionPreview({
  model,
  onOpenTab,
}: {
  model: AssistantContentModel;
  onOpenTab: (tab: InspectorTab) => void;
}) {
  if (!model.execution.hasContent) return null;

  return (
    <section className={inspectorStyles.stack.section} data-slot="assistant-execution-preview">
      <div className={inspectorStyles.heading.section}>Execution</div>
      <div className={inspectorStyles.stack.compact}>
        {model.execution.sections.slice(0, 2).map((section) => {
          return (
            <button
              key={section.id}
              type="button"
              className={inspectorPreviewButtonClass()}
              onClick={() => onOpenTab("execution")}
            >
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="space-y-1">
                  <div className="text-sm font-medium leading-5 text-foreground">
                    {section.label}
                  </div>
                  <div className="text-sm leading-5 text-muted-foreground">
                    {summarizeText(section.summary)}
                  </div>
                </div>
              </div>
              {section.runtimeBadges.length ? (
                <div className={inspectorStyles.badge.row}>
                  {section.runtimeBadges.slice(0, 3).map((badge) => (
                    <Badge
                      key={`${section.id}-${badge}`}
                      variant="outline"
                      className={inspectorStyles.badge.meta}
                    >
                      {badge}
                    </Badge>
                  ))}
                </div>
              ) : null}
            </button>
          );
        })}
      </div>
    </section>
  );
}

export function EvidencePreview({
  model,
  onOpenTab,
}: {
  model: AssistantContentModel;
  onOpenTab: (tab: InspectorTab) => void;
}) {
  if (!model.evidence.hasContent) return null;

  const items = [
    ...model.evidence.citations.slice(0, 1).map((citation) => ({
      key: `citation-${citation.url}`,
      label: citation.title,
      description: citation.quote ?? citation.description,
      badge: "citation",
    })),
    ...model.evidence.sources.slice(0, 1).map((source) => ({
      key: `source-${source.sourceId}`,
      label: source.title,
      description: source.description ?? source.quote ?? source.displayUrl,
      badge: source.kind,
    })),
    ...model.evidence.attachments.slice(0, 1).map((attachment) => ({
      key: `attachment-${attachment.attachmentId}`,
      label: attachment.name,
      description: attachment.mimeType ?? attachment.mediaType,
      badge: "attachment",
    })),
  ].slice(0, 2);

  return (
    <section className={inspectorStyles.stack.section} data-slot="assistant-evidence-preview">
      <div className={inspectorStyles.heading.section}>Evidence</div>
      <div className={inspectorStyles.stack.compact}>
        {items.map((item) => (
          <EvidencePreviewButton
            key={item.key}
            label={item.label}
            description={item.description ? summarizeText(item.description) : undefined}
            iconLabel={item.badge}
            onClick={() => onOpenTab("evidence")}
          />
        ))}
      </div>
    </section>
  );
}
