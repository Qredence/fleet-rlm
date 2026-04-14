import type { ReactNode } from "react";
import {
  Tool,
  ToolContent,
  ToolHeader,
  ToolInput,
  ToolOutput,
} from "@/components/ai-elements/tool";
import { Message, MessageContent } from "@/components/ai-elements/message";
import { CodeBlock, CodeBlockCode } from "@/components/ui/code-block";
import { Streamdown } from "@/components/ui/streamdown";
import { cn } from "@/lib/utils";
import { mapToolState } from "@/lib/utils/prompt-kit-state";
import { RuntimeContextBadge } from "@/features/workspace/ui/assistant-content/model";
import type { ToolSessionItem } from "@/lib/workspace/chat-display-items";
import type { TraceDisplayItem } from "@/lib/workspace/chat-display-items";
import type { ChatRenderPart, ChatRenderToolState } from "@/features/workspace/use-workspace";

type ToolSessionDisplayItem = Extract<TraceDisplayItem, { kind: "tool_session" }>;

export function stringifyValue(value: unknown): string {
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

export function shouldOpenToolRow(
  state: Extract<ChatRenderPart, { kind: "tool" | "sandbox" }>["state"],
) {
  return state === "running" || state === "input-streaming" || state === "output-error";
}

export function shouldOpenTaskRow(status: Extract<ChatRenderPart, { kind: "task" }>["status"]) {
  return status === "in_progress" || status === "error";
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

function toolSessionHeaderLabel(items: ToolSessionItem[]) {
  const first = items[0];
  const toolName = first?.toolName ?? "Tool";
  return first?.eventKind === "tool_call" ? `Calling tool: ${toolName}` : `Tool: ${toolName}`;
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
      <div className="flex flex-col gap-2">
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
      <div className="flex flex-col gap-2">
        <RuntimeContextBadge ctx={item.runtimeContext} />
        {item.part.errorText || item.part.output ? (
          <div className="flex flex-col gap-1">
            <div className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
              Output
            </div>
            <div
              className={cn(
                "rounded-md border px-2.5 py-2 text-foreground typo-label-regular",
                item.part.errorText
                  ? "border-destructive/25 bg-destructive/5 text-destructive"
                  : "border-border-subtle/80 bg-muted/15",
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
          <div className="flex flex-col gap-1">
            <div className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
              Code
            </div>
            <CodeBlock className="border-subtle/80 bg-muted/20">
              <CodeBlockCode code={item.part.code} language="python" />
            </CodeBlock>
          </div>
        ) : null}
      </div>
    );
  }

  if (item.part.kind === "environment_variables") {
    return (
      <div className="rounded-md border-subtle/80 bg-muted/15">
        {item.part.variables.map((variable, index) => (
          <div
            key={`${item.key}-env-${variable.name}-${index}`}
            className={cn(
              "flex flex-col gap-1 px-2.5 py-2 text-foreground",
              index > 0 && "border-t border-border-subtle/70",
            )}
          >
            <div className="flex items-center gap-2">
              <span className="font-mono text-xs font-medium leading-5 text-foreground">
                {variable.name}
              </span>
              {variable.required ? (
                <span className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                  required
                </span>
              ) : null}
            </div>
            <span className="font-mono text-xs leading-5 text-muted-foreground">
              {variable.value}
            </span>
          </div>
        ))}
      </div>
    );
  }

  return <RuntimeContextBadge ctx={item.runtimeContext} />;
}

interface WorkspaceToolSessionMessageProps {
  item: ToolSessionDisplayItem;
}

export function WorkspaceToolSessionMessage({ item }: WorkspaceToolSessionMessageProps) {
  const fallbackState: ChatRenderToolState = "running";
  const latestItem = item.items[item.items.length - 1];
  const latestState = latestItem ? toolSessionStateForItem(latestItem) : fallbackState;

  return (
    <Message from="assistant" className="mb-4">
      <MessageContent className="w-full">
        <Tool defaultOpen={shouldOpenToolRow(latestState)}>
          <ToolHeader
            type="tool-default"
            state={mapToolState(latestState)}
            title={toolSessionHeaderLabel(item.items)}
          />
          <ToolContent className="flex flex-col gap-3">
            {item.items.map((sessionItem) => (
              <div
                key={sessionItem.key}
                className="border-l border-border-subtle/70 pl-3"
                data-slot="tool-session-item"
              >
                <div className="flex flex-col gap-2 py-0.5">
                  <div className="text-foreground typo-label-regular">
                    {toolSessionLine(sessionItem)}
                  </div>
                  {renderToolSessionItemDetails(sessionItem)}
                </div>
              </div>
            ))}
          </ToolContent>
        </Tool>
      </MessageContent>
    </Message>
  );
}
