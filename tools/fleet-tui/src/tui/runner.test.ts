import { afterEach, describe, expect, it, vi } from "vitest";

import { FleetApiClient, FleetApiError } from "../fleet-api-client.js";
import { RunController } from "./runner.js";
import { ConversationStore } from "./store.js";

const originalFetch = globalThis.fetch;

function sseResponse(body: string): Response {
  return new Response(body, { headers: { "x-vercel-ai-ui-message-stream": "v1" } });
}

function completedResponse(runId: string, text: string): Response {
  return sseResponse(
    [
      `data: {"type":"start","messageId":"${runId}","messageMetadata":{}}\n\n`,
      `data: {"type":"text-delta","id":"t","delta":"${text}"}\n\n`,
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
  it("projects a completed turn and clears its active controller", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(completedResponse("r-1", "yo"));
    const { store, controller } = setup();

    controller.start("hi");
    await vi.waitFor(() => expect(store.getState().run.phase).toBe("completed"));

    const assistant = store
      .getState()
      .messages.find((message) => message.kind === "text" && message.role === "assistant");
    expect(assistant).toMatchObject({ kind: "text", text: "yo", streaming: true });
    expect(controller.isRunning()).toBe(false);
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

  it("an aborted overlapping run can never cancel the replacement run", async () => {
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
    const second = new Response(
      new ReadableStream<Uint8Array>({
        start(stream) {
          stream.enqueue(
            encoder.encode('data: {"type":"start","messageId":"run-b","messageMetadata":{}}\n\n'),
          );
        },
      }),
    );
    const { client, store, controller } = setup();
    client.streamTurn = vi.fn().mockResolvedValueOnce(first).mockResolvedValueOnce(second);
    client.requestCancellation = vi.fn().mockResolvedValue({ status: "cancelled" });

    controller.start("first");
    await vi.waitFor(() => expect(store.getState().run.id).toBe("run-a"));
    controller.start("second");
    await vi.waitFor(() => expect(store.getState().run.id).toBe("run-b"));
    failFirst?.();
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(client.requestCancellation).toHaveBeenCalledWith("run-a");
    expect(client.requestCancellation).not.toHaveBeenCalledWith("run-b");
  });
});
