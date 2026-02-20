import { rlmApiClient } from "./client";
import type {
  ChatRequest,
  ChatResponse,
  HealthResponse,
  ReadyResponse,
  SessionStateResponse,
  TaskRequest,
  TaskResponse,
} from "./types";

const DEFAULT_TASK_BODY: Omit<TaskRequest, "task_type" | "docs_path"> = {
  question: "",
  query: "",
  max_iterations: 15,
  max_llm_calls: 30,
  timeout: 600,
  chars: 10_000,
  verbose: true,
};

export interface ChatInput {
  message: string;
  docs_path?: string | null;
  trace?: boolean;
}

export interface TaskInput {
  question?: string;
  docs_path?: string | null;
  query?: string;
  max_iterations?: number;
  max_llm_calls?: number;
  timeout?: number;
  chars?: number;
  verbose?: boolean;
}

function toTaskBody(
  taskType: TaskRequest["task_type"],
  input?: TaskInput,
): TaskRequest {
  return {
    task_type: taskType,
    ...DEFAULT_TASK_BODY,
    ...input,
  };
}

export const rlmCoreEndpoints = {
  health(signal?: AbortSignal) {
    return rlmApiClient.get<HealthResponse>("/health", signal);
  },

  ready(signal?: AbortSignal) {
    return rlmApiClient.get<ReadyResponse>("/ready", signal);
  },

  chat(input: ChatInput, signal?: AbortSignal) {
    const body: ChatRequest = {
      message: input.message,
      docs_path: input.docs_path ?? null,
      trace: input.trace ?? false,
    };
    return rlmApiClient.post<ChatResponse>("/chat", body, signal);
  },

  runBasic(input?: TaskInput, signal?: AbortSignal) {
    return rlmApiClient.post<TaskResponse>(
      "/tasks/basic",
      toTaskBody("basic", input),
      signal,
    );
  },

  runArchitecture(input?: TaskInput, signal?: AbortSignal) {
    return rlmApiClient.post<TaskResponse>(
      "/tasks/architecture",
      toTaskBody("architecture", input),
      signal,
    );
  },

  runLongContext(input?: TaskInput, signal?: AbortSignal) {
    return rlmApiClient.post<TaskResponse>(
      "/tasks/long-context",
      toTaskBody("long_context", input),
      signal,
    );
  },

  checkSecret(signal?: AbortSignal) {
    return rlmApiClient.post<TaskResponse>(
      "/tasks/check-secret",
      undefined,
      signal,
    );
  },

  sessionsState(signal?: AbortSignal) {
    return rlmApiClient.get<SessionStateResponse>("/sessions/state", signal);
  },
};
