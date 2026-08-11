import {
  FleetApiError,
  type FleetApiClient,
  type FleetSkillSelection,
} from "../fleet-api-client.js";
import { streamFleetTurn } from "../fleet-turn-stream.js";
import { LiveTurnProjector } from "./projection.js";
import { newMessageId, type ConversationStore } from "./store.js";

export class RunController {
  private active: RunExecution | null = null;

  constructor(
    private readonly store: ConversationStore,
    private readonly client: FleetApiClient,
  ) {}

  start(text: string, options: RunStartOptions = {}): AbortController {
    if (this.active) return this.active.controller;
    const controller = new AbortController();
    const execution: RunExecution = { controller, runId: null, cancellationRequested: false };
    this.active = execution;
    this.store.setCancelToken(controller);
    this.store.dispatch({ type: "user/submit", text });
    void this.execute(text, execution, options);
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
    void this.ensureCancellation(execution);
  }

  async cancelAndWait(timeoutMs: number): Promise<void> {
    const execution = this.active;
    if (!execution) return;
    this.cancel();
    await Promise.race([
      this.ensureCancellation(execution),
      new Promise<void>((resolve) => setTimeout(resolve, timeoutMs)),
    ]);
  }

  isRunning(): boolean {
    return this.active !== null && !this.active.controller.signal.aborted;
  }

  private async execute(
    text: string,
    execution: RunExecution,
    options: RunStartOptions,
  ): Promise<void> {
    const { controller } = execution;
    let streamOpened = false;
    try {
      const session = this.store.getState().session;
      if (!session) throw new Error("no active session");
      const projector = new LiveTurnProjector(Date.now);

      for await (const chunk of streamFleetTurn({
        client: this.client,
        sessionId: session.id,
        message: text,
        skillSelections: options.skillSelections,
        attachmentIds: options.attachmentIds,
        onStreamOpen: () => {
          streamOpened = true;
          options.onStreamOpen?.();
        },
        // Header fallback: the run is already live once headers commit, so a
        // stream cut before the start chunk must still be cancellable. The
        // start chunk's messageId is authoritative and overwrites this later.
        onRunId: (runId) => {
          execution.runId ??= runId;
        },
        signal: controller.signal,
      })) {
        if (chunk.type === "start") {
          execution.runId = chunk.messageId;
          if (controller.signal.aborted) {
            await this.ensureCancellation(execution);
          }
        }
        if (this.active !== execution) {
          continue;
        }
        for (const event of projector.push(chunk)) this.store.dispatch(event);
      }
    } catch (error) {
      if (controller.signal.aborted) {
        await this.ensureCancellation(execution);
        if (this.active === execution && this.store.getState().run.outcome !== "cancelled") {
          this.store.dispatch({ type: "run/cancelled", reason: "Cancelled by operator" });
        }
      } else if (this.active === execution) {
        const message = errorMessage(error);
        if (!streamOpened) {
          options.onPreStreamFailure?.(text);
          this.store.dispatch({
            type: "run/finish",
            finishReason: "error",
            error: message,
            durationMs: null,
            checkpointVersion: null,
          });
        } else {
          this.store.dispatch({ type: "run/interrupted", error: message });
        }
        const sessionId = this.store.getState().session?.id;
        const recovery =
          streamOpened && sessionId
            ? `\n\nStream interrupted. Reload committed history with /resume ${sessionId}. The prompt was not replayed.`
            : "";
        this.appendError(`${message}${recovery}`);
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

  private ensureCancellation(execution: RunExecution): Promise<void> {
    if (!execution.runId) return Promise.resolve();
    execution.cancellationPromise ??= this.requestRunCancellation(execution);
    return execution.cancellationPromise;
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
  cancellationPromise?: Promise<void>;
};

export type RunStartOptions = {
  skillSelections?: readonly FleetSkillSelection[];
  attachmentIds?: readonly string[];
  onStreamOpen?: () => void;
  onPreStreamFailure?: (draft: string) => void;
};

function errorMessage(error: unknown): string {
  if (error instanceof FleetApiError && error.correlationId) {
    return `${error.message} (request ${error.correlationId}; see .fleet_rlm/logs/latest.log)`;
  }
  if (error instanceof Error) return error.message;
  if (typeof error === "string") return error;
  return "Fleet terminal request failed";
}
