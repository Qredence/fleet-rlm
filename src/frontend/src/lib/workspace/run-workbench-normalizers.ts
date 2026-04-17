import type { ChatAttachmentItem, ChatSourceItem } from "@/lib/workspace/workspace-types";
import type {
  ArtifactSummary,
  CallbackSourceSummary,
  CallbackSummary,
  ContextSourceSummary,
  HumanReviewSummary,
  IterationSummary,
  PromptHandleSummary,
  RunStatus,
  RunSummary,
} from "@/lib/workspace/workspace-types";

export const PROMPT_PREVIEW_LIMIT = 220;
export const ARTIFACT_PREVIEW_LIMIT = 320;

export function asRecord(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return undefined;
  }
  return value as Record<string, unknown>;
}

export function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

export function asText(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed ? trimmed : undefined;
}

export function asNumber(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return undefined;
}

export function collapseWhitespace(value: string, limit: number): string {
  const normalized = value.replace(/\s+/g, " ").trim();
  if (normalized.length <= limit) return normalized;
  return `${normalized.slice(0, Math.max(0, limit - 1)).trimEnd()}…`;
}

export function stringifyValue(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function previewText(value: unknown, limit: number): string {
  return collapseWhitespace(stringifyValue(value), limit);
}

export function preferredArtifactText(value: unknown): string | undefined {
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

export function normalizeWarnings(value: unknown): string[] {
  return asArray(value)
    .map((item) => asText(item))
    .filter((item): item is string => Boolean(item))
    .map((item) => collapseWhitespace(item, ARTIFACT_PREVIEW_LIMIT));
}

export function normalizeContextSource(raw: unknown): ContextSourceSummary | null {
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

export function normalizePromptHandle(raw: unknown): PromptHandleSummary | null {
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

export function dedupePromptHandles(handles: PromptHandleSummary[]): PromptHandleSummary[] {
  const deduped = new Map<string, PromptHandleSummary>();
  for (const handle of handles) {
    deduped.set(handle.handleId, handle);
  }
  return [...deduped.values()];
}

export function collectPromptHandlePayloads(payload?: Record<string, unknown>): unknown[] {
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

export function normalizeArtifact(raw: unknown): ArtifactSummary | null {
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

export function normalizeSource(raw: unknown): ChatSourceItem | null {
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

export function dedupeSources(sources: ChatSourceItem[]): ChatSourceItem[] {
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

export function callbackSourceKey(source?: CallbackSourceSummary): string {
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

export function normalizeAttachment(raw: unknown): ChatAttachmentItem | null {
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

export function dedupeAttachments(attachments: ChatAttachmentItem[]): ChatAttachmentItem[] {
  const deduped = new Map<string, ChatAttachmentItem>();
  for (const attachment of attachments) {
    deduped.set(attachment.attachmentId, attachment);
  }
  return [...deduped.values()];
}

export function normalizeCallbackSource(raw: unknown): CallbackSourceSummary | undefined {
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

export function normalizeCallback(raw: unknown): CallbackSummary | null {
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

export function dedupeCallbacks(callbacks: CallbackSummary[]): CallbackSummary[] {
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
    // Don't regress a terminal status (completed/error/cancelled) back to
    // running when a reused/replayed run re-emits tool_call events without
    // matching tool_result.
    const isTerminal = (s: string) => s === "completed" || s === "error" || s === "cancelled";
    const mergedStatus = isTerminal(current.status) ? current.status : callback.status;
    deduped.set(key, {
      ...current,
      ...callback,
      status: mergedStatus,
      resultPreview: callback.resultPreview ?? current.resultPreview,
      source: callback.source ?? current.source,
    });
  }
  return [...deduped.values()];
}

export function findLatestRunningCallback(
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

export function upsertCallback(
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

export function normalizeIteration(raw: unknown): IterationSummary | null {
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

export function upsertIteration(
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

export function normalizeHumanReview(raw: unknown): HumanReviewSummary | undefined {
  const record = asRecord(raw);
  if (!record) return undefined;
  const repairSteps = asArray(record.repair_steps ?? record.repairSteps)
    .map((item) => asText(item))
    .filter((item): item is string => Boolean(item));
  const required = typeof record.required === "boolean" ? record.required : true;
  return {
    required,
    reason: asText(record.reason),
    repairMode: asText(record.repair_mode ?? record.repairMode),
    repairTarget: asText(record.repair_target ?? record.repairTarget),
    repairSteps,
  };
}

export function normalizeSummary(raw: unknown): RunSummary | undefined {
  const record = asRecord(raw);
  if (!record) return undefined;
  return {
    durationMs: asNumber(record.duration_ms ?? record.durationMs),
    sandboxesUsed: asNumber(record.sandboxes_used ?? record.sandboxesUsed),
    terminationReason: asText(record.termination_reason ?? record.terminationReason),
    error: asText(record.error) ?? null,
    warnings: normalizeWarnings(record.warnings),
    mlflowTraceId: asText(record.mlflow_trace_id ?? record.mlflowTraceId),
    mlflowClientRequestId: asText(record.mlflow_client_request_id ?? record.mlflowClientRequestId),
    humanReview: normalizeHumanReview(record.human_review ?? record.humanReview),
  };
}

export function normalizeRunStatus(value: unknown): RunStatus | undefined {
  const status = asText(value);
  if (!status) return undefined;
  if (status === "needs_human_review") return "needs_human_review";
  if (status === "completed") return "completed";
  if (status === "cancelled") return "cancelled";
  if (status === "error" || status === "failed") return "error";
  if (status === "running") return "running";
  if (status === "bootstrapping") return "bootstrapping";
  return undefined;
}
