/**
 * Test helpers for tui-ink
 * Provides mock implementations for testing BridgeClient and components
 */

import type { BridgeClient, BridgeEvent } from "./bridge.js";

/**
 * Creates a fully mocked BridgeClient for testing
 * All methods are replaced with jest-style mocks that can be spied on
 */
export function createMockBridgeClient(): MockedBridgeClient {
  const eventListeners = new Set<(event: BridgeEvent) => void>();
  const errorListeners = new Set<(error: Error) => void>();

  const mockClient = {
    // Lifecycle
    start: () => {},
    shutdown: () => Promise.resolve(),

    // Event handling
    onEvent: (listener: (event: BridgeEvent) => void) => {
      eventListeners.add(listener);
      return () => eventListeners.delete(listener);
    },
    onError: (listener: (error: Error) => void) => {
      errorListeners.add(listener);
      return () => errorListeners.delete(listener);
    },

    // Core request method
    request: () => Promise.resolve({}),

    // State methods
    stateGet: () => Promise.resolve({ value: undefined, found: false }),
    stateSet: () => Promise.resolve({ ok: true }),
    stateDelete: () => Promise.resolve({ ok: true, existed: false }),
    stateList: () => Promise.resolve({ keys: [], count: 0 }),
    stateClear: () => Promise.resolve({ ok: true, deletedCount: 0 }),

    // Volume methods
    volumeRead: () =>
      Promise.resolve({
        content: "",
        path: "",
        volume_name: "",
        encoding: "utf-8",
      }),
    volumeWrite: () =>
      Promise.resolve({
        ok: true,
        path: "",
        volume_name: "",
        bytes_written: 0,
      }),
    volumeList: () =>
      Promise.resolve({
        files: [],
        count: 0,
        volume_name: "",
      }),
    volumeDelete: () =>
      Promise.resolve({
        ok: true,
        path: "",
        volume_name: "",
      }),
    volumeInfo: () =>
      Promise.resolve({
        name: "",
        version: 0,
        exists: false,
        file_count: 0,
        dir_count: 0,
      }),

    // Memory methods
    memoryRead: <T = unknown>() =>
      Promise.resolve({
        content: undefined as T,
        path: "",
        volume_name: "",
        encoding: "",
      }),
    memoryWrite: () =>
      Promise.resolve({
        ok: true,
        path: "",
        volume_name: "",
        bytes_written: 0,
        key: "",
      }),
    memoryList: () =>
      Promise.resolve({
        keys: [],
        count: 0,
        volume_name: "",
      }),

    // Sandbox methods
    sandboxList: () =>
      Promise.resolve({
        sandboxes: [],
        count: 0,
      }),
    sandboxExec: () =>
      Promise.resolve({
        ok: true,
        returncode: 0,
        stdout: "",
        stderr: "",
        sandbox_id: "",
      }),

    // Test utilities
    _eventListeners: eventListeners,
    _errorListeners: errorListeners,
    _simulateEvent: (event: BridgeEvent) => {
      for (const listener of eventListeners) {
        listener(event);
      }
    },
    _simulateError: (error: Error) => {
      for (const listener of errorListeners) {
        listener(error);
      }
    },
  } as unknown as MockedBridgeClient;

  return mockClient;
}

/**
 * Mocked BridgeClient interface with test utilities
 */
export interface MockedBridgeClient extends BridgeClient {
  /** Access internal event listeners for testing */
  _eventListeners: Set<(event: BridgeEvent) => void>;
  /** Access internal error listeners for testing */
  _errorListeners: Set<(error: Error) => void>;
  /** Simulate an incoming event to all listeners */
  _simulateEvent: (event: BridgeEvent) => void;
  /** Simulate an error to all listeners */
  _simulateError: (error: Error) => void;
}

/**
 * Creates a mock child process for testing BridgeClient internals
 */
export function createMockChildProcess(): MockedChildProcess {
  const eventHandlers: Record<string, Array<(...args: unknown[]) => void>> = {
    exit: [],
    error: [],
    data: [],
    line: [],
    close: [],
  };

  return {
    stdin: {
      destroyed: false,
      write: (data: string) => {
        for (const handler of eventHandlers.data ?? []) {
          handler(data);
        }
        return true;
      },
      end: () => {},
    },
    stdout: {
      on: (event: string, handler: (data: string) => void) => {
        if (!eventHandlers[event]) eventHandlers[event] = [];
        eventHandlers[event].push(handler as (...args: unknown[]) => void);
      },
    },
    stderr: {
      on: (event: string, handler: (data: Buffer) => void) => {
        if (!eventHandlers[event]) eventHandlers[event] = [];
        eventHandlers[event].push(handler as (...args: unknown[]) => void);
      },
    },
    kill: (signal: string) => {
      for (const handler of eventHandlers.exit ?? []) {
        handler(0, signal);
      }
      return true;
    },
    killed: false,
    on: (event: string, handler: (...args: unknown[]) => void) => {
      if (!eventHandlers[event]) eventHandlers[event] = [];
      eventHandlers[event].push(handler);
    },
    _trigger: (event: string, ...args: unknown[]) => {
      for (const handler of eventHandlers[event] ?? []) {
        handler(...args);
      }
    },
  };
}

/**
 * Mocked ChildProcess interface
 */
export interface MockedChildProcess {
  stdin: {
    destroyed: boolean;
    write: (data: string) => boolean;
    end: () => void;
  };
  stdout: {
    on: (event: string, listener: (data: string) => void) => void;
  };
  stderr: {
    on: (event: string, listener: (data: Buffer) => void) => void;
  };
  kill: (signal: string) => boolean;
  killed: boolean;
  on: (event: string, listener: (...args: unknown[]) => void) => void;
  /** Trigger an event manually for testing */
  _trigger: (event: string, ...args: unknown[]) => void;
}

/**
 * Waits for a specified duration
 */
export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Creates a deferred promise that can be resolved/rejected externally
 */
export function createDeferred<T>(): Deferred<T> {
  let resolveFn: (value: T) => void = () => {};
  let rejectFn: (error: Error) => void = () => {};

  const promise = new Promise<T>((resolve, reject) => {
    resolveFn = resolve;
    rejectFn = reject;
  });

  return {
    promise,
    resolve: resolveFn,
    reject: rejectFn,
  };
}

export interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (error: Error) => void;
}

/**
 * Sample event payloads for testing
 */
export const sampleEvents = {
  assistantToken: {
    event: "chat.event",
    params: { kind: "assistant_token", text: "Hello" },
  },
  assistantTokenBatch: {
    event: "chat.event",
    params: { kind: "assistant_token_batch", text: "Hello world" },
  },
  toolCall: {
    event: "chat.event",
    params: { kind: "tool_call", text: "tool call: read_file", payload: { path: "/file.txt" } },
  },
  toolResult: {
    event: "chat.event",
    params: { kind: "tool_result", text: "success", payload: {} },
  },
  final: {
    event: "chat.event",
    params: { kind: "final", text: "Final answer", payload: {} },
  },
  error: {
    event: "chat.event",
    params: { kind: "error", text: "Something went wrong", payload: {} },
  },
  cancelled: {
    event: "chat.event",
    params: { kind: "cancelled", text: "", payload: {} },
  },
  reasoningStep: {
    event: "chat.event",
    params: { kind: "reasoning_step", text: "I need to think about this...", payload: {} },
  },
  status: {
    event: "chat.event",
    params: { kind: "status", text: "Running module: Predict", payload: { module: "Predict" } },
  },
};

/**
 * Creates a mock transcript line
 */
export function createMockTranscriptLine(
  role: "system" | "user" | "assistant" | "tool" | "status" | "error",
  text: string,
) {
  return {
    id: `line-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    role,
    text,
  };
}

/**
 * Creates multiple mock transcript lines
 */
export function createMockTranscript(count: number) {
  const roles: Array<"system" | "user" | "assistant" | "tool" | "status" | "error"> = [
    "system",
    "user",
    "assistant",
    "tool",
    "status",
    "error",
  ];

  return Array.from({ length: count }, (_, i) =>
    createMockTranscriptLine(roles[i % roles.length], `Message ${i + 1}`),
  );
}
