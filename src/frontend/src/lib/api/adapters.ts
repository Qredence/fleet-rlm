/**
 * Adapter layer: backend types → frontend types.
 *
 * This is the SINGLE file that needs updating when the fleet-rlm API
 * schema changes. All other code (hooks, components) only ever see
 * the stable frontend types from `components/data/types.ts`.
 *
 * Each adapter function:
 *   1. Accepts a backend response type (from `./types`)
 *   2. Returns the corresponding frontend type (from `components/data/types`)
 *   3. Handles field renaming, defaults, and any data massage
 *
 * ────────────────────────────────────────────────────────────────────
 * NOTE: The `client.ts` already converts snake_case → camelCase
 * at the JSON level. These adapters handle semantic transforms —
 * field renaming, enrichment, and structural reshaping.
 * ────────────────────────────────────────────────────────────────────
 */

import type {
  ApiTask,
  ApiTaxonomyNode,
  ApiAnalytics,
  ApiUserProfile,
  ApiChatMessage,
  ApiMemoryEntry,
  ApiFsNode,
} from "@/lib/api/types";

import type {
  Skill,
  TaxonomyNode,
  ChatMessage,
  MemoryEntry,
  FsNode,
} from "@/lib/data/types";

import type { UserProfile } from "@/hooks/useAuth";

// ── Task → Skill ────────────────────────────────────────────────────

/**
 * Converts a backend Task (already camelCased by api-client) into
 * the frontend Skill type.
 *
 * The backend calls them "tasks" while the UI calls them "skills".
 * Field mapping after camelCase conversion:
 *   - displayName     → displayName (1:1)
 *   - taxonomyPath    → taxonomyPath (1:1)
 *   - usageCount      → usageCount (1:1)
 *   - qualityScore    → qualityScore (1:1)
 *   - lastUsed        → lastUsed (1:1)
 *   - createdAt       → createdAt (1:1)
 *
 * TODO: Validate once OpenAPI spec is available. Some fields may need
 *       default values or computed properties.
 */
export function adaptTask(task: CamelCase<ApiTask>): Skill {
  return {
    id: task.id,
    name: task.name,
    displayName: task.displayName || task.name,
    version: task.version || "1.0.0",
    domain: task.domain || "general",
    category: task.category || "uncategorized",
    status: task.status || "draft",
    description: task.description || "",
    tags: task.tags || [],
    dependencies: task.dependencies || [],
    taxonomyPath:
      task.taxonomyPath || `/${task.domain}/${task.category}/${task.name}`,
    usageCount: task.usageCount ?? 0,
    lastUsed: task.lastUsed || task.createdAt || new Date().toISOString(),
    qualityScore: task.qualityScore ?? 0,
    author: task.author || "unknown",
    createdAt: task.createdAt || new Date().toISOString(),
  };
}

/** Batch convert tasks → skills. */
export function adaptTasks(tasks: CamelCase<ApiTask>[]): Skill[] {
  return tasks.map(adaptTask);
}

// ── TaxonomyNode → TaxonomyNode ─────────────────────────────────────

/**
 * Backend taxonomy nodes use `skill_count` (→ camelCase `skillCount`)
 * which maps to our `skillCount` directly. Recursively adapts children.
 */
export function adaptTaxonomyNode(
  node: CamelCase<ApiTaxonomyNode>,
): TaxonomyNode {
  return {
    id: node.id,
    name: node.name,
    path: node.path,
    children: (node.children || []).map(adaptTaxonomyNode),
    skillCount: node.skillCount ?? 0,
    skills: node.skills,
  };
}

export function adaptTaxonomy(
  nodes: CamelCase<ApiTaxonomyNode>[],
): TaxonomyNode[] {
  return nodes.map(adaptTaxonomyNode);
}

// ── Analytics ───────────────────────────────────────────────────────

/**
 * The frontend analytics shape is a flat object with pre-computed
 * chart data. The backend may return the same or similar structure.
 */
export interface AnalyticsData {
  totalSkills: number;
  activeSkills: number;
  totalExecutions: number;
  avgQualityScore: number;
  weeklyGrowth: number;
  executionsByDay: { date: string; count: number }[];
  topSkills: { name: string; uses: number }[];
  qualityDist: { range: string; count: number }[];
}

export function adaptAnalytics(raw: CamelCase<ApiAnalytics>): AnalyticsData {
  return {
    totalSkills: raw.totalSkills ?? 0,
    activeSkills: raw.activeSkills ?? 0,
    totalExecutions: raw.totalExecutions ?? 0,
    avgQualityScore: raw.avgQualityScore ?? 0,
    weeklyGrowth: raw.weeklyGrowth ?? 0,
    executionsByDay: raw.executionsByDay || [],
    topSkills: raw.topSkills || [],
    qualityDist: raw.qualityDist || [],
  };
}

// ── User Profile ────────────────────────────────────────────────────

export function adaptUserProfile(raw: CamelCase<ApiUserProfile>): UserProfile {
  return {
    id: raw.id,
    name: raw.name,
    email: raw.email,
    initials:
      raw.initials ||
      raw.name
        .split(" ")
        .map((n: string) => n[0])
        .join("")
        .toUpperCase()
        .slice(0, 2),
    avatarUrl: raw.avatarUrl,
    role: raw.role || "Member",
    plan: raw.plan || "free",
    org: raw.org || "",
  };
}

// ── Chat Message ────────────────────────────────────────────────────

/**
 * Converts backend chat messages into the frontend ChatMessage type.
 *
 * The backend uses `role` (user/assistant/system) + `message_type` for
 * HITL/clarification/reasoning, while the frontend merges these into
 * a single `type` field.
 */
export function adaptChatMessage(msg: CamelCase<ApiChatMessage>): ChatMessage {
  // Map backend role + messageType to frontend type
  let type: ChatMessage["type"];
  const messageType = (msg as Record<string, unknown>).messageType as
    | string
    | undefined;

  if (messageType === "hitl") {
    type = "hitl";
  } else if (messageType === "clarification") {
    type = "clarification";
  } else if (messageType === "reasoning") {
    type = "reasoning";
  } else {
    type = msg.role as ChatMessage["type"];
  }

  return {
    id: msg.id,
    type,
    content: msg.content || "",
    phase: msg.phase as 1 | 2 | 3 | undefined,
    // HITL, clarification, and reasoning data would be parsed from
    // the message metadata. This is a TODO pending actual schema review.
    hitlData: undefined,
    clarificationData: undefined,
    reasoningData: undefined,
  };
}

// ── Memory Entry ────────────────────────────────────────────────────

export function adaptMemoryEntry(
  entry: CamelCase<ApiMemoryEntry>,
): MemoryEntry {
  return {
    id: entry.id,
    type: entry.type,
    content: entry.content,
    source: entry.source || "Unknown",
    createdAt: entry.createdAt || new Date().toISOString(),
    updatedAt: entry.updatedAt || entry.createdAt || new Date().toISOString(),
    relevance: entry.relevance ?? 50,
    tags: entry.tags || [],
    sessionId: entry.sessionId,
    pinned: entry.pinned ?? false,
  };
}

export function adaptMemoryEntries(
  entries: CamelCase<ApiMemoryEntry>[],
): MemoryEntry[] {
  return entries.map(adaptMemoryEntry);
}

// ── File System Node ────────────────────────────────────────────────

export function adaptFsNode(node: CamelCase<ApiFsNode>): FsNode {
  return {
    id: node.id,
    name: node.name,
    path: node.path,
    type: node.type,
    children: node.children ? node.children.map(adaptFsNode) : undefined,
    size: node.size,
    mime: node.mime,
    modifiedAt: node.modifiedAt,
    skillId: node.skillId,
  };
}

export function adaptFsTree(nodes: CamelCase<ApiFsNode>[]): FsNode[] {
  return nodes.map(adaptFsNode);
}

// ── CamelCase utility type ──────────────────────────────────────────
// After api-client does keysToCamel, all fields are camelCase.
// This type helper reflects that transform at the type level.

type CamelCase<T> = {
  [K in keyof T as K extends string
    ? CamelCaseString<K>
    : K]: T[K] extends Array<infer U>
    ? Array<CamelCase<U>>
    : T[K] extends Record<string, unknown>
      ? CamelCase<T[K]>
      : T[K];
};

type CamelCaseString<S extends string> = S extends `${infer P}_${infer Q}`
  ? `${P}${Capitalize<CamelCaseString<Q>>}`
  : S;
