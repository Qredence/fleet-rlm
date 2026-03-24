import type {
  ChatAttachmentItem,
  ChatInlineCitation,
  ChatMessage,
  ChatRenderPart,
  ChatSourceItem,
} from "@/lib/workspace/workspace-types";
import { createLocalId } from "@/lib/id";

const MAX_CITATIONS = 16;
const MAX_SOURCES = 16;
const MAX_ATTACHMENTS = 16;

interface FinalReferenceBundle {
  citations: ChatInlineCitation[];
  sources: ChatSourceItem[];
  attachments: ChatAttachmentItem[];
}

function nextId(prefix: string): string {
  return createLocalId(prefix);
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return undefined;
  }
  return value as Record<string, unknown>;
}

function normalizeUrl(raw: unknown): string | undefined {
  if (typeof raw !== "string") return undefined;
  const value = raw.trim();
  if (!value) return undefined;

  try {
    const parsed = new URL(value);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") {
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

function parseCitations(
  payload?: Record<string, unknown>,
): ChatInlineCitation[] {
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
    if (sourceId && number) {
      anchorsBySourceAndNumber.set(`${sourceId}|${number}`, value);
    }
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
      const finalNumber =
        anchorData?.number ?? rawNumberText ?? String(index + 1);
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
    if (aHasNumber && bHasNumber && aNumber !== bNumber) {
      return aNumber - bNumber;
    }
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
      const fileDisplayUrl =
        asOptionalText(rec.display_url ?? rec.displayUrl) ??
        asOptionalText(rec.host_path ?? rec.hostPath) ??
        asOptionalText(rec.path);
      const url = normalizeUrl(rec.url) ?? displayUrl ?? canonicalUrl;

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
      if (!url && !canonicalUrl && !fileDisplayUrl && !title) continue;

      const source: ChatSourceItem = {
        sourceId,
        kind,
        title,
        url: url ?? undefined,
        canonicalUrl: canonicalUrl ?? url ?? undefined,
        displayUrl: displayUrl ?? fileDisplayUrl ?? url ?? undefined,
        description: asOptionalText(rec.description),
        quote: asOptionalText(rec.quote),
      };

      const dedupeKey =
        source.canonicalUrl ??
        source.url ??
        source.displayUrl ??
        source.sourceId;
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
        asOptionalText(rec.name) ?? asOptionalText(rec.title) ?? "Attachment";

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

export function attachFinalReferences(
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
