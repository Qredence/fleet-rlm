import { afterEach, describe, expect, it, vi } from "vitest";

import { FleetApiClient, FleetApiError } from "../fleet-api-client.js";
import type { FleetTurn } from "../fleet-api-client.js";
import { projectDurableTurns } from "./projection.js";
import { RunController } from "./runner.js";
import { ConversationStore, type Message } from "./store.js";

const originalFetch = globalThis.fetch;

function sseResponse(body: string): Response {
  return new Response(body, { headers: { "x-vercel-ai-ui-message-stream": "v1" } });
}

function completedResponse(runId: string, text: string): Response {
  return sseResponse(
    [
      `data: {"type":"start","messageId":"${runId}","messageMetadata":{}}\n\n`,
      `data: {"type":"text-start","id":"text-${runId}"}\n\n`,
      `data: {"type":"text-delta","id":"text-${runId}","delta":"${text}"}\n\n`,
      `data: {"type":"text-end","id":"text-${runId}"}\n\n`,
      'data: {"type":"finish","finishReason":"stop"}\n\n',
      "data: [DONE]\n\n",
    ].join(""),
  );
}

function setup(): { client: FleetApiClient; store: ConversationStore; controller: RunController } {
  const client = new FleetApiClient({ baseUrl: "http://fleet.test" });
  const store = new ConversationStore();
  store.dispatch({
    type: "session/init",
    session: { id: "s", title: "t", status: "active", resumed: false },
  });
  return { client, store, controller: new RunController(store, client) };
}

afterEach(() => {
  globalThis.fetch = originalFetch;
});

describe("RunController", () => {
  it("publishes execution events while the Turn stream is still open", async () => {
    const encoder = new TextEncoder();
    let streamController: ReadableStreamDefaultController<Uint8Array> | undefined;
    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(
        new ReadableStream<Uint8Array>({
          start(controller) {
            streamController = controller;
          },
        }),
        { headers: { "x-vercel-ai-ui-message-stream": "v1" } },
      ),
    );
    const { store, controller } = setup();

    controller.start("stream the work");
    await vi.waitFor(() => expect(streamController).toBeDefined());
    streamController?.enqueue(
      encoder.encode(
        [
          'data: {"type":"start","messageId":"run-streaming","messageMetadata":{"delivery":"live"}}\n\n',
          'data: {"type":"data-status","data":{"phase":"execution","status":"running"},"transient":true}\n\n',
          'data: {"type":"start-step"}\n\n',
          'data: {"type":"reasoning-start","id":"reasoning-run-streaming-1"}\n\n',
          'data: {"type":"reasoning-delta","id":"reasoning-run-streaming-1","delta":"inspect"}\n\n',
        ].join(""),
      ),
    );

    await vi.waitFor(() => {
      expect(store.getState().run).toMatchObject({
        phase: "running",
        delivery: "live",
        statusPhase: "execution",
        statusDetail: "running",
        startedSteps: 1,
        completedSteps: 0,
      });
      expect(store.getState().messages.at(-1)).toMatchObject({
        kind: "reasoning",
        text: "inspect",
      });
    });

    streamController?.enqueue(
      encoder.encode(
        [
          'data: {"type":"data-rlm-code","id":"code-run-streaming-1","data":{"step":1,"code":"print(1)"}}\n\n',
          'data: {"type":"data-rlm-output","id":"output-run-streaming-1","data":{"step":1,"output":"1"}}\n\n',
          'data: {"type":"finish-step"}\n\n',
        ].join(""),
      ),
    );

    await vi.waitFor(() =>
      expect(
        store
          .getState()
          .messages.slice(-2)
          .map((message) => message.kind),
      ).toEqual(["code", "output"]),
    );
    expect(store.getState().run.completedSteps).toBe(1);
    expect(controller.isRunning()).toBe(true);

    streamController?.enqueue(
      encoder.encode(
        [
          'data: {"type":"finish","finishReason":"stop","messageMetadata":{"durationMs":1250,"checkpointVersion":4}}\n\n',
          "data: [DONE]\n\n",
        ].join(""),
      ),
    );
    streamController?.close();

    await vi.waitFor(() =>
      expect(store.getState().run).toMatchObject({
        phase: "completed",
        outcome: "completed",
        durationMs: 1250,
        checkpointVersion: 4,
      }),
    );
  });

  it("marks a transport failure after stream open as interrupted without replaying", async () => {
    const encoder = new TextEncoder();
    let streamController: ReadableStreamDefaultController<Uint8Array> | undefined;
    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(
        new ReadableStream<Uint8Array>({
          start(stream) {
            streamController = stream;
          },
        }),
        { headers: { "x-vercel-ai-ui-message-stream": "v1" } },
      ),
    );
    const { store, controller } = setup();

    controller.start("do not replay me");
    await vi.waitFor(() => expect(streamController).toBeDefined());
    streamController?.enqueue(
      encoder.encode(
        'data: {"type":"start","messageId":"run-interrupted","messageMetadata":{"delivery":"live"}}\n\n',
      ),
    );
    await vi.waitFor(() => expect(store.getState().run.id).toBe("run-interrupted"));
    streamController?.error(new Error("connection lost"));

    await vi.waitFor(() =>
      expect(store.getState().run).toMatchObject({
        id: "run-interrupted",
        outcome: "interrupted",
        error: "connection lost",
      }),
    );
    expect(store.getState().messages.at(-1)).toMatchObject({
      kind: "error",
      text: expect.stringContaining("/resume s"),
    });
    expect((store.getState().messages.at(-1) as Message & { text: string }).text).toContain(
      "prompt was not replayed",
    );
    expect(globalThis.fetch).toHaveBeenCalledTimes(1);
  });

  it("projects a completed turn and clears its active controller", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(completedResponse("r-1", "yo"));
    const { store, controller } = setup();

    controller.start("hi");
    await vi.waitFor(() => expect(store.getState().run.phase).toBe("completed"));

    const assistant = store
      .getState()
      .messages.find((message) => message.kind === "text" && message.role === "assistant");
    expect(assistant).toMatchObject({ kind: "text", text: "yo", streaming: false });
    expect(controller.isRunning()).toBe(false);
  });

  it("passes pending Skill selections and acknowledges them when the stream opens", async () => {
    const fetchMock = vi.fn().mockResolvedValue(completedResponse("r-skills", "done"));
    globalThis.fetch = fetchMock;
    const { store, controller } = setup();
    const onStreamOpen = vi.fn();

    controller.start("use the skill", {
      skillSelections: [{ id: "00000000-0000-4000-8000-000000000001", expected_version: "2.0.0" }],
      onStreamOpen,
    });
    await vi.waitFor(() => expect(store.getState().run.phase).toBe("completed"));

    expect(JSON.parse(fetchMock.mock.calls[0]?.[1]?.body as string)).toMatchObject({
      skill_selections: [{ id: "00000000-0000-4000-8000-000000000001", expected_version: "2.0.0" }],
    });
    expect(onStreamOpen).toHaveBeenCalledTimes(1);
  });

  it("renders a completed text Turn identically live and after hydration", async () => {
    globalThis.fetch = vi
      .fn()
      .mockResolvedValue(
        sseResponse(
          [
            'data: {"type":"start","messageId":"run-1","messageMetadata":{}}\n\n',
            'data: {"type":"data-status","data":{"phase":"execution","status":"running","message":null},"transient":true}\n\n',
            'data: {"type":"reasoning-start","id":"reasoning-run-1-1"}\n\n',
            'data: {"type":"reasoning-delta","id":"reasoning-run-1-1","delta":"check"}\n\n',
            'data: {"type":"reasoning-end","id":"reasoning-run-1-1"}\n\n',
            'data: {"type":"data-rlm-code","id":"code-run-1-1","data":{"step":1,"code":"print(1)"}}\n\n',
            'data: {"type":"data-rlm-output","id":"output-run-1-1","data":{"step":1,"output":"1"}}\n\n',
            'data: {"type":"data-usage","id":"usage-run-1","data":{"usage":{"iterations":1,"observed_lm_usage":{},"duration_ms":12}}}\n\n',
            'data: {"type":"text-start","id":"text-run-1"}\n\n',
            'data: {"type":"text-delta","id":"text-run-1","delta":"The answer is 1."}\n\n',
            'data: {"type":"text-end","id":"text-run-1"}\n\n',
            'data: {"type":"finish","finishReason":"stop"}\n\n',
            "data: [DONE]\n\n",
          ].join(""),
        ),
      );
    const { store, controller } = setup();

    controller.start("calculate");
    await vi.waitFor(() => expect(store.getState().run.phase).toBe("completed"));

    const live = store.getState().messages;
    const hydrated = projectedMessages(
      projectDurableTurns([
        {
          id: "user-1",
          role: "user",
          parts: [{ type: "text", text: "calculate", state: "done" }],
        },
        {
          id: "run-1",
          role: "assistant",
          metadata: { runId: "run-1" },
          parts: [
            { type: "data-step", data: { step: 1 } },
            { type: "reasoning", text: "check", state: "done" },
            { type: "data-rlm-code", data: { step: 1, code: "print(1)" } },
            { type: "data-rlm-output", data: { step: 1, output: "1" } },
            {
              type: "data-usage",
              data: { iterations: 1, observed_lm_usage: {}, duration_ms: 12 },
            },
            { type: "text", text: "The answer is 1.", state: "done" },
          ],
        },
      ] satisfies FleetTurn[]),
    );

    expect(live.map((message) => message.kind)).toEqual([
      "text",
      "reasoning",
      "code",
      "output",
      "usage",
      "text",
    ]);
    expect(visibleSemantics(live)).toEqual(visibleSemantics(hydrated));
    expect(store.getState().run.statusPhase).toBeNull();
    expect(store.getState().run.statusDetail).toBeNull();
  });

  it("keeps a trajectory-corrected live step identical to durable reload", async () => {
    globalThis.fetch = vi
      .fn()
      .mockResolvedValue(
        sseResponse(
          [
            'data: {"type":"start","messageId":"run-corrected","messageMetadata":{}}\n\n',
            'data: {"type":"data-rlm-code","id":"code-run-corrected-1","data":{"step":1,"code":"stale"}}\n\n',
            'data: {"type":"data-rlm-output","id":"output-run-corrected-1","data":{"step":1,"output":"stale"}}\n\n',
            'data: {"type":"data-rlm-code","id":"code-run-corrected-1","data":{"step":1,"code":"canonical"}}\n\n',
            'data: {"type":"data-rlm-output","id":"output-run-corrected-1","data":{"step":1,"output":"canonical"}}\n\n',
            'data: {"type":"reasoning-start","id":"reasoning-run-corrected-1"}\n\n',
            'data: {"type":"reasoning-delta","id":"reasoning-run-corrected-1","delta":"canonical reasoning"}\n\n',
            'data: {"type":"reasoning-end","id":"reasoning-run-corrected-1"}\n\n',
            'data: {"type":"finish","finishReason":"stop"}\n\n',
            "data: [DONE]\n\n",
          ].join(""),
        ),
      );
    const { store, controller } = setup();

    controller.start("correct the trace");
    await vi.waitFor(() => expect(store.getState().run.phase).toBe("completed"));

    const live = store
      .getState()
      .messages.filter(
        (message) =>
          message.kind === "reasoning" || message.kind === "code" || message.kind === "output",
      );
    const hydrated = projectedMessages(
      projectDurableTurns([
        {
          id: "run-corrected",
          role: "assistant",
          metadata: { runId: "run-corrected" },
          parts: [
            { type: "data-step", data: { step: 1 } },
            { type: "reasoning", text: "canonical reasoning", state: "done" },
            { type: "data-rlm-code", data: { step: 1, code: "canonical" } },
            { type: "data-rlm-output", data: { step: 1, output: "canonical" } },
          ],
        },
      ] satisfies FleetTurn[]),
    ).filter(
      (message) =>
        message.kind === "reasoning" || message.kind === "code" || message.kind === "output",
    );

    expect(live).toHaveLength(3);
    expect(visibleSemantics(live)).toEqual(visibleSemantics(hydrated));
  });

  it("renders structured output and repeated execution evidence identically after hydration", async () => {
    globalThis.fetch = vi
      .fn()
      .mockResolvedValue(
        sseResponse(
          [
            'data: {"type":"start","messageId":"run-structured","messageMetadata":{}}\n\n',
            'data: {"type":"data-status","data":{"phase":"execution","status":"running"},"transient":true}\n\n',
            'data: {"type":"reasoning-start","id":"reasoning-run-structured-1"}\n\n',
            'data: {"type":"reasoning-delta","id":"reasoning-run-structured-1","delta":"verify independently"}\n\n',
            'data: {"type":"reasoning-end","id":"reasoning-run-structured-1"}\n\n',
            'data: {"type":"data-rlm-output","id":"output-run-structured-1","data":{"step":1,"output":"candidate: 9"}}\n\n',
            'data: {"type":"reasoning-start","id":"reasoning-run-structured-2"}\n\n',
            'data: {"type":"reasoning-delta","id":"reasoning-run-structured-2","delta":"verify independently"}\n\n',
            'data: {"type":"reasoning-end","id":"reasoning-run-structured-2"}\n\n',
            'data: {"type":"data-rlm-output","id":"output-run-structured-2","data":{"step":2,"output":"verified: 1"}}\n\n',
            'data: {"type":"data-usage","id":"usage-run-structured","data":{"usage":{"iterations":2,"observed_lm_usage":{},"duration_ms":20}}}\n\n',
            'data: {"type":"data-structured-result","id":"result-run-structured","data":{"schemaId":"digit","schemaVersion":"1","value":{"digit":"1"}}}\n\n',
            'data: {"type":"text-start","id":"text-run-structured"}\n\n',
            'data: {"type":"text-delta","id":"text-run-structured","delta":"The verified digit is 1."}\n\n',
            'data: {"type":"text-end","id":"text-run-structured"}\n\n',
            'data: {"type":"finish","finishReason":"stop"}\n\n',
            "data: [DONE]\n\n",
          ].join(""),
        ),
      );
    const { store, controller } = setup();

    controller.start("find the digit");
    await vi.waitFor(() => expect(store.getState().run.phase).toBe("completed"));

    const live = store.getState().messages;
    const hydrated = projectedMessages(
      projectDurableTurns([
        {
          id: "user-structured",
          role: "user",
          parts: [{ type: "text", text: "find the digit", state: "done" }],
        },
        {
          id: "run-structured",
          role: "assistant",
          metadata: { runId: "run-structured" },
          parts: [
            { type: "data-step", data: { step: 1 } },
            { type: "reasoning", text: "verify independently", state: "done" },
            { type: "data-rlm-output", data: { step: 1, output: "candidate: 9" } },
            { type: "data-step", data: { step: 2 } },
            { type: "reasoning", text: "verify independently", state: "done" },
            { type: "data-rlm-output", data: { step: 2, output: "verified: 1" } },
            {
              type: "data-usage",
              data: { iterations: 2, observed_lm_usage: {}, duration_ms: 20 },
            },
            {
              type: "data-structured-result",
              data: { schemaId: "digit", schemaVersion: "1", value: { digit: "1" } },
            },
            { type: "text", text: "The verified digit is 1.", state: "done" },
          ],
        },
      ] satisfies FleetTurn[]),
    );

    expect(live.map((message) => message.kind)).toEqual([
      "text",
      "reasoning",
      "output",
      "reasoning",
      "output",
      "usage",
      "result",
    ]);
    expect(visibleSemantics(live)).toEqual(visibleSemantics(hydrated));
    expect(
      live.filter(
        (message): message is Extract<Message, { kind: "reasoning" }> =>
          message.kind === "reasoning",
      ),
    ).toHaveLength(2);
    expect(live.at(-1)).toMatchObject({
      kind: "result",
      value: { digit: "1" },
      narrative: "The verified digit is 1.",
    });
  });

  it("shows a correlated backend-log hint for Turn preparation failures", async () => {
    const { client, store, controller } = setup();
    client.streamTurn = vi
      .fn()
      .mockRejectedValue(new FleetApiError(503, "Turn is unavailable", "request-123"));

    controller.start("hi");
    await vi.waitFor(() => expect(store.getState().run.phase).toBe("error"));

    const error = store.getState().messages.find((message) => message.kind === "error");
    expect(error).toMatchObject({
      kind: "error",
      text: "Turn is unavailable (request request-123; see .fleet_rlm/logs/latest.log)",
    });
    expect(
      store
        .getState()
        .messages.some((message) => message.kind === "text" && message.role === "assistant"),
    ).toBe(false);
  });

  it("does not create an assistant message when execution fails", async () => {
    globalThis.fetch = vi
      .fn()
      .mockResolvedValue(
        sseResponse(
          [
            'data: {"type":"start","messageId":"run-failed","messageMetadata":{}}\n\n',
            'data: {"type":"data-status","data":{"phase":"execution","status":"running"},"transient":true}\n\n',
            'data: {"type":"error","errorText":"Turn failed"}\n\n',
            'data: {"type":"finish","finishReason":"error"}\n\n',
            "data: [DONE]\n\n",
          ].join(""),
        ),
      );
    const { store, controller } = setup();

    controller.start("fail");
    await vi.waitFor(() => expect(store.getState().run.phase).toBe("error"));

    expect(store.getState().messages.map((message) => message.kind)).toEqual(["text", "error"]);
    expect(store.getState().run.statusPhase).toBeNull();
  });

  it("does not create an assistant message when submission is cancelled", async () => {
    const { client, store, controller } = setup();
    client.streamTurn = vi.fn(
      ({ signal }) =>
        new Promise<Response>((_resolve, reject) => {
          signal?.addEventListener("abort", () =>
            reject(new DOMException("aborted", "AbortError")),
          );
        }),
    );

    controller.start("cancel");
    await vi.waitFor(() => expect(client.streamTurn).toHaveBeenCalled());
    controller.cancel();
    await vi.waitFor(() => expect(store.getState().run.phase).toBe("idle"));

    expect(store.getState().messages).toMatchObject([
      { kind: "text", role: "user", text: "cancel" },
    ]);
    expect(store.getState().run.statusPhase).toBeNull();
  });

  it("requests durable cancellation for an active run", async () => {
    const cancel = vi.fn().mockResolvedValue({ status: "cancelled" });
    const encoder = new TextEncoder();
    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(
        new ReadableStream<Uint8Array>({
          start(stream) {
            stream.enqueue(
              encoder.encode('data: {"type":"start","messageId":"r-2","messageMetadata":{}}\n\n'),
            );
          },
        }),
        { headers: { "x-vercel-ai-ui-message-stream": "v1" } },
      ),
    );
    const { client, store, controller } = setup();
    client.requestCancellation = cancel;

    controller.start("hi");
    await vi.waitFor(() => expect(store.getState().run.id).toBe("r-2"));
    controller.cancel();

    await vi.waitFor(() => expect(cancel).toHaveBeenCalledWith("r-2"));
  });

  it("never cancels a completed run when the next turn starts", async () => {
    globalThis.fetch = vi
      .fn()
      .mockResolvedValueOnce(completedResponse("r-1", "one"))
      .mockResolvedValueOnce(completedResponse("r-2", "two"));
    const { client, store, controller } = setup();
    client.requestCancellation = vi.fn();

    controller.start("first");
    await vi.waitFor(() => expect(controller.isRunning()).toBe(false));
    controller.start("second");
    await vi.waitFor(() => expect(store.getState().run.id).toBe("r-2"));

    expect(client.requestCancellation).not.toHaveBeenCalled();
  });

  it("appends each completed assistant answer without replacing the prior Turn", async () => {
    globalThis.fetch = vi
      .fn()
      .mockResolvedValueOnce(completedResponse("r-1", "one"))
      .mockResolvedValueOnce(completedResponse("r-2", "two"));
    const { store, controller } = setup();

    controller.start("first");
    await vi.waitFor(() => expect(controller.isRunning()).toBe(false));
    controller.start("second");
    await vi.waitFor(() => expect(controller.isRunning()).toBe(false));

    expect(
      store
        .getState()
        .messages.filter(
          (message): message is Extract<Message, { kind: "text" }> =>
            message.kind === "text" && message.role === "assistant",
        )
        .map((message) => message.text),
    ).toEqual(["one", "two"]);
  });

  it("prevents a replacement while a run is active", async () => {
    const encoder = new TextEncoder();
    let failFirst: (() => void) | undefined;
    const first = new Response(
      new ReadableStream<Uint8Array>({
        start(stream) {
          stream.enqueue(
            encoder.encode('data: {"type":"start","messageId":"run-a","messageMetadata":{}}\n\n'),
          );
          failFirst = () => stream.error(new DOMException("aborted", "AbortError"));
        },
      }),
    );
    const { client, store, controller } = setup();
    client.streamTurn = vi.fn().mockResolvedValueOnce(first);
    client.requestCancellation = vi.fn().mockResolvedValue({ status: "cancelled" });

    controller.start("first");
    await vi.waitFor(() => expect(store.getState().run.id).toBe("run-a"));
    controller.start("second");
    expect(client.streamTurn).toHaveBeenCalledTimes(1);
    expect(store.getState().run.id).toBe("run-a");
    controller.cancel();
    failFirst?.();
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(client.requestCancellation).toHaveBeenCalledWith("run-a");
  });
});

function projectedMessages(events: ReturnType<typeof projectDurableTurns>): Message[] {
  return events.flatMap((event) => (event.type === "message/upsert" ? [event.message] : []));
}

function visibleSemantics(messages: Message[]): unknown[] {
  return messages.map(({ id: _id, ts: _ts, ...message }) => {
    if ("runId" in message) {
      const { runId: _runId, ...visible } = message;
      return visible;
    }
    return message;
  });
}
