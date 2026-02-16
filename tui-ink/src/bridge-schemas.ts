import { z } from "zod";

import type { BridgeSessionInit, MentionItem, SettingsSnapshot } from "./types.js";

export const BridgeUnknownRecordSchema = z.record(z.unknown());

export const BridgeRpcErrorSchema = z
  .object({
    code: z.union([z.string(), z.number()]).optional(),
    message: z.string().optional(),
  })
  .passthrough();

export const BridgeResponseEnvelopeSchema = z
  .object({
    id: z.string(),
    result: z.unknown().optional(),
    error: BridgeRpcErrorSchema.optional(),
  })
  .passthrough();

export const BridgeEventEnvelopeSchema = z
  .object({
    event: z.string(),
    params: BridgeUnknownRecordSchema.optional().default({}),
  })
  .passthrough();

export const BridgeLegacyEventEnvelopeSchema = z
  .object({
    type: z.literal("event"),
    data: BridgeUnknownRecordSchema,
  })
  .passthrough();

export const BridgeSessionInitSchema = z
  .object({
    session_id: z.string().optional(),
    commands: z
      .object({
        tool_commands: z.array(z.string()).optional(),
        wrapper_commands: z.array(z.string()).optional(),
      })
      .optional(),
  })
  .passthrough();

export const BridgeSettingsSnapshotSchema = z
  .object({
    values: z.record(z.string()).optional().default({}),
    masked_values: z.record(z.string()).optional().default({}),
  })
  .passthrough();

export const BridgeStatusPayloadSchema = BridgeUnknownRecordSchema;

export const BridgeChatSubmitSchema = z
  .object({
    assistant_response: z.string().optional(),
    payload: z.unknown().optional(),
  })
  .passthrough();

export const BridgeMentionItemSchema = z.object({
  path: z.string(),
  kind: z.string(),
  score: z.number(),
});

export const BridgeMentionSearchSchema = z
  .object({
    items: z.array(BridgeMentionItemSchema).optional().default([]),
  })
  .passthrough();

export function parseBridgeSessionInit(value: unknown): BridgeSessionInit {
  const parsed = BridgeSessionInitSchema.safeParse(value);
  if (!parsed.success) {
    throw new Error("Invalid session.init response payload");
  }
  return parsed.data as BridgeSessionInit;
}

export function parseBridgeSettingsSnapshot(value: unknown): SettingsSnapshot {
  const parsed = BridgeSettingsSnapshotSchema.safeParse(value);
  if (!parsed.success) {
    throw new Error("Invalid settings.get response payload");
  }
  return parsed.data as SettingsSnapshot;
}

export function parseBridgeStatusPayload(value: unknown): Record<string, unknown> {
  const parsed = BridgeStatusPayloadSchema.safeParse(value);
  if (!parsed.success) {
    throw new Error("Invalid status.get response payload");
  }
  return parsed.data;
}

export function parseBridgeChatSubmit(value: unknown): {
  assistant_response?: string;
  payload?: unknown;
} {
  const parsed = BridgeChatSubmitSchema.safeParse(value);
  if (!parsed.success) {
    throw new Error("Invalid chat.submit response payload");
  }
  return parsed.data;
}

export function parseBridgeMentionSearch(value: unknown): { items: MentionItem[] } {
  const parsed = BridgeMentionSearchSchema.safeParse(value);
  if (!parsed.success) {
    throw new Error("Invalid mention.search response payload");
  }
  return parsed.data as { items: MentionItem[] };
}
