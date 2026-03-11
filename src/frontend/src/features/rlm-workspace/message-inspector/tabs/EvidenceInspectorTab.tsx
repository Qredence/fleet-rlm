import { memo } from "react";
import { TabsContent } from "@/components/ui/tabs";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import type { AssistantContentModel } from "@/features/rlm-workspace/assistant-content/types";
import { ExternalAnchor } from "../components/inspector-components";

export const EvidenceInspectorTab = memo(function EvidenceInspectorTab({ model }: { model: AssistantContentModel }) {
  const { evidence } = model;
  return (
    <TabsContent value="evidence" className="min-h-0 flex-1">
      <ScrollArea className="h-full">
        <div className="space-y-4 px-4 pb-4">
          {evidence.citations.length > 0 ? (
            <section className="space-y-2">
              <div className="text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
                Citations
              </div>
              {evidence.citations.map((citation, index) => (
                <Card
                  key={`${citation.url}-${index}`}
                  className="gap-3 rounded-2xl border-border-subtle/80 shadow-none"
                >
                  <CardHeader className="px-4 pt-4">
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div className="space-y-1">
                        <CardTitle className="text-sm font-medium text-foreground">
                          <ExternalAnchor href={citation.url}>
                            {citation.title}
                          </ExternalAnchor>
                        </CardTitle>
                        {citation.description ? (
                          <CardDescription>{citation.description}</CardDescription>
                        ) : null}
                      </div>
                      <Badge variant="accent" className="rounded-full">
                        #{citation.number ?? index + 1}
                      </Badge>
                    </div>
                  </CardHeader>
                  {citation.quote ? (
                    <CardContent className="px-4 pb-4">
                      <Accordion type="single" collapsible>
                        <AccordionItem value="quote">
                          <AccordionTrigger>Show excerpt</AccordionTrigger>
                          <AccordionContent>
                            <div className="rounded-xl border border-border-subtle/80 bg-muted/20 px-3 py-2 text-sm text-muted-foreground">
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
            <section className="space-y-2">
              <div className="text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
                Sources
              </div>
              {evidence.sources.map((source) => (
                <Card
                  key={source.sourceId}
                  className="gap-3 rounded-2xl border-border-subtle/80 shadow-none"
                >
                  <CardHeader className="px-4 pt-4">
                    <div className="space-y-2">
                      <CardTitle className="text-sm font-medium text-foreground">
                        <ExternalAnchor href={source.url ?? source.canonicalUrl}>
                          {source.title}
                        </ExternalAnchor>
                      </CardTitle>
                      <div className="flex flex-wrap gap-1.5">
                        <Badge variant="secondary" className="rounded-full capitalize">
                          {source.kind}
                        </Badge>
                        {source.displayUrl ? (
                          <Badge variant="outline" className="rounded-full">
                            {source.displayUrl}
                          </Badge>
                        ) : null}
                      </div>
                    </div>
                  </CardHeader>
                  {source.description || source.quote ? (
                    <CardContent className="px-4 pb-4">
                      <Accordion type="single" collapsible>
                        <AccordionItem value="snippet">
                          <AccordionTrigger>Show supporting snippet</AccordionTrigger>
                          <AccordionContent>
                            <div className="space-y-2">
                              {source.description ? (
                                <div className="rounded-xl border border-border-subtle/80 bg-muted/20 px-3 py-2 text-sm text-muted-foreground">
                                  {source.description}
                                </div>
                              ) : null}
                              {source.quote ? (
                                <div className="rounded-xl border border-border-subtle/80 bg-muted/20 px-3 py-2 text-sm text-muted-foreground">
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
            <section className="space-y-2">
              <div className="text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
                Attachments
              </div>
              {evidence.attachments.map((attachment) => (
                <Card
                  key={attachment.attachmentId}
                  className="gap-3 rounded-2xl border-border-subtle/80 shadow-none"
                >
                  <CardHeader className="px-4 pt-4">
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
                      <div className="flex flex-wrap gap-1.5">
                        {attachment.mimeType || attachment.mediaType ? (
                          <Badge variant="secondary" className="rounded-full">
                            {attachment.mimeType ?? attachment.mediaType}
                          </Badge>
                        ) : null}
                        {attachment.sizeBytes != null ? (
                          <Badge variant="outline" className="rounded-full">
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
            <Card className="gap-3 rounded-2xl border-border-subtle/80 shadow-none">
              <CardHeader className="px-4 pt-4">
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
