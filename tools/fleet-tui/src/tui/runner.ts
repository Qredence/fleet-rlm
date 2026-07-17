import { FleetApiError, type FleetApiClient } from "../fleet-api-client.js";
import { streamFleetTurn } from "../fleet-turn-stream.js";
import { LiveTurnProjector } from "./projection.js";
import { newMessageId, type ConversationStore } from "./store.js";

export class RunController {
  private active: RunExecution | null = null;

  constructor(
    private readonly store: ConversationStore,
    private readonly client: FleetApiClient,
  ) {}

  start(text: string): AbortController {
    this.cancel();
    const controller = new AbortController();
    const execution: RunExecution = { controller, runId: null, cancellationRequested: false };
    this.active = execution;
    this.store.setCancelToken(controller);
    this.store.dispatch({ type: "user/submit", text });
    void this.execute(text, execution);
    return controller;
  }

  cancel(): void {
    const execution = this.active;
    const controller = execution?.controller;
    if (!controller || controller.signal.aborted) return;
    if (
      this.store.getState().run.phase === "completed" ||
      this.store.getState().run.phase === "error"
    ) {
      return;
    }
    this.store.dispatch({ type: "run/cancelling" });
    controller.abort();
    void this.requestRunCancellation(execution);
  }

  isRunning(): boolean {
    return this.active !== null && !this.active.controller.signal.aborted;
  }

  private async execute(text: string, execution: RunExecution): Promise<void> {
    const { controller } = execution;
    try {
      const session = this.store.getState().session;
      if (!session) throw new Error("no active session");
      const projector = new LiveTurnProjector(Date.now);
      let streamError: string | null = null;

      for await (const chunk of streamFleetTurn({
        client: this.client,
        sessionId: session.id,
        message: text,
        signal: controller.signal,
      })) {
        if (chunk.type === "start") {
          execution.runId = chunk.messageId;
          if (controller.signal.aborted) {
            await this.requestRunCancellation(execution);
          }
        }
        if (this.active !== execution) {
          continue;
        }
        if (chunk.type === "start") {
          this.store.dispatch({ type: "run/start", runId: chunk.messageId, model: null });
        }
        for (const event of projector.push(chunk)) this.store.dispatch(event);
        if (chunk.type === "error") streamError = chunk.errorText;
        if (chunk.type === "finish") {
          this.store.dispatch({
            type: "run/finish",
            finishReason: chunk.finishReason,
            error: chunk.finishReason === "error" ? streamError : null,
          });
        } else if (chunk.type === "abort") {
          this.store.dispatch({ type: "run/cancelled" });
        }
      }
    } catch (error) {
      if (controller.signal.aborted) {
        await this.requestRunCancellation(execution);
        if (this.active === execution) this.store.dispatch({ type: "run/cancelled" });
      } else if (this.active === execution) {
        const message = errorMessage(error);
        this.store.dispatch({ type: "run/finish", finishReason: "error", error: message });
        this.appendError(message);
      }
    } finally {
      if (this.active === execution) {
        this.active = null;
        this.store.clearCancelToken(controller);
      }
    }
  }

  private async requestRunCancellation(execution: RunExecution): Promise<void> {
    if (!execution.runId || execution.cancellationRequested) return;
    execution.cancellationRequested = true;
    await this.client.requestCancellation(execution.runId).catch(() => undefined);
  }

  private appendError(text: string): void {
    this.store.dispatch({
      type: "message/upsert",
      message: { id: newMessageId("error"), kind: "error", text, ts: Date.now() },
    });
  }
}

type RunExecution = {
  controller: AbortController;
  runId: string | null;
  cancellationRequested: boolean;
};

function errorMessage(error: unknown): string {
  if (error instanceof FleetApiError && error.correlationId) {
    return `${error.message} (request ${error.correlationId}; see .fleet_rlm/logs/latest.log)`;
  }
  if (error instanceof Error) return error.message;
  if (typeof error === "string") return error;
  return "Fleet terminal request failed";
}
