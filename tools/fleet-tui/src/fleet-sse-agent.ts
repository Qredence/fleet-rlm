import type {
  Agent,
  ModelMessage,
  TextStreamPart,
} from "ai";

import { FleetApiClient } from "./fleet-api-client.js";
import { parseSSE, parseUIChunk, toTextStreamPart } from "./sse.js";

type FleetTools = {};
type FleetAgent = Agent<never, FleetTools>;

export class FleetSseAgent implements FleetAgent {
  readonly version = "agent-v1" as const;
  readonly id = "fleet-rlm";
  readonly tools: FleetTools = {};

  constructor(
    private readonly client: FleetApiClient,
    private readonly sessionId: string,
  ) {}

  async generate(
    options: Parameters<FleetAgent["generate"]>[0],
  ): Promise<Awaited<ReturnType<FleetAgent["generate"]>>> {
    const parts: TextStreamPart<FleetTools>[] = [];
    for await (const part of this.streamParts(options.prompt ?? options.messages, options.abortSignal)) {
      parts.push(part);
    }
    const text = parts
      .filter((part): part is Extract<TextStreamPart<FleetTools>, { type: "text-delta" }> => part.type === "text-delta")
      .map((part) => part.text)
      .join("");
    return { text, steps: [] } as unknown as Awaited<ReturnType<FleetAgent["generate"]>>;
  }

  async stream(
    options: Parameters<FleetAgent["stream"]>[0],
  ): Promise<Awaited<ReturnType<FleetAgent["stream"]>>> {
    const parts = toAsyncIterableStream(this.streamParts(options.prompt ?? options.messages, options.abortSignal));
    return { stream: parts, fullStream: parts } as Awaited<ReturnType<FleetAgent["stream"]>>;
  }

  private async *streamParts(
    prompt: string | ModelMessage[] | undefined,
    signal?: AbortSignal,
  ): AsyncGenerator<TextStreamPart<FleetTools>> {
    const message = latestUserText(prompt);
    if (!message) {
      yield { type: "error", error: "Fleet terminal UI requires a text user prompt" };
      yield { type: "finish", finishReason: "error", rawFinishReason: "invalid_prompt", totalUsage: {} } as TextStreamPart<FleetTools>;
      return;
    }

    let response: Response;
    try {
      response = await this.client.streamChat({ message, sessionId: this.sessionId, signal });
    } catch (error) {
      if (signal?.aborted) {
        yield { type: "abort", reason: "Terminal request cancelled" };
      } else {
        yield { type: "error", error: publicError(error) };
      }
      return;
    }

    try {
      for await (const data of parseSSE(response.body!)) {
        const chunk = parseUIChunk(data);
        if (chunk === "[DONE]") {
          return;
        }
        const part = toTextStreamPart(chunk);
        if (part) {
          yield part;
          // Fleet emits error followed by finish:error. The error already
          // terminates the response; consuming finish:error duplicates it in
          // the stock terminal renderer.
          if (part.type === "error") {
            return;
          }
        }
      }
    } catch (error) {
      if (signal?.aborted) {
        yield { type: "abort", reason: "Terminal request cancelled" };
      } else {
        yield { type: "error", error: publicError(error) };
      }
    }
  }
}

function latestUserText(prompt: string | ModelMessage[] | undefined): string | undefined {
  if (prompt === undefined) {
    return undefined;
  }
  if (typeof prompt === "string") {
    return prompt.trim() || undefined;
  }
  for (const message of [...prompt].reverse()) {
    if (message.role !== "user") {
      continue;
    }
    if (typeof message.content === "string" && message.content.trim()) {
      return message.content.trim();
    }
    if (Array.isArray(message.content)) {
      const text = message.content
        .filter((part): part is { type: "text"; text: string } => part.type === "text")
        .map((part) => part.text)
        .join("")
        .trim();
      if (text) {
        return text;
      }
    }
  }
  return undefined;
}

function toAsyncIterableStream<T>(source: AsyncIterable<T>): AsyncIterable<T> & ReadableStream<T> {
  const iterator = source[Symbol.asyncIterator]();
  return new ReadableStream<T>({
    async pull(controller) {
      const next = await iterator.next();
      if (next.done) {
        controller.close();
      } else {
        controller.enqueue(next.value);
      }
    },
    async cancel(reason) {
      await iterator.return?.(reason);
    },
  }) as AsyncIterable<T> & ReadableStream<T>;
}

function publicError(error: unknown): string {
  return error instanceof Error ? error.message : "Fleet terminal request failed";
}
