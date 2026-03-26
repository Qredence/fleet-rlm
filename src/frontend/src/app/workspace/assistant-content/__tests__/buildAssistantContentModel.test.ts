import { describe, expect, it } from "vite-plus/test";
import {
  buildAssistantContentModel,
  getRuntimeBadgeStrings,
} from "@/app/workspace/assistant-content/model";
import type {
  AssistantTurnDisplayItem,
  AssistantTurnReasoningItem,
  AssistantTurnTrajectoryItem,
} from "@/lib/workspace/chat-display-items";
import type {
  ChatMessage,
  ChatRenderPart,
  RuntimeContext,
} from "@/screens/workspace/use-workspace";

const runtimeContext: RuntimeContext = {
  depth: 1,
  maxDepth: 3,
  executionProfile: "RLM_DELEGATE",
  sandboxActive: true,
  effectiveMaxIters: 20,
  executionMode: "rlm",
  sandboxId: "sb-1234567890",
};

function makeAssistantMessage(
  renderParts?: ChatRenderPart[],
  content = "Answer",
): ChatMessage {
  return {
    id: "assistant-message",
    type: "assistant",
    content,
    phase: 1,
    renderParts,
  };
}

function makeTraceMessage(id: string): ChatMessage {
  return {
    id,
    type: "trace",
    content: "",
    traceSource: "summary",
    phase: 1,
  };
}

function makeReasoningPart(
  text: string,
  label = "reasoning",
): Extract<ChatRenderPart, { kind: "reasoning" }> {
  return {
    kind: "reasoning",
    label,
    parts: [{ type: "text", text }],
    isStreaming: false,
    runtimeContext,
  };
}

function makeTrajectoryPart(
  steps: Array<{
    id: string;
    index?: number;
    label: string;
    status: "pending" | "active" | "complete" | "error";
    details?: string[];
  }>,
): Extract<ChatRenderPart, { kind: "chain_of_thought" }> {
  return {
    kind: "chain_of_thought",
    title: "Trajectory",
    steps,
    runtimeContext,
  };
}

function makeReasoningItem(
  key: string,
  part: Extract<ChatRenderPart, { kind: "reasoning" }>,
): AssistantTurnReasoningItem {
  return {
    key,
    message: makeTraceMessage(`${key}-message`),
    part,
  };
}

function makeTrajectoryItem(
  key: string,
  part: Extract<ChatRenderPart, { kind: "chain_of_thought" }>,
): AssistantTurnTrajectoryItem {
  return {
    key,
    message: makeTraceMessage(`${key}-message`),
    part,
  };
}

function makeAssistantTurn(
  overrides: Partial<AssistantTurnDisplayItem> = {},
): AssistantTurnDisplayItem {
  return {
    kind: "assistant_turn",
    key: "assistant-turn",
    turnId: "turn-1",
    message: makeAssistantMessage(),
    isPendingShell: false,
    reasoningItems: [],
    trajectoryItems: [],
    attachedToolSessions: [],
    attachedTraceParts: [],
    ...overrides,
  };
}

describe("buildAssistantContentModel", () => {
  it("preserves runtime badge formatting in the canonical model helpers", () => {
    expect(
      getRuntimeBadgeStrings({
        ...runtimeContext,
        depth: 0,
        runtimeMode: "daytona_pilot",
        volumeName: "rlm-volume-dspy",
      }),
    ).toEqual([
      "runtime daytona_pilot",
      "mode rlm",
      "sandbox",
      "sandbox sb-1234567",
      "rlm-volume-dspy",
      "rlm delegate",
    ]);
  });

  it("builds overview and sorted timeline items from trace reasoning and trajectory data", () => {
    const item = makeAssistantTurn({
      reasoningItems: [
        makeReasoningItem("overview", makeReasoningPart("Inspect repository")),
        makeReasoningItem(
          "thought-1",
          makeReasoningPart("Second fallback thought", "thought_1"),
        ),
        makeReasoningItem(
          "thought-0",
          makeReasoningPart("First fallback thought", "thought_0"),
        ),
        makeReasoningItem(
          "final",
          makeReasoningPart("All set", "final_reasoning"),
        ),
      ],
      trajectoryItems: [
        makeTrajectoryItem(
          "trajectory",
          makeTrajectoryPart([
            {
              id: "step-1",
              index: 1,
              label: "Tool: grep_repo",
              status: "complete",
              details: ["Observation · Found usage"],
            },
            {
              id: "step-0",
              index: 0,
              label: "Tool: list_files",
              status: "complete",
              details: ["Observation · Found entrypoint"],
            },
          ]),
        ),
      ],
    });

    const model = buildAssistantContentModel(item);

    expect(model.trajectory.overview).toMatchObject({
      label: "Planning",
      text: "Inspect repository",
    });
    expect(model.trajectory.items.map((entry) => entry.title)).toEqual([
      "Trajectory 01 · List files",
      "Trajectory 02 · Grep repo",
      "Synthesis",
    ]);
    expect(model.trajectory.items.map((entry) => entry.source)).toEqual([
      "cot",
      "cot",
      "final_reasoning",
    ]);
    expect(model.trajectory.items.at(-1)?.body).toBe("All set");
    expect(model.summary.runtimeBadges).toContain("depth 1/3");
    expect(model.summary.runtimeBadges).toContain("sandbox");
  });

  it("includes message render parts and replaces duplicate final synthesis", () => {
    const item = makeAssistantTurn({
      message: makeAssistantMessage([
        makeTrajectoryPart([
          {
            id: "step-0",
            index: 0,
            label: "Tool: inspect_entrypoint",
            status: "complete",
            details: ["Observation · Found entrypoint"],
          },
        ]),
        makeReasoningPart("Observation · Found entrypoint", "final_reasoning"),
      ]),
    });

    const model = buildAssistantContentModel(item);

    expect(model.trajectory.items).toHaveLength(1);
    expect(model.trajectory.items[0]).toMatchObject({
      title: "Synthesis",
      body: "Observation · Found entrypoint",
      source: "final_reasoning",
    });
    expect(model.trajectory.displayMode).toBe("timeline");
  });
});
