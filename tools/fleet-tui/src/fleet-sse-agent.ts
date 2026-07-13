import type { Agent, ModelMessage, TextStreamPart } from "ai";

import type { FleetApiClient } from "./fleet-api-client.js";
import { parseSSE, parseUIChunk, toTextStreamParts } from "./sse.js";

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
    for await (const part of this.streamParts(
      options.prompt ?? options.messages,
      options.abortSignal,
    )) {
      parts.push(part);
    }
    const text = parts
      .filter(
        (part): part is Extract<TextStreamPart<FleetTools>, { type: "text-delta" }> =>
          part.type === "text-delta",
      )
      .map((part) => part.text)
      .join("");
    return { text, steps: [] } as unknown as Awaited<ReturnType<FleetAgent["generate"]>>;
  }

  async stream(
    options: Parameters<FleetAgent["stream"]>[0],
  ): Promise<Awaited<ReturnType<FleetAgent["stream"]>>> {
    const parts = toAsyncIterableStream(
      this.streamParts(options.prompt ?? options.messages, options.abortSignal),
    );
    return { stream: parts, fullStream: parts } as Awaited<ReturnType<FleetAgent["stream"]>>;
  }

  private async *streamParts(
    prompt: string | ModelMessage[] | undefined,
    signal?: AbortSignal,
  ): AsyncGenerator<TextStreamPart<FleetTools>> {
    const message = latestUserText(prompt);
    if (!message) {
      return;
    }

    const idempotencyKey = crypto.randomUUID();
    let response: Response;
    try {
      response = await this.openWithOneNetworkRetry(message, idempotencyKey, signal);
    } catch (error) {
      if (signal?.aborted) {
        yield { type: "abort", reason: "Terminal request cancelled" };
      } else {
        yield { type: "error", error: publicError(error) };
      }
      return;
    }

    let runId: string | undefined;
    let sawStart = false;
    let sawTerminal = false;
    let sawError = false;
    let sawDone = false;
    try {
      for await (const data of parseSSE(response.body!)) {
        const chunk = parseUIChunk(data);
        if (chunk === "[DONE]") {
          if (sawDone) {
            throw new Error("Fleet API emitted duplicate [DONE] markers");
          }
          if (!sawTerminal) {
            throw new Error("Fleet API stream ended before a terminal chunk");
          }
          sawDone = true;
          continue;
        }
        if (sawDone) {
          throw new Error("Fleet API emitted a chunk after [DONE]");
        }
        if (sawTerminal) {
          throw new Error("Fleet API emitted a chunk after its terminal chunk");
        }
        if (chunk.type === "start") {
          if (sawStart) {
            throw new Error("Fleet API emitted duplicate start chunks");
          }
          sawStart = true;
          runId = chunk.messageId;
          if (!runId) {
            throw new Error("Fleet API start chunk is missing its Run id");
          }
        } else if (!sawStart) {
          throw new Error("Fleet API stream did not start with a start chunk");
        }
        if (chunk.type === "error") {
          sawError = true;
        } else if (chunk.type === "abort") {
          sawTerminal = true;
        } else if (chunk.type === "finish") {
          if (chunk.finishReason === "error" && !sawError) {
            throw new Error("Fleet API emitted finish:error without an error chunk");
          }
          sawTerminal = true;
        }
        if (chunk.type === "finish" && chunk.finishReason === "error") {
          continue;
        }
        for (const part of toTextStreamParts(chunk)) {
          yield part;
        }
      }
      if (!sawDone) {
        throw new Error("Fleet API stream ended before [DONE]");
      }
    } catch (error) {
      if (signal?.aborted) {
        if (runId) {
          await this.client.requestCancellation(runId).catch(() => undefined);
        }
        yield { type: "abort", reason: "Terminal request cancelled" };
      } else {
        yield { type: "error", error: publicError(error) };
      }
    }
  }

  private async openWithOneNetworkRetry(
    message: string,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<Response> {
    try {
      return await this.client.streamTurn({
        message,
        sessionId: this.sessionId,
        idempotencyKey,
        signal,
      });
    } catch (error) {
      if (
        signal?.aborted ||
        !(error instanceof Error) ||
        !("status" in error) ||
        error.status !== 0
      ) {
        throw error;
      }
      return this.client.streamTurn({ message, sessionId: this.sessionId, idempotencyKey, signal });
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
