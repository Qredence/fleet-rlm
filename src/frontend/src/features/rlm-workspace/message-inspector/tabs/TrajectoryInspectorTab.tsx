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
import { Streamdown } from "@/components/ui/streamdown";
import type { AssistantContentModel } from "@/features/rlm-workspace/assistant-content/types";
import { statusTone } from "../utils/inspector-utils";
import { renderBadges } from "../components/inspector-components";

export const TrajectoryInspectorTab = memo(function TrajectoryInspectorTab({
  model,
}: {
  model: AssistantContentModel;
}) {
  const items = model.trajectory.items;
  return (
    <TabsContent value="trajectory" className="min-h-0 flex-1">
      <ScrollArea className="h-full">
        <div className="space-y-3 px-4 pb-4">
          {model.trajectory.overview ? (
            <Card className="gap-3 rounded-2xl border-border-subtle/80 shadow-none">
              <CardHeader className="px-4 pt-4">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <CardTitle className="text-sm font-medium">Planning</CardTitle>
                    <CardDescription>
                      Overview of the reasoning path for this turn.
                    </CardDescription>
                  </div>
                  {model.trajectory.overview.duration != null ? (
                    <Badge variant="secondary" className="rounded-full">
                      {Math.round(model.trajectory.overview.duration)}s
                    </Badge>
                  ) : null}
                </div>
                {renderBadges(model.trajectory.overview.runtimeBadges)}
              </CardHeader>
              <CardContent className="px-4 pb-4">
                <div className="text-sm leading-6 text-foreground">
                  <Streamdown
                    content={model.trajectory.overview.text}
                    streaming={false}
                  />
                </div>
              </CardContent>
            </Card>
          ) : null}

          {items.length === 0 ? (
            <Card className="gap-3 rounded-2xl border-border-subtle/80 shadow-none">
              <CardHeader className="px-4 pt-4">
                <CardTitle className="text-sm font-medium">
                  No trajectory recorded
                </CardTitle>
                <CardDescription>
                  This turn does not include structured reasoning steps.
                </CardDescription>
              </CardHeader>
            </Card>
          ) : (
            items.map((item) => {
              const tone = statusTone(item.status);
              return (
                <Card
                  key={item.id}
                  className="gap-3 rounded-2xl border-border-subtle/80 shadow-none"
                >
                  <CardHeader className="px-4 pt-4">
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div>
                        <CardTitle className="text-sm font-medium">
                          {item.title}
                        </CardTitle>
                        {item.body ? (
                          <CardDescription>
                            Full reasoning for this trajectory step.
                          </CardDescription>
                        ) : null}
                      </div>
                      <Badge variant={tone.variant} className="rounded-full">
                        {tone.label}
                      </Badge>
                    </div>
                    {renderBadges(item.runtimeBadges)}
                  </CardHeader>
                  {item.body || item.details?.length ? (
                    <CardContent className="space-y-3 px-4 pb-4">
                      {item.body ? (
                        <div className="text-sm leading-6 text-foreground">
                          <Streamdown content={item.body} streaming={false} />
                        </div>
                      ) : null}
                      {!item.body && item.details?.length ? (
                        <div className="space-y-2">
                          {item.details.map((detail, index) => (
                            <div
                              key={`${item.id}-detail-${index}`}
                              className="rounded-xl border border-border-subtle/80 bg-muted/20 px-3 py-2 text-sm text-muted-foreground"
                            >
                              {detail}
                            </div>
                          ))}
                        </div>
                      ) : null}
                    </CardContent>
                  ) : null}
                </Card>
              );
            })
          )}
        </div>
      </ScrollArea>
    </TabsContent>
  );
});
