import type {
  ChatMlflowSpanMetadata,
  ChatRenderPart,
  ChatRenderToolState,
} from "@/lib/workspace/workspace-types";

export type AgentToolState = "input-streaming" | "call" | "output-available" | "output-error";

export type AgentToolPart = {
  type: string;
  toolCallId: string;
  state: AgentToolState;
  input?: unknown;
  output?: unknown;
  startedAt?: number;
  toolName?: string;
  mlflowSpan?: ChatMlflowSpanMetadata;
};

type SearchResultRow = { source: string; title: string; date: string };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function mapToolState(state: ChatRenderToolState): AgentToolState {
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

export function normalizeToolInput(toolType: string, input: unknown): Record<string, unknown> {
  const base = isRecord(input) ? { ...input } : {};
  const normalized = toolType.toLowerCase();

  if (
    /(load[_-]?document|read[_-]?(?:file|document)(?:[_-]?slice)?|open[_-]?document|document[_-]?read|file[_-]?read)/.test(
      normalized,
    )
  ) {
    const filePath =
      (typeof base.file_path === "string" && base.file_path.trim()) ||
      (typeof base.path === "string" && base.path.trim()) ||
      (typeof base.document === "string" && base.document.trim()) ||
      "";
    if (filePath && !base.file_path) base.file_path = filePath;
  }

  if (normalized.includes("glob") || normalized.includes("list")) {
    const pattern =
      (typeof base.pattern === "string" && base.pattern.trim()) ||
      (typeof base.path === "string" && base.path.trim()) ||
      (typeof base.query === "string" && base.query.trim()) ||
      "";
    if (pattern && !base.pattern) base.pattern = pattern;
  }

  if (/(grep|search|find)/.test(normalized)) {
    const query =
      (typeof base.query === "string" && base.query.trim()) ||
      (typeof base.pattern === "string" && base.pattern.trim()) ||
      (typeof base.path === "string" && base.path.trim()) ||
      "";
    if (query && !base.query) base.query = query;
  }

  return base;
}

function stringifyValue(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function sanitizeToolName(value: string): string {
  const compact = value
    .split(/[^a-zA-Z0-9]+/)
    .filter(Boolean)
    .map((segment) => segment.charAt(0).toUpperCase() + segment.slice(1))
    .join("");
  return compact || "Tool";
}

export function toolPartType(toolType: string): string {
  const normalized = toolType.toLowerCase();
  if (normalized === "mlflow_span") {
    return "tool-MlflowSpan";
  }
  if (normalized.startsWith("mcp__")) {
    return `tool-${toolType}`;
  }
  if (/(bash|exec|command|terminal|run|shell|python|repl|interpreter|sandbox)/.test(normalized)) {
    return "tool-Bash";
  }
  if (
    /(load[_-]?document|read[_-]?(?:file|document)(?:[_-]?slice)?|open[_-]?document|document[_-]?read|file[_-]?read)/.test(
      normalized,
    )
  ) {
    return "tool-Read";
  }
  if (
    /(list[_-]?files?|list[_-]?dir|glob|tree|ls|directory[_-]?listing|browse[_-]?files?)/.test(
      normalized,
    )
  ) {
    return "tool-Glob";
  }
  if (/(write|create_file)/.test(normalized)) return "tool-Write";
  if (/(edit|patch|notebook)/.test(normalized)) return "tool-Edit";
  if (/(grep|find|search)/.test(normalized)) {
    return normalized.includes("web") ? "tool-WebSearch" : "tool-Grep";
  }
  if (/(webfetch|fetch|url|browser)/.test(normalized)) return "tool-WebFetch";
  if (/(todo|task_list)/.test(normalized)) return "tool-TodoWrite";
  if (/(plan|planning)/.test(normalized)) return "tool-PlanWrite";
  if (/(delegate|sub_rlm|agent|recursive)/.test(normalized)) return "tool-Agent";
  if (/(think|reason)/.test(normalized)) return "tool-Thinking";
  return `tool-${sanitizeToolName(toolType)}`;
}

function isSearchToolType(toolType: string): boolean {
  const normalized = toolType.toLowerCase();
  return /(grep|find|search|glob|list[_-]?files?|list[_-]?dir|websearch)/.test(normalized);
}

function searchSourceForToolType(toolType: string): string {
  return toolType.toLowerCase().includes("web") ? "web" : "github";
}

function toSearchResultRow(title: string, toolType: string): SearchResultRow {
  return {
    source: searchSourceForToolType(toolType),
    title,
    date: "",
  };
}

function parseSearchResultRows(value: unknown, toolType: string): SearchResultRow[] {
  if (value == null) return [];

  if (Array.isArray(value)) {
    return value
      .map((item) => {
        if (typeof item === "string" && item.trim()) {
          return toSearchResultRow(item.trim(), toolType);
        }
        if (!isRecord(item)) return null;
        const title =
          (typeof item.title === "string" && item.title.trim()) ||
          (typeof item.path === "string" && item.path.trim()) ||
          (typeof item.name === "string" && item.name.trim()) ||
          (typeof item.file === "string" && item.file.trim()) ||
          "";
        if (!title) return null;
        const source =
          (typeof item.source === "string" && item.source.trim()) ||
          searchSourceForToolType(toolType);
        const date = typeof item.date === "string" ? item.date : "";
        return { source, title, date };
      })
      .filter((item): item is SearchResultRow => Boolean(item));
  }

  if (typeof value === "string" && value.trim()) {
    return value
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => toSearchResultRow(line, toolType));
  }

  if (isRecord(value)) {
    for (const key of ["results", "matches", "files", "paths", "items"] as const) {
      const nested = value[key];
      if (nested != null) {
        const parsed = parseSearchResultRows(nested, toolType);
        if (parsed.length > 0) return parsed;
      }
    }
  }

  return [];
}

export function normalizeSearchOutput(
  output: unknown,
  toolType: string,
): Record<string, unknown> | undefined {
  if (!isSearchToolType(toolType)) {
    if (isRecord(output)) return output;
    const text = stringifyValue(output);
    return text ? { result: text } : undefined;
  }

  if (isRecord(output) && Array.isArray(output.results)) {
    const normalized = parseSearchResultRows(output.results, toolType);
    if (normalized.length > 0) {
      return { ...output, results: normalized };
    }
  }

  const results = parseSearchResultRows(output, toolType);
  if (results.length > 0) {
    return { results };
  }

  if (isRecord(output)) return output;
  const text = stringifyValue(output);
  return text ? { result: text } : undefined;
}

export function enrichDelegateToolInput(
  toolType: string,
  title: string,
  input: Record<string, unknown>,
): Record<string, unknown> {
  const normalized = toolType.toLowerCase();
  if (!/(delegate|sub_rlm|agent|recursive)/.test(normalized)) {
    return input;
  }

  const description =
    (typeof input.description === "string" && input.description.trim()) ||
    (typeof input.task === "string" && input.task.trim()) ||
    title.trim() ||
    "";
  const subagentType =
    (typeof input.subagent_type === "string" && input.subagent_type.trim()) ||
    (typeof input.agent_type === "string" && input.agent_type.trim()) ||
    (typeof input.name === "string" && input.name.trim()) ||
    title.trim() ||
    "Agent";

  return {
    ...input,
    ...(description ? { description } : {}),
    subagent_type: subagentType,
  };
}

function commandInput(part: Extract<ChatRenderPart, { kind: "tool" | "sandbox" }>) {
  if (part.kind === "sandbox") {
    return {
      command: part.code || "",
      description: part.title,
      language: part.language,
    };
  }
  if (isRecord(part.input)) {
    return enrichDelegateToolInput(
      part.toolType,
      part.title,
      normalizeToolInput(part.toolType, part.input),
    );
  }
  const input = stringifyValue(part.input);
  return enrichDelegateToolInput(part.toolType, part.title, {
    command: input || part.title || part.toolType,
    description: part.title,
  });
}

function outputRecord(part: Extract<ChatRenderPart, { kind: "tool" | "sandbox" }>) {
  if (part.errorText) return { error: part.errorText };
  if (part.kind === "sandbox") return part.output ? { stdout: part.output } : undefined;
  if (part.toolType === "mlflow_span") {
    if (part.output == null) return undefined;
    return isRecord(part.output) ? part.output : { result: part.output };
  }
  return normalizeSearchOutput(part.output, part.toolType);
}

export function stableToolCallId(
  messageId: string,
  kind: string,
  index: number,
  stepIndex?: number,
  parentId?: string,
) {
  const suffix = stepIndex == null ? index : stepIndex;
  const base = `${messageId}:${kind}:${suffix}`;
  return parentId ? `${parentId}:${base}` : base;
}

function stableToolIdentityCallId(messageId: string, identityKey: string, parentId?: string) {
  const base = `${messageId}:${identityKey}`;
  return parentId ? `${parentId}:${base}` : base;
}

export function chatRenderPartToAgentToolPart(
  part: ChatRenderPart,
  messageId: string,
  index: number,
  options?: { parentId?: string; startedAt?: number },
): AgentToolPart | null {
  if (part.kind === "reasoning") {
    const text = part.parts.map((item) => item.text).join("\n");
    if (!text.trim()) return null;
    return {
      type: "tool-Thinking",
      toolCallId: stableToolCallId(messageId, "reasoning", index, undefined, options?.parentId),
      state: part.isStreaming ? "input-streaming" : "output-available",
      input: { thought: text, label: part.label ?? "Reasoning" },
      output: part.isStreaming ? undefined : { reasoning: text },
    };
  }

  if (part.kind === "tool" || part.kind === "sandbox") {
    const toolType = part.kind === "sandbox" ? "sandbox" : part.toolType;
    const state = mapToolState(part.state);
    return {
      type: toolPartType(toolType),
      toolCallId:
        part.kind === "tool" && part.identityKey
          ? stableToolIdentityCallId(messageId, part.identityKey, options?.parentId)
          : stableToolCallId(messageId, toolType, index, part.stepIndex, options?.parentId),
      state,
      input:
        part.kind === "tool" && part.toolType === "mlflow_span" ? part.input : commandInput(part),
      output: outputRecord(part),
      toolName:
        part.kind === "sandbox"
          ? part.title && part.title !== "Sandbox"
            ? part.title
            : "sandbox"
          : toolType,
      ...(part.kind === "tool" && part.mlflowSpan ? { mlflowSpan: part.mlflowSpan } : {}),
      ...((state === "call" || state === "input-streaming") && options?.startedAt != null
        ? { startedAt: options.startedAt }
        : {}),
    };
  }

  if (part.kind === "task") {
    return {
      type: "tool-TodoWrite",
      toolCallId: stableToolCallId(messageId, "task", index, undefined, options?.parentId),
      state:
        part.status === "in_progress"
          ? "call"
          : part.status === "error"
            ? "output-error"
            : "output-available",
      input: {
        action: "update",
        title: part.title,
        todos: part.items?.map((item) => ({
          content: item.text,
          status: part.status,
          file: item.file?.name,
        })),
      },
      output: part.status === "in_progress" ? undefined : { status: part.status },
    };
  }

  if (part.kind === "queue") {
    return {
      type: "tool-PlanWrite",
      toolCallId: stableToolCallId(messageId, "plan", index, undefined, options?.parentId),
      state: part.items.every((item) => item.completed) ? "output-available" : "call",
      input: {
        action: "update",
        plan: {
          title: part.title,
          steps: part.items.map((item) => ({
            content: item.label,
            description: item.description,
            status: item.completed ? "completed" : "pending",
          })),
        },
      },
      output: part.items.every((item) => item.completed) ? { status: "completed" } : undefined,
    };
  }

  if (part.kind === "status_note") {
    return {
      type: "tool-Status",
      toolCallId: stableToolCallId(messageId, "status", index, part.stepIndex, options?.parentId),
      state: part.tone === "error" ? "output-error" : "output-available",
      input: { message: part.text, tone: part.tone },
      output: { message: part.text, tone: part.tone },
    };
  }

  if (part.kind === "environment_variables") {
    return {
      type: "tool-EnvironmentVariables",
      toolCallId: stableToolCallId(messageId, "env", index, undefined, options?.parentId),
      state: "output-available",
      input: { title: part.title ?? "Environment variables" },
      output: {
        variables: part.variables.map((variable) => ({
          name: variable.name,
          value: variable.value,
          required: variable.required,
        })),
      },
    };
  }

  if (part.kind === "request_row") {
    return {
      type: "tool-RequestRow",
      toolCallId: stableToolCallId(messageId, "request_row", index, undefined, options?.parentId),
      state: "output-available",
      input: { label: part.label, value: part.value, preview: part.preview },
      output: { label: part.label, value: part.value },
    };
  }

  if (part.kind === "skills_row") {
    return {
      type: "tool-SkillsRow",
      toolCallId: stableToolCallId(messageId, "skills_row", index, undefined, options?.parentId),
      state: "output-available",
      input: { label: part.label, skills: part.skills, preview: part.preview },
      output: { label: part.label, skills: part.skills },
    };
  }

  if (part.kind === "history_row") {
    return {
      type: "tool-HistoryRow",
      toolCallId: stableToolCallId(messageId, "history_row", index, undefined, options?.parentId),
      state: "output-available",
      input: {
        label: part.label,
        turnCount: part.turnCount,
        value: part.value,
        preview: part.preview,
      },
      output: { label: part.label, turnCount: part.turnCount, value: part.value },
    };
  }

  if (part.kind === "core_memory_row") {
    return {
      type: "tool-CoreMemoryRow",
      toolCallId: stableToolCallId(
        messageId,
        "core_memory_row",
        index,
        undefined,
        options?.parentId,
      ),
      state: "output-available",
      input: { label: part.label, value: part.value, preview: part.preview },
      output: { label: part.label, value: part.value },
    };
  }

  if (part.kind === "context_row") {
    return {
      type: "tool-ContextRow",
      toolCallId: stableToolCallId(messageId, "context_row", index, undefined, options?.parentId),
      state: "output-available",
      input: { label: part.label, value: part.value, preview: part.preview },
      output: { label: part.label, value: part.value },
    };
  }

  return null;
}
