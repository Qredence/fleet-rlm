import { memo } from "react";
import { TabsContent } from "@/components/ui/tabs";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { cn } from "@/lib/utils/cn";
import type { AssistantContentModel } from "@/features/rlm-workspace/assistant-content/model";
import {
  inspectorStyles,
  inspectorInsetClass,
} from "@/features/rlm-workspace/shared/inspector-styles";
import { ExternalAnchor } from "../ui/inspector-ui";

export const EvidenceInspectorTab = memo(function EvidenceInspectorTab({
  model,
}: {
  model: AssistantContentModel;
}) {
  const { evidence } = model;
  return (
    <TabsContent value="evidence" className="min-h-0 flex-1">
      <ScrollArea className="h-full">
        <div className={inspectorStyles.tab.content}>
          {evidence.citations.length > 0 ? (
            <section className={inspectorStyles.stack.section}>
              <div className={inspectorStyles.heading.section}>Citations</div>
              {evidence.citations.map((citation, index) => (
                <Card key={`${citation.url}-${index}`} className={inspectorStyles.card.root}>
                  <CardHeader className={inspectorStyles.card.header}>
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div className="space-y-1">
                        <CardTitle className="text-sm font-medium text-foreground">
                          <ExternalAnchor href={citation.url}>{citation.title}</ExternalAnchor>
                        </CardTitle>
                        {citation.description ? (
                          <CardDescription>{citation.description}</CardDescription>
                        ) : null}
                      </div>
                      <Badge variant="accent" className={inspectorStyles.badge.status}>
                        #{citation.number ?? index + 1}
                      </Badge>
                    </div>
                  </CardHeader>
                  {citation.quote ? (
                    <CardContent className={inspectorStyles.card.content}>
                      <Accordion type="single" collapsible>
                        <AccordionItem value="quote">
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
                    <div className="space-y-2">
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
                        <AccordionItem value="snippet">
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
                      <div className="space-y-1">
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
        </div>
      </ScrollArea>
    </TabsContent>
  );
});
