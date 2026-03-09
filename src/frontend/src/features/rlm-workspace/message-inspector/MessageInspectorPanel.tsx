import { useEffect, useMemo, useState } from "react";
import { ExternalLink, GitBranch } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Streamdown } from "@/components/ui/streamdown";
import { buildChatDisplayItems } from "@/features/rlm-workspace/chatDisplayItems";
import { buildAssistantContentModel } from "@/features/rlm-workspace/assistant-content/buildAssistantContentModel";
import type {
  AssistantContentModel,
  ExecutionSection,
  ToolSessionItem,
} from "@/features/rlm-workspace/assistant-content/types";
import { useChatStore } from "@/stores/chatStore";
import { useNavigationStore } from "@/stores/navigationStore";
import type { InspectorTab, RuntimeContext } from "@/lib/data/types";
import { ArtifactGraph } from "@/components/domain/artifacts/ArtifactGraph";
import { summarizeArtifactStep } from "@/components/domain/artifacts/parsers/artifactPayloadSummaries";
import type { ExecutionStep } from "@/stores/artifactStore";
import { cn } from "@/lib/utils/cn";

type TabOption = {
  id: InspectorTab;
  label: string;
};

function EmptyInspectorState({
  hasAssistantTurns,
}: {
  hasAssistantTurns: boolean;
}) {
  return (
    <div className="flex h-full items-center justify-center px-4 py-6">
      <Card className="w-full max-w-md border-border-subtle/80 bg-card/70 shadow-none">
        <CardHeader>
          <CardTitle>Message Inspector</CardTitle>
          <CardDescription>
            {hasAssistantTurns
              ? "Select an assistant response in the chat to inspect its trajectory, execution details, evidence, and relationships."
              : "Send a message to populate the inspector with assistant-turn details."}
          </CardDescription>
        </CardHeader>
      </Card>
    </div>
  );
}

function runtimeContextStrings(ctx?: RuntimeContext): string[] {
  if (!ctx) return [];
  const pills: string[] = [];
  if (ctx.depth > 0) pills.push(`depth ${ctx.depth}/${ctx.maxDepth}`);
  if (ctx.executionMode && ctx.executionMode !== "react") {
    pills.push(`mode ${ctx.executionMode}`);
  }
  if (ctx.sandboxActive) pills.push("sandbox");
  if (ctx.executionProfile !== "ROOT_INTERLOCUTOR") {
    pills.push(ctx.executionProfile.toLowerCase().replace(/_/g, " "));
  }
  if (ctx.volumeName) pills.push(ctx.volumeName);
  return pills;
}

function renderBadges(
  values: string[],
  variant: "outline" | "secondary" | "warning" | "success" | "accent" = "outline",
) {
  if (values.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {values.map((value) => (
        <Badge
          key={value}
          variant={variant}
          className="rounded-full text-[10px] font-medium"
        >
          {value}
        </Badge>
      ))}
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

function stringifyValue(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function executionSectionState(
  section: ExecutionSection,
): "pending" | "running" | "completed" | "failed" {
  if (section.kind === "tool_session") {
    const latest = section.session.items[section.session.items.length - 1];
    if (!latest) return "running";
    return toolSessionItemState(latest);
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

  if ("errorText" in section.part && section.part.errorText) {
    return "failed";
  }
  if (
    "state" in section.part &&
    (section.part.state === "running" || section.part.state === "input-streaming")
  ) {
    return "running";
  }
  return "completed";
}

function toolSessionItemState(
  item: ToolSessionItem,
): "pending" | "running" | "completed" | "failed" {
  if (item.part.kind === "tool" || item.part.kind === "sandbox") {
    if (item.part.errorText) return "failed";
    return item.part.state === "running" || item.part.state === "input-streaming"
      ? "running"
      : "completed";
  }
  if (item.part.kind === "status_note") {
    if (item.part.tone === "error") return "failed";
    if (item.part.tone === "warning") return "running";
  }
  return "completed";
}

function sectionGroups(sections: ExecutionSection[]) {
  return [
    {
      key: "tool-sessions",
      label: "Tool sessions",
      sections: sections.filter(
        (section) =>
          section.kind === "tool_session" ||
          section.kind === "tool" ||
          section.kind === "task" ||
          section.kind === "queue",
      ),
    },
    {
      key: "sandbox-runs",
      label: "Sandbox runs",
      sections: sections.filter((section) => section.kind === "sandbox"),
    },
    {
      key: "environment",
      label: "Environment",
      sections: sections.filter(
        (section) => section.kind === "environment_variables",
      ),
    },
    {
      key: "errors",
      label: "Errors / warnings",
      sections: sections.filter((section) => section.kind === "status_note"),
    },
  ].filter((group) => group.sections.length > 0);
}

function hasMeaningfulGraph(steps: ExecutionStep[]) {
  if (steps.length < 2) return false;

  const lanes = new Set(
    steps
      .map((step) => step.lane_key ?? `${step.actor_kind ?? "unknown"}:${step.actor_id ?? ""}`)
      .filter(Boolean),
  );
  if (lanes.size > 1) return true;

  if (steps.some((step) => step.actor_kind === "delegate" || step.actor_kind === "sub_agent")) {
    return true;
  }

  const childCounts = new Map<string, number>();
  for (const step of steps) {
    if (!step.parent_id) continue;
    childCounts.set(step.parent_id, (childCounts.get(step.parent_id) ?? 0) + 1);
  }

  return [...childCounts.values()].some((count) => count > 1);
}

function selectedTurnStatus(
  model: AssistantContentModel,
): "pending" | "running" | "completed" | "failed" {
  if (
    model.execution.sections.some(
      (section) => executionSectionState(section) === "failed",
    )
  ) {
    return "failed";
  }
  if (model.trajectory.items.some((item) => item.status === "failed")) {
    return "failed";
  }
  if (
    model.answer.showStreamingShell ||
    model.execution.sections.some((section) => {
      const state = executionSectionState(section);
      return state === "pending" || state === "running";
    }) ||
    model.trajectory.items.some(
      (item) => item.status === "pending" || item.status === "running",
    ) ||
    model.trajectory.overview?.isStreaming
  ) {
    return "running";
  }
  return "completed";
}

function selectedTurnDescription(model: AssistantContentModel) {
  if (model.answer.showStreamingShell) {
    return "Live trajectory and execution details for the in-progress assistant turn.";
  }
  if (model.answer.hasContent) {
    return "Expanded trajectory, execution details, evidence, and relationships for this assistant response.";
  }
  return "Expanded trajectory, execution details, evidence, and relationships for this assistant turn.";
}

function ExternalAnchor({
  href,
  children,
}: {
  href?: string;
  children: React.ReactNode;
}) {
  if (!href) {
    return <span className="text-foreground">{children}</span>;
  }
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="inline-flex items-center gap-1 text-foreground hover:text-accent"
    >
      <span>{children}</span>
      <ExternalLink className="size-3.5" />
    </a>
  );
}

function DetailBlock({
  label,
  value,
  tone = "default",
}: {
  label: string;
  value?: string;
  tone?: "default" | "error";
}) {
  if (!value) return null;
  return (
    <div className="space-y-1.5">
      <div className="text-[10px] uppercase tracking-[0.16em] text-muted-foreground">
        {label}
      </div>
      <div
        className={cn(
          "rounded-xl border px-3 py-2 text-sm",
          tone === "error"
            ? "border-destructive/20 bg-destructive/5 text-destructive"
            : "border-border-subtle/80 bg-muted/25 text-foreground",
        )}
      >
        <Streamdown content={value} streaming={false} />
      </div>
    </div>
  );
}

function ToolSessionDetails({ sessionItems }: { sessionItems: ToolSessionItem[] }) {
  return (
    <div className="space-y-3">
      {sessionItems.map((item) => {
        const badges = runtimeContextStrings(item.runtimeContext);
        const state = toolSessionItemState(item);
        const tone = statusTone(state);
        const outputValue =
          item.part.kind === "tool"
            ? stringifyValue(item.part.output)
            : item.part.kind === "sandbox"
              ? item.part.output
              : "";
        const inputValue =
          item.part.kind === "tool"
            ? stringifyValue(item.part.input)
            : "";
        const codeValue = item.part.kind === "sandbox" ? item.part.code : "";

        return (
          <Card key={item.key} className="gap-3 rounded-2xl border-border-subtle/80 shadow-none">
            <CardHeader className="px-4 pt-4">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="space-y-1">
                  <CardTitle className="text-sm font-medium text-foreground">
                    {item.part.kind === "status_note"
                      ? `Status: ${item.part.text}`
                      : `${item.eventKind.replace("_", " ")}: ${item.toolName ?? "tool"}`}
                  </CardTitle>
                </div>
                <Badge variant={tone.variant} className="rounded-full">
                  {tone.label}
                </Badge>
              </div>
              {renderBadges(badges)}
            </CardHeader>
            <CardContent className="space-y-3 px-4 pb-4">
              <DetailBlock label="Input" value={inputValue} />
              <DetailBlock
                label="Output"
                value={outputValue}
                tone={item.part.kind !== "sandbox" ? "default" : item.part.errorText ? "error" : "default"}
              />
              <DetailBlock
                label="Error"
                value={
                  item.part.kind === "tool" || item.part.kind === "sandbox"
                    ? item.part.errorText
                    : undefined
                }
                tone="error"
              />
              <DetailBlock label="Code" value={codeValue} />
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}

function renderExecutionSectionDetails(section: ExecutionSection) {
  switch (section.kind) {
    case "tool_session":
      return <ToolSessionDetails sessionItems={section.session.items} />;
    case "queue":
      return (
        <div className="space-y-2">
          {section.part.items.map((item) => (
            <div
              key={item.id}
              className="rounded-xl border border-border-subtle/80 bg-muted/20 px-3 py-2"
            >
              <div className="text-sm font-medium text-foreground">{item.label}</div>
              {item.description ? (
                <div className="mt-1 text-sm text-muted-foreground">
                  {item.description}
                </div>
              ) : null}
            </div>
          ))}
        </div>
      );
    case "task":
      return (
        <div className="space-y-2">
          {(section.part.items ?? []).map((item) => (
            <div
              key={item.id}
              className="rounded-xl border border-border-subtle/80 bg-muted/20 px-3 py-2"
            >
              <div className="text-sm text-foreground">{item.text}</div>
            </div>
          ))}
        </div>
      );
    case "tool":
      return (
        <div className="space-y-3">
          <DetailBlock label="Input" value={stringifyValue(section.part.input)} />
          <DetailBlock
            label="Output"
            value={stringifyValue(section.part.output)}
            tone={section.part.errorText ? "error" : "default"}
          />
          <DetailBlock label="Error" value={section.part.errorText} tone="error" />
          {renderBadges(runtimeContextStrings(section.part.runtimeContext))}
        </div>
      );
    case "sandbox":
      return (
        <div className="space-y-3">
          <DetailBlock label="Code" value={section.part.code} />
          <DetailBlock
            label="Output"
            value={section.part.output}
            tone={section.part.errorText ? "error" : "default"}
          />
          <DetailBlock label="Error" value={section.part.errorText} tone="error" />
          {renderBadges(runtimeContextStrings(section.part.runtimeContext))}
        </div>
      );
    case "environment_variables":
      return (
        <div className="space-y-2">
          {section.part.variables.map((variable) => (
            <div
              key={variable.name}
              className="rounded-xl border border-border-subtle/80 bg-muted/20 px-3 py-2"
            >
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-foreground">
                  {variable.name}
                </span>
                {variable.required ? (
                  <Badge variant="secondary" className="rounded-full">
                    required
                  </Badge>
                ) : null}
              </div>
              <div className="mt-1 text-sm text-muted-foreground">
                {variable.value}
              </div>
            </div>
          ))}
        </div>
      );
    case "status_note":
      return (
        <div className="space-y-3">
          <DetailBlock
            label="Status"
            value={section.part.text}
            tone={section.part.tone === "error" ? "error" : "default"}
          />
          {renderBadges(runtimeContextStrings(section.part.runtimeContext))}
        </div>
      );
  }
}

function TrajectoryInspectorTab({ model }: { model: AssistantContentModel }) {
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
                    <CardTitle className="text-sm font-medium">
                      Planning
                    </CardTitle>
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
                <CardTitle className="text-sm font-medium">No trajectory recorded</CardTitle>
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
}

function ExecutionInspectorTab({ model }: { model: AssistantContentModel }) {
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
}

function EvidenceInspectorTab({ model }: { model: AssistantContentModel }) {
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
}

function GraphInspectorTab({ steps }: { steps: ExecutionStep[] }) {
  const [activeStepId, setActiveStepId] = useState<string | undefined>(
    steps[steps.length - 1]?.id,
  );

  useEffect(() => {
    setActiveStepId((current) => {
      if (current && steps.some((step) => step.id === current)) {
        return current;
      }
      return steps[steps.length - 1]?.id;
    });
  }, [steps]);

  const selectedStep = steps.find((step) => step.id === activeStepId) ?? steps[steps.length - 1];
  const laneCount = new Set(
    steps
      .map((step) => step.lane_key ?? `${step.actor_kind ?? "unknown"}:${step.actor_id ?? ""}`)
      .filter(Boolean),
  ).size;
  const branchingParents = new Set(
    steps
      .map((step) => step.parent_id)
      .filter((parentId, index, all) => parentId && all.indexOf(parentId) !== index),
  ).size;

  return (
    <TabsContent value="graph" className="min-h-0 flex-1">
      <ScrollArea className="h-full">
        <div className="space-y-3 px-4 pb-4">
          <div className="grid gap-3 md:grid-cols-3">
            <Card className="gap-2 rounded-2xl border-border-subtle/80 shadow-none">
              <CardHeader className="px-4 pt-4">
                <CardDescription>Steps</CardDescription>
                <CardTitle className="text-xl font-semibold text-foreground">
                  {steps.length}
                </CardTitle>
              </CardHeader>
            </Card>
            <Card className="gap-2 rounded-2xl border-border-subtle/80 shadow-none">
              <CardHeader className="px-4 pt-4">
                <CardDescription>Execution lanes</CardDescription>
                <CardTitle className="text-xl font-semibold text-foreground">
                  {laneCount}
                </CardTitle>
              </CardHeader>
            </Card>
            <Card className="gap-2 rounded-2xl border-border-subtle/80 shadow-none">
              <CardHeader className="px-4 pt-4">
                <CardDescription>Branch points</CardDescription>
                <CardTitle className="text-xl font-semibold text-foreground">
                  {branchingParents}
                </CardTitle>
              </CardHeader>
            </Card>
          </div>

          <Card className="gap-3 rounded-2xl border-border-subtle/80 shadow-none">
            <CardHeader className="px-4 pt-4">
              <div className="flex items-center gap-2">
                <GitBranch className="size-4 text-accent" />
                <CardTitle className="text-sm font-medium text-foreground">
                  Relationships
                </CardTitle>
              </div>
              <CardDescription>
                Parent-child lineage, actor lanes, and delegated branches for this turn.
              </CardDescription>
            </CardHeader>
            <CardContent className="px-4 pb-4">
              <div className="h-[420px] overflow-hidden rounded-2xl border border-border-subtle/80 bg-muted/15">
                <ArtifactGraph
                  steps={steps}
                  activeStepId={activeStepId}
                  onSelectStep={(stepId) => setActiveStepId(stepId)}
                  isVisible
                />
              </div>
            </CardContent>
          </Card>

          {selectedStep ? (
            <Card className="gap-3 rounded-2xl border-border-subtle/80 shadow-none">
              <CardHeader className="px-4 pt-4">
                <CardTitle className="text-sm font-medium text-foreground">
                  Selected node
                </CardTitle>
                <CardDescription>{selectedStep.label}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3 px-4 pb-4">
                <div className="text-sm text-foreground">
                  {summarizeArtifactStep(selectedStep)}
                </div>
                <div className="flex flex-wrap gap-1.5">
                  <Badge variant="secondary" className="rounded-full capitalize">
                    {selectedStep.type}
                  </Badge>
                  {selectedStep.actor_kind ? (
                    <Badge variant="outline" className="rounded-full">
                      {selectedStep.actor_kind.replace(/_/g, " ")}
                    </Badge>
                  ) : null}
                  {selectedStep.actor_id ? (
                    <Badge variant="outline" className="rounded-full">
                      {selectedStep.actor_id}
                    </Badge>
                  ) : null}
                </div>
                <DetailBlock label="Input" value={stringifyValue(selectedStep.input)} />
                <DetailBlock label="Output" value={stringifyValue(selectedStep.output)} />
              </CardContent>
            </Card>
          ) : null}
        </div>
      </ScrollArea>
    </TabsContent>
  );
}

export function MessageInspectorPanel() {
  const messages = useChatStore((state) => state.messages);
  const isStreaming = useChatStore((state) => state.isStreaming);
  const turnArtifactsByMessageId = useChatStore(
    (state) => state.turnArtifactsByMessageId,
  );
  const {
    selectedAssistantTurnId,
    activeInspectorTab,
    setInspectorTab,
  } = useNavigationStore();

  const assistantTurns = useMemo(
    () =>
      buildChatDisplayItems(messages, {
        showPendingAssistantShell: isStreaming,
      }).flatMap((item) =>
        item.kind === "assistant_turn" ? [item] : [],
      ),
    [isStreaming, messages],
  );

  const selectedTurn =
    assistantTurns.find((item) => item.turnId === selectedAssistantTurnId) ??
    null;
  const model = useMemo(
    () => (selectedTurn ? buildAssistantContentModel(selectedTurn) : null),
    [selectedTurn],
  );
  const graphSteps = selectedTurn
    ? turnArtifactsByMessageId[selectedTurn.turnId] ?? []
    : [];
  const showGraph = hasMeaningfulGraph(graphSteps);

  const tabs = useMemo<TabOption[]>(() => {
    if (!model) return [];
    return [
      { id: "trajectory", label: "Trajectory" },
      ...(model.execution.hasContent
        ? ([{ id: "execution", label: "Execution" }] as TabOption[])
        : []),
      ...(model.evidence.hasContent
        ? ([{ id: "evidence", label: "Evidence" }] as TabOption[])
        : []),
      ...(showGraph ? ([{ id: "graph", label: "Graph" }] as TabOption[]) : []),
    ];
  }, [model, showGraph]);

  useEffect(() => {
    if (!tabs.some((tab) => tab.id === activeInspectorTab)) {
      setInspectorTab("trajectory");
    }
  }, [activeInspectorTab, setInspectorTab, tabs]);

  if (!selectedTurn || !model) {
    return <EmptyInspectorState hasAssistantTurns={assistantTurns.length > 0} />;
  }

  const currentTab =
    tabs.find((tab) => tab.id === activeInspectorTab)?.id ?? "trajectory";
  const turnStatus = statusTone(selectedTurnStatus(model));
  const summaryBadges = [
    ...model.summary.runtimeBadges,
    ...(model.summary.sandboxActive ? ["sandbox active"] : []),
  ];

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="px-4 py-4">
        <Card className="gap-3 rounded-2xl border-border-subtle/80 bg-card/70 shadow-none">
          <CardHeader className="space-y-3 px-4 pt-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="space-y-1">
                <div className="text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
                  Message Inspector
                </div>
                <CardTitle className="text-sm font-medium text-foreground">
                  {selectedTurn.isPendingShell
                    ? "Assistant turn in progress"
                    : "Selected assistant turn"}
                </CardTitle>
                <CardDescription className="max-w-prose text-sm leading-6">
                  {selectedTurnDescription(model)}
                </CardDescription>
              </div>
              <Badge variant={turnStatus.variant} className="rounded-full">
                {turnStatus.label}
              </Badge>
            </div>

            <div className="flex flex-wrap gap-1.5">
              {model.summary.trajectoryCount > 0 ? (
                <Badge variant="secondary" className="rounded-full">
                  {model.summary.trajectoryCount} trajector
                  {model.summary.trajectoryCount === 1 ? "y" : "ies"}
                </Badge>
              ) : null}
              {model.summary.toolSessionCount > 0 ? (
                <Badge variant="secondary" className="rounded-full">
                  {model.summary.toolSessionCount} tool session
                  {model.summary.toolSessionCount === 1 ? "" : "s"}
                </Badge>
              ) : null}
              {model.summary.sourceCount > 0 ? (
                <Badge variant="secondary" className="rounded-full">
                  {model.summary.sourceCount} source
                  {model.summary.sourceCount === 1 ? "" : "s"}
                </Badge>
              ) : null}
              {model.summary.attachmentCount > 0 ? (
                <Badge variant="secondary" className="rounded-full">
                  {model.summary.attachmentCount} attachment
                  {model.summary.attachmentCount === 1 ? "" : "s"}
                </Badge>
              ) : null}
            </div>

            {summaryBadges.length > 0 ? renderBadges(summaryBadges) : null}
          </CardHeader>
        </Card>
      </div>

      <Separator className="bg-border-subtle/70" />

      <Tabs
        value={currentTab}
        onValueChange={(value) => setInspectorTab(value as InspectorTab)}
        className="min-h-0 flex-1 gap-0"
      >
        <div className="px-4 py-3">
          <TabsList className="flex w-full">
            {tabs.map((tab) => (
              <TabsTrigger key={tab.id} value={tab.id}>
                {tab.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </div>

        <Separator className="bg-border-subtle/70" />

        <TrajectoryInspectorTab model={model} />
        {model.execution.hasContent ? (
          <ExecutionInspectorTab model={model} />
        ) : null}
        {model.evidence.hasContent ? <EvidenceInspectorTab model={model} /> : null}
        {showGraph ? <GraphInspectorTab steps={graphSteps} /> : null}
      </Tabs>
    </div>
  );
}
