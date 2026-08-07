/** Multi-turn conversation store. Renderer-independent source of truth. */

import { summarizeExecution, type ExecutionSummary } from "./execution-summary.js";

export type Phase = "idle" | "submitting" | "running" | "cancelling" | "completed" | "error";

export type Role = "user" | "assistant" | "system";

export type Message =
  | {
      id: string;
      kind: "text";
      role: Role;
      text: string;
      ts: number;
      streaming: boolean;
      runId?: string;
    }
  | {
      id: string;
      kind: "reasoning";
      runId: string;
      step: number;
      text: string;
      ts: number;
    }
  | {
      id: string;
      kind: "tool";
      runId: string;
      toolCallId: string;
      name: string;
      input: unknown;
      output?: unknown;
      error?: string;
      startedAt: number;
      endedAt?: number;
      status: "pending" | "running" | "success" | "error";
      ts: number;
    }
  | {
      id: string;
      kind: "code";
      runId: string;
      step: number;
      code: string;
      language?: string;
      streaming?: boolean;
      ts: number;
    }
  | {
      id: string;
      kind: "output";
      runId: string;
      step: number;
      output: string;
      streaming?: boolean;
      ts: number;
    }
  | {
      id: string;
      kind: "result";
      runId: string;
      schemaId: string;
      schemaVersion: string;
      value: unknown;
      narrative?: string;
      ts: number;
    }
  | {
      id: string;
      kind: "skill";
      runId: string;
      skillId: string;
      name: string;
      phase: "activated" | "loaded";
      version: string;
      trust?: string;
      affordances?: string[];
      ts: number;
    }
  | {
      id: string;
      kind: "attachment";
      runId: string;
      attachmentId: string;
      filename: string;
      bytes: number;
      ts: number;
    }
  | {
      id: string;
      kind: "artifact";
      runId: string;
      artifactId: string;
      name: string;
      artifactKind: string;
      bytes: number;
      ts: number;
    }
  | {
      id: string;
      kind: "usage";
      runId: string;
      iterations: number | null;
      inputTokens: number | null;
      outputTokens: number | null;
      durationMs: number | null;
      observedLmUsage: Record<string, unknown>;
      executionSummary?: ExecutionSummary;
      ts: number;
    }
  | { id: string; kind: "warning"; runId: string; code: string; message: string; ts: number }
  | { id: string; kind: "error"; text: string; ts: number };

export type Run = {
  id: string | null;
  phase: Phase;
  statusPhase: string | null;
  statusDetail: string | null;
  delivery: "live" | "replay" | null;
  outcome: "completed" | "failed" | "cancelled" | "interrupted" | null;
  abortReason: string | null;
  startedAt: number | null;
  endedAt: number | null;
  finishReason: string | null;
  error: string | null;
  durationMs: number | null;
  checkpointVersion: number | null;
  toolCount: number;
  startedSteps: number;
  completedSteps: number;
  traceId: string | null;
};

export type Session = {
  id: string;
  title: string;
  status: string;
  resumed: boolean;
};

export type PendingSkillSelection = {
  id: string;
  expectedVersion: string;
  displayName: string;
};

export type State = {
  session: Session | null;
  messages: Message[];
  run: Run;
  pendingSkillSelections: PendingSkillSelection[];
};

let messageCounter = 0;

export function newMessageId(prefix: string): string {
  messageCounter += 1;
  return `${prefix}-${messageCounter}-${Date.now().toString(36)}`;
}

function initialState(): State {
  return {
    session: null,
    messages: [],
    run: {
      id: null,
      phase: "idle",
      statusPhase: null,
      statusDetail: null,
      delivery: null,
      outcome: null,
      abortReason: null,
      startedAt: null,
      endedAt: null,
      finishReason: null,
      error: null,
      durationMs: null,
      checkpointVersion: null,
      toolCount: 0,
      startedSteps: 0,
      completedSteps: 0,
      traceId: null,
    },
    pendingSkillSelections: [],
  };
}

type Event =
  | { type: "session/init"; session: Session }
  | { type: "session/hydrate"; session: Session; events: Event[] }
  | { type: "user/submit"; text: string }
  | {
      type: "run/start";
      runId: string;
      delivery: "live" | "replay" | null;
      traceId?: string | null;
    }
  | { type: "run/step-start" }
  | { type: "run/step-finish" }
  | { type: "run/status"; phase: string; detail: string }
  | {
      type: "run/finish";
      finishReason: string | null;
      error: string | null;
      durationMs: number | null;
      checkpointVersion: number | null;
      traceId?: string | null;
    }
  | { type: "run/cancelling" }
  | { type: "run/cancelled"; reason: string }
  | { type: "run/interrupted"; error: string }
  | { type: "message/upsert"; message: Message }
  | { type: "message/patch"; id: string; patch: Partial<Message> }
  | { type: "skill-selection/pin"; selection: PendingSkillSelection }
  | { type: "skill-selection/clear" }
  | { type: "skill-selection/replace"; selections: PendingSkillSelection[] }
  | { type: "skill-selection/consume"; selections: PendingSkillSelection[] }
  | { type: "clear" }
  | { type: "reset" };

type Listener = () => void;

export class ConversationStore {
  private state: State = initialState();
  private listeners: Set<Listener> = new Set();
  private cancelToken: AbortController | null = null;

  getState(): State {
    return this.state;
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  setCancelToken(controller: AbortController | null): void {
    this.cancelToken = controller;
  }

  clearCancelToken(controller: AbortController): void {
    if (this.cancelToken === controller) this.cancelToken = null;
  }

  cancelActiveRun(): AbortController | null {
    if (this.state.run.phase !== "running" && this.state.run.phase !== "submitting") {
      return null;
    }
    if (this.cancelToken && !this.cancelToken.signal.aborted) {
      this.cancelToken.abort();
    }
    this.update({ type: "run/cancelling" });
    return this.cancelToken;
  }

  dispatch(event: Event): void {
    this.update(event);
  }

  private update(event: Event): void {
    const next = reduce(this.state, event);
    if (next === this.state) return;
    this.state = next;
    for (const listener of this.listeners) listener();
  }
}

function settleStreamingMessages(messages: Message[], runId: string | null): Message[] {
  let changed = false;
  const settled = messages.map((message) => {
    const belongsToRun =
      message.kind === "text"
        ? message.role === "assistant" && (message.runId === runId || message.runId === undefined)
        : "runId" in message && message.runId === runId;
    if (!belongsToRun) return message;

    if (message.kind === "text" && message.streaming) {
      changed = true;
      return { ...message, streaming: false };
    }
    if ((message.kind === "code" || message.kind === "output") && message.streaming) {
      changed = true;
      return { ...message, streaming: false };
    }
    return message;
  });
  return changed ? settled : messages;
}

function reduce(state: State, event: Event): State {
  switch (event.type) {
    case "session/init":
      return { ...state, session: event.session };
    case "session/hydrate": {
      const hydrated = event.events.reduce<State>(reduce, {
        ...initialState(),
        session: event.session,
      });
      return { ...hydrated, run: initialState().run };
    }
    case "user/submit": {
      const userMessage: Message = {
        id: newMessageId("user"),
        kind: "text",
        role: "user",
        text: event.text,
        ts: Date.now(),
        streaming: false,
      };
      return {
        ...state,
        messages: [...state.messages, userMessage],
        run: { ...initialState().run, phase: "submitting", startedAt: Date.now() },
      };
    }
    case "run/start":
      return {
        ...state,
        run: {
          ...state.run,
          id: event.runId,
          phase: "running",
          statusPhase: null,
          statusDetail: null,
          delivery: event.delivery,
          outcome: null,
          abortReason: null,
          startedAt: Date.now(),
          endedAt: null,
          finishReason: null,
          error: null,
          durationMs: null,
          checkpointVersion: null,
          toolCount: 0,
          startedSteps: 0,
          completedSteps: 0,
          traceId: event.traceId ?? null,
        },
      };
    case "run/step-start":
      return {
        ...state,
        run: { ...state.run, startedSteps: state.run.startedSteps + 1 },
      };
    case "run/step-finish":
      return {
        ...state,
        run: { ...state.run, completedSteps: state.run.completedSteps + 1 },
      };
    case "run/status":
      return {
        ...state,
        run: {
          ...state.run,
          statusPhase: event.phase,
          statusDetail: event.detail,
        },
      };
    case "run/finish":
      return {
        ...state,
        messages: settleStreamingMessages(state.messages, state.run.id),
        run: {
          ...state.run,
          phase: event.error ? "error" : "completed",
          outcome: event.error ? "failed" : "completed",
          statusPhase: null,
          statusDetail: null,
          endedAt: Date.now(),
          finishReason: event.finishReason,
          error: event.error,
          durationMs: event.durationMs,
          checkpointVersion: event.checkpointVersion,
          traceId: event.traceId ?? state.run.traceId,
        },
      };
    case "run/cancelling":
      return { ...state, run: { ...state.run, phase: "cancelling" } };
    case "run/cancelled":
      return {
        ...state,
        messages: settleStreamingMessages(state.messages, state.run.id),
        run: {
          ...state.run,
          phase: "idle",
          outcome: "cancelled",
          abortReason: event.reason,
          statusPhase: null,
          statusDetail: null,
          endedAt: Date.now(),
        },
      };
    case "run/interrupted":
      return {
        ...state,
        messages: settleStreamingMessages(state.messages, state.run.id),
        run: {
          ...state.run,
          phase: "error",
          outcome: "interrupted",
          error: event.error,
          statusPhase: null,
          statusDetail: null,
          endedAt: Date.now(),
        },
      };
    case "message/upsert": {
      const incoming =
        event.message.kind === "usage"
          ? {
              ...event.message,
              executionSummary: summarizeExecution(
                [
                  ...state.messages.filter((message) => message.id !== event.message.id),
                  event.message,
                ],
                event.message.runId,
              ),
            }
          : event.message;
      const existing = state.messages.findIndex((m) => m.id === incoming.id);
      let run = state.run;
      if (existing < 0 && incoming.kind === "tool") {
        run = { ...run, toolCount: run.toolCount + 1 };
      }
      if (existing >= 0) {
        const messages = state.messages.slice();
        messages[existing] = incoming;
        return { ...state, messages, run };
      }
      if (incoming.kind === "reasoning") {
        const reasoning = incoming;
        const firstStepDetail = state.messages.findIndex(
          (message) =>
            (message.kind === "code" || message.kind === "output") &&
            message.runId === reasoning.runId &&
            message.step === reasoning.step,
        );
        if (firstStepDetail >= 0) {
          const messages = state.messages.slice();
          messages.splice(firstStepDetail, 0, reasoning);
          return { ...state, messages, run };
        }
      }
      return { ...state, messages: [...state.messages, incoming], run };
    }
    case "message/patch": {
      const existing = state.messages.findIndex((m) => m.id === event.id);
      if (existing < 0) return state;
      const target = state.messages[existing] as Message;
      const run = state.run;
      if (target.kind === "text") {
        const messages = state.messages.slice();
        messages[existing] = { ...target, ...event.patch } as Message;
        return { ...state, messages, run };
      }
      const messages = state.messages.slice();
      messages[existing] = { ...target, ...event.patch } as Message;
      return { ...state, messages, run };
    }
    case "skill-selection/pin": {
      const existing = state.pendingSkillSelections.findIndex(
        (selection) => selection.id === event.selection.id,
      );
      if (existing >= 0) {
        const pendingSkillSelections = state.pendingSkillSelections.slice();
        pendingSkillSelections[existing] = event.selection;
        return { ...state, pendingSkillSelections };
      }
      if (state.pendingSkillSelections.length >= 4) return state;
      return {
        ...state,
        pendingSkillSelections: [...state.pendingSkillSelections, event.selection],
      };
    }
    case "skill-selection/clear":
      return state.pendingSkillSelections.length === 0
        ? state
        : { ...state, pendingSkillSelections: [] };
    case "skill-selection/replace":
      return { ...state, pendingSkillSelections: event.selections.slice(0, 4) };
    case "skill-selection/consume": {
      const consumed = new Set(
        event.selections.map((selection) => `${selection.id}\u0000${selection.expectedVersion}`),
      );
      const pendingSkillSelections = state.pendingSkillSelections.filter(
        (selection) => !consumed.has(`${selection.id}\u0000${selection.expectedVersion}`),
      );
      return pendingSkillSelections.length === state.pendingSkillSelections.length
        ? state
        : { ...state, pendingSkillSelections };
    }
    case "clear":
      return {
        ...state,
        messages: [],
        run: initialState().run,
      };
    case "reset":
      return initialState();
    default:
      return state;
  }
}
export type { Event as StoreEvent };
