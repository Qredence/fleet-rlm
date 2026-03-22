import type { ChatAttachmentItem, ChatSourceItem } from "@/screens/workspace/model/workspace-types";
import type { WsServerMessage } from "@/lib/rlm-api";
import { normalizeDaytonaMode } from "@/screens/workspace/model/daytona-mode";
import type {
  ActivityEntry,
  ArtifactSummary,
  CallbackSourceSummary,
  CallbackSummary,
  CompatBackfillInfo,
  ContextSourceSummary,
  IterationSummary,
  PromptHandleSummary,
  RunSummary,
  RunWorkbenchState,
} from "@/screens/workspace/model/run-workbench-types";

const PROMPT_PREVIEW_LIMIT = 220;
const ARTIFACT_PREVIEW_LIMIT = 320;

function asRecord(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return undefined;
  }
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

function preferredArtifactText(value: unknown): string | undefined {
  const direct = asText(value);
  if (direct) return direct;

  const record = asRecord(value);
  if (!record) return undefined;

  for (const key of ["final_markdown", "summary", "text", "content", "message"]) {
    const candidate = asText(record[key]);
    if (candidate) return candidate;
  }

  const nestedValue = record.value;
  if (nestedValue !== value) {
    return preferredArtifactText(nestedValue);
  }

  return undefined;
}

function normalizeWarnings(value: unknown): string[] {
  return asArray(value)
    .map((item) => asText(item))
    .filter((item): item is string => Boolean(item))
    .map((item) => collapseWhitespace(item, ARTIFACT_PREVIEW_LIMIT));
}

function normalizeContextSource(raw: unknown): ContextSourceSummary | null {
  const record = asRecord(raw);
  if (!record) return null;
  const sourceId =
    asText(record.source_id ?? record.sourceId) ?? asText(record.host_path ?? record.hostPath);
  const hostPath = asText(record.host_path ?? record.hostPath);
  if (!sourceId || !hostPath) return null;
  return {
    sourceId,
    kind: asText(record.kind) ?? "file",
    hostPath,
    stagedPath: asText(record.staged_path ?? record.stagedPath),
    sourceType: asText(record.source_type ?? record.sourceType),
    extractionMethod: asText(record.extraction_method ?? record.extractionMethod),
    fileCount: asNumber(record.file_count ?? record.fileCount),
    skippedCount: asNumber(record.skipped_count ?? record.skippedCount),
    warnings: normalizeWarnings(record.warnings),
  };
}

function normalizePromptHandle(raw: unknown): PromptHandleSummary | null {
  const record = asRecord(raw);
  if (!record) return null;
  const handleId = asText(record.handle_id) ?? asText(record.handleId) ?? asText(record.id);
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

function dedupePromptHandles(handles: PromptHandleSummary[]): PromptHandleSummary[] {
  const deduped = new Map<string, PromptHandleSummary>();
  for (const handle of handles) {
    deduped.set(handle.handleId, handle);
  }
  return [...deduped.values()];
}

function collectPromptHandlePayloads(payload?: Record<string, unknown>): unknown[] {
  const node = asRecord(payload?.node);
  const promptManifest = asRecord(
    payload?.prompt_manifest ??
      payload?.promptManifest ??
      node?.prompt_manifest ??
      node?.promptManifest,
  );

  return [
    ...asArray(payload?.prompts ?? payload?.prompt_handles ?? payload?.promptHandles),
    ...asArray(node?.prompt_handles ?? node?.promptHandles),
    ...asArray(promptManifest?.handles),
  ];
}

function normalizeArtifact(raw: unknown): ArtifactSummary | null {
  const record = asRecord(raw);
  if (!record) {
    const textPreview = previewText(preferredArtifactText(raw) ?? raw, ARTIFACT_PREVIEW_LIMIT);
    if (!textPreview) return null;
    return {
      value: raw,
      textPreview,
    };
  }

  const value = record.value;
  const textPreview = previewText(
    preferredArtifactText(record) ?? preferredArtifactText(value) ?? value,
    ARTIFACT_PREVIEW_LIMIT,
  );
  return {
    kind: asText(record.kind),
    value,
    variableName: asText(record.variable_name ?? record.variableName),
    finalizationMode: asText(record.finalization_mode ?? record.finalizationMode),
    textPreview: textPreview || undefined,
  };
}

function normalizeSource(raw: unknown): ChatSourceItem | null {
  const record = asRecord(raw);
  if (!record) return null;
  const sourceId =
    asText(record.source_id ?? record.sourceId) ?? asText(record.id) ?? asText(record.path);
  const title =
    asText(record.title) ??
    asText(record.path) ??
    asText(record.host_path ?? record.hostPath) ??
    "Source";
  if (!sourceId) return null;

  const kindRaw = asText(record.kind)?.toLowerCase();
  const kind: ChatSourceItem["kind"] =
    kindRaw === "web" || kindRaw === "file" || kindRaw === "artifact" || kindRaw === "tool_output"
      ? kindRaw
      : "other";

  return {
    sourceId,
    kind,
    title,
    url: asText(record.url),
    canonicalUrl: asText(record.canonical_url ?? record.canonicalUrl),
    displayUrl:
      asText(record.display_url ?? record.displayUrl) ??
      asText(record.host_path ?? record.hostPath) ??
      asText(record.path),
    description: asText(record.description),
    quote: asText(record.quote),
  };
}

function dedupeSources(sources: ChatSourceItem[]): ChatSourceItem[] {
  const deduped = new Map<string, ChatSourceItem>();
  for (const source of sources) {
    const key =
      source.kind === "web"
        ? (source.canonicalUrl ?? source.url ?? source.displayUrl ?? source.sourceId)
        : (source.sourceId ?? source.canonicalUrl ?? source.url ?? source.displayUrl);
    deduped.set(key, source);
  }
  return [...deduped.values()];
}

function callbackSourceKey(source?: CallbackSourceSummary): string {
  if (!source) return "";
  return [
    source.kind,
    source.sourceId,
    source.path,
    source.startLine,
    source.endLine,
    source.chunkIndex,
    source.header,
    source.pattern,
  ]
    .map((value) => String(value ?? ""))
    .join("|");
}

function normalizeAttachment(raw: unknown): ChatAttachmentItem | null {
  const record = asRecord(raw);
  if (!record) return null;
  const attachmentId =
    asText(record.attachment_id ?? record.attachmentId) ?? asText(record.id) ?? asText(record.name);
  if (!attachmentId) return null;
  return {
    attachmentId,
    name: asText(record.name) ?? asText(record.title) ?? "Attachment",
    url: asText(record.url),
    previewUrl: asText(record.preview_url ?? record.previewUrl),
    mimeType: asText(record.mime_type ?? record.mimeType),
    mediaType: asText(record.media_type ?? record.mediaType),
    sizeBytes: asNumber(record.size_bytes ?? record.sizeBytes),
    kind: asText(record.kind),
    description: asText(record.description),
  };
}

function dedupeAttachments(attachments: ChatAttachmentItem[]): ChatAttachmentItem[] {
  const deduped = new Map<string, ChatAttachmentItem>();
  for (const attachment of attachments) {
    deduped.set(attachment.attachmentId, attachment);
  }
  return [...deduped.values()];
}

function normalizeCallbackSource(raw: unknown): CallbackSourceSummary | undefined {
  const record = asRecord(raw);
  if (!record) return undefined;
  return {
    kind: asText(record.kind),
    sourceId: asText(record.source_id ?? record.sourceId),
    path: asText(record.path),
    startLine: asNumber(record.start_line ?? record.startLine),
    endLine: asNumber(record.end_line ?? record.endLine),
    chunkIndex: asNumber(record.chunk_index ?? record.chunkIndex),
    header: asText(record.header),
    pattern: asText(record.pattern),
    preview: asText(record.preview),
  };
}

function normalizeCallback(raw: unknown): CallbackSummary | null {
  const record = asRecord(raw);
  if (!record) return null;
  const task = asText(record.task);
  const callbackName =
    asText(record.callback_name ?? record.callbackName) ??
    asText(record.tool_name ?? record.toolName);
  if (!task || !callbackName) return null;
  return {
    id: asText(record.id) ?? `${callbackName}-${record.iteration ?? "na"}-${task.slice(0, 24)}`,
    callbackName,
    iteration: asNumber(record.iteration),
    status: asText(record.status) ?? "completed",
    task,
    label: asText(record.label),
    resultPreview: previewText(
      record.result_preview ?? record.resultPreview,
      ARTIFACT_PREVIEW_LIMIT,
    ),
    source: normalizeCallbackSource(record.source),
  };
}

function dedupeCallbacks(callbacks: CallbackSummary[]): CallbackSummary[] {
  const deduped = new Map<string, CallbackSummary>();
  for (const callback of callbacks) {
    const key = [
      callback.callbackName,
      callback.iteration ?? "na",
      callback.task,
      callback.label ?? "",
      callbackSourceKey(callback.source),
    ].join("|");
    const current = deduped.get(key);
    if (!current) {
      deduped.set(key, callback);
      continue;
    }
    deduped.set(key, {
      ...current,
      ...callback,
      resultPreview: callback.resultPreview ?? current.resultPreview,
      source: callback.source ?? current.source,
    });
  }
  return [...deduped.values()];
}

function findLatestRunningCallback(
  callbacks: CallbackSummary[],
  {
    callbackName,
    iteration,
  }: {
    callbackName: string;
    iteration: number | undefined;
  },
): CallbackSummary | undefined {
  for (let index = callbacks.length - 1; index >= 0; index -= 1) {
    const callback = callbacks[index];
    if (!callback) continue;
    if (callback.callbackName !== callbackName) continue;
    if (callback.iteration !== iteration) continue;
    if (callback.status !== "running") continue;
    return callback;
  }
  return undefined;
}

function upsertCallback(
  callbacks: CallbackSummary[],
  callback: CallbackSummary,
): CallbackSummary[] {
  const next = [...callbacks];
  const index = next.findIndex((item) => item.id === callback.id);
  if (index < 0) {
    next.push(callback);
    return dedupeCallbacks(next);
  }

  next[index] = {
    ...next[index],
    ...callback,
    task: callback.task || next[index]?.task || "",
    label: callback.label ?? next[index]?.label,
    resultPreview: callback.resultPreview ?? next[index]?.resultPreview,
    source: callback.source ?? next[index]?.source,
  };
  return dedupeCallbacks(next);
}

function normalizeIteration(raw: unknown): IterationSummary | null {
  const record = asRecord(raw);
  if (!record) return null;
  const iteration = asNumber(record.iteration);
  if (iteration == null) return null;
  const error = asText(record.error) ?? null;
  const statusRaw = asText(record.status)?.toLowerCase();
  const status: IterationSummary["status"] =
    statusRaw === "pending" ||
    statusRaw === "running" ||
    statusRaw === "completed" ||
    statusRaw === "error"
      ? statusRaw
      : error
        ? "error"
        : "completed";
  const summary =
    asText(record.summary) ??
    asText(record.reasoning_summary ?? record.reasoningSummary) ??
    (error ? collapseWhitespace(error, ARTIFACT_PREVIEW_LIMIT) : "");

  return {
    id: `iteration-${iteration}`,
    iteration,
    status,
    phase: asText(record.phase),
    summary: summary || `Iteration ${iteration}`,
    reasoningSummary: asText(record.reasoning_summary ?? record.reasoningSummary),
    code: typeof record.code === "string" ? record.code : undefined,
    stdout: typeof record.stdout === "string" ? record.stdout : undefined,
    stderr: typeof record.stderr === "string" ? record.stderr : undefined,
    error,
    durationMs: asNumber(record.duration_ms ?? record.durationMs),
    callbackCount: asNumber(record.callback_count ?? record.callbackCount),
    finalized: Boolean(record.finalized),
  };
}

function upsertIteration(
  iterations: IterationSummary[],
  partial: IterationSummary,
): IterationSummary[] {
  const next = [...iterations];
  const index = next.findIndex((item) => item.iteration === partial.iteration);
  if (index < 0) {
    next.push(partial);
  } else {
    next[index] = {
      ...next[index],
      ...partial,
      phase: partial.phase ?? next[index]?.phase,
      summary: partial.summary || next[index]?.summary || "",
      code: partial.code ?? next[index]?.code,
      stdout: partial.stdout ?? next[index]?.stdout,
      stderr: partial.stderr ?? next[index]?.stderr,
      error: partial.error ?? next[index]?.error ?? null,
      durationMs: partial.durationMs ?? next[index]?.durationMs,
      callbackCount: partial.callbackCount ?? next[index]?.callbackCount,
      finalized: partial.finalized ?? next[index]?.finalized,
    };
  }
  return next.sort((left, right) => left.iteration - right.iteration);
}

function normalizeSummary(raw: unknown): RunSummary | undefined {
  const record = asRecord(raw);
  if (!record) return undefined;
  return {
    durationMs: asNumber(record.duration_ms ?? record.durationMs),
    sandboxesUsed: asNumber(record.sandboxes_used ?? record.sandboxesUsed),
    terminationReason: asText(record.termination_reason ?? record.terminationReason),
    error: asText(record.error) ?? null,
    warnings: normalizeWarnings(record.warnings),
  };
}

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

function resolveCompatSummary(
  payload?: Record<string, unknown>,
  compatRunResult?: Record<string, unknown>,
): RunSummary | undefined {
  return normalizeSummary(payload?.summary ?? compatRunResult?.summary);
}

function resolveCompatFinalArtifact(
  payload?: Record<string, unknown>,
  compatRunResult?: Record<string, unknown>,
): ArtifactSummary | null | undefined {
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
    state.status === "bootstrapping" || state.status === "running" || state.status === "completed";
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
): RunWorkbenchState["status"] {
  if (frame.type === "error") return "error";
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

  return {
    ...next,
    status: statusFromFrame(next.status, frame),
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
    summary: canonicalSummary ?? (useCompatSummary ? compatSummary : undefined) ?? next.summary,
    errorMessage: frame.data.kind === "error" ? frame.data.text : (next.errorMessage ?? null),
    compatBackfillCount,
    lastCompatBackfill,
  };
}
