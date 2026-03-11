import type { WsServerMessage } from "@/lib/rlm-api";
import type {
  DaytonaArtifactSummary,
  DaytonaChildLinkSummary,
  DaytonaPromptHandleSummary,
  DaytonaRunNode,
  DaytonaWorkbenchStateData,
  DaytonaRunSummary,
  DaytonaTimelineEntry,
} from "@/features/rlm-workspace/daytona-workbench/types";

const PROMPT_PREVIEW_LIMIT = 220;
const ARTIFACT_PREVIEW_LIMIT = 320;

function asRecord(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  return value as Record<string, unknown>;
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function asText(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed ? trimmed : undefined;
}

function asNumber(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return undefined;
}

function collapseWhitespace(value: string, limit: number): string {
  const normalized = value.replace(/\s+/g, " ").trim();
  if (normalized.length <= limit) return normalized;
  return `${normalized.slice(0, Math.max(0, limit - 1)).trimEnd()}…`;
}

function stringifyValue(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function previewText(value: unknown, limit: number): string {
  return collapseWhitespace(stringifyValue(value), limit);
}

function normalizeWarnings(value: unknown): string[] {
  return asArray(value)
    .map((item) => asText(item))
    .filter((item): item is string => Boolean(item))
    .map((item) => collapseWhitespace(item, ARTIFACT_PREVIEW_LIMIT));
}

function normalizePromptHandle(raw: unknown): DaytonaPromptHandleSummary | null {
  const record = asRecord(raw);
  if (!record) return null;
  const handleId =
    asText(record.handle_id) ??
    asText(record.handleId) ??
    asText(record.id);
  if (!handleId) return null;
  return {
    handleId,
    kind: asText(record.kind),
    label: asText(record.label),
    path: asText(record.path),
    charCount: asNumber(record.char_count ?? record.charCount),
    lineCount: asNumber(record.line_count ?? record.lineCount),
    preview: previewText(record.preview, PROMPT_PREVIEW_LIMIT) || undefined,
  };
}

function normalizeArtifact(raw: unknown): DaytonaArtifactSummary | null {
  const record = asRecord(raw);
  if (!record) {
    const textPreview = previewText(raw, ARTIFACT_PREVIEW_LIMIT);
    if (!textPreview) return null;
    return {
      value: raw,
      textPreview,
    };
  }

  const value = record.value;
  const textPreview = previewText(
    record.summary ?? record.final_markdown ?? value,
    ARTIFACT_PREVIEW_LIMIT,
  );
  return {
    kind: asText(record.kind),
    value,
    variableName: asText(record.variable_name ?? record.variableName),
    finalizationMode: asText(
      record.finalization_mode ?? record.finalizationMode,
    ),
    textPreview: textPreview || undefined,
  };
}

function normalizeChildLink(raw: unknown): DaytonaChildLinkSummary | null {
  const record = asRecord(raw);
  if (!record) return null;
  const taskRecord = asRecord(record.task);
  const taskText =
    asText(taskRecord?.task) ??
    asText(record.task_text) ??
    asText(record.task);
  if (!taskText) return null;

  const sourceRecord = asRecord(taskRecord?.source ?? record.source);
  return {
    childId: asText(record.child_id ?? record.childId) ?? null,
    callbackName:
      asText(record.callback_name ?? record.callbackName) ?? "llm_query",
    status: asText(record.status) ?? "unknown",
    resultPreview: previewText(
      record.result_preview ?? record.resultPreview,
      ARTIFACT_PREVIEW_LIMIT,
    ),
    task: {
      task: taskText,
      label: asText(taskRecord?.label),
      source: sourceRecord
        ? {
            kind: asText(sourceRecord.kind),
            sourceId: asText(sourceRecord.source_id ?? sourceRecord.sourceId),
            path: asText(sourceRecord.path),
            startLine: asNumber(
              sourceRecord.start_line ?? sourceRecord.startLine,
            ),
            endLine: asNumber(sourceRecord.end_line ?? sourceRecord.endLine),
            preview: previewText(sourceRecord.preview, PROMPT_PREVIEW_LIMIT),
          }
        : undefined,
    },
  };
}

function normalizeNode(nodeId: string, raw: unknown): DaytonaRunNode | null {
  const record = asRecord(raw);
  if (!record) return null;

  const promptManifest = asRecord(record.prompt_manifest ?? record.promptManifest);
  const promptHandles = [
    ...asArray(record.prompt_handles ?? record.promptHandles),
    ...asArray(promptManifest?.handles),
  ]
    .map((item) => normalizePromptHandle(item))
    .filter((item): item is DaytonaPromptHandleSummary => item !== null);

  const childIds = asArray(record.child_ids ?? record.childIds)
    .map((item) => asText(item))
    .filter((item): item is string => Boolean(item));

  const childLinks = asArray(record.child_links ?? record.childLinks)
    .map((item) => normalizeChildLink(item))
    .filter((item): item is DaytonaChildLinkSummary => item !== null);

  return {
    nodeId,
    parentId: asText(record.parent_id ?? record.parentId) ?? null,
    depth: asNumber(record.depth) ?? 0,
    task: asText(record.task) ?? `Node ${nodeId.slice(0, 8)}`,
    status: asText(record.status) ?? "running",
    sandboxId: asText(record.sandbox_id ?? record.sandboxId),
    workspacePath: asText(record.workspace_path ?? record.workspacePath),
    iterationCount: asNumber(record.iteration_count ?? record.iterationCount),
    error: asText(record.error) ?? null,
    warnings: normalizeWarnings(record.warnings),
    promptHandles,
    childIds,
    childLinks,
    finalArtifact: normalizeArtifact(record.final_artifact ?? record.finalArtifact),
  };
}

function mergeNode(
  current: DaytonaRunNode | undefined,
  next: DaytonaRunNode,
): DaytonaRunNode {
  return {
    ...current,
    ...next,
    promptHandles:
      next.promptHandles.length > 0 ? next.promptHandles : current?.promptHandles ?? [],
    childIds: next.childIds.length > 0 ? next.childIds : current?.childIds ?? [],
    childLinks:
      next.childLinks.length > 0 ? next.childLinks : current?.childLinks ?? [],
    warnings: next.warnings?.length ? next.warnings : current?.warnings ?? [],
    finalArtifact: next.finalArtifact ?? current?.finalArtifact ?? null,
  };
}

function normalizeSummary(raw: unknown): DaytonaRunSummary | undefined {
  const record = asRecord(raw);
  if (!record) return undefined;
  return {
    durationMs: asNumber(record.duration_ms ?? record.durationMs),
    sandboxesUsed: asNumber(record.sandboxes_used ?? record.sandboxesUsed),
    terminationReason: asText(
      record.termination_reason ?? record.terminationReason,
    ),
    error: asText(record.error) ?? null,
    warnings: normalizeWarnings(record.warnings),
  };
}

function extractRuntime(payload?: Record<string, unknown>): Record<string, unknown> | undefined {
  return asRecord(payload?.runtime) ?? payload;
}

function inferNodeFromPayload(
  payload?: Record<string, unknown>,
): Partial<DaytonaRunNode> | null {
  if (!payload) return null;
  const nodeRecord =
    asRecord(payload.node) ??
    (payload.node_id ? payload : undefined);
  if (!nodeRecord) return null;
  const nodeId = asText(nodeRecord.node_id ?? nodeRecord.nodeId);
  if (!nodeId) return null;
  const promptManifest = asRecord(nodeRecord.prompt_manifest ?? nodeRecord.promptManifest);
  const promptHandles = [
    ...asArray(nodeRecord.prompt_handles ?? nodeRecord.promptHandles),
    ...asArray(promptManifest?.handles),
  ]
    .map((item) => normalizePromptHandle(item))
    .filter((item): item is DaytonaPromptHandleSummary => item !== null);
  return {
    nodeId,
    parentId: asText(nodeRecord.parent_id ?? nodeRecord.parentId) ?? null,
    depth: asNumber(nodeRecord.depth) ?? 0,
    task: asText(nodeRecord.task) ?? `Node ${nodeId.slice(0, 8)}`,
    status: asText(nodeRecord.status) ?? "running",
    sandboxId:
      asText(nodeRecord.sandbox_id ?? nodeRecord.sandboxId) ??
      asText(extractRuntime(payload)?.sandbox_id),
    promptHandles,
    childIds: asArray(nodeRecord.child_ids ?? nodeRecord.childIds)
      .map((item) => asText(item))
      .filter((item): item is string => Boolean(item)),
    childLinks: asArray(nodeRecord.child_links ?? nodeRecord.childLinks)
      .map((item) => normalizeChildLink(item))
      .filter((item): item is DaytonaChildLinkSummary => item !== null),
    finalArtifact: normalizeArtifact(
      nodeRecord.final_artifact ?? nodeRecord.finalArtifact,
    ),
    workspacePath: asText(nodeRecord.workspace_path ?? nodeRecord.workspacePath),
    iterationCount: asNumber(nodeRecord.iteration_count ?? nodeRecord.iterationCount),
    error: asText(nodeRecord.error) ?? null,
    warnings: normalizeWarnings(nodeRecord.warnings),
  };
}

function buildTimelineEntry(frame: WsServerMessage): DaytonaTimelineEntry {
  if (frame.type === "error") {
    return {
      id: `frame-error-${Date.now()}`,
      kind: "error",
      text: frame.message,
    };
  }

  const payload = asRecord(frame.data.payload);
  const runtime = extractRuntime(payload);
  const nodeRecord = asRecord(payload?.node);
  const promptManifest = asRecord(
    payload?.prompt_manifest ?? nodeRecord?.prompt_manifest,
  );
  const promptHandles = [
    ...asArray(payload?.prompt_handles),
    ...asArray(nodeRecord?.prompt_handles),
    ...asArray(promptManifest?.handles),
  ]
    .map((item) => normalizePromptHandle(item))
    .filter((item): item is DaytonaPromptHandleSummary => item !== null);

  const artifact = normalizeArtifact(
    payload?.final_artifact ?? payload?.artifact ?? nodeRecord?.final_artifact,
  );

  return {
    id: String(frame.data.event_id ?? `${frame.data.kind}-${frame.data.timestamp ?? Date.now()}`),
    kind: frame.data.kind,
    text: frame.data.text,
    timestamp: frame.data.timestamp,
    nodeId:
      asText(nodeRecord?.node_id ?? nodeRecord?.nodeId) ??
      asText(payload?.node_id ?? payload?.nodeId),
    parentId:
      asText(nodeRecord?.parent_id ?? nodeRecord?.parentId) ??
      asText(payload?.parent_id ?? payload?.parentId) ??
      null,
    depth:
      asNumber(nodeRecord?.depth) ??
      asNumber(payload?.depth) ??
      asNumber(runtime?.depth),
    sandboxId:
      asText(nodeRecord?.sandbox_id ?? nodeRecord?.sandboxId) ??
      asText(payload?.sandbox_id ?? payload?.sandboxId) ??
      asText(runtime?.sandbox_id),
    phase: asText(payload?.phase),
    status:
      asText(nodeRecord?.status) ??
      asText(payload?.status) ??
      asText(payload?.node_status),
    promptHandleCount: promptHandles.length || undefined,
    artifactPreview: artifact?.textPreview,
    warning: asText(payload?.warning),
    rawPayload: payload,
  };
}

function hydrateFromRunResult(
  state: DaytonaWorkbenchStateData,
  raw: Record<string, unknown>,
): DaytonaWorkbenchStateData {
  const nodesRaw = asRecord(raw.nodes) ?? {};
  const nodes = { ...state.nodes };
  const nodeOrder = [...state.nodeOrder];

  for (const [nodeId, nodePayload] of Object.entries(nodesRaw)) {
    const normalized = normalizeNode(nodeId, nodePayload);
    if (!normalized) continue;
    nodes[nodeId] = mergeNode(nodes[nodeId], normalized);
    if (!nodeOrder.includes(nodeId)) nodeOrder.push(nodeId);
  }

  const rootId = asText(raw.root_id ?? raw.rootId) ?? state.rootId;
  return {
    ...state,
    runId: asText(raw.run_id ?? raw.runId) ?? state.runId,
    repoUrl: asText(raw.repo) ?? state.repoUrl,
    repoRef: asText(raw.ref) ?? state.repoRef ?? null,
    task: asText(raw.task) ?? state.task,
    rootId,
    nodes,
    nodeOrder,
    selectedNodeId: state.selectedNodeId ?? rootId ?? nodeOrder[0] ?? null,
    finalArtifact:
      normalizeArtifact(raw.final_artifact ?? raw.finalArtifact) ??
      state.finalArtifact ??
      null,
    summary: normalizeSummary(raw.summary) ?? state.summary,
  };
}

export function createInitialDaytonaWorkbenchState(): DaytonaWorkbenchStateData {
  return {
    status: "idle",
    nodes: {},
    nodeOrder: [],
    timeline: [],
    selectedNodeId: null,
    selectedTab: "node",
    finalArtifact: null,
    summary: undefined,
    lastFrame: null,
  };
}

export function startDaytonaWorkbenchRun(
  _state: DaytonaWorkbenchStateData,
  input: { task: string; repoUrl: string; repoRef?: string | null },
): DaytonaWorkbenchStateData {
  return {
    ...createInitialDaytonaWorkbenchState(),
    status: "bootstrapping",
    task: input.task,
    repoUrl: input.repoUrl,
    repoRef: input.repoRef ?? null,
  };
}

function isDaytonaWorkbenchFrame(frame: WsServerMessage): boolean {
  if (frame.type === "error") return false;
  const payload = asRecord(frame.data.payload);
  const runtime = extractRuntime(payload);
  return (
    asText(payload?.runtime_mode) === "daytona_pilot" ||
    asText(runtime?.runtime_mode) === "daytona_pilot" ||
    payload?.run_result != null ||
    payload?.final_artifact != null
  );
}

export function shouldApplyDaytonaFrame(
  state: DaytonaWorkbenchStateData,
  frame: WsServerMessage,
): boolean {
  if (frame.type === "error") {
    return state.status === "bootstrapping" || state.status === "running";
  }
  return isDaytonaWorkbenchFrame(frame);
}

export function applyDaytonaFrameToWorkbenchState(
  state: DaytonaWorkbenchStateData,
  frame: WsServerMessage,
): DaytonaWorkbenchStateData {
  let next: DaytonaWorkbenchStateData = {
    ...state,
    lastFrame: frame,
  };

  const timelineEntry = buildTimelineEntry(frame);
  next = {
    ...next,
    timeline: [...next.timeline, timelineEntry],
  };

  if (frame.type === "error") {
    return {
      ...next,
      status: "error",
      errorMessage: frame.message,
    };
  }

  const payload = asRecord(frame.data.payload);
  const runtime = extractRuntime(payload);
  const runResult = asRecord(payload?.run_result ?? payload?.runResult);
  const nodePartial = inferNodeFromPayload(payload);

  if (nodePartial?.nodeId) {
    const normalizedNode: DaytonaRunNode = mergeNode(
      next.nodes[nodePartial.nodeId],
      {
        nodeId: nodePartial.nodeId,
        parentId: nodePartial.parentId ?? null,
        depth: nodePartial.depth ?? 0,
        task: nodePartial.task ?? `Node ${nodePartial.nodeId.slice(0, 8)}`,
        status: nodePartial.status ?? "running",
        sandboxId: nodePartial.sandboxId,
        promptHandles: nodePartial.promptHandles ?? [],
        childIds: nodePartial.childIds ?? [],
        childLinks: nodePartial.childLinks ?? [],
        warnings: nodePartial.warnings ?? [],
        finalArtifact: nodePartial.finalArtifact ?? null,
        workspacePath: nodePartial.workspacePath,
        iterationCount: nodePartial.iterationCount,
        error: nodePartial.error ?? null,
      },
    );
    next = {
      ...next,
      nodes: {
        ...next.nodes,
        [normalizedNode.nodeId]: normalizedNode,
      },
      nodeOrder: next.nodeOrder.includes(normalizedNode.nodeId)
        ? next.nodeOrder
        : [...next.nodeOrder, normalizedNode.nodeId],
      selectedNodeId:
        next.selectedNodeId ??
        normalizedNode.nodeId,
    };
  }

  if (runResult) {
    next = hydrateFromRunResult(next, runResult);
  }

  const statusFromKind =
    frame.data.kind === "final"
      ? "completed"
      : frame.data.kind === "error"
        ? "error"
        : frame.data.kind === "warning"
          ? next.status === "idle"
            ? "bootstrapping"
            : next.status
        : frame.data.kind === "cancelled"
          ? "cancelled"
          : nodePartial?.status === "cancelling" || timelineEntry.status === "cancelling"
            ? "cancelling"
          : next.status === "idle"
            ? "bootstrapping"
            : "running";

  const runtimeRunId =
    asText(payload?.run_id ?? payload?.runId) ??
    asText(runtime?.run_id) ??
    next.runId;

  const rootId =
    asText(payload?.root_id ?? payload?.rootId) ??
    asText(runResult?.root_id ?? runResult?.rootId) ??
    next.rootId;

  return {
    ...next,
    status: statusFromKind as DaytonaWorkbenchStateData["status"],
    runId: runtimeRunId,
    rootId,
    selectedNodeId:
      next.selectedNodeId ?? rootId ?? next.nodeOrder[0] ?? null,
    finalArtifact:
      normalizeArtifact(payload?.final_artifact ?? payload?.finalArtifact) ??
      next.finalArtifact ??
      null,
    summary: normalizeSummary(payload?.summary) ?? next.summary,
    errorMessage:
      frame.data.kind === "error"
        ? frame.data.text
        : next.errorMessage ?? null,
  };
}
