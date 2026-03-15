import type { AssistantTurnDisplayItem } from "@/features/rlm-workspace/chatDisplayItems";
import { getRuntimeBadgeStrings } from "@/features/rlm-workspace/assistant-content/runtimeBadges";
import type {
  CompactReasoning,
  TrajectoryItem,
} from "@/features/rlm-workspace/assistant-content/types";
import {
  humanizeLabel,
  uniqueStrings,
} from "@/features/rlm-workspace/assistant-content/modelUtils";

type MergedReasoningPart = {
  key: string;
  label: string;
  text: string;
  isStreaming: boolean;
  duration?: number;
  runtimeBadges: string[];
};

type OrderedTrajectoryItem = {
  originalOrder: number;
  item: TrajectoryItem;
};

export interface AssistantTrajectoryModel {
  overview?: CompactReasoning;
  items: TrajectoryItem[];
  hasCot: boolean;
}

function reasoningSectionOrder(label: string) {
  if (label === "reasoning") return 0;
  if (/^thought_\d+$/.test(label)) return 1;
  if (label === "final_reasoning") return 2;
  return 3;
}

function compareReasoningLabels(leftLabel: string, rightLabel: string) {
  const leftOrder = reasoningSectionOrder(leftLabel);
  const rightOrder = reasoningSectionOrder(rightLabel);
  if (leftOrder !== rightOrder) return leftOrder - rightOrder;

  const leftThought = leftLabel.match(/^thought_(\d+)$/);
  const rightThought = rightLabel.match(/^thought_(\d+)$/);
  if (leftThought && rightThought) {
    return Number(leftThought[1]) - Number(rightThought[1]);
  }

  return leftLabel.localeCompare(rightLabel);
}

function buildAssistantTurnReasoningParts(item: AssistantTurnDisplayItem) {
  const fromTrace = item.reasoningItems.map((reasoningItem) => ({
    key: reasoningItem.key,
    part: reasoningItem.part,
  }));
  const message = item.message;
  const fromMessage =
    message?.renderParts?.flatMap((part, idx) =>
      part.kind === "reasoning" ? [{ key: `${message.id}-${part.kind}-${idx}`, part }] : [],
    ) ?? [];
  return [...fromTrace, ...fromMessage];
}

function buildAssistantTurnTrajectoryParts(item: AssistantTurnDisplayItem) {
  const fromTrace = item.trajectoryItems.map((trajectoryItem) => ({
    key: trajectoryItem.key,
    part: trajectoryItem.part,
  }));
  const message = item.message;
  const fromMessage =
    message?.renderParts?.flatMap((part, idx) =>
      part.kind === "chain_of_thought" ? [{ key: `${message.id}-${part.kind}-${idx}`, part }] : [],
    ) ?? [];
  return [...fromTrace, ...fromMessage];
}

function mergeReasoningParts(reasoningParts: ReturnType<typeof buildAssistantTurnReasoningParts>) {
  if (reasoningParts.length === 0) return [] satisfies MergedReasoningPart[];

  const groups = new Map<
    string,
    {
      key: string;
      texts: string[];
      isStreaming: boolean;
      duration?: number;
      runtimeBadges: string[];
    }
  >();

  for (const { key, part } of reasoningParts) {
    const label = part.label?.trim() || "reasoning";
    const group = groups.get(label);
    if (group) {
      group.texts.push(...part.parts.map((entry) => entry.text));
      group.isStreaming = part.isStreaming;
      if (part.duration != null) group.duration = part.duration;
      group.runtimeBadges = uniqueStrings([
        ...group.runtimeBadges,
        ...getRuntimeBadgeStrings(part.runtimeContext),
      ]);
      continue;
    }
    groups.set(label, {
      key,
      texts: part.parts.map((entry) => entry.text),
      isStreaming: part.isStreaming,
      duration: part.duration,
      runtimeBadges: getRuntimeBadgeStrings(part.runtimeContext),
    });
  }

  return [...groups.entries()]
    .sort(([leftLabel], [rightLabel]) => compareReasoningLabels(leftLabel, rightLabel))
    .map(([label, group]) => ({
      key: group.key,
      label,
      text: group.texts.join(""),
      isStreaming: group.isStreaming,
      duration: group.duration,
      runtimeBadges: group.runtimeBadges,
    }));
}

function buildOverviewReasoning(
  mergedReasoning: MergedReasoningPart[],
): CompactReasoning | undefined {
  const overviewGroups = mergedReasoning.filter(
    (group) => !/^thought_\d+$/.test(group.label) && group.label !== "final_reasoning",
  );
  if (overviewGroups.length === 0) return undefined;

  return {
    key: overviewGroups[0]?.key ?? "assistant-overview",
    label: "Planning",
    text: overviewGroups.map((group) => group.text).join("\n\n"),
    duration: overviewGroups[overviewGroups.length - 1]?.duration,
    isStreaming: overviewGroups.some((group) => group.isStreaming),
    runtimeBadges: uniqueStrings(overviewGroups.flatMap((group) => group.runtimeBadges)),
  };
}

function parseThoughtIndex(label: string): number | undefined {
  const match = label.match(/^thought_(\d+)$/);
  if (!match) return undefined;
  return Number(match[1]);
}

function trajectoryTitle(index: number, label?: string) {
  const prefix = `Trajectory ${String(index + 1).padStart(2, "0")}`;
  return label?.trim() ? `${prefix} · ${humanizeLabel(label)}` : prefix;
}

function trajectoryBody(details?: string[]) {
  return details?.filter(Boolean).join("\n\n") ?? "";
}

function mapTrajectoryStatus(
  status: "pending" | "active" | "complete" | "error",
): TrajectoryItem["status"] {
  if (status === "pending") return "pending";
  if (status === "active") return "running";
  if (status === "error") return "failed";
  return "completed";
}

function normalizeText(value: string) {
  return value
    .toLowerCase()
    .replace(/[`*_~]/g, "")
    .replace(/[^\p{L}\p{N}\s]/gu, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function isMateriallyDuplicative(left: string, right: string) {
  const normalizedLeft = normalizeText(left);
  const normalizedRight = normalizeText(right);
  if (!normalizedLeft || !normalizedRight) return false;
  return (
    normalizedLeft === normalizedRight ||
    normalizedLeft.includes(normalizedRight) ||
    normalizedRight.includes(normalizedLeft)
  );
}

function buildTrajectoryItems(
  trajectoryParts: ReturnType<typeof buildAssistantTurnTrajectoryParts>,
  mergedReasoning: MergedReasoningPart[],
) {
  const cotItems: OrderedTrajectoryItem[] = trajectoryParts.flatMap(({ part }) =>
    part.steps.map((step, order) => ({
      originalOrder: order,
      item: {
        id: step.id,
        index: step.index,
        title: trajectoryTitle(step.index ?? order, step.label),
        body: trajectoryBody(step.details),
        details: step.details,
        status: mapTrajectoryStatus(step.status),
        runtimeBadges: getRuntimeBadgeStrings(part.runtimeContext),
        source: "cot" as const,
      },
    })),
  );

  cotItems.sort((left, right) => {
    const leftIndex =
      typeof left.item.index === "number" ? left.item.index : Number.POSITIVE_INFINITY;
    const rightIndex =
      typeof right.item.index === "number" ? right.item.index : Number.POSITIVE_INFINITY;
    if (leftIndex !== rightIndex) return leftIndex - rightIndex;
    return left.originalOrder - right.originalOrder;
  });

  const thoughtItems: OrderedTrajectoryItem[] = mergedReasoning
    .filter((group) => /^thought_\d+$/.test(group.label))
    .map((group, order) => {
      const index = parseThoughtIndex(group.label);
      return {
        originalOrder: order,
        item: {
          id: group.key,
          index,
          title: trajectoryTitle(index ?? order),
          body: group.text,
          details: [],
          status: group.isStreaming ? "running" : "completed",
          runtimeBadges: group.runtimeBadges,
          source: "reasoning" as const,
        },
      };
    });

  const existingIndexes = new Set(
    cotItems
      .map(({ item }) => item.index)
      .filter((value): value is number => typeof value === "number"),
  );

  const fallbackThoughtItems =
    cotItems.length === 0
      ? thoughtItems
      : thoughtItems.filter(({ item }) => item.index == null || !existingIndexes.has(item.index));

  const combined = [...cotItems, ...fallbackThoughtItems].sort((left, right) => {
    const leftIndex =
      typeof left.item.index === "number" ? left.item.index : Number.POSITIVE_INFINITY;
    const rightIndex =
      typeof right.item.index === "number" ? right.item.index : Number.POSITIVE_INFINITY;
    if (leftIndex !== rightIndex) return leftIndex - rightIndex;
    return left.originalOrder - right.originalOrder;
  });

  const trajectoryItems: TrajectoryItem[] = combined.map(({ item }) => item);

  const finalReasoning = mergedReasoning.find((group) => group.label === "final_reasoning");
  if (finalReasoning) {
    const finalItem: TrajectoryItem = {
      id: finalReasoning.key,
      title: "Synthesis",
      body: finalReasoning.text,
      details: [],
      status: finalReasoning.isStreaming ? "running" : "completed",
      runtimeBadges: finalReasoning.runtimeBadges,
      source: "final_reasoning",
    };

    const lastItem = trajectoryItems[trajectoryItems.length - 1];
    if (lastItem && isMateriallyDuplicative(lastItem.body, finalItem.body)) {
      trajectoryItems[trajectoryItems.length - 1] = finalItem;
    } else {
      trajectoryItems.push(finalItem);
    }
  }

  return {
    items: trajectoryItems,
    hasCot: cotItems.length > 0,
  };
}

export function buildAssistantTrajectoryModel(
  item: AssistantTurnDisplayItem,
): AssistantTrajectoryModel {
  const reasoningParts = mergeReasoningParts(buildAssistantTurnReasoningParts(item));
  const overview = buildOverviewReasoning(reasoningParts);
  const { items, hasCot } = buildTrajectoryItems(
    buildAssistantTurnTrajectoryParts(item),
    reasoningParts,
  );

  return {
    overview,
    items,
    hasCot,
  };
}
