import { memo } from "react";
import { TabsContent } from "@/components/ui/tabs";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import type { AssistantContentModel } from "@/features/rlm-workspace/assistant-content/types";
import { sectionGroups, executionSectionState, statusTone } from "../utils/inspector-utils";
import { renderBadges, renderExecutionSectionDetails } from "../components/inspector-components";

export const ExecutionInspectorTab = memo(function ExecutionInspectorTab({ model }: { model: AssistantContentModel }) {
  const groups = sectionGroups(model.execution.sections);
  return (
    <TabsContent value="execution" className="min-h-0 flex-1">
      <ScrollArea className="h-full">
        <div className="space-y-4 px-4 pb-4">
          {groups.map((group) => (
            <section key={group.key} className="space-y-2">
              <div className="flex items-center justify-between gap-2">
                <div className="text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
                  {group.label}
                </div>
                <Badge variant="secondary" className="rounded-full">
                  {group.sections.length}
                </Badge>
              </div>

              <div className="space-y-3">
                {group.sections.map((section) => {
                  const tone = statusTone(executionSectionState(section));
                  return (
                    <Card
                      key={section.id}
                      className="gap-3 rounded-2xl border-border-subtle/80 shadow-none"
                    >
                      <CardHeader className="px-4 pt-4">
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
                                    className="rounded-full"
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
