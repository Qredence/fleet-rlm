/**
 * Unit tests for the Zustand chat store (chatStore.ts).
 *
 * The `streamChatOverWs` function is mocked at the module level so that these
 * tests run completely offline without any WebSocket infrastructure.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";

// ── module mocks ──────────────────────────────────────────────────────────────
// Mock the entire rlm-api module before the store is imported so that the
// store's module-level reference to `streamChatOverWs` is the mock.
vi.mock("@/lib/rlm-api", () => ({
  streamChatOverWs: vi.fn(),
  createBackendSessionId: vi.fn(() => "mock-session-id"),
  rlmApiConfig: {
    trace: true,
    workspaceId: "test-workspace",
    userId: "test-user",
    wsUrl: "ws://localhost:8000/api/v1/ws/chat",
  },
}));

// Mock the event adapter so it is a pure pass-through in these tests
vi.mock("@/lib/workspace/backend-chat-event-adapter", () => ({
  applyWsFrameToMessages: vi.fn((messages, _frame) => ({
    messages,
    terminal: false,
    errored: false,
  })),
}));

vi.mock("@/lib/telemetry/client", () => ({
  telemetryClient: {
    isAnonymousTelemetryEnabled: vi.fn(() => true),
  },
}));

// ── imports ────────────────────────────────────────────────────────────────────
// Imported after vi.mock so the mocked versions are resolved.
import { streamChatOverWs } from "@/lib/rlm-api";
import { useChatStore } from "@/lib/workspace/chat-store";

// ── helpers ────────────────────────────────────────────────────────────────────
/** Reset Zustand store state between tests */
function resetStore() {
  useChatStore.setState({
    messages: [],
    isStreaming: false,
    sessionId: "mock-session-id",
    error: null,
    runtimeMode: "modal_chat",
    streamController: null,
  });
}

// ── suite ──────────────────────────────────────────────────────────────────────
describe("useChatStore — state management", () => {
  beforeEach(() => {
    resetStore();
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // ── initial state ──────────────────────────────────────────────────────────
  it("starts with empty messages, not streaming, and a session id", () => {
    const { messages, isStreaming, sessionId, error, runtimeMode } = useChatStore.getState();
    expect(messages).toEqual([]);
    expect(isStreaming).toBe(false);
    expect(typeof sessionId).toBe("string");
    expect(sessionId.length).toBeGreaterThan(0);
    expect(error).toBeNull();
    expect(runtimeMode).toBe("modal_chat");
  });

  // ── setSessionId ───────────────────────────────────────────────────────────
  it("setSessionId updates the session id", () => {
    useChatStore.getState().setSessionId("new-session");
    expect(useChatStore.getState().sessionId).toBe("new-session");
  });

  // ── addMessage ─────────────────────────────────────────────────────────────
  it("addMessage appends a message to the list", () => {
    const msg = {
      id: "m1",
      type: "user" as const,
      content: "Hello",
      phase: 1 as const,
    };
    useChatStore.getState().addMessage(msg);
    const { messages } = useChatStore.getState();
    expect(messages).toHaveLength(1);
    expect(messages[0]).toEqual(msg);
  });

  it("addMessage appends to existing messages", () => {
    const first = {
      id: "m1",
      type: "user" as const,
      content: "Hello",
      phase: 1 as const,
    };
    const second = {
      id: "m2",
      type: "assistant" as const,
      content: "Hi there",
      streaming: false,
      phase: 1 as const,
    };
    useChatStore.getState().addMessage(first);
    useChatStore.getState().addMessage(second);
    expect(useChatStore.getState().messages).toHaveLength(2);
  });

  // ── clearMessages ──────────────────────────────────────────────────────────
  it("clearMessages resets the messages array", () => {
    useChatStore.getState().addMessage({
      id: "m1",
      type: "user" as const,
      content: "Hello",
      phase: 1 as const,
    });
    useChatStore.getState().clearMessages();
    expect(useChatStore.getState().messages).toEqual([]);
  });

  // ── setMessages ────────────────────────────────────────────────────────────
  it("setMessages accepts a direct array", () => {
    const msgs = [{ id: "m1", type: "user" as const, content: "hi", phase: 1 as const }];
    useChatStore.getState().setMessages(msgs);
    expect(useChatStore.getState().messages).toEqual(msgs);
  });

  it("setMessages accepts an updater function", () => {
    const first = {
      id: "m1",
      type: "user" as const,
      content: "hi",
      phase: 1 as const,
    };
    useChatStore.getState().addMessage(first);
    useChatStore.getState().setMessages((prev) => [
      ...prev,
      {
        id: "m2",
        type: "system" as const,
        content: "msg",
        phase: 1 as const,
      },
    ]);
    expect(useChatStore.getState().messages).toHaveLength(2);
  });

  // ── resetSession ───────────────────────────────────────────────────────────
  it("resetSession clears messages and resets streaming state", () => {
    useChatStore.setState({
      messages: [
        {
          id: "m1",
          type: "user" as const,
          content: "hi",
          phase: 1 as const,
        },
      ],
      isStreaming: true,
      error: "some error",
    });
    useChatStore.getState().resetSession();
    const { messages, isStreaming, error } = useChatStore.getState();
    expect(messages).toEqual([]);
    expect(isStreaming).toBe(false);
    expect(error).toBeNull();
  });

  // ── stopStreaming ──────────────────────────────────────────────────────────
  it("stopStreaming aborts the active controller and resets streaming", () => {
    const controller = new AbortController();
    const abortSpy = vi.spyOn(controller, "abort");
    useChatStore.setState({ isStreaming: true, streamController: controller });

    useChatStore.getState().stopStreaming();

    expect(abortSpy).toHaveBeenCalledOnce();
    expect(useChatStore.getState().isStreaming).toBe(false);
    expect(useChatStore.getState().streamController).toBeNull();
  });

  it("stopStreaming is a no-op when no controller is active", () => {
    useChatStore.setState({ isStreaming: false, streamController: null });
    expect(() => useChatStore.getState().stopStreaming()).not.toThrow();
  });
});

// ── suite: streaming ───────────────────────────────────────────────────────────
describe("useChatStore — streamMessage", () => {
  beforeEach(() => {
    resetStore();
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("skips streaming when text is empty", async () => {
    await useChatStore.getState().streamMessage("   ");
    expect(streamChatOverWs).not.toHaveBeenCalled();
  });

  it("skips streaming when already streaming", async () => {
    useChatStore.setState({ isStreaming: true });
    await useChatStore.getState().streamMessage("hello");
    expect(streamChatOverWs).not.toHaveBeenCalled();
  });

  it("sets isStreaming to true while in flight, then false after", async () => {
    vi.mocked(streamChatOverWs).mockResolvedValue(undefined);

    const streamPromise = useChatStore.getState().streamMessage("test message");
    // streamChatOverWs was called, which means isStreaming was set to true.
    expect(streamChatOverWs).toHaveBeenCalledOnce();
    await streamPromise;
    expect(useChatStore.getState().isStreaming).toBe(false);
  });

  it("passes correct payload to streamChatOverWs", async () => {
    vi.mocked(streamChatOverWs).mockResolvedValue(undefined);
    useChatStore.setState({ sessionId: "sess-abc" });

    await useChatStore.getState().streamMessage("test");

    const [payload] = vi.mocked(streamChatOverWs).mock.calls[0] ?? [];
    expect(payload).toMatchObject({
      type: "message",
      content: "test",
      trace: true,
      trace_mode: "compact",
      runtime_mode: "modal_chat",
      execution_mode: "auto",
      analytics_enabled: true,
      session_id: "sess-abc",
    });
    expect(payload).not.toHaveProperty("workspace_id");
    expect(payload).not.toHaveProperty("user_id");
  });

  it("passes execution mode overrides to the websocket payload", async () => {
    vi.mocked(streamChatOverWs).mockResolvedValue(undefined);

    await useChatStore.getState().streamMessage("test", undefined, undefined, {
      executionMode: "tools_only",
    });

    const [payload] = vi.mocked(streamChatOverWs).mock.calls[0] ?? [];
    expect(payload).toMatchObject({
      runtime_mode: "modal_chat",
      execution_mode: "tools_only",
    });
  });

  it("keeps default Daytona websocket payloads free of repo setup fields", async () => {
    vi.mocked(streamChatOverWs).mockResolvedValue(undefined);
    useChatStore.setState({ runtimeMode: "daytona_pilot" });

    await useChatStore.getState().streamMessage("trace the repo");

    const [payload] = vi.mocked(streamChatOverWs).mock.calls[0] ?? [];
    expect(payload).toMatchObject({
      runtime_mode: "daytona_pilot",
    });
    expect(payload).not.toHaveProperty("execution_mode");
    expect(payload).not.toHaveProperty("repo_url");
    expect(payload).not.toHaveProperty("repo_ref");
    expect(payload).not.toHaveProperty("context_paths");
    expect(payload).not.toHaveProperty("max_depth");
    expect(payload).not.toHaveProperty("batch_concurrency");
  });

  it("still forwards explicit Daytona source fields and batch concurrency when passed as stream options", async () => {
    vi.mocked(streamChatOverWs).mockResolvedValue(undefined);
    useChatStore.setState({ runtimeMode: "daytona_pilot" });

    await useChatStore.getState().streamMessage("trace the repo", undefined, undefined, {
      repoUrl: "https://github.com/qredence/fleet-rlm.git",
      repoRef: "main",
      contextPaths: ["/Users/zocho/Documents/spec.pdf", "/workspace/docs"],
      batchConcurrency: 6,
    });

    const [payload] = vi.mocked(streamChatOverWs).mock.calls[0] ?? [];
    expect(payload).toMatchObject({
      runtime_mode: "daytona_pilot",
      repo_url: "https://github.com/qredence/fleet-rlm.git",
      repo_ref: "main",
      context_paths: ["/Users/zocho/Documents/spec.pdf", "/workspace/docs"],
      batch_concurrency: 6,
    });
    expect(payload).not.toHaveProperty("max_depth");
  });

  it("extends the first-frame timeout for Daytona streams", async () => {
    vi.mocked(streamChatOverWs).mockResolvedValue(undefined);
    useChatStore.setState({ runtimeMode: "daytona_pilot" });

    await useChatStore.getState().streamMessage("trace the repo");

    const [, options] = vi.mocked(streamChatOverWs).mock.calls[0] ?? [];
    expect(options).toMatchObject({
      firstFrameTimeoutMs: 60000,
    });
  });

  it("uses trace override=false to disable websocket trace mode", async () => {
    vi.mocked(streamChatOverWs).mockResolvedValue(undefined);

    await useChatStore
      .getState()
      .streamMessage("test", undefined, undefined, { traceEnabled: false });

    const [payload] = vi.mocked(streamChatOverWs).mock.calls[0] ?? [];
    expect(payload).toMatchObject({
      trace: false,
      trace_mode: "off",
    });
  });

  it("uses trace override=true to enable compact trace mode", async () => {
    vi.mocked(streamChatOverWs).mockResolvedValue(undefined);

    await useChatStore
      .getState()
      .streamMessage("test", undefined, undefined, { traceEnabled: true });

    const [payload] = vi.mocked(streamChatOverWs).mock.calls[0] ?? [];
    expect(payload).toMatchObject({
      trace: true,
      trace_mode: "compact",
    });
  });

  it("calls onFrameCallback for every yielded frame", async () => {
    const fakeFrame = {
      type: "event" as const,
      data: { kind: "assistant_token" as const, text: "Hi!" },
    };

    vi.mocked(streamChatOverWs).mockImplementation(async (_payload, opts) => {
      opts.onFrame(fakeFrame);
      opts.onFrame(fakeFrame);
    });

    const frameSpy = vi.fn();
    await useChatStore.getState().streamMessage("hello", frameSpy);
    expect(frameSpy).toHaveBeenCalledTimes(2);
    expect(frameSpy).toHaveBeenCalledWith(fakeFrame);
  });

  it("sets error in state and re-throws when streamChatOverWs rejects", async () => {
    const err = new Error("Stream failed");
    vi.mocked(streamChatOverWs).mockRejectedValue(err);

    await expect(useChatStore.getState().streamMessage("hello")).rejects.toThrow("Stream failed");

    expect(useChatStore.getState().error).toBe("Stream failed");
    expect(useChatStore.getState().isStreaming).toBe(false);
  });

  it("does not set error state when request is aborted", async () => {
    vi.mocked(streamChatOverWs).mockImplementation(async (_payload, opts) => {
      // Simulate the abort signal being triggered
      opts.signal?.dispatchEvent(new Event("abort"));
      const err = new Error("aborted");
      Object.assign(err, { name: "AbortError" });
      throw err;
    });

    // Manually trigger stopStreaming during the call
    vi.mocked(streamChatOverWs).mockImplementation(async () => {
      useChatStore.getState().stopStreaming();
    });

    // streamMessage should not set error for aborted requests
    await useChatStore.getState().streamMessage("hello");
    expect(useChatStore.getState().error).toBeNull();
  });
});
