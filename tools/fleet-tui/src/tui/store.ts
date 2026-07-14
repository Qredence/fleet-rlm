/** Multi-turn conversation store. Single source of truth for the Ink TUI. */

import { useSyncExternalStore } from "react";

export type Phase = "idle" | "submitting" | "running" | "cancelling" | "completed" | "error";

export type Role = "user" | "assistant" | "system";

export type Message =
  | { id: string; kind: "text"; role: Role; text: string; ts: number; streaming: boolean }
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
      ts: number;
    }
  | {
      id: string;
      kind: "output";
      runId: string;
      step: number;
      output: string;
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
      version: string;
      trust: string;
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
      prompt: number;
      completion: number;
      toolCalls: number;
      llmCalls: number;
      ts: number;
    }
  | { id: string; kind: "warning"; runId: string; code: string; message: string; ts: number }
  | { id: string; kind: "status"; runId: string; phase: string; detail: string; ts: number }
  | { id: string; kind: "error"; text: string; ts: number };

export type Run = {
  id: string | null;
  phase: Phase;
  model: string | null;
  startedAt: number | null;
  endedAt: number | null;
  finishReason: string | null;
  error: string | null;
  toolCount: number;
  completedSteps: number;
};

export type Session = {
  id: string;
  title: string;
  status: string;
  resumed: boolean;
};

export type State = {
  session: Session | null;
  messages: Message[];
  run: Run;
  selectedId: string | null;
  focusedGroupId: string | null;
  expandedIds: Set<string>;
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
      model: null,
      startedAt: null,
      endedAt: null,
      finishReason: null,
      error: null,
      toolCount: 0,
      completedSteps: 0,
    },
    selectedId: null,
    focusedGroupId: null,
    expandedIds: new Set(),
  };
}

type Event =
  | { type: "session/init"; session: Session }
  | { type: "session/hydrate"; session: Session; events: Event[] }
  | { type: "user/submit"; text: string }
  | { type: "run/start"; runId: string; model: string | null }
  | { type: "run/finish"; finishReason: string | null; error: string | null }
  | { type: "run/cancelling" }
  | { type: "run/cancelled" }
  | { type: "message/upsert"; message: Message }
  | { type: "message/patch"; id: string; patch: Partial<Message> }
  | { type: "message/toggle-expanded"; id: string }
  | { type: "focus/set"; id: string | null }
  | { type: "focus/group"; groupId: string | null }
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

function reduce(state: State, event: Event): State {
  switch (event.type) {
    case "session/init":
      return { ...state, session: event.session };
    case "session/hydrate":
      return event.events.reduce<State>(reduce, {
        ...initialState(),
        session: event.session,
      });
    case "user/submit": {
      const userMessage: Message = {
        id: newMessageId("user"),
        kind: "text",
        role: "user",
        text: event.text,
        ts: Date.now(),
        streaming: false,
      };
      const assistantMessage: Message = {
        id: newMessageId("assistant"),
        kind: "text",
        role: "assistant",
        text: "",
        ts: Date.now(),
        streaming: true,
      };
      return {
        ...state,
        messages: [...state.messages, userMessage, assistantMessage],
        run: { ...state.run, phase: "submitting" },
      };
    }
    case "run/start":
      return {
        ...state,
        run: {
          ...state.run,
          id: event.runId,
          phase: "running",
          model: event.model ?? state.run.model,
          startedAt: Date.now(),
          endedAt: null,
          finishReason: null,
          error: null,
          toolCount: 0,
          completedSteps: 0,
        },
      };
    case "run/finish":
      return {
        ...state,
        run: {
          ...state.run,
          phase: event.error ? "error" : "completed",
          endedAt: Date.now(),
          finishReason: event.finishReason,
          error: event.error,
        },
      };
    case "run/cancelling":
      return { ...state, run: { ...state.run, phase: "cancelling" } };
    case "run/cancelled":
      return { ...state, run: { ...state.run, phase: "idle" } };
    case "message/upsert": {
      const existing = state.messages.findIndex((m) => m.id === event.message.id);
      const expandedIds = new Set(state.expandedIds);
      if (isToggleableMessage(event.message)) {
        expandedIds.add(event.message.id);
      }
      let run = state.run;
      if (existing < 0 && event.message.kind === "tool") {
        run = { ...run, toolCount: run.toolCount + 1 };
      }
      if (existing < 0 && event.message.kind === "output") {
        run = { ...run, completedSteps: run.completedSteps + 1 };
      }
      if (existing >= 0) {
        const messages = state.messages.slice();
        messages[existing] = event.message;
        return { ...state, messages, expandedIds, run };
      }
      return { ...state, messages: [...state.messages, event.message], expandedIds, run };
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
    case "message/toggle-expanded": {
      const expanded = new Set(state.expandedIds);
      if (expanded.has(event.id)) {
        expanded.delete(event.id);
      } else {
        expanded.add(event.id);
      }
      return { ...state, expandedIds: expanded };
    }
    case "focus/set":
      return { ...state, selectedId: event.id };
    case "focus/group":
      return { ...state, focusedGroupId: event.groupId };
    case "clear":
      return {
        ...state,
        messages: [],
        run: { ...state.run, phase: "idle" },
        selectedId: null,
        focusedGroupId: null,
        expandedIds: new Set(),
      };
    case "reset":
      return initialState();
    default:
      return state;
  }
}

export function useConversationStore(store: ConversationStore): State {
  return useSyncExternalStore(
    (listener) => store.subscribe(listener),
    () => store.getState(),
    () => store.getState(),
  );
}

export function isToggleableMessage(message: Message): boolean {
  return (
    message.kind === "reasoning" ||
    message.kind === "tool" ||
    message.kind === "code" ||
    message.kind === "output" ||
    message.kind === "result"
  );
}

export function listToggleableMessageIds(messages: Message[]): string[] {
  return messages.filter(isToggleableMessage).map((message) => message.id);
}

export type { Event as StoreEvent };
