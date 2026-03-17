import { memo } from "react";
import { TabsContent } from "@/components/ui/tabs";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Card, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import type { AssistantContentModel } from "@/features/rlm-workspace/assistant-content/model";
import { inspectorStyles } from "@/features/rlm-workspace/shared/inspector-styles";
import {
  executionSectionState,
  renderBadges,
  renderExecutionSectionDetails,
  sectionGroups,
  statusTone,
} from "../ui/inspector-ui";

export const ExecutionInspectorTab = memo(function ExecutionInspectorTab({
  model,
}: {
  model: AssistantContentModel;
}) {
  const groups = sectionGroups(model.execution.sections);
  return (
    <TabsContent value="execution" className="min-h-0 flex-1">
      <ScrollArea className="h-full">
        <div className={inspectorStyles.tab.content}>
          {groups.map((group) => (
            <section key={group.key} className={inspectorStyles.stack.section}>
              <div className="flex items-center justify-between gap-2">
                <div className={inspectorStyles.heading.section}>{group.label}</div>
                <Badge variant="secondary" className={inspectorStyles.badge.meta}>
                  {group.sections.length}
                </Badge>
              </div>

              <div className={inspectorStyles.stack.cards}>
                {group.sections.map((section) => {
                  const tone = statusTone(executionSectionState(section));
                  return (
                    <Card key={section.id} className={inspectorStyles.card.root}>
                      <CardHeader className={inspectorStyles.card.header}>
                        <Accordion
                          type="single"
                          collapsible
                          defaultValue={section.defaultOpen ? "details" : undefined}
                        >
                          <AccordionItem value="details" className="border-b-0">
                            <AccordionTrigger className="py-0 hover:no-underline">
                              <div className="flex flex-1 flex-col gap-2 text-left">
                                <div className="flex flex-wrap items-start justify-between gap-2">
                                  <div>
                                    <CardTitle className="text-sm font-medium text-foreground">
                                      {section.label}
                                    </CardTitle>
                                    <CardDescription>{section.summary}</CardDescription>
                                  </div>
                                  <Badge
                                    variant={tone.variant}
                                    className={inspectorStyles.badge.status}
                                  >
                                    {tone.label}
                                  </Badge>
                                </div>
                                {renderBadges(section.runtimeBadges)}
                              </div>
                            </AccordionTrigger>
                            <AccordionContent className="pt-3">
                              {renderExecutionSectionDetails(section)}
                            </AccordionContent>
                          </AccordionItem>
                        </Accordion>
                      </CardHeader>
                    </Card>
                  );
                })}
              </div>
            </section>
          ))}
        </div>
      </ScrollArea>
    </TabsContent>
  );
});
