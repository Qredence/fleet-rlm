import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { WsMessageRequest } from "../wsClient";

// ── Mocks ────────────────────────────────────────────────────────────────────

// Polyfill global fetch if needed, though we just mock WebSocket here.
const MockWebSocket = vi.fn();
MockWebSocket.prototype.addEventListener = vi.fn();
MockWebSocket.prototype.removeEventListener = vi.fn();
MockWebSocket.prototype.send = vi.fn();
MockWebSocket.prototype.close = vi.fn();
MockWebSocket.prototype.readyState = 0; // CONNECTING

vi.stubGlobal("WebSocket", MockWebSocket);
vi.stubGlobal("localStorage", {
  getItem: vi.fn(() => null),
});

async function loadWsClientModule() {
  vi.resetModules();
  return import("../wsClient");
}

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

  const dummyMessage: WsMessageRequest = {
    type: "message",
    content: "test",
    session_id: "test",
  };

  it("attempts to reconnect on close until max retries", async () => {
    vi.stubEnv("VITE_FLEET_WS_URL", "ws://localhost:8000/api/v1/ws/chat");
    const { streamChatOverWs } = await loadWsClientModule();

    let wsInstanceCount = 0;

    // We capture each newly created ws inside the mock
    interface MockWebSocketInstance {
      readyState: number;
      addEventListener: (
        event: string,
        cb: (...args: unknown[]) => void,
      ) => void;
      removeEventListener: import("vitest").Mock<never, never[]>;
      send: import("vitest").Mock<never, never[]>;
      close: import("vitest").Mock<never, never[]>;
      trigger: (event: string, arg?: unknown) => void;
    }
    const sockets: MockWebSocketInstance[] = [];

    MockWebSocket.mockImplementation(function (this: MockWebSocketInstance) {
      wsInstanceCount++;
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

      // helper to trigger events on this socket
      this.trigger = (event: string, arg?: unknown) => {
        for (const cb of listeners[event] || []) cb(arg);
      };

      sockets.push(this);
      return this;
    });

    const statusChangeMock = vi.fn();

    // Start streaming
    const streamPromise = streamChatOverWs(dummyMessage, {
      onFrame: vi.fn(),
      onStatusChange: statusChangeMock,
      maxRetries: 2,
      initialBackoff: 10,
      maxBackoff: 100,
    });

    // Attempt 0
    await Promise.resolve(); // flush microtasks
    expect(wsInstanceCount).toBe(1);
    expect(statusChangeMock).toHaveBeenCalledWith("connecting");

    // Simulate connection failure on Attempt 0
    sockets[0].trigger("close", { code: 1006, wasClean: false });

    // Backoff is initialBackoff = 10ms
    await vi.advanceTimersByTimeAsync(15);

    // Attempt 1
    expect(wsInstanceCount).toBe(2);
    expect(statusChangeMock).toHaveBeenCalledWith("reconnecting");

    // Simulate connection failure on Attempt 1
    sockets[1].trigger("close", { code: 1006, wasClean: false });

    // Backoff is calculateBackoff(attempt - 1) -> calculateBackoff(1) -> 10 * 2^1 = 20ms
    await vi.advanceTimersByTimeAsync(25);

    // Attempt 2
    expect(wsInstanceCount).toBe(3);

    // Simulate connection failure on Attempt 2
    sockets[2].trigger("close", { code: 1006, wasClean: false });

    // Now it should throw since maxRetries = 2
    await expect(streamPromise).rejects.toThrow(/after 2 retries/i);
  });
});
