import { describe, expect, it } from "vite-plus/test";
import { applyWsFrameToMessages } from "@/lib/workspace/backend-chat-event-adapter";
import type { WsServerMessage } from "@/lib/rlm-api";

function makeCompletionEvent(text: string, payload?: Record<string, unknown>): WsServerMessage {
  return {
    type: "event",
    data: {
      kind: "execution_completed",
      text,
      payload: {
        ...payload,
        source_type: "execution_completed",
        run_summary: { status: "completed" },
      },
    },
  };
}

describe("resolveFinalAssistantText - prefers full content over summary (VAL-A-018c)", () => {
  it("code_file artifact: returns value.content, not value.summary", () => {
    const fullCode =
      'def fibonacci(n):\n    """Calculate the first N Fibonacci numbers.\n\n    Args:\n        n: The number of Fibonacci numbers to generate.\n\n    Returns:\n        A list containing the first N Fibonacci numbers.\n    """\n    if n <= 0:\n        return []\n    if n == 1:\n        return [0]\n    fibs = [0, 1]\n    for i in range(2, n):\n        fibs.append(fibs[-1] + fibs[-2])\n    return fibs\n\n\n# Example usage\nif __name__ == "__main__":\n    result = fibonacci(10)\n    print(f"First 10 Fibonacci numbers: {result}")';
    const summary =
      "def fibonacci(n): Calculate the first N Fibonacci numbers. Args: n: The number of Fibonacci…";

    const frame = makeCompletionEvent("Canonical text", {
      final_artifact: {
        kind: "code_file",
        value: {
          content: fullCode,
          summary,
        },
      },
    });

    const result = applyWsFrameToMessages([], frame);
    const assistant = result.messages.find((m) => m.type === "assistant");

    expect(assistant?.content).toBe(fullCode);
    expect(assistant?.content).not.toBe(summary);
    expect(assistant?.content.length).toBeGreaterThan(320);
  });

  it("markdown artifact: returns value.final_markdown, not value.summary", () => {
    const fullMarkdown =
      "# Hash Map\n\nA hash map is a data structure...\n\n## Complexity\n\nTime complexity: O(1) average...\n\n## Collision Resolution\n\nThere are several strategies...";
    const summary = "# Hash Map A hash map is a data structure that implements…";

    const frame = makeCompletionEvent("Done", {
      final_artifact: {
        kind: "markdown",
        value: {
          final_markdown: fullMarkdown,
          summary,
        },
      },
    });

    const result = applyWsFrameToMessages([], frame);
    const assistant = result.messages.find((m) => m.type === "assistant");

    expect(assistant?.content).toBe(fullMarkdown);
    expect(assistant?.content).not.toBe(summary);
    expect(assistant?.content).toContain("## Collision Resolution");
  });

  it("assistant_response artifact: returns value.text, not value.summary", () => {
    const fullText = "Hello! I'm here to help you with your questions.";
    const summary = "Hello! I'm here to help…";

    const frame = makeCompletionEvent("Done", {
      final_artifact: {
        kind: "assistant_response",
        value: {
          text: fullText,
          summary,
        },
      },
    });

    const result = applyWsFrameToMessages([], frame);
    const assistant = result.messages.find((m) => m.type === "assistant");

    expect(assistant?.content).toBe(fullText);
    expect(assistant?.content).not.toBe(summary);
  });

  it("code_file artifact: falls back to value.text when value.content absent", () => {
    const fallbackText = "print('fallback')";
    const frame = makeCompletionEvent("Done", {
      final_artifact: {
        kind: "code_file",
        value: {
          text: fallbackText,
          summary: "short",
        },
      },
    });

    const result = applyWsFrameToMessages([], frame);
    const assistant = result.messages.find((m) => m.type === "assistant");

    expect(assistant?.content).toBe(fallbackText);
  });

  it("markdown artifact: falls back to value.content when value.final_markdown absent", () => {
    const fallbackContent = "# Fallback\nSome content here";
    const frame = makeCompletionEvent("Done", {
      final_artifact: {
        kind: "markdown",
        value: {
          content: fallbackContent,
          summary: "short",
        },
      },
    });

    const result = applyWsFrameToMessages([], frame);
    const assistant = result.messages.find((m) => m.type === "assistant");

    expect(assistant?.content).toBe(fallbackContent);
  });
});
