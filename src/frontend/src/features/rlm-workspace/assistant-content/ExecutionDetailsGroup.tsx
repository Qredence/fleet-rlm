import type { ReactNode } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Streamdown } from "@/components/ui/streamdown";
import {
  Queue,
  QueueItem,
  QueueItemContent,
  QueueItemDescription,
  QueueItemIndicator,
  QueueList,
  QueueSection,
  QueueSectionContent,
  QueueSectionLabel,
  QueueSectionTrigger,
} from "@/components/ui/queue";
import {
  Task,
  TaskContent,
  TaskItem,
  TaskItemFile,
  TaskTrigger,
} from "@/components/ai-elements/task";
import {
  Tool,
  ToolContent,
  ToolHeader,
  ToolInput,
  ToolOutput,
} from "@/components/ai-elements/tool";
import {
  Sandbox,
  SandboxContent,
  SandboxHeader,
  SandboxTabContent,
  SandboxTabs,
  SandboxTabsBar,
  SandboxTabsList,
  SandboxTabsTrigger,
} from "@/components/ai-elements/sandbox";
import {
  EnvironmentVariable,
  EnvironmentVariableCopyButton,
  EnvironmentVariableGroup,
  EnvironmentVariableName,
  EnvironmentVariableRequired,
  EnvironmentVariables,
  EnvironmentVariablesContent,
  EnvironmentVariablesHeader,
  EnvironmentVariablesTitle,
  EnvironmentVariablesToggle,
  EnvironmentVariableValue,
} from "@/components/ai-elements/environment-variables";
import type { ChatRenderToolState } from "@/lib/data/types";
import { cn } from "@/lib/utils/cn";
import { mapToolState } from "@/lib/utils/ai-elements-state";
import { inspectorStyles } from "@/features/rlm-workspace/shared/inspector-styles";
import { RuntimeContextBadge } from "@/features/rlm-workspace/assistant-content/runtimeBadges";
import type {
  AssistantContentModel,
  ExecutionSection,
  ToolSessionItem,
} from "@/features/rlm-workspace/assistant-content/types";

const MONO_BASE_STYLE = {
  fontSize: "var(--font-text-sm-size)",
  fontWeight: "var(--font-text-sm-weight)",
  fontFamily: "var(--font-mono)",
  lineHeight: "var(--font-text-sm-line-height)",
  letterSpacing: "var(--font-text-sm-tracking)",
} as const;

const MONO_BASE_MEDIUM_STYLE = {
  ...MONO_BASE_STYLE,
  fontWeight: "var(--font-weight-medium)",
} as const;

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

function toolSessionStateForItem(item: ToolSessionItem): ChatRenderToolState {
  if (item.part.kind === "tool" || item.part.kind === "sandbox") {
    return item.part.state;
  }
  if (item.part.kind === "environment_variables") return "output-available";
  if (
    item.part.tone === "error" ||
    /(error|failed|failure|rejected|unable)/i.test(item.part.text)
  ) {
    return "output-error";
  }
  if (
    item.part.tone === "success" ||
    /(done|complete|completed|finished|success)/i.test(item.part.text)
  ) {
    return "output-available";
  }
  return "running";
}

function toolSessionLine(item: ToolSessionItem) {
  if (item.part.kind === "status_note") {
    return `Status: ${item.part.text}`;
  }
  const toolName = item.toolName ?? "tool";
  return `${item.eventKind}: ${toolName}`;
}

function renderToolSessionItemDetails(item: ToolSessionItem): ReactNode {
  if (item.part.kind === "tool") {
    const outputText = stringifyValue(item.part.output).trim();
    const hasOutput = Boolean(item.part.errorText || outputText);

    return (
      <div className="space-y-2">
        <RuntimeContextBadge ctx={item.runtimeContext} />
        {item.part.input != null ? <ToolInput input={item.part.input} /> : null}
        {hasOutput ? (
          <ToolOutput
            errorText={item.part.errorText}
            output={
              item.part.errorText ? undefined : (
                <div className="w-full">
                  <Streamdown content={outputText} streaming={false} />
                </div>
              )
            }
          />
        ) : null}
      </div>
    );
  }

  if (item.part.kind === "sandbox") {
    return (
      <div className="space-y-2">
        <RuntimeContextBadge ctx={item.runtimeContext} />
        {item.part.errorText || item.part.output ? (
          <div className="space-y-1">
            <div className={inspectorStyles.heading.detail}>Output</div>
            <div
              className={cn(
                "rounded-md border px-2.5 py-2 text-foreground",
                item.part.errorText
                  ? "border-destructive/25 bg-destructive/5 text-destructive"
                  : "border-border-subtle/80 bg-muted/15",
                "typo-label-regular",
              )}
            >
              {item.part.errorText ? (
                item.part.errorText
              ) : (
                <Streamdown content={item.part.output ?? ""} streaming={false} />
              )}
            </div>
          </div>
        ) : null}
        {item.part.code ? (
          <div className="space-y-1">
            <div className={inspectorStyles.heading.detail}>Code</div>
            <pre
              className="overflow-x-auto rounded-md border-subtle/80 bg-muted/20 px-2.5 py-2 text-foreground"
              style={MONO_BASE_STYLE}
            >
              <code>{item.part.code}</code>
            </pre>
          </div>
        ) : null}
      </div>
    );
  }

  if (item.part.kind === "environment_variables") {
    return (
      <div className="rounded-md border-subtle/80 bg-muted/15">
        {item.part.variables.map((variable, idx) => (
          <div
            key={`${item.key}-env-${variable.name}-${idx}`}
            className={cn(
              "flex flex-col gap-1 px-2.5 py-2 text-foreground",
              idx > 0 && "border-t border-border-subtle/70",
            )}
          >
            <div className="flex items-center gap-2">
              <span className="text-foreground" style={MONO_BASE_MEDIUM_STYLE}>
                {variable.name}
              </span>
              {variable.required ? (
                <span className={inspectorStyles.heading.detail}>required</span>
              ) : null}
            </div>
            <span className="text-muted-foreground" style={MONO_BASE_STYLE}>
              {variable.value}
            </span>
          </div>
        ))}
      </div>
    );
  }

  return <RuntimeContextBadge ctx={item.runtimeContext} />;
}

function renderExecutionSection(section: ExecutionSection) {
  if (section.kind === "tool_session") {
    const latestItem = section.session.items[section.session.items.length - 1];
    const latestState = latestItem ? toolSessionStateForItem(latestItem) : ("running" as const);

    return (
      <Tool key={section.id} defaultOpen={section.defaultOpen}>
        <ToolHeader type="tool-default" state={mapToolState(latestState)} title={section.label} />
        <ToolContent className="space-y-3">
          <div className="text-xs text-muted-foreground">{section.summary}</div>
          {section.runtimeBadges.length ? (
            <div className={inspectorStyles.runtime.inline}>
              {section.runtimeBadges.join(" · ")}
            </div>
          ) : null}
          {section.session.items.map((sessionItem) => (
            <div
              key={sessionItem.key}
              className="border-l border-border-subtle/70 pl-3"
              data-slot="execution-tool-session-item"
            >
              <div className="space-y-2 py-0.5">
                <div className="text-foreground typo-label-regular">
                  {toolSessionLine(sessionItem)}
                </div>
                {renderToolSessionItemDetails(sessionItem)}
              </div>
            </div>
          ))}
        </ToolContent>
      </Tool>
    );
  }

  switch (section.kind) {
    case "task":
      return (
        <Task key={section.id} defaultOpen={section.defaultOpen}>
          <TaskTrigger title={section.label} />
          <TaskContent>
            <div className="space-y-2">
              <div className="text-xs text-muted-foreground">{section.summary}</div>
              {section.part.items?.length ? (
                <div className="space-y-1">
                  {section.part.items.map((item) => (
                    <TaskItem key={item.id}>
                      <span>{item.text}</span>
                      {item.file ? (
                        <TaskItemFile className="ml-2">{item.file.name}</TaskItemFile>
                      ) : null}
                    </TaskItem>
                  ))}
                </div>
              ) : (
                <TaskItem>No additional details</TaskItem>
              )}
            </div>
          </TaskContent>
        </Task>
      );
    case "queue":
      return (
        <Queue key={section.id}>
          <QueueSection defaultOpen>
            <QueueSectionTrigger>
              <QueueSectionLabel label={section.label} count={section.part.items.length} />
            </QueueSectionTrigger>
            <QueueSectionContent>
              <div className="space-y-2">
                <div className="text-xs text-muted-foreground">{section.summary}</div>
                <QueueList>
                  {section.part.items.map((item) => (
                    <QueueItem key={item.id}>
                      <QueueItemIndicator completed={item.completed} />
                      <QueueItemContent completed={item.completed}>{item.label}</QueueItemContent>
                      {item.description ? (
                        <QueueItemDescription completed={item.completed}>
                          {item.description}
                        </QueueItemDescription>
                      ) : null}
                    </QueueItem>
                  ))}
                </QueueList>
              </div>
            </QueueSectionContent>
          </QueueSection>
        </Queue>
      );
    case "tool": {
      const outputText = stringifyValue(section.part.output);
      return (
        <Tool key={section.id} defaultOpen={section.defaultOpen}>
          <ToolHeader
            type={`tool-${section.part.toolType}`}
            state={mapToolState(section.part.state)}
            title={section.label}
          />
          <ToolContent>
            <div className="space-y-2">
              <div className="text-xs text-muted-foreground">{section.summary}</div>
              <RuntimeContextBadge ctx={section.part.runtimeContext} />
              {section.part.input != null ? <ToolInput input={section.part.input} /> : null}
              <ToolOutput
                errorText={section.part.errorText}
                output={
                  section.part.errorText ? undefined : outputText ? (
                    <div className="w-full">
                      <Streamdown content={outputText} streaming={false} />
                    </div>
                  ) : (
                    <span className="text-muted-foreground">No output</span>
                  )
                }
              />
            </div>
          </ToolContent>
        </Tool>
      );
    }
    case "sandbox": {
      const code = section.part.code ?? "";
      const output = section.part.output ?? "";
      return (
        <Sandbox key={section.id} defaultOpen={section.defaultOpen}>
          <SandboxHeader title={section.label} state={mapToolState(section.part.state)} />
          <SandboxContent>
            <div className="space-y-2 px-2.5 py-1.5">
              <div className="text-xs text-muted-foreground">{section.summary}</div>
              <RuntimeContextBadge ctx={section.part.runtimeContext} />
            </div>
            <SandboxTabs defaultValue="output">
              <SandboxTabsBar>
                <SandboxTabsList>
                  <SandboxTabsTrigger value="output">Output</SandboxTabsTrigger>
                  <SandboxTabsTrigger value="code">Code</SandboxTabsTrigger>
                </SandboxTabsList>
              </SandboxTabsBar>
              <SandboxTabContent value="output">
                {section.part.errorText ? (
                  <div className="rounded-md border border-destructive/30 bg-destructive/5 p-2 text-destructive typo-label-regular">
                    {section.part.errorText}
                  </div>
                ) : output ? (
                  <Streamdown content={output} streaming={false} />
                ) : (
                  <div className="text-muted-foreground typo-label-regular">No output yet</div>
                )}
              </SandboxTabContent>
              <SandboxTabContent value="code">
                {code ? (
                  <pre
                    className="overflow-x-auto rounded-md border-subtle bg-muted/30 p-2"
                    style={MONO_BASE_STYLE}
                  >
                    <code>{code}</code>
                  </pre>
                ) : (
                  <div className="text-muted-foreground typo-label-regular">No code captured</div>
                )}
              </SandboxTabContent>
            </SandboxTabs>
          </SandboxContent>
        </Sandbox>
      );
    }
    case "environment_variables":
      return (
        <EnvironmentVariables key={section.id} defaultShowValues={false}>
          <EnvironmentVariablesHeader>
            <EnvironmentVariablesTitle>{section.label}</EnvironmentVariablesTitle>
            <EnvironmentVariablesToggle />
          </EnvironmentVariablesHeader>
          <EnvironmentVariablesContent>
            <div className="mb-2 text-xs text-muted-foreground">{section.summary}</div>
            {section.part.variables.map((variable, idx) => (
              <EnvironmentVariable
                key={`${variable.name}-${idx}`}
                name={variable.name}
                value={variable.value}
              >
                <EnvironmentVariableGroup>
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <EnvironmentVariableName />
                      {variable.required ? <EnvironmentVariableRequired /> : null}
                    </div>
                    <EnvironmentVariableValue />
                  </div>
                  <EnvironmentVariableCopyButton aria-label={`Copy ${variable.name}`} />
                </EnvironmentVariableGroup>
              </EnvironmentVariable>
            ))}
          </EnvironmentVariablesContent>
        </EnvironmentVariables>
      );
    case "status_note":
      return (
        <Alert
          key={section.id}
          variant={section.part.tone === "error" ? "destructive" : "default"}
          className={cn(
            "px-3 py-2.5",
            section.part.tone === "warning" && "border-accent/25 bg-accent/5 text-foreground",
            section.part.tone === "success" && "border-primary/25 bg-primary/5 text-foreground",
            (!section.part.tone || section.part.tone === "neutral") &&
              "border-border-subtle/80 bg-muted/20 text-muted-foreground",
          )}
        >
          <AlertDescription>
            <div className="space-y-1">
              <div className="typo-label-regular">{section.summary}</div>
              <RuntimeContextBadge ctx={section.part.runtimeContext} />
            </div>
          </AlertDescription>
        </Alert>
      );
  }
}

export function ExecutionDetailsGroup({
  execution,
}: {
  execution: AssistantContentModel["execution"];
}) {
  if (!execution.hasContent) return null;

  return (
    <section className="space-y-3" data-slot="assistant-execution">
      <div className={inspectorStyles.heading.section}>Execution</div>
      <div className="space-y-3">
        {execution.sections.map((section) => (
          <div key={section.id} data-slot={`execution-section-${section.kind}`}>
            {renderExecutionSection(section)}
          </div>
        ))}
      </div>
    </section>
  );
}
