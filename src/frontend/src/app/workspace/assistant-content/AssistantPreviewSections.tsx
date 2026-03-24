import { Badge } from "@/components/ui/badge";
import type { InspectorTab } from "@/screens/workspace/use-workspace";
import type { AssistantContentModel } from "@/app/workspace/assistant-content/model";
import {
  inspectorStyles,
  inspectorPreviewButtonClass,
} from "@/app/workspace/inspector/inspector-styles";

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
    <button
      type="button"
      className={inspectorPreviewButtonClass()}
      onClick={onClick}
    >
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
    <section
      className={inspectorStyles.stack.section}
      data-slot="assistant-evidence-preview"
    >
      <div className={inspectorStyles.heading.section}>Evidence</div>
      <div className={inspectorStyles.stack.compact}>
        {items.map((item) => (
          <EvidencePreviewButton
            key={item.key}
            label={item.label}
            description={
              item.description ? summarizeText(item.description) : undefined
            }
            iconLabel={item.badge}
            onClick={() => onOpenTab("evidence")}
          />
        ))}
      </div>
    </section>
  );
}
