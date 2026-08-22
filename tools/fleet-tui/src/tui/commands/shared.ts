/** Shared helpers for slash command handlers. */

import { FleetApiError } from "../../fleet-api-client.js";
import type { ConversationStore, Message } from "../store.js";
import { newMessageId } from "../store.js";

import type { CommandContext } from "./registry.js";

/**
 * Adds a message to the conversation store.
 *
 * @param message - The message to add
 */
function appendMessage(store: ConversationStore, message: Message): void {
  store.dispatch({ type: "message/upsert", message });
}

/**
 * Appends a system text message to the conversation store.
 *
 * @param text - The message content to append
 */
export function appendSystem(store: ConversationStore, text: string): void {
  appendMessage(store, {
    id: newMessageId("system"),
    kind: "text",
    role: "system",
    text,
    ts: Date.now(),
    streaming: false,
  });
}

/**
 * Reports a one-shot success: a transient flash when interactive, otherwise
 * the existing system transcript message (tests/non-interactive contexts).
 */
export function notifySuccess(ctx: CommandContext, message: string): void {
  if (ctx.notify) {
    ctx.notify(message);
    return;
  }
  appendSystem(ctx.store, message);
}

/**
 * Formats an error for display in the transcript.
 *
 * @param error - The error value to format
 * @returns The error message, including the request ID and log-file location for Fleet API errors with a correlation ID
 */
export function errorMessage(error: unknown): string {
  if (error instanceof FleetApiError && error.correlationId) {
    return `${error.message} (request ${error.correlationId}; see .fleet_rlm/logs/latest.log)`;
  }
  if (error instanceof Error) return error.message;
  if (typeof error === "string") return error;
  return "Fleet terminal request failed";
}
