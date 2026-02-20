/**
 * Unit tests for backendChatEventAdapter.ts
 *
 * Tests the `applyWsFrameToMessages` function which transforms raw WebSocket
 * server frames into the frontend's `ChatMessage[]` list.
 */
import { describe, expect, it } from "vitest";
import { applyWsFrameToMessages } from "@/app/pages/skill-creation/backendChatEventAdapter";
import type { ChatMessage } from "@/lib/data/types";
import type { WsServerMessage } from "@/lib/rlm-api";

// ── helpers ────────────────────────────────────────────────────────────────────
function makeEvent(
  kind: string,
  text: string,
  payload?: Record<string, unknown>,
): WsServerMessage {
  return {
    type: "event",
    data: { kind: kind as never, text, payload },
  };
}

function makeError(message: string): WsServerMessage {
  return { type: "error", message };
}

// ── suite ──────────────────────────────────────────────────────────────────────
describe("applyWsFrameToMessages", () => {
  // ── error frame ──────────────────────────────────────────────────────────────
  describe("error frame (type: 'error')", () => {
    it("appends a system message with the backend error text", () => {
      const { messages, terminal, errored } = applyWsFrameToMessages(
        [],
        makeError("Something went wrong"),
      );
      expect(errored).toBe(true);
      expect(terminal).toBe(true);
      expect(messages).toHaveLength(1);
      const msg = messages[0];
      expect(msg?.type).toBe("system");
      expect(msg?.content).toContain("Something went wrong");
    });

    it("closes any open reasoning bubble before adding the error message", () => {
      const initialMessages: ChatMessage[] = [
        {
          id: "r1",
          type: "reasoning",
          content: "",
          phase: 1,
          reasoningData: {
            parts: [{ type: "text", text: "thinking..." }],
            isThinking: true,
          },
        },
      ];

      const { messages } = applyWsFrameToMessages(
        initialMessages,
        makeError("Oops"),
      );

      // The reasoning bubble should be closed (isThinking: false)
      const reasoning = messages.find((m) => m.type === "reasoning");
      expect(reasoning?.reasoningData?.isThinking).toBe(false);
    });
  });

  // ── assistant_token ──────────────────────────────────────────────────────────
  describe("assistant_token event", () => {
    it("creates a streaming assistant bubble and appends the token", () => {
      const { messages, terminal, errored } = applyWsFrameToMessages(
        [],
        makeEvent("assistant_token", "Hello"),
      );
      expect(terminal).toBe(false);
      expect(errored).toBe(false);
      expect(messages).toHaveLength(1);
      const msg = messages[0];
      expect(msg?.type).toBe("assistant");
      expect(msg?.content).toContain("Hello");
      expect(
        (msg as Extract<ChatMessage, { type: "assistant" }>).streaming,
      ).toBe(true);
    });

    it("accumulates multiple tokens into the same bubble", () => {
      let msgs: ChatMessage[] = [];
      msgs = applyWsFrameToMessages(
        msgs,
        makeEvent("assistant_token", "Hello"),
      ).messages;
      msgs = applyWsFrameToMessages(
        msgs,
        makeEvent("assistant_token", " world"),
      ).messages;
      msgs = applyWsFrameToMessages(
        msgs,
        makeEvent("assistant_token", "!"),
      ).messages;

      // Still only one assistant bubble
      const assistantMsgs = msgs.filter((m) => m.type === "assistant");
      expect(assistantMsgs).toHaveLength(1);
      expect(assistantMsgs[0]?.content).toBe("Hello world!");
    });
  });

  // ── reasoning_step ───────────────────────────────────────────────────────────
  describe("reasoning_step event", () => {
    it("creates a reasoning bubble with isThinking: true", () => {
      const { messages } = applyWsFrameToMessages(
        [],
        makeEvent("reasoning_step", "Analyzing input"),
      );
      const reasoning = messages.find((m) => m.type === "reasoning");
      expect(reasoning).toBeDefined();
      expect(reasoning?.reasoningData?.isThinking).toBe(true);
      expect(reasoning?.reasoningData?.parts[0]?.text).toBe("Analyzing input");
    });

    it("appends additional steps to the same open reasoning bubble", () => {
      let msgs: ChatMessage[] = [];
      msgs = applyWsFrameToMessages(
        msgs,
        makeEvent("reasoning_step", "Step 1"),
      ).messages;
      msgs = applyWsFrameToMessages(
        msgs,
        makeEvent("reasoning_step", "Step 2"),
      ).messages;

      const reasoning = msgs.find((m) => m.type === "reasoning");
      expect(reasoning?.reasoningData?.parts).toHaveLength(2);
    });
  });

  // ── final ─────────────────────────────────────────────────────────────────────
  describe("final event", () => {
    it("closes the streaming assistant bubble with the final text", () => {
      // Start with a streaming assistant bubble
      let msgs: ChatMessage[] = [];
      msgs = applyWsFrameToMessages(
        msgs,
        makeEvent("assistant_token", "Hello"),
      ).messages;

      // Apply the final event with a complete response
      const { messages, terminal } = applyWsFrameToMessages(
        msgs,
        makeEvent("final", "Hello, how can I help?"),
      );

      expect(terminal).toBe(true);
      const assistant = messages.find((m) => m.type === "assistant");
      expect(
        (assistant as Extract<ChatMessage, { type: "assistant" }>)?.streaming,
      ).toBe(false);
      expect(assistant?.content).toBe("Hello, how can I help?");
    });

    it("closes any open reasoning bubbles on final", () => {
      let msgs: ChatMessage[] = [];
      msgs = applyWsFrameToMessages(
        msgs,
        makeEvent("reasoning_step", "Thinking..."),
      ).messages;
      msgs = applyWsFrameToMessages(msgs, makeEvent("final", "Done")).messages;

      const reasoning = msgs.find((m) => m.type === "reasoning");
      expect(reasoning?.reasoningData?.isThinking).toBe(false);
    });

    it("creates a new assistant bubble if none existed", () => {
      const { messages } = applyWsFrameToMessages(
        [],
        makeEvent("final", "Standalone response"),
      );
      const assistant = messages.find((m) => m.type === "assistant");
      expect(assistant?.content).toBe("Standalone response");
    });

    it("appends guardrail warnings as a system message", () => {
      const { messages } = applyWsFrameToMessages(
        [],
        makeEvent("final", "ok", {
          guardrail_warnings: ["Potentially harmful content detected"],
        }),
      );

      const system = messages.find((m) => m.type === "system");
      expect(system).toBeDefined();
      expect(system?.content).toContain("Potentially harmful content detected");
    });
  });

  // ── cancelled ─────────────────────────────────────────────────────────────────
  describe("cancelled event", () => {
    it("marks stream as terminal and appends a cancellation notice", () => {
      const { messages, terminal } = applyWsFrameToMessages(
        [],
        makeEvent("cancelled", ""),
      );
      expect(terminal).toBe(true);
      const sys = messages.find((m) => m.type === "system");
      expect(sys?.content).toContain("cancelled");
    });
  });

  // ── status / tool_call / tool_result ──────────────────────────────────────────
  describe("tool events (status, tool_call, tool_result)", () => {
    it.each(["status", "tool_call", "tool_result"] as const)(
      "%s event creates a reasoning-style message (not terminal)",
      (kind) => {
        const { messages, terminal, errored } = applyWsFrameToMessages(
          [],
          makeEvent(kind, "some info"),
        );
        expect(terminal).toBe(false);
        expect(errored).toBe(false);
        expect(messages).toHaveLength(1);
      },
    );
  });
});
