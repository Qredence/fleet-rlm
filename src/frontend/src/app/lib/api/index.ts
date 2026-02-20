/**
 * Public barrel for the fleet-rlm API layer.
 *
 * Re-exports everything consumers need from a single entry point:
 *
 * ```ts
 * import { isMockMode, apiClient, taskEndpoints, adaptTask } from '../../lib/api';
 * ```
 */

// ── Config ──────────────────────────────────────────────────────────
export { apiConfig, isMockMode, isWsAvailable } from "./config";

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
} from "./client";

// ── Endpoints ───────────────────────────────────────────────────────
export {
  taskEndpoints,
  taxonomyEndpoints,
  sessionEndpoints,
  chatEndpoints,
  analyticsEndpoints,
  authEndpoints,
  searchEndpoints,
  memoryEndpoints,
  filesystemEndpoints,
} from "./endpoints";
export type { TaskListParams, MemoryListParams } from "./endpoints";

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
} from "./adapters";
export type { AnalyticsData } from "./adapters";

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
} from "./types";
