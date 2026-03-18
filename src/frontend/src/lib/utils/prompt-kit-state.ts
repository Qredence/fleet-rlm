/**
 * State mapping utilities for prompt-kit components.
 *
 * Maps workspace state into the AI SDK-facing state values used by the local
 * prompt-kit wrappers.
 */

import type { ToolUIPart } from "ai";
import type { ChatRenderToolState } from "@/lib/data/types";

/**
 * Maps our custom ChatRenderToolState to AI SDK ToolUIPart["state"].
 *
 * Our states:
 * - "input-streaming" → tool is receiving input
 * - "running" → tool is executing
 * - "output-available" → tool completed successfully
 * - "output-error" → tool failed
 *
 * AI SDK states:
 * - "input-streaming" → input is being streamed
 * - "input-available" → input is ready
 * - "output-available" → output is available
 * - "output-error" → error occurred
 * - "approval-requested" → awaiting approval
 * - "approval-responded" → approval responded
 * - "output-denied" → output denied
 */
export function mapToolState(state: ChatRenderToolState): ToolUIPart["state"] {
  switch (state) {
    case "input-streaming":
      return "input-streaming";
    case "running":
      return "input-available";
    case "output-available":
      return "output-available";
    case "output-error":
      return "output-error";
    default:
      return "input-available";
  }
}

/**
 * Maps our confirmation state to AI SDK ToolUIPart["state"].
 *
 * Our states:
 * - "approval-requested" → awaiting approval
 * - "approved" → was approved
 * - "rejected" → was rejected
 *
 * Note: "approved" and "rejected" are terminal states that don't have
 * direct equivalents in AI SDK. We map them to the closest compatible states.
 */
export function mapConfirmationState(
  state: "approval-requested" | "approved" | "rejected",
): ToolUIPart["state"] {
  switch (state) {
    case "approval-requested":
      return "approval-requested";
    case "approved":
      return "approval-responded";
    case "rejected":
      return "output-denied";
    default:
      return "approval-requested";
  }
}

/**
 * Maps our task status to the Task component's expected status.
 *
 * Our statuses: "pending" | "in_progress" | "completed" | "error"
 * Task statuses: "pending" | "active" | "complete"
 */
export function mapTaskStatus(
  status: "pending" | "in_progress" | "completed" | "error",
): "pending" | "active" | "complete" {
  switch (status) {
    case "pending":
      return "pending";
    case "in_progress":
      return "active";
    case "completed":
      return "complete";
    case "error":
      return "complete"; // Error is shown differently
    default:
      return "pending";
  }
}
