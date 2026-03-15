import {
  Attachment,
  AttachmentInfo,
  AttachmentPreview,
  Attachments,
} from "@/components/ai-elements/attachments";
import {
  InlineCitation,
  InlineCitationCard,
  InlineCitationCardBody,
  InlineCitationCardTrigger,
  InlineCitationQuote,
  InlineCitationSource,
  InlineCitationText,
} from "@/components/ai-elements/inline-citation";
import { Source, Sources, SourcesContent, SourcesTrigger } from "@/components/ai-elements/sources";
import type { AssistantContentModel } from "@/features/rlm-workspace/assistant-content/types";
import { inspectorStyles } from "@/features/rlm-workspace/shared/inspector-styles";

export function EvidenceGroup({ evidence }: { evidence: AssistantContentModel["evidence"] }) {
  if (!evidence.hasContent) return null;

  return (
    <section className="space-y-3" data-slot="assistant-evidence">
      <div className={inspectorStyles.heading.section}>Evidence</div>

      {evidence.citations.length > 0 ? (
        <div data-slot="evidence-citations">
          <InlineCitation>
            <InlineCitationText>
              <span className="text-xs text-muted-foreground">Citations</span>
            </InlineCitationText>
            <InlineCitationCard>
              <InlineCitationCardTrigger
                sources={evidence.citations.map((citation) => citation.url)}
              />
              <InlineCitationCardBody>
                <div className="space-y-3">
                  {evidence.citations.map((citation, idx) => (
                    <div
                      key={`${citation.url}-${idx}`}
                      className="space-y-2 rounded-md border-subtle p-2"
                    >
                      <InlineCitationSource
                        title={citation.title}
                        url={citation.url}
                        description={citation.description}
                      />
                      {citation.quote ? (
                        <InlineCitationQuote>{citation.quote}</InlineCitationQuote>
                      ) : null}
                    </div>
                  ))}
                </div>
              </InlineCitationCardBody>
            </InlineCitationCard>
          </InlineCitation>
        </div>
      ) : null}

      {evidence.sources.length > 0 ? (
        <div data-slot="evidence-sources">
          <Sources defaultOpen={false}>
            <SourcesTrigger count={evidence.sources.length} />
            <SourcesContent>
              <div className="space-y-2">
                {evidence.sources.map((source) => (
                  <Source
                    key={`${source.sourceId}-${source.url ?? source.canonicalUrl ?? source.title}`}
                    href={source.url ?? source.canonicalUrl ?? "#"}
                    title={source.title ?? "Source"}
                  >
                    {source.description || source.quote || source.displayUrl}
                  </Source>
                ))}
              </div>
            </SourcesContent>
          </Sources>
        </div>
      ) : null}

      {evidence.attachments.length > 0 ? (
        <div data-slot="evidence-attachments">
          <Attachments variant="grid">
            {evidence.attachments.map((attachment) => (
              <Attachment
                key={attachment.attachmentId}
                data={{
                  id: attachment.attachmentId,
                  type: "file",
                  filename: attachment.name ?? "unknown",
                  url: attachment.url ?? "",
                  mediaType:
                    attachment.mimeType ?? attachment.mediaType ?? "application/octet-stream",
                }}
              >
                <AttachmentPreview />
                <AttachmentInfo showMediaType />
              </Attachment>
            ))}
          </Attachments>
        </div>
      ) : null}
    </section>
  );
}
