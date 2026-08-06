import type { FleetApiClient, FleetSkillSelection } from "./fleet-api-client.js";
import { parseSSE, parseUIChunk, type FleetUIMessageChunk } from "./sse.js";

export type StreamFleetTurnOptions = {
  client: FleetApiClient;
  sessionId: string;
  message: string;
  idempotencyKey?: string;
  skillSelections?: readonly FleetSkillSelection[];
  onStreamOpen?: () => void;
  signal?: AbortSignal;
};

export async function* streamFleetTurn({
  client,
  sessionId,
  message,
  idempotencyKey = crypto.randomUUID(),
  skillSelections = [],
  onStreamOpen,
  signal,
}: StreamFleetTurnOptions): AsyncGenerator<FleetUIMessageChunk> {
  const response = await openWithOneNetworkRetry(
    client,
    { message, sessionId, idempotencyKey, skillSelections, onStreamOpen, signal },
    signal,
  );

  if (!response.body) throw new Error("Fleet API returned an empty stream body");
  const lifecycle = new StreamLifecycle();

  for await (const data of parseSSE(response.body)) {
    const chunk = parseUIChunk(data);
    lifecycle.accept(chunk);
    if (chunk === "[DONE]") continue;
    yield chunk;
  }

  lifecycle.assertComplete();
}

class StreamLifecycle {
  private started = false;
  private terminal = false;
  private done = false;
  private sawError = false;
  private stepDepth = 0;
  private readonly reasoningOpen = new Set<string>();
  private readonly reasoningEnded = new Set<string>();
  private readonly textOpen = new Set<string>();
  private readonly textEnded = new Set<string>();
  private readonly toolsOpen = new Set<string>();
  private readonly toolsEnded = new Set<string>();

  accept(chunk: FleetUIMessageChunk | "[DONE]"): void {
    if (chunk === "[DONE]") {
      if (this.done) throw new Error("Fleet API emitted duplicate [DONE] markers");
      if (!this.terminal) throw new Error("Fleet API emitted [DONE] before a terminal chunk");
      this.done = true;
      return;
    }

    if (this.done) throw new Error("Fleet API emitted a chunk after [DONE]");
    if (this.terminal) throw new Error("Fleet API emitted a chunk after its terminal chunk");
    if (!this.started && chunk.type !== "start") {
      throw new Error("Fleet API stream did not start with a start chunk");
    }
    if (this.started && chunk.type === "start") {
      throw new Error("Fleet API emitted duplicate start chunks");
    }

    switch (chunk.type) {
      case "start":
        this.started = true;
        return;
      case "start-step":
        this.stepDepth += 1;
        return;
      case "finish-step":
        if (this.stepDepth === 0) {
          throw new Error("Fleet API emitted finish-step without a matching start-step");
        }
        this.stepDepth -= 1;
        return;
      case "reasoning-start":
        this.begin(chunk.id, this.reasoningOpen, this.reasoningEnded, "reasoning stream");
        return;
      case "reasoning-delta":
        this.requireOpen(chunk.id, this.reasoningOpen, this.reasoningEnded, "reasoning stream");
        return;
      case "reasoning-end":
        this.end(chunk.id, this.reasoningOpen, this.reasoningEnded, "reasoning stream");
        return;
      case "text-start":
        this.begin(chunk.id, this.textOpen, this.textEnded, "text stream");
        return;
      case "text-delta":
        this.requireOpen(chunk.id, this.textOpen, this.textEnded, "text stream");
        return;
      case "text-end":
        this.end(chunk.id, this.textOpen, this.textEnded, "text stream");
        return;
      case "tool-input-available":
        this.begin(chunk.toolCallId, this.toolsOpen, this.toolsEnded, "tool call");
        return;
      case "tool-output-available":
      case "tool-output-error":
        this.end(chunk.toolCallId, this.toolsOpen, this.toolsEnded, "tool call");
        return;
      case "error":
        if (this.sawError) throw new Error("Fleet API emitted duplicate error chunks");
        this.sawError = true;
        return;
      case "finish":
        if (chunk.finishReason === "error" && !this.sawError) {
          throw new Error("Fleet API emitted finish:error without an error chunk");
        }
        this.terminal = true;
        return;
      case "abort":
        this.terminal = true;
        return;
      default:
        return;
    }
  }

  assertComplete(): void {
    if (!this.started) throw new Error("Fleet API stream ended before a start chunk");
    if (!this.terminal) throw new Error("Fleet API stream ended before a terminal chunk");
    if (!this.done) throw new Error("Fleet API stream ended before [DONE]");
  }

  private begin(id: string, open: Set<string>, ended: Set<string>, label: string): void {
    if (open.has(id) || ended.has(id)) {
      throw new Error(`Fleet API emitted duplicate ${label} start for ${id}`);
    }
    open.add(id);
  }

  private requireOpen(id: string, open: Set<string>, ended: Set<string>, label: string): void {
    if (!open.has(id) || ended.has(id)) {
      throw new Error(`Fleet API emitted data for an inactive ${label} ${id}`);
    }
  }

  private end(id: string, open: Set<string>, ended: Set<string>, label: string): void {
    this.requireOpen(id, open, ended, label);
    open.delete(id);
    ended.add(id);
  }
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
