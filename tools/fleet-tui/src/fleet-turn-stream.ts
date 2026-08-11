import type { FleetApiClient, FleetSkillSelection } from "./fleet-api-client.js";
import { parseSSE, parseUIChunk, type FleetUIMessageChunk } from "./sse.js";

export type StreamFleetTurnOptions = {
  client: FleetApiClient;
  sessionId: string;
  message: string;
  idempotencyKey?: string;
  skillSelections?: readonly FleetSkillSelection[];
  attachmentIds?: readonly string[];
  onStreamOpen?: () => void;
  /** Fires with the backend's X-Fleet-Run-ID once stream headers are accepted. */
  onRunId?: (runId: string) => void;
  signal?: AbortSignal;
};

export async function* streamFleetTurn({
  client,
  sessionId,
  message,
  idempotencyKey = crypto.randomUUID(),
  skillSelections = [],
  attachmentIds = [],
  onStreamOpen,
  onRunId,
  signal,
}: StreamFleetTurnOptions): AsyncGenerator<FleetUIMessageChunk> {
  const response = await openWithOneNetworkRetry(
    client,
    { message, sessionId, idempotencyKey, skillSelections, attachmentIds, onStreamOpen, signal },
    signal,
  );

  if (!response.body) throw new Error("Fleet API returned an empty stream body");
  // The run is live on the backend the moment headers commit, so surface the
  // run id from the header as a fallback: it lets the operator cancel even if
  // the stream is cut before the first (start) chunk arrives.
  const runId = response.headers.get("X-Fleet-Run-ID");
  if (runId) onRunId?.(runId);
  const lifecycle = new StreamLifecycle();

  for await (const data of parseSSE(response.body)) {
    const chunk = parseUIChunk(data);
    lifecycle.accept(chunk);
    if (chunk === "[DONE]") continue;
    yield chunk;
  }

  lifecycle.assertComplete();
}

// Exported for the fixture test that validates the backend projector's golden
// stream through the same ordering grammar the live TUI enforces.
export class StreamLifecycle {
  private started = false;
  private terminal = false;
  private cleanFinish = false;
  private done = false;
  private sawError = false;
  // A claim/preparation failure (or pre-stream cancellation) legally ends the
  // stream without a start chunk: transient preparation heartbeats, then one
  // error/abort terminal.
  private openFailed = false;
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
    if (!this.started) {
      // The stream opens with transient preparation heartbeats; after the
      // Turn claim resolves it either starts or ends with a startless
      // error/abort terminal.
      if (chunk.type === "data-status") return;
      if (this.sawError && chunk.type === "start") {
        throw new Error("Fleet API emitted a start chunk after an error chunk");
      }
      if (chunk.type !== "start" && chunk.type !== "error" && chunk.type !== "abort") {
        // Without a start only an error-closed finish terminal may follow.
        if (!(chunk.type === "finish" && chunk.finishReason === "error" && this.sawError)) {
          throw new Error("Fleet API stream did not start with a start chunk");
        }
      }
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
        if (!this.started) this.openFailed = true;
        return;
      case "finish":
        if (chunk.finishReason === "error" && !this.sawError) {
          throw new Error("Fleet API emitted finish:error without an error chunk");
        }
        this.terminal = true;
        this.cleanFinish = chunk.finishReason === "stop";
        return;
      case "abort":
        if (!this.started) this.openFailed = true;
        this.terminal = true;
        return;
      default:
        return;
    }
  }

  assertComplete(): void {
    if (!this.started && !this.openFailed) {
      throw new Error("Fleet API stream ended before a start chunk");
    }
    if (!this.terminal) throw new Error("Fleet API stream ended before a terminal chunk");
    if (!this.done) throw new Error("Fleet API stream ended before [DONE]");
    if (!this.cleanFinish) return;
    if (this.stepDepth !== 0) {
      throw new Error("Fleet API stream finished with unclosed start-step chunks");
    }
    this.assertAllClosed(this.reasoningOpen, "reasoning stream");
    this.assertAllClosed(this.textOpen, "text stream");
    this.assertAllClosed(this.toolsOpen, "tool call");
  }

  private assertAllClosed(open: Set<string>, label: string): void {
    if (open.size > 0) {
      throw new Error(`Fleet API stream finished with an open ${label}: ${[...open].join(", ")}`);
    }
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
