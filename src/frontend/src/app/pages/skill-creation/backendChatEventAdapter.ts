import type {
  ChatAttachmentItem,
  ChatEnvVarItem,
  ChatInlineCitation,
  ChatMessage,
  ChatQueueItem,
  ChatRenderPart,
  ChatRenderToolState,
  ChatSourceItem,
  ChatTraceStep,
} from "@/lib/data/types";
import type { WsServerEvent, WsServerMessage } from "@/lib/rlm-api";
import { createLocalId } from "@/lib/id";
import { QueryClient } from "@tanstack/react-query";

const DEFAULT_PHASE = 1 as const;
const MAX_CITATIONS = 16;
const MAX_SOURCES = 16;
const MAX_ATTACHMENTS = 16;

interface ApplyFrameResult {
  messages: ChatMessage[];
  terminal: boolean;
  errored: boolean;
}

interface FinalReferenceBundle {
  citations: ChatInlineCitation[];
  sources: ChatSourceItem[];
  attachments: ChatAttachmentItem[];
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  return value as Record<string, unknown>;
}

function normalizeUrl(raw: unknown): string | undefined {
  if (typeof raw !== "string") return undefined;
  const value = raw.trim();
  if (!value) return undefined;

  try {
    const parsed = new URL(value);
    if (
      parsed.protocol === "http:" || parsed.protocol === "https:"
    ) {
      return parsed.toString();
    }
    return undefined;
  } catch {
    return undefined;
  }
}

function asOptionalText(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed ? trimmed : undefined;
}

function asOptionalNumber(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return undefined;
}

function nextId(prefix: string): string {
  return createLocalId(prefix);
}

function appendSystem(messages: ChatMessage[], text: string): ChatMessage[] {
  if (!text.trim()) return messages;
  return [
    ...messages,
    {
      id: nextId("sys"),
      type: "system",
      content: text,
      phase: DEFAULT_PHASE,
    },
  ];
}

function latestStreamingAssistantIndex(messages: ChatMessage[]): number {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const msg = messages[i];
    if (msg?.type === "assistant" && msg.streaming) return i;
  }
  return -1;
}

function latestOpenReasoningIndex(messages: ChatMessage[]): number {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const msg = messages[i];
    if (msg?.type === "reasoning" && msg.reasoningData?.isThinking) return i;
  }
  return -1;
}

function latestTraceIndex(
  messages: ChatMessage[],
  predicate: (part: ChatRenderPart) => boolean,
): number {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const msg = messages[i];
    if (!msg || msg.type !== "trace" || !msg.renderParts) continue;
    if (msg.renderParts.some(predicate)) return i;
  }
  return -1;
}

function ensureStreamingAssistant(messages: ChatMessage[]): ChatMessage[] {
  if (latestStreamingAssistantIndex(messages) >= 0) return messages;
  return [
    ...messages,
    {
      id: nextId("assistant"),
      type: "assistant",
      content: "",
      streaming: true,
      phase: DEFAULT_PHASE,
    },
  ];
}

function appendAssistantToken(
  messages: ChatMessage[],
  token: string,
): ChatMessage[] {
  if (!token) return messages;
  const withAssistant = ensureStreamingAssistant(messages);
  const idx = latestStreamingAssistantIndex(withAssistant);
  if (idx < 0) return withAssistant;

  const copy = [...withAssistant];
  const target = copy[idx];
  if (!target) return withAssistant;
  copy[idx] = { ...target, content: `${target.content}${token}` };
  return copy;
}

function upsertReasoningRenderPart(
  msg: ChatMessage,
  parts: { type: "text"; text: string }[],
  isThinking: boolean,
  duration?: number,
): ChatMessage {
  const nextPart: ChatRenderPart = {
    kind: "reasoning",
    parts,
    isStreaming: isThinking,
    duration,
  };
  return {
    ...msg,
    renderParts: [nextPart],
  };
}

type ReasoningAppendMode = "line" | "chunk";

function mergeReasoningParts(
  existingParts: { type: "text"; text: string }[],
  text: string,
  mode: ReasoningAppendMode,
): { type: "text"; text: string }[] {
  if (existingParts.length === 0) {
    return [{ type: "text", text }];
  }

  if (mode === "line") {
    return [...existingParts, { type: "text", text }];
  }

  const nextParts = [...existingParts];
  const last = nextParts[nextParts.length - 1];
  if (!last) return [{ type: "text", text }];

  const incoming = text;
  const lastText = last.text;

  const startsNewStructuredLine =
    /^(status:|tool call:|tool result:|plan:|warning:|error:)/i.test(incoming);
  const looksLikeSentenceChunk =
    incoming.length <= 32 ||
    /^[a-z0-9(,[\]'"`]/.test(incoming) ||
    /^[)\].,;:!?]/.test(incoming);
  const shouldJoin =
    !startsNewStructuredLine &&
    (looksLikeSentenceChunk || !/[.!?:]\s*$/.test(lastText));

  if (shouldJoin) {
    const needsSpace =
      !/\s$/.test(lastText) &&
      !/^[)\].,;:!?]/.test(incoming) &&
      !/^['"`]/.test(incoming);
    nextParts[nextParts.length - 1] = {
      type: "text",
      text: `${lastText}${needsSpace ? " " : ""}${incoming}`,
    };
    return nextParts;
  }

  nextParts.push({ type: "text", text: incoming });
  return nextParts;
}

function appendReasoning(
  messages: ChatMessage[],
  text: string,
  mode: ReasoningAppendMode = "line",
): ChatMessage[] {
  const trimmed = text.trim();
  if (!trimmed) return messages;

  const idx = latestOpenReasoningIndex(messages);
  if (idx >= 0) {
    const msg = messages[idx];
    if (!msg?.reasoningData) return messages;
    const parts = mergeReasoningParts(msg.reasoningData.parts, trimmed, mode);

    const copy = [...messages];
    copy[idx] = upsertReasoningRenderPart(
      {
        ...msg,
        reasoningData: {
          ...msg.reasoningData,
          parts,
          isThinking: true,
        },
      },
      parts,
      true,
      msg.reasoningData.duration,
    );
    return copy;
  }

  const parts = mergeReasoningParts([], trimmed, mode);
  return [
    ...messages,
    upsertReasoningRenderPart(
      {
        id: nextId("reasoning"),
        type: "reasoning",
        content: "",
        phase: DEFAULT_PHASE,
        reasoningData: {
          parts,
          isThinking: true,
        },
      },
      parts,
      true,
    ),
  ];
}

function finishReasoning(messages: ChatMessage[]): ChatMessage[] {
  let updated = false;
  const next = messages.map((msg) => {
    if (msg.type !== "reasoning" || !msg.reasoningData?.isThinking) return msg;
    updated = true;
    const nextMsg = {
      ...msg,
      reasoningData: {
        ...msg.reasoningData,
        isThinking: false,
      },
    };
    return upsertReasoningRenderPart(
      nextMsg,
      nextMsg.reasoningData.parts,
      false,
      nextMsg.reasoningData.duration,
    );
  });
  return updated ? next : messages;
}

function completeAssistant(
  messages: ChatMessage[],
  text: string,
): ChatMessage[] {
  const idx = latestStreamingAssistantIndex(messages);

  if (idx >= 0) {
    const copy = [...messages];
    const current = copy[idx];
    if (!current) return messages;
    copy[idx] = {
      ...current,
      content: text || current.content,
      streaming: false,
    };
    return copy;
  }

  if (!text.trim()) return messages;

  return [
    ...messages,
    {
      id: nextId("assistant"),
      type: "assistant",
      content: text,
      streaming: false,
      phase: DEFAULT_PHASE,
    },
  ];
}

function readGuardrailWarnings(
  payload: Record<string, unknown> | undefined,
): string[] {
  const raw = payload?.guardrail_warnings;
  if (!Array.isArray(raw)) return [];
  return raw
    .map((item) => (typeof item === "string" ? item.trim() : ""))
    .filter(Boolean);
}

function appendTracePart(
  messages: ChatMessage[],
  part: ChatRenderPart,
  content = "",
): ChatMessage[] {
  return [
    ...messages,
    {
      id: nextId("trace"),
      type: "trace",
      content,
      phase: DEFAULT_PHASE,
      renderParts: [part],
    },
  ];
}

function upsertQueue(messages: ChatMessage[], text: string): ChatMessage[] {
  const label = text.trim() || "Plan update";
  const idx = latestTraceIndex(messages, (part) => part.kind === "queue");
  const queueItem: ChatQueueItem = {
    id: nextId("queue-item"),
    label,
    completed: false,
  };

  if (idx < 0) {
    return appendTracePart(
      messages,
      {
        kind: "queue",
        title: "Plan",
        items: [queueItem],
      },
      text,
    );
  }

  const copy = [...messages];
  const msg = copy[idx];
  if (!msg?.renderParts?.length) return messages;
  const nextParts = msg.renderParts.map((part) => {
    if (part.kind !== "queue") return part;
    return { ...part, items: [...part.items, queueItem] };
  });
  copy[idx] = { ...msg, content: label, renderParts: nextParts };
  return copy;
}

function trajectoryStepData(
  payload?: Record<string, unknown>,
): Record<string, unknown> | undefined {
  const raw = payload?.step_data;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return undefined;
  return raw as Record<string, unknown>;
}

function extractTrajectoryThought(
  text: string,
  payload?: Record<string, unknown>,
): string | undefined {
  const stepData = trajectoryStepData(payload);
  const thought = asOptionalText(stepData?.thought);
  if (thought) return thought;
  const fallback = text.trim();
  return fallback || undefined;
}

function upsertChainOfThought(
  messages: ChatMessage[],
  text: string,
  payload?: Record<string, unknown>,
): ChatMessage[] {
  const stepData = trajectoryStepData(payload);

  const stepIndex =
    typeof payload?.step_index === "number" ? payload.step_index : undefined;
  const action = asOptionalText(stepData?.action);
  const toolName = asOptionalText(stepData?.tool_name);
  const fallbackLabel = text.trim();
  const label =
    action ||
    (toolName ? `Tool: ${toolName}` : undefined) ||
    (stepIndex != null ? `Step ${stepIndex + 1}` : undefined) ||
    fallbackLabel ||
    "Trace step";

  const details: string[] = [];
  if (toolName) {
    details.push(`Tool: ${toolName}`);
  }
  if (typeof stepData?.input === "string" && stepData.input.trim()) {
    details.push(`Input: ${stepData.input}`);
  }
  if (
    typeof stepData?.observation === "string" &&
    stepData.observation.trim()
  ) {
    details.push(`Observation: ${stepData.observation}`);
  }
  if (typeof stepData?.output === "string" && stepData.output.trim()) {
    details.push(`Output: ${stepData.output}`);
  }

  const step: ChatTraceStep = {
    id: nextId("trace-step"),
    label,
    status: "active",
    details,
  };

  const idx = latestTraceIndex(
    messages,
    (part) => part.kind === "chain_of_thought",
  );
  if (idx < 0) {
    return appendTracePart(
      messages,
      {
        kind: "chain_of_thought",
        title: "Execution trace",
        steps: [step],
      },
      label,
    );
  }

  const copy = [...messages];
  const msg = copy[idx];
  if (!msg?.renderParts) return messages;
  const nextParts = msg.renderParts.map((part) => {
    if (part.kind !== "chain_of_thought") return part;
    const completedSteps = part.steps.map((s) =>
      s.status === "active" ? { ...s, status: "complete" as const } : s,
    );
    return { ...part, steps: [...completedSteps, step] };
  });
  copy[idx] = { ...msg, content: label, renderParts: nextParts };
  return copy;
}

function finalizeTraceParts(messages: ChatMessage[]): ChatMessage[] {
  return messages.map((msg) => {
    if (msg.type !== "trace" || !msg.renderParts) return msg;
    const renderParts = msg.renderParts.map((part) => {
      switch (part.kind) {
        case "chain_of_thought":
          return {
            ...part,
            steps: part.steps.map((s) =>
              s.status === "active" ? { ...s, status: "complete" as const } : s,
            ),
          };
        case "queue":
          return {
            ...part,
            items: part.items.map((it) => ({ ...it, completed: true })),
          };
        case "task":
          return part.status === "in_progress"
            ? { ...part, status: "completed" as const }
            : part;
        default:
          return part;
      }
    });
    return { ...msg, renderParts };
  });
}

function inferToolState(
  kind: "tool_call" | "tool_result",
  text: string,
): ChatRenderToolState {
  if (kind === "tool_call") return "running";
  return /error|failed/i.test(text) ? "output-error" : "output-available";
}

function parseEnvVariablesFromPayload(
  payload?: Record<string, unknown>,
): ChatEnvVarItem[] | null {
  if (!payload) return null;

  const objectCandidates: unknown[] = [
    payload.env,
    payload.variables,
    payload.tool_output,
    payload.output,
  ];

  for (const candidate of objectCandidates) {
    if (!candidate || typeof candidate !== "object" || Array.isArray(candidate))
      continue;
    const entries = Object.entries(candidate as Record<string, unknown>).filter(
      ([k, v]) =>
        /^[A-Z0-9_]+$/.test(k) &&
        (typeof v === "string" ||
          typeof v === "number" ||
          typeof v === "boolean"),
    );
    if (entries.length === 0) continue;
    return entries
      .slice(0, 50)
      .map(([name, value]) => ({ name, value: String(value) }));
  }

  const strCandidates: unknown[] = [
    payload.tool_output,
    payload.output,
    payload.tool_input,
    payload.tool_args,
  ];
  for (const candidate of strCandidates) {
    if (typeof candidate !== "string") continue;
    const rows = candidate
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => {
        const m = line.match(/^([A-Z0-9_]+)=(.*)$/);
        if (!m) return null;
        return { name: m[1], value: m[2] };
      })
      .filter((v): v is ChatEnvVarItem => v != null);
    if (rows.length > 0) return rows.slice(0, 50);
  }

  return null;
}

function isSandboxPayload(payload?: Record<string, unknown>): boolean {
  if (!payload) return false;
  const step = payload.step;
  if (step && typeof step === "object" && !Array.isArray(step)) {
    const stepType = String(
      (step as Record<string, unknown>).type ?? "",
    ).toLowerCase();
    if (stepType === "repl") return true;
  }
  const toolName = String(payload.tool_name ?? "").toLowerCase();
  return ["python", "repl", "shell", "exec", "interpreter"].some((s) =>
    toolName.includes(s),
  );
}

function sandboxFromPayload(
  kind: "tool_call" | "tool_result",
  text: string,
  payload?: Record<string, unknown>,
): ChatRenderPart {
  const step =
    payload?.step &&
    typeof payload.step === "object" &&
    !Array.isArray(payload.step)
      ? (payload.step as Record<string, unknown>)
      : undefined;
  const code =
    (typeof step?.input === "string" && step.input) ||
    (typeof payload?.tool_input === "string" && payload.tool_input) ||
    (typeof payload?.tool_args === "string" && payload.tool_args) ||
    "";
  const output =
    (typeof step?.output === "string" && step.output) ||
    (typeof payload?.tool_output === "string" && payload.tool_output) ||
    text;
  const state = inferToolState(kind, text);
  return {
    kind: "sandbox",
    title: String(payload?.tool_name ?? "Sandbox"),
    state,
    code,
    output,
    errorText: state === "output-error" ? output : undefined,
    language: "text",
  };
}

function toolFromPayload(
  kind: "tool_call" | "tool_result",
  text: string,
  payload?: Record<string, unknown>,
): ChatRenderPart {
  const state = inferToolState(kind, text);
  return {
    kind: "tool",
    title: String(payload?.tool_name ?? (text || "Tool")),
    toolType: String(payload?.tool_name ?? "tool"),
    state,
    input: payload?.tool_input ?? payload?.tool_args ?? payload?.input,
    output: payload?.tool_output ?? payload?.output ?? text,
    errorText: state === "output-error" ? text || "Tool error" : undefined,
  };
}

function upsertToolLikePart(
  messages: ChatMessage[],
  kind: "tool_call" | "tool_result",
  text: string,
  payload?: Record<string, unknown>,
): ChatMessage[] {
  const envVars = parseEnvVariablesFromPayload(payload);
  if (envVars && kind === "tool_result") {
    return appendTracePart(
      messages,
      {
        kind: "environment_variables",
        title: String(payload?.tool_name ?? "Environment variables"),
        variables: envVars,
      },
      text,
    );
  }

  const part = isSandboxPayload(payload)
    ? sandboxFromPayload(kind, text, payload)
    : toolFromPayload(kind, text, payload);

  if (kind === "tool_result") {
    const toolName = String(payload?.tool_name ?? "");
    const idx = latestTraceIndex(messages, (existing) => {
      if (existing.kind !== part.kind) return false;
      if (existing.kind === "tool") {
        const open =
          existing.state === "running" || existing.state === "input-streaming";
        return (
          open &&
          (!toolName ||
            existing.toolType === toolName ||
            existing.title === toolName)
        );
      }
      if (existing.kind === "sandbox") {
        const open =
          existing.state === "running" || existing.state === "input-streaming";
        return open && (!toolName || existing.title === toolName);
      }
      return false;
    });

    if (idx >= 0) {
      const copy = [...messages];
      const msg = copy[idx];
      if (msg?.renderParts) {
        copy[idx] = {
          ...msg,
          content: text || msg.content,
          renderParts: msg.renderParts.map((rp) => {
            if (rp.kind !== part.kind) return rp;
            if (rp.kind === "tool" && part.kind === "tool") {
              const open =
                rp.state === "running" || rp.state === "input-streaming";
              const nameMatches =
                !toolName || rp.toolType === toolName || rp.title === toolName;
              if (!open || !nameMatches) return rp;
              return {
                ...rp,
                state: part.state,
                output: part.output,
                errorText: part.errorText,
              };
            }
            if (rp.kind === "sandbox" && part.kind === "sandbox") {
              const open =
                rp.state === "running" || rp.state === "input-streaming";
              const nameMatches = !toolName || rp.title === toolName;
              if (!open || !nameMatches) return rp;
              return {
                ...rp,
                state: part.state,
                output: part.output,
                errorText: part.errorText,
                code: part.code || rp.code,
              };
            }
            return rp;
          }),
        };
        return copy;
      }
    }
  }

  return appendTracePart(messages, part, text);
}

function parseCitations(payload?: Record<string, unknown>): ChatInlineCitation[] {
  const raw = payload?.citations;
  if (!Array.isArray(raw) || raw.length === 0) return [];

  const rawAnchors = Array.isArray(payload?.citation_anchors)
    ? payload.citation_anchors
    : [];
  const anchorsById = new Map<
    string,
    {
      number?: string;
      startChar?: number;
      endChar?: number;
    }
  >();
  const anchorsBySourceAndNumber = new Map<
    string,
    {
      number?: string;
      startChar?: number;
      endChar?: number;
    }
  >();

  for (const anchorItem of rawAnchors) {
    const anchor = asRecord(anchorItem);
    if (!anchor) continue;
    const anchorId =
      asOptionalText(anchor.anchor_id) ?? asOptionalText(anchor.anchorId);
    const sourceId =
      asOptionalText(anchor.source_id) ?? asOptionalText(anchor.sourceId);
    const number =
      asOptionalText(anchor.number) ??
      (() => {
        const parsed = asOptionalNumber(anchor.number);
        return parsed != null ? String(parsed) : undefined;
      })();
    const startChar = asOptionalNumber(anchor.start_char ?? anchor.startChar);
    const endChar = asOptionalNumber(anchor.end_char ?? anchor.endChar);
    const value = { number, startChar, endChar };
    if (anchorId) anchorsById.set(anchorId, value);
    if (sourceId && number) anchorsBySourceAndNumber.set(`${sourceId}|${number}`, value);
  }

  const citations = raw
    .slice(0, MAX_CITATIONS)
    .map<ChatInlineCitation | null>((item, index) => {
      const rec = asRecord(item);
      if (!rec) return null;

      const url = normalizeUrl(rec.url ?? rec.source_url ?? rec.canonical_url);
      if (!url) return null;

      const title =
        asOptionalText(rec.title ?? rec.source_title) ??
        asOptionalText(rec.source) ??
        "Source";
      const numericNumber = asOptionalNumber(rec.number);
      const sourceId =
        asOptionalText(rec.source_id) ??
        asOptionalText(rec.sourceId) ??
        undefined;
      const anchorId =
        asOptionalText(rec.anchor_id) ??
        asOptionalText(rec.anchorId) ??
        undefined;
      const anchorFromId = anchorId ? anchorsById.get(anchorId) : undefined;
      const rawNumberText =
        asOptionalText(rec.number) ??
        (numericNumber != null ? String(numericNumber) : undefined);
      const anchorFromSource = sourceId
        ? anchorsBySourceAndNumber.get(
            `${sourceId}|${rawNumberText ?? String(index + 1)}`,
          )
        : undefined;
      const anchorData = anchorFromId ?? anchorFromSource;
      const finalNumber = anchorData?.number ?? rawNumberText ?? String(index + 1);
      const startChar =
        asOptionalNumber(rec.start_char ?? rec.startChar) ??
        anchorData?.startChar;
      const endChar =
        asOptionalNumber(rec.end_char ?? rec.endChar) ?? anchorData?.endChar;

      return {
        number: finalNumber,
        title,
        url,
        description: asOptionalText(rec.description),
        quote: asOptionalText(rec.quote ?? rec.evidence),
        sourceId,
        anchorId,
        startChar,
        endChar,
      };
    })
    .filter((value): value is ChatInlineCitation => value != null);

  const deduped = new Map<string, ChatInlineCitation>();
  for (const citation of citations) {
    const key = [
      citation.anchorId ?? "",
      citation.sourceId ?? "",
      citation.url,
      citation.quote ?? "",
    ].join("|");
    if (!deduped.has(key)) deduped.set(key, citation);
  }

  const sorted = [...deduped.values()].sort((a, b) => {
    const aNumber = Number(a.number);
    const bNumber = Number(b.number);
    const aHasNumber = Number.isFinite(aNumber);
    const bHasNumber = Number.isFinite(bNumber);
    if (aHasNumber && bHasNumber && aNumber !== bNumber) return aNumber - bNumber;
    if (aHasNumber !== bHasNumber) return aHasNumber ? -1 : 1;
    const aStart = a.startChar ?? Number.POSITIVE_INFINITY;
    const bStart = b.startChar ?? Number.POSITIVE_INFINITY;
    if (aStart !== bStart) return aStart - bStart;
    return a.url.localeCompare(b.url);
  });

  return sorted.slice(0, MAX_CITATIONS).map((citation, index) => ({
    ...citation,
    number: citation.number ?? String(index + 1),
  }));
}

function parseSources(
  payload: Record<string, unknown> | undefined,
  citations: ChatInlineCitation[],
): ChatSourceItem[] {
  const rawSources = payload?.sources;
  const sources = new Map<string, ChatSourceItem>();

  if (Array.isArray(rawSources)) {
    for (const item of rawSources.slice(0, MAX_SOURCES)) {
      const rec = asRecord(item);
      if (!rec) continue;

      const canonicalUrl = normalizeUrl(rec.canonical_url ?? rec.canonicalUrl);
      const displayUrl = normalizeUrl(rec.display_url ?? rec.displayUrl);
      const url = normalizeUrl(rec.url) ?? displayUrl ?? canonicalUrl;
      if (!url && !canonicalUrl) continue;

      const sourceId =
        asOptionalText(rec.source_id) ??
        asOptionalText(rec.sourceId) ??
        asOptionalText(rec.id) ??
        `source-${sources.size + 1}`;
      const kindRaw = asOptionalText(rec.kind)?.toLowerCase();
      const kind: ChatSourceItem["kind"] =
        kindRaw === "web" ||
        kindRaw === "file" ||
        kindRaw === "artifact" ||
        kindRaw === "tool_output"
          ? kindRaw
          : "other";
      const title = asOptionalText(rec.title) ?? "Source";

      const source: ChatSourceItem = {
        sourceId,
        kind,
        title,
        url: url ?? undefined,
        canonicalUrl: canonicalUrl ?? url ?? undefined,
        displayUrl: displayUrl ?? url ?? undefined,
        description: asOptionalText(rec.description),
        quote: asOptionalText(rec.quote),
      };

      const dedupeKey = source.canonicalUrl ?? source.url ?? source.sourceId;
      sources.set(dedupeKey, source);
    }
  }

  if (sources.size === 0) {
    for (const citation of citations) {
      const dedupeKey = citation.url;
      if (sources.has(dedupeKey)) continue;
      sources.set(dedupeKey, {
        sourceId: citation.sourceId ?? `source-${sources.size + 1}`,
        kind: "web",
        title: citation.title,
        url: citation.url,
        canonicalUrl: citation.url,
        displayUrl: citation.url,
        description: citation.description,
        quote: citation.quote,
      });
    }
  }

  return [...sources.values()].slice(0, MAX_SOURCES);
}

function parseAttachments(
  payload?: Record<string, unknown>,
): ChatAttachmentItem[] {
  const raw = payload?.attachments;
  if (!Array.isArray(raw) || raw.length === 0) return [];

  const attachments = raw
    .slice(0, MAX_ATTACHMENTS)
    .map<ChatAttachmentItem | null>((item) => {
      const rec = asRecord(item);
      if (!rec) return null;

      const attachmentId =
        asOptionalText(rec.attachment_id) ??
        asOptionalText(rec.attachmentId) ??
        asOptionalText(rec.id) ??
        nextId("attachment");
      const name =
        asOptionalText(rec.name) ??
        asOptionalText(rec.title) ??
        "Attachment";

      return {
        attachmentId,
        name,
        url: normalizeUrl(rec.url ?? rec.download_url ?? rec.downloadUrl),
        previewUrl: normalizeUrl(rec.preview_url ?? rec.previewUrl),
        mimeType: asOptionalText(rec.mime_type ?? rec.mimeType),
        mediaType: asOptionalText(rec.media_type ?? rec.mediaType),
        sizeBytes: asOptionalNumber(rec.size_bytes ?? rec.sizeBytes),
        kind: asOptionalText(rec.kind),
        description: asOptionalText(rec.description),
      };
    })
    .filter((value): value is ChatAttachmentItem => value != null);

  const deduped = new Map<string, ChatAttachmentItem>();
  for (const attachment of attachments) {
    const key = attachment.attachmentId;
    if (!deduped.has(key)) deduped.set(key, attachment);
  }
  return [...deduped.values()].slice(0, MAX_ATTACHMENTS);
}

function parseFinalReferences(
  payload?: Record<string, unknown>,
): FinalReferenceBundle {
  const citations = parseCitations(payload);
  return {
    citations,
    sources: parseSources(payload, citations),
    attachments: parseAttachments(payload),
  };
}

function upsertAssistantRenderPart(
  messages: ChatMessage[],
  part: ChatRenderPart,
): ChatMessage[] {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const msg = messages[i];
    if (!msg || msg.type !== "assistant") continue;

    const nextParts = [...(msg.renderParts ?? [])];
    const existingIndex = nextParts.findIndex((candidate) => {
      if (candidate.kind !== part.kind) return false;
      if (part.kind === "inline_citation_group") return true;
      if (part.kind === "sources") return true;
      if (part.kind === "attachments") return true;
      return false;
    });
    if (existingIndex >= 0) {
      nextParts[existingIndex] = part;
    } else {
      nextParts.push(part);
    }

    const copy = [...messages];
    copy[i] = { ...msg, renderParts: nextParts };
    return copy;
  }
  return messages;
}

function attachFinalReferences(
  messages: ChatMessage[],
  payload?: Record<string, unknown>,
): ChatMessage[] {
  const refs = parseFinalReferences(payload);
  let next = messages;

  if (refs.citations.length > 0) {
    next = upsertAssistantRenderPart(next, {
      kind: "inline_citation_group",
      citations: refs.citations,
    });
  }
  if (refs.sources.length > 0) {
    next = upsertAssistantRenderPart(next, {
      kind: "sources",
      title: "Sources",
      sources: refs.sources,
    });
  }
  if (refs.attachments.length > 0) {
    next = upsertAssistantRenderPart(next, {
      kind: "attachments",
      attachments: refs.attachments,
      variant: "grid",
    });
  }
  return next;
}

function resolveHitlByMessageId(
  messages: ChatMessage[],
  messageId: string,
  resolution: string,
): ChatMessage[] {
  let changed = false;
  const next = messages.map((msg) => {
    if (changed || msg.id !== messageId || msg.type !== "hitl" || !msg.hitlData) {
      return msg;
    }
    changed = true;
    return {
      ...msg,
      hitlData: {
        ...msg.hitlData,
        resolved: true,
        resolvedLabel: resolution,
      },
    };
  });
  return changed ? next : messages;
}

function rollbackHitlByMessageId(
  messages: ChatMessage[],
  messageId: string,
): ChatMessage[] {
  let changed = false;
  const next = messages.map((msg) => {
    if (changed || msg.id !== messageId || msg.type !== "hitl" || !msg.hitlData) {
      return msg;
    }
    changed = true;
    return {
      ...msg,
      hitlData: {
        ...msg.hitlData,
        resolved: false,
        resolvedLabel: undefined,
      },
    };
  });
  return changed ? next : messages;
}

function applyEvent(
  messages: ChatMessage[],
  frame: WsServerEvent,
  queryClient?: QueryClient,
): ApplyFrameResult {
  const { kind, text, payload } = frame.data;

  switch (kind) {
    case "assistant_token": {
      return {
        messages: appendAssistantToken(messages, text),
        terminal: false,
        errored: false,
      };
    }
    case "reasoning_step": {
      return {
        messages: appendReasoning(messages, text, "chunk"),
        terminal: false,
        errored: false,
      };
    }
    case "trajectory_step": {
      let next = messages;
      const thought = extractTrajectoryThought(text, payload);
      if (thought) {
        next = appendReasoning(next, thought, "line");
      }
      next = upsertChainOfThought(next, text, payload);
      return {
        messages: next,
        terminal: false,
        errored: false,
      };
    }
    case "status": {
      return {
        messages: appendReasoning(messages, `Status: ${text}`, "line"),
        terminal: false,
        errored: false,
      };
    }
    case "tool_call": {
      return {
        messages: upsertToolLikePart(messages, "tool_call", text, payload),
        terminal: false,
        errored: false,
      };
    }
    case "tool_result": {
      return {
        messages: upsertToolLikePart(messages, "tool_result", text, payload),
        terminal: false,
        errored: false,
      };
    }
    case "plan_update": {
      let next = finishReasoning(messages);
      next = upsertQueue(next, text || "Running plan...");
      return { messages: next, terminal: false, errored: false };
    }
    case "rlm_executing": {
      let next = finishReasoning(messages);
      const toolName =
        typeof payload?.tool_name === "string" && payload.tool_name
          ? payload.tool_name
          : "Sub-agent iteration";
      next = appendTracePart(
        next,
        {
          kind: "task",
          title: `Executing ${toolName}`,
          status: "in_progress",
          items: text ? [{ id: nextId("task-item"), text }] : undefined,
        },
        `Executing ${toolName}...`,
      );
      return { messages: next, terminal: false, errored: false };
    }
    case "memory_update": {
      let next = finishReasoning(messages);
      next = appendTracePart(
        next,
        {
          kind: "task",
          title: text || "Updating memory...",
          status: "completed",
          items: text ? [{ id: nextId("task-item"), text }] : undefined,
        },
        text || "Updating memory...",
      );

      if (queryClient) {
        queryClient.invalidateQueries({ queryKey: ["memory"] });
      }
      return { messages: next, terminal: false, errored: false };
    }
    case "hitl_request": {
      const hitlPayload = asRecord(payload?.hitl ?? payload);
      const question =
        asOptionalText(hitlPayload?.question) || text.trim() || "Approval needed";
      const messageId =
        asOptionalText(hitlPayload?.message_id ?? hitlPayload?.messageId) ??
        nextId("hitl");
      const rawActions = hitlPayload?.actions;
      const actions = Array.isArray(rawActions)
        ? rawActions
            .map((item) => {
              const rec = asRecord(item);
              if (!rec) return null;
              const label = asOptionalText(rec.label);
              if (!label) return null;
              const variant = asOptionalText(rec.variant);
              return {
                label,
                variant:
                  variant === "primary" || variant === "secondary"
                    ? variant
                    : "secondary",
              } as const;
            })
            .filter(
              (
                value,
              ): value is { label: string; variant: "primary" | "secondary" } =>
                value != null,
            )
        : [];

      return {
        messages: [
          ...messages,
          {
            id: messageId,
            type: "hitl",
            content: question,
            phase: DEFAULT_PHASE,
            hitlData: {
              question,
              actions:
                actions.length > 0
                  ? actions
                  : [
                      { label: "Approve", variant: "primary" },
                      { label: "Reject", variant: "secondary" },
                    ],
            },
          },
        ],
        terminal: false,
        errored: false,
      };
    }
    case "hitl_resolved": {
      const messageId = asOptionalText(payload?.message_id ?? payload?.messageId);
      const resolution =
        asOptionalText(payload?.resolution) ??
        asOptionalText(payload?.label) ??
        text.trim();
      if (!resolution) return { messages, terminal: false, errored: false };

      if (messageId) {
        return {
          messages: resolveHitlByMessageId(messages, messageId, resolution),
          terminal: false,
          errored: false,
        };
      }

      let updated = false;
      const next = messages.map((msg) => {
        if (updated || msg.type !== "hitl" || !msg.hitlData || msg.hitlData.resolved) {
          return msg;
        }
        updated = true;
        return {
          ...msg,
          hitlData: {
            ...msg.hitlData,
            resolved: true,
            resolvedLabel: resolution,
          },
        };
      });
      return { messages: next, terminal: false, errored: false };
    }
    case "command_ack": {
      const command = asOptionalText(payload?.command);
      const result = asRecord(payload?.result);
      const messageId = asOptionalText(result?.message_id ?? result?.messageId);
      const resolution =
        asOptionalText(result?.resolution) ?? asOptionalText(result?.action_label);
      let next = messages;
      if (command === "resolve_hitl" && messageId && resolution) {
        next = resolveHitlByMessageId(next, messageId, resolution);
      }
      return {
        messages: appendTracePart(
          next,
          {
            kind: "status_note",
            tone: "success",
            text: text || "Action acknowledged",
          },
          text || "Action acknowledged",
        ),
        terminal: false,
        errored: false,
      };
    }
    case "command_reject": {
      const command = asOptionalText(payload?.command);
      const result = asRecord(payload?.result);
      const messageId = asOptionalText(result?.message_id ?? result?.messageId);
      let next = messages;
      if (command === "resolve_hitl" && messageId) {
        next = rollbackHitlByMessageId(next, messageId);
      }
      return {
        messages: appendTracePart(
          next,
          {
            kind: "status_note",
            tone: "error",
            text: text || "Action rejected",
          },
          text || "Action rejected",
        ),
        terminal: false,
        errored: false,
      };
    }
    case "final": {
      let next = completeAssistant(messages, text);
      next = finishReasoning(next);
      next = finalizeTraceParts(next);

      const finalReasoning =
        typeof payload?.final_reasoning === "string"
          ? payload.final_reasoning.trim()
          : "";
      if (finalReasoning) {
        next = appendReasoning(next, `Final reasoning: ${finalReasoning}`);
        next = finishReasoning(next);
      }

      next = attachFinalReferences(next, payload);

      const warnings = readGuardrailWarnings(payload);
      if (warnings.length > 0) {
        next = appendSystem(
          next,
          `Guardrail warnings:\n- ${warnings.join("\n- ")}`,
        );
      }

      return { messages: next, terminal: true, errored: false };
    }
    case "cancelled": {
      let next = finishReasoning(messages);
      next = finalizeTraceParts(next);
      next = appendSystem(next, text || "Request cancelled.");
      return { messages: next, terminal: true, errored: false };
    }
    case "error": {
      let next = finishReasoning(messages);
      next = finalizeTraceParts(next);
      next = appendSystem(
        next,
        `Backend error: ${text || "Unknown server error."}`,
      );
      return { messages: next, terminal: true, errored: true };
    }
    default: {
      return { messages, terminal: false, errored: false };
    }
  }
}

export function applyWsFrameToMessages(
  messages: ChatMessage[],
  frame: WsServerMessage,
  queryClient?: QueryClient,
): ApplyFrameResult {
  if (frame.type === "error") {
    const next = finalizeTraceParts(
      appendSystem(messages, `Backend error: ${frame.message}`),
    );
    return { messages: finishReasoning(next), terminal: true, errored: true };
  }
  return applyEvent(messages, frame, queryClient);
}
