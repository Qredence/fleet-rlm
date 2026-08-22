/** Shared helpers for slash command handlers. */

import { FleetApiError } from "../../fleet-api-client.js";
import type { ConversationStore, Message } from "../store.js";
import { newMessageId } from "../store.js";

import type { CommandContext } from "./registry.js";

function appendMessage(store: ConversationStore, message: Message): void {
  store.dispatch({ type: "message/upsert", message });
}

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
 * Renders an error for the transcript. `FleetApiError`s with a correlation ID
 * include the request ID so the failure can be traced in the host logs
 * (`.fleet_rlm/logs/latest.log`). Shared by the command handlers and the run
 * controller so the operator sees the same wording everywhere.
 */
export function errorMessage(error: unknown): string {
  if (error instanceof FleetApiError && error.correlationId) {
    return `${error.message} (request ${error.correlationId}; see .fleet_rlm/logs/latest.log)`;
  }
  if (error instanceof Error) return error.message;
  if (typeof error === "string") return error;
  return "Fleet terminal request failed";
}
