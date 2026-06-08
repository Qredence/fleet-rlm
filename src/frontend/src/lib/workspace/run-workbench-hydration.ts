import type { WsServerMessage } from "@/lib/rlm-api";
import { normalizeDaytonaMode } from "@/lib/workspace/daytona-mode";
import type {
  ActivityEntry,
  CallbackSummary,
  ChatAttachmentItem,
  ChatSourceItem,
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

function isExecutionCompletedPayload(
  payload?: Record<string, unknown>,
  frame?: WsServerMessage,
): boolean {
  return (
    (frame?.type === "event" && frame.data.kind === "execution_completed") ||
    asText(payload?.source_type ?? payload?.sourceType) === "execution_completed"
  );
}

function getCanonicalRunSummary(
  payload?: Record<string, unknown>,
): Record<string, unknown> | undefined {
  return asRecord(payload?.run_summary ?? payload?.runSummary);
}

function artifactFromCompletionText(text: string) {
  const trimmed = text.trim();
  if (!trimmed) return null;
  return normalizeArtifact({
    kind: "assistant_response",
    value: {
      text: trimmed,
      final_markdown: trimmed,
      summary: trimmed,
    },
    finalization_mode: "RETURN",
  });
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

function buildActivityEntry(frame: WsServerMessage): ActivityEntry {
  if (frame.type === "error") {
    return {
      id: `frame-error-${Date.now()}`,
      kind: "error",
      text: frame.message,
    };
  }

  const payload = asRecord(frame.data.payload);
  const kind = frame.data.kind;
  return {
    id: String(frame.data.event_id ?? `${kind}-${frame.data.timestamp ?? Date.now()}`),
    kind,
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
  const sourceType = asText(payload?.source_type ?? payload?.sourceType);
  return (
    frame.data.kind === "execution_started" ||
    frame.data.kind === "execution_step" ||
    frame.data.kind === "execution_completed" ||
    sourceType === "execution_started" ||
    sourceType === "execution_step" ||
    sourceType === "execution_completed" ||
    payload?.run_summary != null
  );
}

export function shouldApplyRunFrame(state: RunWorkbenchState, frame: WsServerMessage): boolean {
  const acceptsRawError = state.status === "bootstrapping" || state.status === "running";
  if (frame.type === "error") {
    return acceptsRawError;
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
  if (payload?.cancelled === true) return "cancelled";
  if (frame.data.kind === "execution_completed") return "completed";
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

  if (runSummary) {
    next = hydrateFromRunSummary(next, runSummary);
  }

  const isCanonicalCompletion = isExecutionCompletedPayload(payload, frame);

  const payloadPrompts =
    frame.data.kind !== "execution_completed"
      ? dedupePromptHandles([
          ...next.promptHandles,
          ...collectPromptHandlePayloads(payload)
            .map((item) => normalizePromptHandle(item))
            .filter((item): item is PromptHandleSummary => item !== null),
        ])
      : next.promptHandles;

  const payloadContextSources =
    frame.data.kind !== "execution_completed"
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
        status: frame.data.kind === "execution_completed" ? "completed" : "running",
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
      status: frame.data.kind === "execution_step" ? "running" : "completed",
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

  const payloadSources =
    frame.data.kind !== "execution_completed"
      ? dedupeSources([
          ...next.sources,
          ...asArray(payload?.sources)
            .map((item) => normalizeSource(item))
            .filter((item): item is ChatSourceItem => item !== null),
        ])
      : next.sources;
  const payloadAttachments =
    frame.data.kind !== "execution_completed"
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
  const explicitFinalArtifact = normalizeArtifact(
    payload?.final_artifact ??
      payload?.finalArtifact ??
      runSummary?.final_artifact ??
      runSummary?.finalArtifact,
  );
  const hasRunResultBackfill = asRecord(payload?.run_result ?? payload?.runResult) != null;
  const canonicalFinalArtifact = isCanonicalCompletion
    ? (explicitFinalArtifact ??
        (!hasRunResultBackfill ? artifactFromCompletionText(frame.data.text) : null))
    : undefined;
  const mergedSummary = mergeMlflowTraceMetadata(canonicalSummary ?? next.summary, payload);

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
    finalArtifact: canonicalFinalArtifact ?? next.finalArtifact ?? null,
    summary: mergedSummary,
    errorMessage: nextStatus === "error" ? frame.data.text : (next.errorMessage ?? null),
  };
}
