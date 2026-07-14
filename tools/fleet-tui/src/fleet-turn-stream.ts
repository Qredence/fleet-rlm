import type { FleetApiClient } from "./fleet-api-client.js";
import { parseSSE, parseUIChunk, type FleetUIMessageChunk } from "./sse.js";

export type StreamFleetTurnOptions = {
  client: FleetApiClient;
  sessionId: string;
  message: string;
  idempotencyKey?: string;
  signal?: AbortSignal;
};

export async function* streamFleetTurn({
  client,
  sessionId,
  message,
  idempotencyKey = crypto.randomUUID(),
  signal,
}: StreamFleetTurnOptions): AsyncGenerator<FleetUIMessageChunk> {
  const response = await openWithOneNetworkRetry(
    client,
    { message, sessionId, idempotencyKey, signal },
    signal,
  );

  let sawStart = false;
  let sawError = false;
  let sawTerminal = false;
  let sawDone = false;

  for await (const data of parseSSE(response.body!)) {
    const chunk = parseUIChunk(data);
    if (chunk === "[DONE]") {
      if (sawDone) throw new Error("Fleet API emitted duplicate [DONE] markers");
      if (!sawTerminal) throw new Error("Fleet API emitted [DONE] before a terminal chunk");
      sawDone = true;
      continue;
    }
    if (sawDone) throw new Error("Fleet API emitted a chunk after [DONE]");
    if (sawTerminal) {
      if (chunk.type === "finish" || chunk.type === "abort") {
        throw new Error("Fleet API emitted duplicate terminal chunks");
      }
      throw new Error("Fleet API emitted a chunk after its terminal chunk");
    }
    if (!sawStart) {
      if (chunk.type !== "start") {
        throw new Error("Fleet API stream did not start with a start chunk");
      }
      sawStart = true;
    } else if (chunk.type === "start") {
      throw new Error("Fleet API emitted duplicate start chunks");
    }

    if (chunk.type === "error") sawError = true;
    if (chunk.type === "finish") {
      if (chunk.finishReason === "error" && !sawError) {
        throw new Error("Fleet API emitted finish:error without an error chunk");
      }
      sawTerminal = true;
    } else if (chunk.type === "abort") {
      sawTerminal = true;
    }

    yield chunk;
  }

  if (!sawDone) throw new Error("Fleet API stream ended before [DONE]");
}

async function openWithOneNetworkRetry(
  client: FleetApiClient,
  request: Parameters<FleetApiClient["streamTurn"]>[0],
  signal?: AbortSignal,
): Promise<Response> {
  try {
    return await client.streamTurn(request);
  } catch (error) {
    if (signal?.aborted || !hasStatusZero(error)) throw error;
    return client.streamTurn(request);
  }
}

function hasStatusZero(error: unknown): error is { status: 0 } {
  return typeof error === "object" && error !== null && "status" in error && error.status === 0;
}
