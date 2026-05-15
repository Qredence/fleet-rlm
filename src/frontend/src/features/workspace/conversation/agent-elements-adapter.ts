/**
 * Adapter bridging fleet-rlm ChatRenderPart to agent-elements tool part format.
 *
 * Agent-elements tools accept a `part` prop shaped like an AI SDK tool
 * invocation: `{ toolCallId, state, input, output }`.
 * This module maps fleet-rlm's internal render-part types to that shape.
 */

import type { ChatRenderPart, ChatRenderToolState } from "@/lib/workspace/workspace-types";

/** The part shape agent-elements tool components accept. */
export interface AgentElementsToolPart {
  toolCallId: string;
  type: string;
  state: "input-streaming" | "call" | "output-available" | "output-error";
  input: Record<string, unknown>;
  output: Record<string, unknown> | undefined;
}

type ToolPart = Extract<ChatRenderPart, { kind: "tool" }>;
type SandboxPart = Extract<ChatRenderPart, { kind: "sandbox" }>;

function mapState(state: ChatRenderToolState): AgentElementsToolPart["state"] {
  switch (state) {
    case "input-streaming":
      return "input-streaming";
    case "running":
      return "call";
    case "output-available":
      return "output-available";
    case "output-error":
      return "output-error";
  }
}

/**
 * Which agent-elements tool component matches a given toolType?
 *
 * Returns the tool-type key used by `ToolRenderer` switch:
 * "Bash", "Edit", "Search", "Thinking", or null for unsupported.
 */
export function resolveAgentElementsTool(
  part: ToolPart | SandboxPart,
): "Bash" | "Edit" | "Search" | "Thinking" | null {
  if (part.kind === "sandbox") return "Bash";

  const t = part.toolType.toLowerCase();
  if (
    t.includes("bash") ||
    t.includes("exec") ||
    t.includes("command") ||
    t.includes("terminal") ||
    t.includes("run") ||
    t.includes("shell")
  ) {
    return "Bash";
  }
  if (t.includes("edit") || t.includes("write") || t.includes("patch")) {
    return "Edit";
  }
  if (t.includes("search") || t.includes("grep") || t.includes("glob") || t.includes("find")) {
    return "Search";
  }
  if (t.includes("think") || t.includes("reason")) {
    return "Thinking";
  }
  return null;
}

/** Convert a tool ChatRenderPart to agent-elements part shape. */
export function toolPartToAgentElements(part: ToolPart, key: string): AgentElementsToolPart {
  return {
    toolCallId: key,
    type: `tool-${resolveAgentElementsTool(part) ?? part.toolType}`,
    state: mapState(part.state),
    input: (typeof part.input === "object" && part.input !== null ? part.input : {}) as Record<
      string,
      unknown
    >,
    output: part.errorText
      ? { error: part.errorText }
      : typeof part.output === "object" && part.output !== null
        ? (part.output as Record<string, unknown>)
        : part.output != null
          ? { result: part.output }
          : undefined,
  };
}

/** Convert a sandbox ChatRenderPart to agent-elements BashTool part shape. */
export function sandboxPartToAgentElements(part: SandboxPart, key: string): AgentElementsToolPart {
  return {
    toolCallId: key,
    type: "tool-Bash",
    state: mapState(part.state),
    input: {
      command: part.code ?? "",
      ...(part.language ? { language: part.language } : {}),
    },
    output: part.errorText
      ? { error: part.errorText }
      : part.output
        ? { stdout: part.output }
        : undefined,
  };
}

/** Convert a reasoning ChatRenderPart to agent-elements ThinkingTool part shape. */
export function reasoningPartToAgentElements(
  part: Extract<ChatRenderPart, { kind: "reasoning" }>,
  key: string,
): AgentElementsToolPart {
  const text = part.parts.map((p) => p.text).join("\n");
  return {
    toolCallId: key,
    type: "tool-Thinking",
    state: part.isStreaming ? "call" : "output-available",
    input: { thought: text },
    output: part.isStreaming ? undefined : { reasoning: text },
  };
}
