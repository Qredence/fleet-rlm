import type { TextStreamPart } from "ai";

type UIChunk = {
  type: string;
  messageId?: string;
  data?: unknown;
  id?: string;
  delta?: string;
  text?: string;
  toolCallId?: string;
  toolName?: string;
  input?: unknown;
  output?: unknown;
  errorText?: string;
  reason?: string;
  finishReason?: string;
};

const chunkTypes = new Set([
  "start",
  "start-step",
  "finish-step",
  "reasoning-start",
  "reasoning-delta",
  "reasoning-end",
  "data-status",
  "data-skill",
  "data-rlm-code",
  "data-rlm-output",
  "tool-input-available",
  "tool-output-available",
  "tool-output-error",
  "data-attachment",
  "data-warning",
  "data-artifact",
  "data-usage",
  "data-structured-result",
  "text-start",
  "text-delta",
  "text-end",
  "finish",
  "abort",
  "error",
]);

const finishReasons = new Set(["stop", "length", "content-filter", "tool-calls", "error", "other"]);

export async function* parseSSE(body: ReadableStream<Uint8Array>): AsyncGenerator<string> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let separator = buffer.search(/\r?\n\r?\n/);
      while (separator >= 0) {
        const frame = buffer.slice(0, separator);
        buffer = buffer.slice(separator).replace(/^\r?\n\r?\n/, "");
        const data = frame
          .split(/\r?\n/)
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trimStart())
          .join("\n");
        if (data) {
          yield data;
        }
        separator = buffer.search(/\r?\n\r?\n/);
      }
      if (done) {
        const frame = buffer.trim();
        if (frame) {
          const data = frame
            .split(/\r?\n/)
            .filter((line) => line.startsWith("data:"))
            .map((line) => line.slice(5).trimStart())
            .join("\n");
          if (data) {
            yield data;
          }
        }
        break;
      }
    }
  } finally {
    reader.releaseLock();
  }
}

export function parseUIChunk(data: string): UIChunk | "[DONE]" {
  if (data === "[DONE]") {
    return data;
  }
  const parsed: unknown = JSON.parse(data);
  if (!isUIChunk(parsed)) {
    throw new Error("Fleet API returned an invalid AI SDK UI stream chunk");
  }
  return parsed;
}

export function toTextStreamParts(chunk: UIChunk): TextStreamPart<{}>[] {
  if (chunk.type === "data-rlm-code") {
    const data = requireRecord(chunk.data, "RLM code data");
    const step = requiredStep(data, chunk.id);
    return [
      {
        type: "tool-call",
        toolCallId: `rlm-step-${step}`,
        toolName: `RLM step ${step}`,
        input: requireString(data.code, "RLM code"),
        dynamic: true,
        providerExecuted: true,
      } as TextStreamPart<{}>,
    ];
  }
  if (chunk.type === "data-rlm-output") {
    const data = requireRecord(chunk.data, "RLM output data");
    const step = requiredStep(data, chunk.id);
    return [
      {
        type: "tool-result",
        toolCallId: `rlm-step-${step}`,
        output: requireString(data.output, "RLM output"),
        dynamic: true,
        providerExecuted: true,
      } as TextStreamPart<{}>,
    ];
  }
  if (chunk.type.startsWith("data-")) {
    // The terminal renderer has no custom-data panel.  Do not pretend status,
    // usage, or artifacts are model reasoning: real reasoning and tool events
    // below retain their native UI treatment.
    return [];
  }
  switch (chunk.type) {
    case "start":
      return [{ type: "start" }];
    case "start-step":
      return [{ type: "start-step", request: {}, warnings: [] } as TextStreamPart<{}>];
    case "finish-step":
      return [
        {
          type: "finish-step",
          response: {},
          usage: {},
          performance: {},
          finishReason: "stop",
          rawFinishReason: undefined,
          providerMetadata: undefined,
        } as TextStreamPart<{}>,
      ];
    case "text-start":
      return [{ type: "text-start", id: requireString(chunk.id, "text id") }];
    case "text-delta":
      return [
        {
          type: "text-delta",
          id: requireString(chunk.id, "text id"),
          text: requireString(chunk.delta, "text delta"),
        },
      ];
    case "text-end":
      return [{ type: "text-end", id: requireString(chunk.id, "text id") }];
    case "reasoning-start":
      return [{ type: "reasoning-start", id: requireString(chunk.id, "reasoning id") }];
    case "reasoning-delta":
      return [
        {
          type: "reasoning-delta",
          id: requireString(chunk.id, "reasoning id"),
          text: requireString(chunk.delta, "reasoning delta"),
        },
      ];
    case "reasoning-end":
      return [{ type: "reasoning-end", id: requireString(chunk.id, "reasoning id") }];
    case "tool-input-available":
      return [
        {
          type: "tool-call",
          toolCallId: requireString(chunk.toolCallId, "tool call id"),
          toolName: requireString(chunk.toolName, "tool name"),
          input: chunk.input,
          dynamic: true,
          providerExecuted: true,
        } as TextStreamPart<{}>,
      ];
    case "tool-output-available":
      return [
        {
          type: "tool-result",
          toolCallId: requireString(chunk.toolCallId, "tool call id"),
          output: chunk.output,
          dynamic: true,
          providerExecuted: true,
        } as TextStreamPart<{}>,
      ];
    case "tool-output-error":
      return [
        {
          type: "tool-error",
          toolCallId: requireString(chunk.toolCallId, "tool call id"),
          error: chunk.errorText ?? "Tool failed",
          dynamic: true,
          providerExecuted: true,
        } as TextStreamPart<{}>,
      ];
    case "finish":
      return [
        {
          type: "finish",
          finishReason: finishReasons.has(chunk.finishReason ?? "")
            ? (chunk.finishReason as "stop")
            : "other",
          rawFinishReason: chunk.finishReason,
          totalUsage: {},
        } as TextStreamPart<{}>,
      ];
    case "abort":
      return [{ type: "abort", reason: chunk.reason }];
    case "error":
      return [{ type: "error", error: chunk.errorText ?? "Fleet turn failed" }];
    default:
      throw new Error(`Fleet API returned unsupported chunk type: ${chunk.type}`);
  }
}

function isUIChunk(value: unknown): value is UIChunk {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as { type?: unknown }).type === "string" &&
    chunkTypes.has((value as { type: string }).type)
  );
}

function requireString(value: unknown, label: string): string {
  if (typeof value !== "string" || !value) {
    throw new Error(`Fleet API stream is missing ${label}`);
  }
  return value;
}

function requireRecord(value: unknown, label: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`Fleet API stream is missing ${label}`);
  }
  return value as Record<string, unknown>;
}

function requiredStep(data: Record<string, unknown>, fallback: string | undefined): string {
  const step = data.step;
  if (typeof step === "number" || typeof step === "string") {
    return String(step);
  }
  return requireString(fallback, "RLM step id");
}
