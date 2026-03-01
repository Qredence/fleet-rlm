/**
 * Public barrel for the fleet-rlm API layer.
 *
 * Re-exports everything consumers need from a single entry point:
 *
 * ```ts
 * import { isMockMode, apiClient } from '@/lib/api';
 * ```
 */

// ── Config ──────────────────────────────────────────────────────────
export { apiConfig, isMockMode, isWsAvailable } from "@/lib/api/config";

// ── Client ──────────────────────────────────────────────────────────
export {
  apiClient,
  streamSSE,
  setAccessToken,
  getAccessToken,
  clearTokens,
  keysToCamel,
  keysToSnake,
  ApiClientError,
} from "@/lib/api/client";

// ── Endpoints ───────────────────────────────────────────────────────
export {
  sessionStateEndpoints,
} from "@/lib/api/endpoints";
export type {
  TaskListParams,
  MemoryListParams,
  SessionStateResponse,
  SessionStateSummary,
} from "@/lib/api/endpoints";

// ── Adapters ────────────────────────────────────────────────────────
export {
  adaptTask,
  adaptTasks,
  adaptTaxonomyNode,
  adaptTaxonomy,
  adaptAnalytics,
  adaptUserProfile,
  adaptChatMessage,
  adaptMemoryEntry,
  adaptMemoryEntries,
  adaptFsNode,
  adaptFsTree,
} from "@/lib/api/adapters";
export type { AnalyticsData } from "@/lib/api/adapters";

// ── Backend Types ───────────────────────────────────────────────────
export type {
  ApiTask,
  ApiTaskCreate,
  ApiTaskUpdate,
  ApiTaskListResponse,
  ApiTaxonomyNode,
  ApiSession,
  ApiSessionCreate,
  ApiChatMessage,
  ApiChatRequest,
  ApiChatResponse,
  ApiStreamEventType,
  ApiStreamEvent,
  ApiAnalytics,
  ApiLoginRequest,
  ApiLoginResponse,
  ApiUserProfile,
  ApiError,
  ApiMemoryEntry,
  ApiMemoryCreate,
  ApiMemoryUpdate,
  ApiMemoryListResponse,
  ApiFsNode,
  ApiFsFileContent,
} from "@/lib/api/types";
