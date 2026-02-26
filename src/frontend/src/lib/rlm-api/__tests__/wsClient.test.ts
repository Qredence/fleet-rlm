import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { WsCommandRequest, WsMessageRequest } from "../wsClient";

const MockWebSocket = vi.fn();
MockWebSocket.prototype.addEventListener = vi.fn();
MockWebSocket.prototype.removeEventListener = vi.fn();
MockWebSocket.prototype.send = vi.fn();
MockWebSocket.prototype.close = vi.fn();
MockWebSocket.prototype.readyState = 0;
Object.assign(MockWebSocket, {
  CONNECTING: 0,
  OPEN: 1,
  CLOSING: 2,
  CLOSED: 3,
});

vi.stubGlobal("WebSocket", MockWebSocket);
vi.stubGlobal("localStorage", {
  getItem: vi.fn(() => null),
});

async function loadWsClientModule() {
  vi.resetModules();
  return import("../wsClient");
}

interface MockWebSocketInstance {
  readyState: number;
  addEventListener: (
    event: string,
    cb: (...args: unknown[]) => void,
  ) => void;
  removeEventListener: import("vitest").Mock;
  send: import("vitest").Mock;
  close: import("vitest").Mock;
  trigger: (event: string, arg?: unknown) => void;
}

function installSocketFactory() {
  let wsInstanceCount = 0;
  const sockets: MockWebSocketInstance[] = [];

  MockWebSocket.mockImplementation(function (this: MockWebSocketInstance) {
    wsInstanceCount += 1;
    const listeners: Record<string, Array<(...args: unknown[]) => void>> = {};

    this.readyState = 0;
    this.addEventListener = (
      event: string,
      cb: (...args: unknown[]) => void,
    ) => {
      if (!listeners[event]) listeners[event] = [];
      listeners[event].push(cb);
    };
    this.removeEventListener = vi.fn();
    this.send = vi.fn();
    this.close = vi.fn();

    this.trigger = (event: string, arg?: unknown) => {
      if (event === "open") {
        this.readyState = 1;
      }
      if (event === "close") {
        this.readyState = 3;
      }
      for (const cb of listeners[event] || []) cb(arg);
    };

    sockets.push(this);
    return this;
  });

  return {
    sockets,
    getCount: () => wsInstanceCount,
  };
}

const dummyMessage: WsMessageRequest = {
  type: "message",
  content: "test",
  session_id: "test",
};

const dummyCommand: WsCommandRequest = {
  type: "command",
  command: "resolve_hitl",
  args: {
    message_id: "hitl-1",
    action_label: "Approve",
  },
  session_id: "test",
};

describe("streamChatOverWs - Reconnection & Backoff", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it("attempts to reconnect on close until max retries", async () => {
    vi.stubEnv("VITE_FLEET_WS_URL", "ws://localhost:8000/api/v1/ws/chat");
    const { streamChatOverWs } = await loadWsClientModule();
    const { sockets, getCount } = installSocketFactory();

    const statusChangeMock = vi.fn();

    const streamPromise = streamChatOverWs(dummyMessage, {
      onFrame: vi.fn(),
      onStatusChange: statusChangeMock,
      maxRetries: 2,
      initialBackoff: 10,
      maxBackoff: 100,
    });

    await Promise.resolve();
    expect(getCount()).toBe(1);
    expect(statusChangeMock).toHaveBeenCalledWith("connecting");

    sockets[0]?.trigger("close", { code: 1006, wasClean: false });
    await vi.advanceTimersByTimeAsync(15);

    expect(getCount()).toBe(2);
    expect(statusChangeMock).toHaveBeenCalledWith("reconnecting");

    sockets[1]?.trigger("close", { code: 1006, wasClean: false });
    await vi.advanceTimersByTimeAsync(25);

    expect(getCount()).toBe(3);

    sockets[2]?.trigger("close", { code: 1006, wasClean: false });

    await expect(streamPromise).rejects.toThrow(/after 2 retries/i);
  });

  it("treats final event as terminal for chat stream", async () => {
    vi.stubEnv("VITE_FLEET_WS_URL", "ws://localhost:8000/api/v1/ws/chat");
    const { streamChatOverWs } = await loadWsClientModule();
    const { sockets, getCount } = installSocketFactory();

    const streamPromise = streamChatOverWs(dummyMessage, {
      onFrame: vi.fn(),
      maxRetries: 0,
      initialBackoff: 10,
      maxBackoff: 100,
    });

    await Promise.resolve();
    expect(getCount()).toBe(1);

    sockets[0]?.trigger("open");
    sockets[0]?.trigger("message", {
      data: JSON.stringify({
        type: "event",
        data: {
          kind: "final",
          text: "done",
        },
      }),
    });

    await expect(streamPromise).resolves.toBeUndefined();
    expect(sockets[0]?.close).toHaveBeenCalled();
  });

  it("ignores malformed frames and continues processing subsequent frames", async () => {
    vi.stubEnv("VITE_FLEET_WS_URL", "ws://localhost:8000/api/v1/ws/chat");
    const { streamChatOverWs } = await loadWsClientModule();
    const { sockets } = installSocketFactory();

    const onFrame = vi.fn();
    const streamPromise = streamChatOverWs(dummyMessage, {
      onFrame,
      maxRetries: 0,
      initialBackoff: 10,
      maxBackoff: 100,
    });

    await Promise.resolve();

    sockets[0]?.trigger("open");
    sockets[0]?.trigger("message", { data: "{not-json" });
    sockets[0]?.trigger("message", {
      data: JSON.stringify({
        type: "event",
        data: {
          kind: "final",
          text: "done",
        },
      }),
    });

    await expect(streamPromise).resolves.toBeUndefined();
    expect(onFrame).toHaveBeenCalledTimes(1);
  });

  it("sends cancel and closes socket when aborted", async () => {
    vi.stubEnv("VITE_FLEET_WS_URL", "ws://localhost:8000/api/v1/ws/chat");
    const { streamChatOverWs } = await loadWsClientModule();
    const { sockets } = installSocketFactory();

    const controller = new AbortController();
    const streamPromise = streamChatOverWs(dummyMessage, {
      onFrame: vi.fn(),
      signal: controller.signal,
      maxRetries: 0,
      initialBackoff: 10,
      maxBackoff: 100,
    });

    await Promise.resolve();

    sockets[0]?.trigger("open");
    controller.abort();

    await expect(streamPromise).resolves.toBeUndefined();
    expect(sockets[0]?.send).toHaveBeenCalledWith(
      JSON.stringify({ type: "cancel" }),
    );
    expect(sockets[0]?.close).toHaveBeenCalled();
  });

  it("keeps execution subscriptions open after execution_completed frames", async () => {
    vi.stubEnv("VITE_FLEET_WS_URL", "ws://localhost:8000/api/v1/ws/chat");
    const { subscribeToExecutionStream } = await loadWsClientModule();
    const { sockets, getCount } = installSocketFactory();

    const onFrame = vi.fn();
    const unsubscribe = subscribeToExecutionStream("session-1", {
      onFrame,
      maxRetries: 0,
      initialBackoff: 10,
      maxBackoff: 100,
    });

    await Promise.resolve();
    expect(getCount()).toBe(1);

    sockets[0]?.trigger("open");
    sockets[0]?.trigger("message", {
      data: JSON.stringify({
        type: "execution_completed",
        output: "run completed",
        timestamp: new Date().toISOString(),
      }),
    });

    await Promise.resolve();
    expect(onFrame).toHaveBeenCalledTimes(1);
    expect(sockets[0]?.close).not.toHaveBeenCalled();

    sockets[0]?.trigger("message", {
      data: JSON.stringify({
        type: "execution_step",
        step: {
          id: "step-2",
          type: "tool",
          label: "Tool result",
          output: "ok",
          timestamp: Date.now() / 1000,
        },
      }),
    });

    await Promise.resolve();
    expect(onFrame).toHaveBeenCalledTimes(2);

    unsubscribe();
  });

  it("does not retry command requests by default", async () => {
    vi.stubEnv("VITE_FLEET_WS_URL", "ws://localhost:8000/api/v1/ws/chat");
    const { sendCommandOverWs } = await loadWsClientModule();
    const { sockets, getCount } = installSocketFactory();

    const commandPromise = sendCommandOverWs(dummyCommand, {
      onFrame: vi.fn(),
      initialBackoff: 10,
      maxBackoff: 100,
    });

    await Promise.resolve();
    expect(getCount()).toBe(1);

    sockets[0]?.trigger("open");
    sockets[0]?.trigger("close", { code: 1006, wasClean: false });

    await expect(commandPromise).rejects.toThrow(/after 0 retries/i);
    expect(getCount()).toBe(1);
  });
});
