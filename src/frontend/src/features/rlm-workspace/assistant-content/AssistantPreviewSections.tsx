import { Badge } from "@/components/ui/badge";
import { Streamdown } from "@/components/ui/streamdown";
import type { InspectorTab } from "@/lib/data/types";
import type {
  AssistantContentModel,
  ExecutionSection,
} from "@/features/rlm-workspace/assistant-content/types";
import { cn } from "@/lib/utils/cn";

function previewButtonClasses(selected = false) {
  return cn(
    "w-full rounded-2xl border border-border-subtle/80 bg-muted/18 px-3.5 py-3 text-left transition-colors hover:border-border-strong hover:bg-muted/28",
    selected && "border-accent/35 bg-accent/7",
  );
}

function previewHeading(label: string) {
  return (
    <div className="text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
      {label}
    </div>
  );
}

function statusTone(
  status: "pending" | "running" | "completed" | "failed",
): { label: string; variant: "secondary" | "warning" | "success" | "destructive-subtle" } {
  switch (status) {
    case "pending":
      return { label: "Pending", variant: "secondary" };
    case "running":
      return { label: "Running", variant: "warning" };
    case "failed":
      return { label: "Failed", variant: "destructive-subtle" };
    default:
      return { label: "Completed", variant: "success" };
  }
}

function executionSectionState(
  section: ExecutionSection,
): "pending" | "running" | "completed" | "failed" {
  if (section.kind === "tool_session") {
    const latest = section.session.items[section.session.items.length - 1];
    if (!latest) return "running";
    if (
      latest.part.kind === "tool" ||
      latest.part.kind === "sandbox"
    ) {
      if (latest.part.errorText) return "failed";
      return latest.part.state === "running" ||
        latest.part.state === "input-streaming"
        ? "running"
        : "completed";
    }
    if (latest.part.kind === "status_note") {
      if (latest.part.tone === "error") return "failed";
      if (latest.part.tone === "warning") return "running";
    }
    return "completed";
  }

  if (section.kind === "task") {
    if (section.part.status === "error") return "failed";
    if (section.part.status === "in_progress") return "running";
    if (section.part.status === "pending") return "pending";
    return "completed";
  }

  if (section.kind === "queue") {
    return section.part.items.every((item) => item.completed)
      ? "completed"
      : "running";
  }

  if (section.kind === "status_note") {
    if (section.part.tone === "error") return "failed";
    if (section.part.tone === "warning") return "running";
    return "completed";
  }

  if ("errorText" in section.part && section.part.errorText) return "failed";
  if (
    "state" in section.part &&
    (section.part.state === "running" || section.part.state === "input-streaming")
  ) {
    return "running";
  }
  return "completed";
}

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
    <button type="button" className={previewButtonClasses()} onClick={onClick}>
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
        <Badge variant="secondary" className="rounded-full">
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
    <section className="space-y-2" data-slot="assistant-trajectory-preview">
      {previewHeading("Trajectory")}
      <div className="space-y-2">
        {items.map((item) => {
          const tone = statusTone(item.status);
          return (
            <button
              key={item.id}
              type="button"
              className={previewButtonClasses()}
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
                <Badge variant={tone.variant} className="rounded-full">
                  {tone.label}
                </Badge>
              </div>
              {item.runtimeBadges.length ? (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {item.runtimeBadges.slice(0, 3).map((badge) => (
                    <Badge
                      key={`${item.id}-${badge}`}
                      variant="outline"
                      className="rounded-full text-[10px]"
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
    <section className="space-y-2" data-slot="assistant-execution-preview">
      {previewHeading("Execution")}
      <div className="space-y-2">
        {model.execution.sections.slice(0, 2).map((section) => {
          const tone = statusTone(executionSectionState(section));
          return (
            <button
              key={section.id}
              type="button"
              className={previewButtonClasses()}
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
                <Badge variant={tone.variant} className="rounded-full">
                  {tone.label}
                </Badge>
              </div>
              {section.runtimeBadges.length ? (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {section.runtimeBadges.slice(0, 3).map((badge) => (
                    <Badge
                      key={`${section.id}-${badge}`}
                      variant="outline"
                      className="rounded-full text-[10px]"
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
    <section className="space-y-2" data-slot="assistant-evidence-preview">
      {previewHeading("Evidence")}
      <div className="space-y-2">
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
