import type { ChatAttachmentItem, ChatSourceItem } from "@/lib/workspace/workspace-types";
import type { WsServerMessage } from "@/lib/rlm-api";
import { normalizeDaytonaMode } from "@/lib/workspace/daytona-mode";
import type {
  ActivityEntry,
  CallbackSummary,
  CompatBackfillInfo,
  ContextSourceSummary,
  IterationSummary,
  PromptHandleSummary,
  RunSummary,
  RunWorkbenchState,
} from "@/lib/workspace/workspace-types";
import {
  ARTIFACT_PREVIEW_LIMIT,
  asArray,
  asNumber,
  asRecord,
  asText,
  collapseWhitespace,
  collectPromptHandlePayloads,
  dedupeAttachments,
  dedupeCallbacks,
  dedupePromptHandles,
  dedupeSources,
  findLatestRunningCallback,
  normalizeArtifact,
  normalizeAttachment,
  normalizeCallback,
  normalizeCallbackSource,
  normalizeContextSource,
  normalizeIteration,
  normalizePromptHandle,
  normalizeRunStatus,
  normalizeSource,
  normalizeSummary,
  previewText,
  upsertCallback,
  upsertIteration,
} from "./run-workbench-normalizers";

function extractRuntime(payload?: Record<string, unknown>): Record<string, unknown> | undefined {
  return asRecord(payload?.runtime) ?? payload;
}

function isExecutionCompletedPayload(payload?: Record<string, unknown>): boolean {
  return asText(payload?.source_type ?? payload?.sourceType) === "execution_completed";
}

function getCanonicalRunSummary(
  payload?: Record<string, unknown>,
): Record<string, unknown> | undefined {
  return asRecord(payload?.run_summary ?? payload?.runSummary);
}

function getCompatRunResult(
  payload?: Record<string, unknown>,
): Record<string, unknown> | undefined {
  return asRecord(payload?.run_result ?? payload?.runResult);
}

function mergeMlflowTraceMetadata(
  summary: RunSummary | undefined,
  payload?: Record<string, unknown>,
): RunSummary | undefined {
  const mlflowTraceId = asText(payload?.mlflow_trace_id ?? payload?.mlflowTraceId);
  const mlflowClientRequestId = asText(
    payload?.mlflow_client_request_id ?? payload?.mlflowClientRequestId,
  );

  if (!summary && !mlflowTraceId && !mlflowClientRequestId) {
    return undefined;
  }

  return {
    ...summary,
    ...(mlflowTraceId ? { mlflowTraceId } : {}),
    ...(mlflowClientRequestId ? { mlflowClientRequestId } : {}),
  };
}

function resolveCompatSummary(
  payload?: Record<string, unknown>,
  compatRunResult?: Record<string, unknown>,
) {
  return normalizeSummary(payload?.summary ?? compatRunResult?.summary);
}

function resolveCompatFinalArtifact(
  payload?: Record<string, unknown>,
  compatRunResult?: Record<string, unknown>,
) {
  return normalizeArtifact(
    payload?.final_artifact ??
      payload?.finalArtifact ??
      compatRunResult?.final_artifact ??
      compatRunResult?.finalArtifact,
  );
}

function compatBackfillEventId(frame: WsServerMessage): string {
  if (frame.type === "error") return `frame-error-${Date.now()}`;
  return String(frame.data.event_id ?? `${frame.data.kind}-${frame.data.timestamp ?? Date.now()}`);
}

function buildActivityEntry(frame: WsServerMessage): ActivityEntry {
  if (frame.type === "error") {
    return {
      id: `frame-error-${Date.now()}`,
      kind: "error",
      text: frame.message,
    };
  }

  const payload = asRecord(frame.data.payload);
  return {
    id: String(frame.data.event_id ?? `${frame.data.kind}-${frame.data.timestamp ?? Date.now()}`),
    kind: frame.data.kind,
    text: frame.data.text,
    timestamp: frame.data.timestamp,
    iteration: asNumber(payload?.iteration),
    phase: asText(payload?.phase),
    status: asText(payload?.status),
    durationMs: asNumber(payload?.duration_ms ?? payload?.durationMs),
    callbackCount: asNumber(payload?.callback_count ?? payload?.callbackCount),
    warning: asText(payload?.warning),
  };
}

function hydrateFromRunSummary(
  state: RunWorkbenchState,
  raw: Record<string, unknown>,
): RunWorkbenchState {
  const prompts = dedupePromptHandles(
    asArray(raw.prompts ?? raw.prompt_handles ?? raw.promptHandles)
      .map((item) => normalizePromptHandle(item))
      .filter((item): item is PromptHandleSummary => item !== null),
  );
  const iterations = asArray(raw.iterations)
    .map((item) => normalizeIteration(item))
    .filter((item): item is IterationSummary => item !== null);
  const callbacks = dedupeCallbacks(
    asArray(raw.callbacks)
      .map((item) => normalizeCallback(item))
      .filter((item): item is CallbackSummary => item !== null),
  );
  const sources = dedupeSources(
    asArray(raw.sources)
      .map((item) => normalizeSource(item))
      .filter((item): item is ChatSourceItem => item !== null),
  );
  const attachments = dedupeAttachments(
    asArray(raw.attachments)
      .map((item) => normalizeAttachment(item))
      .filter((item): item is ChatAttachmentItem => item !== null),
  );

  return {
    ...state,
    runId: asText(raw.run_id ?? raw.runId) ?? state.runId,
    repoUrl: asText(raw.repo ?? raw.repo_url ?? raw.repoUrl) ?? state.repoUrl,
    repoRef: asText(raw.ref) ?? state.repoRef ?? null,
    task: asText(raw.task) ?? state.task,
    contextSources: asArray(raw.context_sources ?? raw.contextSources)
      .map((item) => normalizeContextSource(item))
      .filter((item): item is ContextSourceSummary => item !== null),
    promptHandles: prompts,
    iterations,
    callbacks,
    sources,
    attachments,
    selectedIterationId: state.selectedIterationId ?? iterations[0]?.id ?? null,
    selectedCallbackId: state.selectedCallbackId ?? callbacks[0]?.id ?? null,
    finalArtifact:
      normalizeArtifact(raw.final_artifact ?? raw.finalArtifact) ?? state.finalArtifact ?? null,
    summary: normalizeSummary(raw.summary) ?? state.summary,
  };
}

export function createInitialRunWorkbenchState(): RunWorkbenchState {
  return {
    status: "idle",
    runId: undefined,
    repoUrl: undefined,
    repoRef: null,
    daytonaMode: undefined,
    task: undefined,
    contextSources: [],
    iterations: [],
    callbacks: [],
    promptHandles: [],
    sources: [],
    attachments: [],
    activity: [],
    selectedIterationId: null,
    selectedCallbackId: null,
    selectedTab: "iterations",
    finalArtifact: null,
    summary: undefined,
    errorMessage: null,
    lastFrame: null,
    compatBackfillCount: 0,
    lastCompatBackfill: null,
  };
}

export function startRunWorkbenchRun(
  _state: RunWorkbenchState,
  input: {
    task: string;
    repoUrl?: string;
    repoRef?: string | null;
    contextPaths?: string[];
  },
): RunWorkbenchState {
  return {
    ...createInitialRunWorkbenchState(),
    status: "bootstrapping",
    task: input.task,
    repoUrl: input.repoUrl,
    repoRef: input.repoRef ?? null,
    contextSources: (input.contextPaths ?? []).map((hostPath, index) => ({
      sourceId: `pending-${index + 1}`,
      kind: "local_path",
      hostPath,
    })),
  };
}

export function failRunWorkbenchRun(
  state: RunWorkbenchState,
  errorMessage: string,
): RunWorkbenchState {
  const message =
    collapseWhitespace(errorMessage, ARTIFACT_PREVIEW_LIMIT) || "Workspace run failed.";

  return {
    ...state,
    status: "error",
    errorMessage: message,
    summary: {
      ...state.summary,
      terminationReason: state.summary?.terminationReason ?? "failed",
      error: message,
    },
    activity: [
      ...state.activity,
      {
        id: `local-error-${state.activity.length + 1}`,
        kind: "error",
        text: message,
      },
    ],
  };
}

function isRunWorkbenchFrame(frame: WsServerMessage): boolean {
  if (frame.type === "error") return true;
  const payload = asRecord(frame.data.payload);
  const runtime = extractRuntime(payload);
  const sourceType = asText(payload?.source_type ?? payload?.sourceType);
  return (
    sourceType === "execution_started" ||
    sourceType === "execution_step" ||
    sourceType === "execution_completed" ||
    asText(payload?.runtime_mode) === "daytona_pilot" ||
    asText(runtime?.runtime_mode) === "daytona_pilot" ||
    payload?.run_summary != null ||
    payload?.run_result != null ||
    payload?.final_artifact != null ||
    payload?.iterations != null ||
    frame.data.kind === "final" ||
    frame.data.kind === "cancelled" ||
    frame.data.kind === "error"
  );
}

export function shouldApplyRunFrame(state: RunWorkbenchState, frame: WsServerMessage): boolean {
  const acceptsTerminalCompat =
    state.status === "bootstrapping" ||
    state.status === "running" ||
    state.status === "completed" ||
    state.status === "needs_human_review";
  const acceptsRawError = state.status === "bootstrapping" || state.status === "running";
  if (frame.type === "error") {
    return acceptsRawError;
  }
  if (
    (frame.data.kind === "final" ||
      frame.data.kind === "cancelled" ||
      frame.data.kind === "error") &&
    acceptsTerminalCompat
  ) {
    return true;
  }
  return isRunWorkbenchFrame(frame);
}

function statusFromFrame(
  current: RunWorkbenchState["status"],
  frame: WsServerMessage,
  payload?: Record<string, unknown>,
  runSummary?: Record<string, unknown>,
): RunWorkbenchState["status"] {
  if (frame.type === "error") return "error";
  const payloadStatus = normalizeRunStatus(runSummary?.status ?? payload?.status);
  if (payloadStatus) return payloadStatus;
  if (frame.data.kind === "final") return "completed";
  if (frame.data.kind === "cancelled") return "cancelled";
  if (frame.data.kind === "error") return "error";
  if (current === "idle") return "bootstrapping";
  return "running";
}

export function applyFrameToRunWorkbenchState(
  state: RunWorkbenchState,
  frame: WsServerMessage,
): RunWorkbenchState {
  let next: RunWorkbenchState = {
    ...state,
    lastFrame: frame,
    activity: [...state.activity, buildActivityEntry(frame)],
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
  const runSummary = getCanonicalRunSummary(payload);
  const compatRunResult = getCompatRunResult(payload);

  if (runSummary) {
    next = hydrateFromRunSummary(next, runSummary);
  }

  const isCanonicalCompletion = isExecutionCompletedPayload(payload);
  const isTerminalCompatFrame =
    !isCanonicalCompletion &&
    frame.type === "event" &&
    (frame.data.kind === "final" || frame.data.kind === "cancelled" || frame.data.kind === "error");

  const payloadPrompts = !isTerminalCompatFrame
    ? dedupePromptHandles([
        ...next.promptHandles,
        ...collectPromptHandlePayloads(payload)
          .map((item) => normalizePromptHandle(item))
          .filter((item): item is PromptHandleSummary => item !== null),
      ])
    : next.promptHandles;

  const payloadContextSources = !isTerminalCompatFrame
    ? asArray(payload?.context_sources ?? payload?.contextSources)
        .map((item) => normalizeContextSource(item))
        .filter((item): item is ContextSourceSummary => item !== null)
    : [];

  if (payloadContextSources.length > 0) {
    next = {
      ...next,
      contextSources: payloadContextSources,
    };
  }

  if (payloadPrompts.length > 0) {
    next = {
      ...next,
      promptHandles: payloadPrompts,
    };
  }

  const iterationNumber = asNumber(payload?.iteration);
  if (iterationNumber != null && !runSummary) {
    next = {
      ...next,
      iterations: upsertIteration(next.iterations, {
        id: `iteration-${iterationNumber}`,
        iteration: iterationNumber,
        status:
          frame.data.kind === "error"
            ? "error"
            : frame.data.kind === "final"
              ? "completed"
              : "running",
        phase: asText(payload?.phase),
        summary: frame.data.text,
        durationMs: asNumber(payload?.duration_ms ?? payload?.durationMs),
        callbackCount: asNumber(payload?.callback_count ?? payload?.callbackCount),
      }),
      selectedIterationId: next.selectedIterationId ?? `iteration-${iterationNumber}`,
    };
  }

  const callbackName = asText(payload?.callback_name ?? payload?.callbackName);
  if (callbackName) {
    const toolInput = asRecord(payload?.tool_input ?? payload?.toolInput);
    const toolResult = asRecord(payload?.tool_result ?? payload?.toolResult);
    const toolTask = asRecord(toolInput?.task);
    const latestRunningCallback = findLatestRunningCallback(next.callbacks, {
      callbackName,
      iteration: iterationNumber,
    });
    const callback: CallbackSummary = {
      id:
        latestRunningCallback?.id ??
        `${callbackName}-${iterationNumber ?? "na"}-${next.callbacks.length + 1}`,
      callbackName,
      iteration: iterationNumber,
      status: frame.data.kind === "tool_call" ? "running" : "completed",
      task:
        asText(toolInput?.task) ??
        asText(toolTask?.task) ??
        latestRunningCallback?.task ??
        frame.data.text,
      label: asText(toolTask?.label) ?? latestRunningCallback?.label,
      resultPreview:
        previewText(
          toolResult?.result_preview ??
            toolResult?.resultPreview ??
            toolResult?.result_previews ??
            toolResult?.count,
          ARTIFACT_PREVIEW_LIMIT,
        ) || latestRunningCallback?.resultPreview,
      source: normalizeCallbackSource(toolTask?.source) ?? latestRunningCallback?.source,
    };
    next = {
      ...next,
      callbacks: upsertCallback(next.callbacks, callback),
      selectedCallbackId: next.selectedCallbackId ?? callback.id,
    };
  }

  const payloadSources = !isTerminalCompatFrame
    ? dedupeSources([
        ...next.sources,
        ...asArray(payload?.sources)
          .map((item) => normalizeSource(item))
          .filter((item): item is ChatSourceItem => item !== null),
      ])
    : next.sources;
  const payloadAttachments = !isTerminalCompatFrame
    ? dedupeAttachments([
        ...next.attachments,
        ...asArray(payload?.attachments)
          .map((item) => normalizeAttachment(item))
          .filter((item): item is ChatAttachmentItem => item !== null),
      ])
    : next.attachments;

  const canonicalSummary = isCanonicalCompletion
    ? normalizeSummary(payload?.summary ?? runSummary?.summary)
    : undefined;
  const canonicalFinalArtifact = isCanonicalCompletion
    ? normalizeArtifact(
        payload?.final_artifact ??
          payload?.finalArtifact ??
          runSummary?.final_artifact ??
          runSummary?.finalArtifact,
      )
    : undefined;
  const compatSummary = resolveCompatSummary(payload, compatRunResult);
  const compatFinalArtifact = resolveCompatFinalArtifact(payload, compatRunResult);
  const useCompatSummary = !isCanonicalCompletion && next.summary == null && compatSummary != null;
  const useCompatFinalArtifact =
    !isCanonicalCompletion && next.finalArtifact == null && compatFinalArtifact != null;
  const mergedSummary = mergeMlflowTraceMetadata(
    canonicalSummary ?? (useCompatSummary ? compatSummary : undefined) ?? next.summary,
    payload,
  );

  let compatBackfillCount = next.compatBackfillCount;
  let lastCompatBackfill = next.lastCompatBackfill;
  if (useCompatSummary || useCompatFinalArtifact) {
    compatBackfillCount += 1;
    lastCompatBackfill = {
      eventId: compatBackfillEventId(frame),
      runtimeMode:
        asText(payload?.runtime_mode ?? payload?.runtimeMode) ??
        asText(runtime?.runtime_mode ?? runtime?.runtimeMode),
      usedSummary: useCompatSummary,
      usedFinalArtifact: useCompatFinalArtifact,
    } satisfies CompatBackfillInfo;
  }

  const nextStatus = statusFromFrame(next.status, frame, payload, runSummary);
  // When the run reaches a terminal state, finalize any orphaned "running"
  // callbacks that were never resolved (e.g. reused runs replaying tool_call
  // events without matching tool_result events).
  const terminalCallbackStatus = nextStatus === "cancelled" ? "cancelled" : "completed";
  const finalCallbacks =
    nextStatus === "completed" ||
    nextStatus === "needs_human_review" ||
    nextStatus === "error" ||
    nextStatus === "cancelled"
      ? next.callbacks.map((cb) =>
          cb.status === "running" ? { ...cb, status: terminalCallbackStatus } : cb,
        )
      : next.callbacks;

  return {
    ...next,
    status: nextStatus,
    callbacks: finalCallbacks,
    runId: asText(payload?.run_id ?? payload?.runId) ?? asText(runtime?.run_id) ?? next.runId,
    daytonaMode:
      normalizeDaytonaMode(
        asText(payload?.daytona_mode ?? payload?.daytonaMode) ??
          asText(runtime?.daytona_mode ?? runtime?.daytonaMode),
      ) ?? next.daytonaMode,
    sources: payloadSources,
    attachments: payloadAttachments,
    finalArtifact:
      canonicalFinalArtifact ??
      (useCompatFinalArtifact ? compatFinalArtifact : undefined) ??
      next.finalArtifact ??
      null,
    summary: mergedSummary,
    errorMessage: frame.data.kind === "error" ? frame.data.text : (next.errorMessage ?? null),
    compatBackfillCount,
    lastCompatBackfill,
  };
}
