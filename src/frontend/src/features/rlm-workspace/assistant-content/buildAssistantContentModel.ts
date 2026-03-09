import type {
  ChatRenderPart,
  ChatRenderToolState,
} from "@/lib/data/types";
import type {
  AssistantTurnDisplayItem,
  ToolSessionItem,
} from "@/features/rlm-workspace/chatDisplayItems";
import { getRuntimeBadgeStrings } from "@/features/rlm-workspace/assistant-content/runtimeBadges";
import type {
  AssistantContentModel,
  CompactReasoning,
  DirectExecutionPart,
  ExecutionHighlight,
  ExecutionSection,
  TrajectoryItem,
} from "@/features/rlm-workspace/assistant-content/types";

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
      part.kind === "reasoning"
        ? [{ key: `${message.id}-${part.kind}-${idx}`, part }]
        : [],
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
      part.kind === "chain_of_thought"
        ? [{ key: `${message.id}-${part.kind}-${idx}`, part }]
        : [],
    ) ?? [];
  return [...fromTrace, ...fromMessage];
}

function mergeReasoningParts(
  reasoningParts: ReturnType<typeof buildAssistantTurnReasoningParts>,
) {
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
    .sort(([leftLabel], [rightLabel]) =>
      compareReasoningLabels(leftLabel, rightLabel),
    )
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
    runtimeBadges: uniqueStrings(
      overviewGroups.flatMap((group) => group.runtimeBadges),
    ),
  };
}

function parseThoughtIndex(label: string): number | undefined {
  const match = label.match(/^thought_(\d+)$/);
  if (!match) return undefined;
  return Number(match[1]);
}

function humanizeLabel(value: string) {
  const trimmed = value.trim();
  if (!trimmed) return trimmed;
  return trimmed
    .replace(/^tool:\s*/i, "")
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .replace(/^\w/, (letter) => letter.toUpperCase());
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

function uniqueStrings(values: string[]) {
  return [...new Set(values.filter(Boolean))];
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
      typeof left.item.index === "number"
        ? left.item.index
        : Number.POSITIVE_INFINITY;
    const rightIndex =
      typeof right.item.index === "number"
        ? right.item.index
        : Number.POSITIVE_INFINITY;
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
          details: [] as string[],
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
      : thoughtItems.filter(
          ({ item }) =>
            item.index == null || !existingIndexes.has(item.index),
        );

  const combined = [...cotItems, ...fallbackThoughtItems].sort((left, right) => {
    const leftIndex =
      typeof left.item.index === "number"
        ? left.item.index
        : Number.POSITIVE_INFINITY;
    const rightIndex =
      typeof right.item.index === "number"
        ? right.item.index
        : Number.POSITIVE_INFINITY;
    if (leftIndex !== rightIndex) return leftIndex - rightIndex;
    return left.originalOrder - right.originalOrder;
  });

  const trajectoryItems: TrajectoryItem[] = combined.map(({ item }) => item);

  const finalReasoning = mergedReasoning.find(
    (group) => group.label === "final_reasoning",
  );
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

function shouldOpenToolRow(state: ChatRenderToolState) {
  return (
    state === "running" ||
    state === "input-streaming" ||
    state === "output-error"
  );
}

function shouldOpenTaskRow(
  status: Extract<ChatRenderPart, { kind: "task" }>["status"],
) {
  return status === "in_progress" || status === "error";
}

function toolSessionStateForItem(item: ToolSessionItem): ChatRenderToolState {
  if (item.part.kind === "tool" || item.part.kind === "sandbox") {
    return item.part.state;
  }
  if (item.part.kind === "environment_variables") return "output-available";
  if (
    item.part.tone === "error" ||
    /(error|failed|failure|rejected|unable)/i.test(item.part.text)
  ) {
    return "output-error";
  }
  if (
    item.part.tone === "success" ||
    /(done|complete|completed|finished|success)/i.test(item.part.text)
  ) {
    return "output-available";
  }
  return "running";
}

function toolSessionHeaderLabel(items: ToolSessionItem[]) {
  const first = items[0];
  const toolName = first?.toolName ?? "Tool";
  return first?.eventKind === "tool_call"
    ? `Calling tool: ${toolName}`
    : `Tool: ${toolName}`;
}

function summarizeToolSession(
  session: AssistantContentModel["attachedToolSessions"][number],
) {
  const latestItem = session.items[session.items.length - 1];
  const toolName = latestItem?.toolName ?? "tool";
  if (!latestItem) return `Waiting for ${toolName}`;
  if (latestItem.part.kind === "status_note") return latestItem.part.text;
  if (latestItem.part.kind === "environment_variables") {
    return `Prepared ${latestItem.part.variables.length} environment variable${latestItem.part.variables.length === 1 ? "" : "s"}`;
  }
  if (latestItem.part.kind === "tool") {
    if (latestItem.part.errorText) return `Failed to run ${toolName}`;
    if (latestItem.part.state === "running") return `Calling ${toolName}`;
    return `Completed ${toolName}`;
  }
  if (latestItem.part.errorText) return `Sandbox execution failed`;
  if (latestItem.part.state === "running") return `Running sandbox`;
  return `Completed sandbox execution`;
}

function directExecutionRuntimeBadges(part: DirectExecutionPart) {
  if (
    part.kind === "tool" ||
    part.kind === "sandbox" ||
    part.kind === "status_note"
  ) {
    return getRuntimeBadgeStrings(part.runtimeContext);
  }
  return [];
}

function directExecutionSummary(part: DirectExecutionPart) {
  switch (part.kind) {
    case "task":
      return part.title;
    case "queue":
      return `${part.items.length} queued item${part.items.length === 1 ? "" : "s"}`;
    case "tool":
      if (part.errorText) return `Failed to run ${part.title || part.toolType}`;
      if (part.state === "running" || part.state === "input-streaming") {
        return `Calling ${part.title || part.toolType}`;
      }
      return `Completed ${part.title || part.toolType}`;
    case "sandbox":
      if (part.errorText) return "Sandbox execution failed";
      if (part.state === "running" || part.state === "input-streaming") {
        return `Running ${part.title || "sandbox"}`;
      }
      return `Completed ${part.title || "sandbox"}`;
    case "environment_variables":
      return `Prepared ${part.variables.length} environment variable${part.variables.length === 1 ? "" : "s"}`;
    case "status_note":
      return part.text;
  }
}

function directExecutionLabel(part: DirectExecutionPart) {
  switch (part.kind) {
    case "task":
      return part.title;
    case "queue":
      return part.title;
    case "tool":
      return part.title || part.toolType;
    case "sandbox":
      return part.title || "Sandbox";
    case "environment_variables":
      return part.title ?? "Environment variables";
    case "status_note":
      return "Status";
  }
}

function directExecutionDefaultOpen(part: DirectExecutionPart) {
  switch (part.kind) {
    case "task":
      return shouldOpenTaskRow(part.status);
    case "queue":
      return true;
    case "tool":
    case "sandbox":
      return shouldOpenToolRow(part.state);
    case "environment_variables":
      return false;
    case "status_note":
      return part.tone === "error" || part.tone === "warning";
  }
}

function buildExecutionSections(
  item: AssistantTurnDisplayItem,
  supplementalParts: Exclude<ChatRenderPart, { kind: "reasoning" | "chain_of_thought" }>[],
) {
  const sections: ExecutionSection[] = item.attachedToolSessions.map((session) => {
    const latestItem = session.items[session.items.length - 1];
    const latestState = latestItem
      ? toolSessionStateForItem(latestItem)
      : ("running" as const);
    const runtimeBadges = uniqueStrings(
      session.items.flatMap((sessionItem) =>
        getRuntimeBadgeStrings(sessionItem.runtimeContext),
      ),
    );

    return {
      id: session.key,
      kind: "tool_session",
      label: toolSessionHeaderLabel(session.items),
      summary: summarizeToolSession(session),
      defaultOpen: shouldOpenToolRow(latestState),
      runtimeBadges,
      session,
    };
  });

  const directExecutionParts = supplementalParts.filter(
    (part): part is DirectExecutionPart =>
      part.kind === "task" ||
      part.kind === "queue" ||
      part.kind === "tool" ||
      part.kind === "sandbox" ||
      part.kind === "environment_variables" ||
      part.kind === "status_note",
  );

  for (const [idx, part] of directExecutionParts.entries()) {
    sections.push({
      id: `execution-${part.kind}-${idx}`,
      kind: part.kind,
      label: directExecutionLabel(part),
      summary: directExecutionSummary(part),
      defaultOpen: directExecutionDefaultOpen(part),
      runtimeBadges: directExecutionRuntimeBadges(part),
      part,
    } as ExecutionSection);
  }

  return {
    sections,
    directExecutionParts,
  };
}

function executionSectionState(
  section: ExecutionSection,
): ExecutionHighlight["status"] {
  if (section.kind === "tool_session") {
    const latest = section.session.items[section.session.items.length - 1];
    if (!latest) return "running";
    const latestState = toolSessionStateForItem(latest);
    if (latestState === "output-error") return "failed";
    if (latestState === "running" || latestState === "input-streaming") {
      return "running";
    }
    return "completed";
  }

  if (section.kind === "task") {
    if (section.part.status === "error") return "failed";
    if (section.part.status === "in_progress") return "running";
    if (section.part.status === "pending") return "pending";
    return "completed";
  }

  if (section.kind === "queue") {
    return section.part.items.every((item) => item.completed)
      ? "completed"
      : "running";
  }

  if (section.kind === "status_note") {
    if (section.part.tone === "error") return "failed";
    if (section.part.tone === "warning") return "running";
    return "completed";
  }

  if ("errorText" in section.part && section.part.errorText) return "failed";
  if (
    "state" in section.part &&
    (section.part.state === "running" || section.part.state === "input-streaming")
  ) {
    return "running";
  }
  return "completed";
}

function normalizeToolKey(value: string) {
  return value.trim().toLowerCase().replace(/\s+/g, " ");
}

function sessionToolName(
  session: AssistantContentModel["attachedToolSessions"][number],
) {
  const latestItem = session.items[session.items.length - 1];
  return latestItem?.toolName ?? session.items[0]?.toolName;
}

function aggregateExecutionStatus(
  statuses: ExecutionHighlight["status"][],
): ExecutionHighlight["status"] {
  if (statuses.some((status) => status === "failed")) return "failed";
  if (statuses.some((status) => status === "running")) return "running";
  if (statuses.some((status) => status === "pending")) return "pending";
  return "completed";
}

function buildExecutionHighlights(sections: ExecutionSection[]) {
  type Candidate = ExecutionHighlight & {
    groupKey?: string;
    groupable: boolean;
  };

  const candidates: Candidate[] = [];

  for (const section of sections) {
    const status = executionSectionState(section);
    const runtimeBadges = section.runtimeBadges;

    switch (section.kind) {
      case "task":
      case "queue":
        candidates.push({
          id: section.id,
          label: section.label,
          summary: section.summary,
          status,
          runtimeBadges,
          groupable: false,
        });
        break;
      case "sandbox":
        candidates.push({
          id: section.id,
          label: humanizeLabel(section.label || "Sandbox"),
          summary:
            status === "running"
              ? "Sandbox running"
              : status === "failed"
                ? "Sandbox execution failed"
                : section.summary,
          status,
          runtimeBadges,
          groupable: false,
        });
        break;
      case "status_note":
        if (section.part.tone !== "neutral") {
          candidates.push({
            id: section.id,
            label:
              section.part.tone === "warning"
                ? "Warning"
                : section.part.tone === "error"
                  ? "Error"
                  : "Status",
            summary: section.summary,
            status,
            runtimeBadges,
            groupable: false,
          });
        }
        break;
      case "environment_variables":
        break;
      case "tool_session": {
        const name = humanizeLabel(sessionToolName(section.session) ?? "Tool");
        candidates.push({
          id: section.id,
          label: name,
          summary:
            status === "failed" ? `${name} failed` : `Completed ${name}`,
          status,
          runtimeBadges,
          groupKey: normalizeToolKey(name),
          groupable: status !== "failed",
        });
        break;
      }
      case "tool": {
        const name = humanizeLabel(section.label || section.part.toolType);
        candidates.push({
          id: section.id,
          label: name,
          summary:
            status === "failed" ? `${name} failed` : `Completed ${name}`,
          status,
          runtimeBadges,
          groupKey: normalizeToolKey(name),
          groupable: status !== "failed",
        });
        break;
      }
    }
  }

  const highlights: ExecutionHighlight[] = [];
  for (let index = 0; index < candidates.length; ) {
    const candidate = candidates[index];
    if (!candidate) break;

    if (!candidate.groupable || !candidate.groupKey) {
      highlights.push(candidate);
      index += 1;
      continue;
    }

    const grouped = [candidate];
    let cursor = index + 1;
    while (cursor < candidates.length) {
      const next = candidates[cursor];
      if (
        !next?.groupable ||
        !next.groupKey ||
        next.groupKey !== candidate.groupKey
      ) {
        break;
      }
      grouped.push(next);
      cursor += 1;
    }

    if (grouped.length > 1) {
      highlights.push({
        id: grouped[0]?.id ?? candidate.id,
        label: candidate.label,
        summary: `${candidate.label} ×${grouped.length}`,
        status: aggregateExecutionStatus(grouped.map((item) => item.status)),
        runtimeBadges: uniqueStrings(
          grouped.flatMap((item) => item.runtimeBadges),
        ),
        count: grouped.length,
      });
    }

    index = cursor;
  }

  return highlights;
}

function buildEvidence(
  supplementalParts: Exclude<ChatRenderPart, { kind: "reasoning" | "chain_of_thought" }>[],
) {
  const citations = supplementalParts
    .filter(
      (part): part is Extract<ChatRenderPart, { kind: "inline_citation_group" }> =>
        part.kind === "inline_citation_group",
    )
    .flatMap((part) => part.citations);

  const sources = supplementalParts
    .filter(
      (part): part is Extract<ChatRenderPart, { kind: "sources" }> =>
        part.kind === "sources",
    )
    .flatMap((part) => part.sources);

  const attachments = supplementalParts
    .filter(
      (part): part is Extract<ChatRenderPart, { kind: "attachments" }> =>
        part.kind === "attachments",
    )
    .flatMap((part) => part.attachments);

  return {
    citations,
    sources,
    attachments,
    hasContent:
      citations.length > 0 || sources.length > 0 || attachments.length > 0,
  };
}

export function buildAssistantContentModel(
  item: AssistantTurnDisplayItem,
): AssistantContentModel {
  const answerText = item.message?.content ?? "";
  const reasoningParts = mergeReasoningParts(buildAssistantTurnReasoningParts(item));
  const overview = buildOverviewReasoning(reasoningParts);
  const { items: trajectoryItems, hasCot } = buildTrajectoryItems(
    buildAssistantTurnTrajectoryParts(item),
    reasoningParts,
  );

  const supplementalParts = [
    ...item.attachedTraceParts.map((tracePart) => tracePart.part),
    ...(item.message?.renderParts ?? []),
  ].filter(
    (part): part is Exclude<ChatRenderPart, { kind: "reasoning" | "chain_of_thought" }> =>
      part.kind !== "reasoning" && part.kind !== "chain_of_thought",
  );
  const evidence = buildEvidence(supplementalParts);
  const { sections, directExecutionParts } = buildExecutionSections(
    item,
    supplementalParts,
  );
  const executionHighlights = buildExecutionHighlights(sections);

  const trajectoryCount = trajectoryItems.length > 0 ? trajectoryItems.length : overview ? 1 : 0;
  const runtimeBadges = uniqueStrings([
    ...(overview?.runtimeBadges ?? []),
    ...trajectoryItems.flatMap((trajectoryItem) => trajectoryItem.runtimeBadges),
    ...sections.flatMap((section) => section.runtimeBadges),
  ]);
  const sandboxActive =
    sections.some((section) => section.runtimeBadges.includes("sandbox")) ||
    runtimeBadges.includes("sandbox");
  const evidenceCategoryCount = [
    evidence.citations.length > 0,
    evidence.sources.length > 0,
    evidence.attachments.length > 0,
  ].filter(Boolean).length;

  const simple =
    sections.length === 0 &&
    trajectoryCount <= 1 &&
    evidenceCategoryCount <= 1 &&
    runtimeBadges.length === 0;
  const complex =
    trajectoryCount > 1 ||
    sections.length > 1 ||
    evidenceCategoryCount > 1 ||
    hasCot;

  const complexity: AssistantContentModel["complexity"] = simple
    ? "simple"
    : complex
      ? "complex"
      : "medium";
  const hasRichChatSections =
    Boolean(overview) ||
    trajectoryItems.length > 0 ||
    executionHighlights.length > 0 ||
    evidence.hasContent;

  return {
    item,
    answer: {
      text: answerText,
      hasContent: answerText.length > 0,
      showStreamingShell:
        (item.isPendingShell || Boolean(item.message?.streaming)) &&
        answerText.length === 0,
    },
    trajectory: {
      overview,
      items: trajectoryItems,
      displayMode:
        trajectoryItems.length === 0 && !overview
          ? "hidden"
          : trajectoryItems.length > 1 || hasCot
            ? "timeline"
            : "compact",
      hasContent: Boolean(overview) || trajectoryItems.length > 0,
    },
    execution: {
      sections,
      highlights: executionHighlights,
      hasContent: sections.length > 0,
      hasChatHighlights: executionHighlights.length > 0,
      toolSessionCount: item.attachedToolSessions.length,
      sandboxActive,
    },
    evidence,
    summary: {
      show:
        complexity === "simple" &&
        !hasRichChatSections &&
        (trajectoryCount > 0 ||
          item.attachedToolSessions.length > 0 ||
          evidence.sources.length > 0 ||
          evidence.attachments.length > 0 ||
          sandboxActive ||
          runtimeBadges.length > 0),
      trajectoryCount,
      toolSessionCount: item.attachedToolSessions.length,
      sourceCount: evidence.sources.length,
      attachmentCount: evidence.attachments.length,
      sandboxActive,
      runtimeBadges,
    },
    complexity,
    supplementalParts,
    attachedToolSessions: item.attachedToolSessions,
    attachedTraceParts: item.attachedTraceParts,
    directExecutionParts,
  };
}
