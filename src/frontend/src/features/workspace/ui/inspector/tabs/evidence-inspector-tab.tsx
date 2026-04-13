import { memo } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { cn } from "@/lib/utils";
import type { AssistantContentModel } from "@/features/workspace/ui/assistant-content/model";
import {
  inspectorStyles,
  inspectorInsetClass,
} from "@/features/workspace/ui/inspector/inspector-styles";
import { ExternalAnchor } from "../ui/inspector-ui";
import { InspectorTabPanel } from "../inspector-tab-panel";

export const EvidenceInspectorTab = memo(function EvidenceInspectorTab({
  model,
}: {
  model: AssistantContentModel;
}) {
  const { evidence } = model;
  return (
    <InspectorTabPanel value="evidence">
      {evidence.citations.length > 0 ? (
        <section className={inspectorStyles.stack.section}>
          <div className={inspectorStyles.heading.section}>Citations</div>
          {evidence.citations.map((citation, index) => (
            <Card key={`${citation.url}-${index}`} className={inspectorStyles.card.root}>
              <CardHeader className={inspectorStyles.card.header}>
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="flex flex-col gap-1">
                    <CardTitle className="text-sm font-medium text-foreground">
                      <ExternalAnchor href={citation.url}>{citation.title}</ExternalAnchor>
                    </CardTitle>
                    {citation.description ? (
                      <CardDescription>{citation.description}</CardDescription>
                    ) : null}
                  </div>
                  <Badge variant="secondary" className={inspectorStyles.badge.status}>
                    #{citation.number ?? index + 1}
                  </Badge>
                </div>
              </CardHeader>
              {citation.quote ? (
                <CardContent className={inspectorStyles.card.content}>
                  <Accordion type="single" collapsible>
                    <AccordionItem
                      value={`quote-${citation.anchorId ?? citation.sourceId ?? index}`}
                    >
                      <AccordionTrigger>Show excerpt</AccordionTrigger>
                      <AccordionContent>
                        <div className={cn(inspectorInsetClass(), "text-sm")}>
                          {citation.quote}
                        </div>
                      </AccordionContent>
                    </AccordionItem>
                  </Accordion>
                </CardContent>
              ) : null}
            </Card>
          ))}
        </section>
      ) : null}

      {evidence.sources.length > 0 ? (
        <section className={inspectorStyles.stack.section}>
          <div className={inspectorStyles.heading.section}>Sources</div>
          {evidence.sources.map((source) => (
            <Card key={source.sourceId} className={inspectorStyles.card.root}>
              <CardHeader className={inspectorStyles.card.header}>
                <div className="flex flex-col gap-2">
                  <CardTitle className="text-sm font-medium text-foreground">
                    <ExternalAnchor href={source.url ?? source.canonicalUrl}>
                      {source.title}
                    </ExternalAnchor>
                  </CardTitle>
                  <div className={inspectorStyles.badge.row}>
                    <Badge
                      variant="secondary"
                      className={cn(inspectorStyles.badge.status, "capitalize")}
                    >
                      {source.kind}
                    </Badge>
                    {source.displayUrl ? (
                      <Badge variant="secondary" className={inspectorStyles.badge.meta}>
                        {source.displayUrl}
                      </Badge>
                    ) : null}
                  </div>
                </div>
              </CardHeader>
              {source.description || source.quote ? (
                <CardContent className={inspectorStyles.card.content}>
                  <Accordion type="single" collapsible>
                    <AccordionItem value={`snippet-${source.sourceId}`}>
                      <AccordionTrigger>Show supporting snippet</AccordionTrigger>
                      <AccordionContent>
                        <div className={inspectorStyles.stack.compact}>
                          {source.description ? (
                            <div className={cn(inspectorInsetClass(), "text-sm")}>
                              {source.description}
                            </div>
                          ) : null}
                          {source.quote ? (
                            <div className={cn(inspectorInsetClass(), "text-sm")}>
                              {source.quote}
                            </div>
                          ) : null}
                        </div>
                      </AccordionContent>
                    </AccordionItem>
                  </Accordion>
                </CardContent>
              ) : null}
            </Card>
          ))}
        </section>
      ) : null}

      {evidence.attachments.length > 0 ? (
        <section className={inspectorStyles.stack.section}>
          <div className={inspectorStyles.heading.section}>Attachments</div>
          {evidence.attachments.map((attachment) => (
            <Card key={attachment.attachmentId} className={inspectorStyles.card.root}>
              <CardHeader className={inspectorStyles.card.header}>
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="flex flex-col gap-1">
                    <CardTitle className="text-sm font-medium text-foreground">
                      <ExternalAnchor href={attachment.url ?? attachment.previewUrl}>
                        {attachment.name}
                      </ExternalAnchor>
                    </CardTitle>
                    {attachment.description ? (
                      <CardDescription>{attachment.description}</CardDescription>
                    ) : null}
                  </div>
                  <div className={inspectorStyles.badge.row}>
                    {attachment.mimeType || attachment.mediaType ? (
                      <Badge variant="secondary" className={inspectorStyles.badge.meta}>
                        {attachment.mimeType ?? attachment.mediaType}
                      </Badge>
                    ) : null}
                    {attachment.sizeBytes != null ? (
                      <Badge variant="secondary" className={inspectorStyles.badge.meta}>
                        {attachment.sizeBytes} bytes
                      </Badge>
                    ) : null}
                  </div>
                </div>
              </CardHeader>
            </Card>
          ))}
        </section>
      ) : null}

      {!evidence.hasContent ? (
        <Card className={inspectorStyles.card.root}>
          <CardHeader className={inspectorStyles.card.header}>
            <CardTitle className="text-sm font-medium">No evidence attached</CardTitle>
            <CardDescription>
              This response does not include explicit citations, sources, or attachments.
            </CardDescription>
          </CardHeader>
        </Card>
      ) : null}
    </InspectorTabPanel>
  );
});
