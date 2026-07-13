import type { TextStreamPart } from "ai";

type UIChunk = {
  type: string;
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

export function toTextStreamPart(chunk: UIChunk): TextStreamPart<{}> | undefined {
  switch (chunk.type) {
    case "start":
      return { type: "start" };
    case "start-step":
      return { type: "start-step", request: {}, warnings: [] } as TextStreamPart<{}>;
    case "finish-step":
      return {
        type: "finish-step",
        response: {},
        usage: {},
        performance: {},
        finishReason: "stop",
        rawFinishReason: undefined,
        providerMetadata: undefined,
      } as TextStreamPart<{}>;
    case "text-start":
      return { type: "text-start", id: requireString(chunk.id, "text id") };
    case "text-delta":
      return {
        type: "text-delta",
        id: requireString(chunk.id, "text id"),
        text: requireString(chunk.delta, "text delta"),
      };
    case "text-end":
      return { type: "text-end", id: requireString(chunk.id, "text id") };
    case "reasoning-start":
      return { type: "reasoning-start", id: requireString(chunk.id, "reasoning id") };
    case "reasoning-delta":
      return {
        type: "reasoning-delta",
        id: requireString(chunk.id, "reasoning id"),
        text: requireString(chunk.delta, "reasoning delta"),
      };
    case "reasoning-end":
      return { type: "reasoning-end", id: requireString(chunk.id, "reasoning id") };
    case "tool-input-available":
      return {
        type: "tool-call",
        toolCallId: requireString(chunk.toolCallId, "tool call id"),
        toolName: requireString(chunk.toolName, "tool name"),
        input: chunk.input,
        dynamic: true,
        providerExecuted: true,
      } as TextStreamPart<{}>;
    case "tool-output-available":
      return {
        type: "tool-result",
        toolCallId: requireString(chunk.toolCallId, "tool call id"),
        output: chunk.output,
        dynamic: true,
        providerExecuted: true,
      } as TextStreamPart<{}>;
    case "tool-output-error":
      return {
        type: "tool-error",
        toolCallId: requireString(chunk.toolCallId, "tool call id"),
        error: chunk.errorText ?? "Tool failed",
        dynamic: true,
        providerExecuted: true,
      } as TextStreamPart<{}>;
    case "finish":
      return {
        type: "finish",
        finishReason: finishReasons.has(chunk.finishReason ?? "")
          ? (chunk.finishReason as "stop")
          : "other",
        rawFinishReason: chunk.finishReason,
        totalUsage: {},
      } as TextStreamPart<{}>;
    case "abort":
      return { type: "abort", reason: chunk.reason };
    case "error":
      return { type: "error", error: chunk.errorText ?? "Fleet turn failed" };
    default:
      // Fleet data-* parts are durable server-side data, intentionally not rendered in v1.
      return undefined;
  }
}

function isUIChunk(value: unknown): value is UIChunk {
  return typeof value === "object" && value !== null && typeof (value as { type?: unknown }).type === "string";
}

function requireString(value: unknown, label: string): string {
  if (typeof value !== "string" || !value) {
    throw new Error(`Fleet API stream is missing ${label}`);
  }
  return value;
}
