import type {
  ApiAnalytics,
  ApiChatMessage,
  ApiFsNode,
  ApiMemoryEntry,
  ApiTask,
  ApiTaxonomyNode,
  ApiUserProfile,
} from "@/lib/rlm-api/types";
import type { UserProfile } from "@/lib/auth/types";
import type {
  ChatMessage,
  FsNode,
  MemoryEntry,
  Skill,
  TaxonomyNode,
} from "@/lib/data/types";

export function keysToCamel<T>(obj: unknown): T {
  if (Array.isArray(obj)) {
    return obj.map((v) => keysToCamel(v)) as unknown as T;
  }
  if (obj !== null && typeof obj === "object" && !(obj instanceof Date)) {
    const mapped = Object.entries(obj as Record<string, unknown>).map(
      ([key, value]) => [snakeToCamel(key), keysToCamel(value)],
    );
    return Object.fromEntries(mapped) as T;
  }
  return obj as T;
}

export function keysToSnake<T>(obj: unknown): T {
  if (Array.isArray(obj)) {
    return obj.map((v) => keysToSnake(v)) as unknown as T;
  }
  if (obj !== null && typeof obj === "object" && !(obj instanceof Date)) {
    const mapped = Object.entries(obj as Record<string, unknown>).map(
      ([key, value]) => [camelToSnake(key), keysToSnake(value)],
    );
    return Object.fromEntries(mapped) as T;
  }
  return obj as T;
}

export function adaptTask(task: ApiTask | CamelCase<ApiTask>): Skill {
  const input = keysToCamel<CamelCase<ApiTask>>(task);
  return {
    id: input.id,
    name: input.name,
    displayName: input.displayName || input.name,
    version: input.version || "1.0.0",
    domain: input.domain || "general",
    category: input.category || "uncategorized",
    status: input.status || "draft",
    description: input.description || "",
    tags: input.tags || [],
    dependencies: input.dependencies || [],
    taxonomyPath:
      input.taxonomyPath || `/${input.domain}/${input.category}/${input.name}`,
    usageCount: input.usageCount ?? 0,
    lastUsed: input.lastUsed || input.createdAt || new Date().toISOString(),
    qualityScore: input.qualityScore ?? 0,
    author: input.author || "unknown",
    createdAt: input.createdAt || new Date().toISOString(),
  };
}

export function adaptTasks(
  tasks: Array<ApiTask | CamelCase<ApiTask>>,
): Skill[] {
  return tasks.map(adaptTask);
}

export function adaptTaxonomyNode(
  node: ApiTaxonomyNode | CamelCase<ApiTaxonomyNode>,
): TaxonomyNode {
  const input = keysToCamel<CamelCase<ApiTaxonomyNode>>(node);
  return {
    id: input.id,
    name: input.name,
    path: input.path,
    children: (input.children || []).map(adaptTaxonomyNode),
    skillCount: input.skillCount ?? 0,
    skills: input.skills,
  };
}

export function adaptTaxonomy(
  nodes: Array<ApiTaxonomyNode | CamelCase<ApiTaxonomyNode>>,
): TaxonomyNode[] {
  return nodes.map(adaptTaxonomyNode);
}

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

export function adaptUserProfile(raw: CamelCase<ApiUserProfile>): UserProfile {
  return {
    id: raw.id,
    name: raw.name,
    email: raw.email,
    initials:
      raw.initials ||
      raw.name
        .split(" ")
        .map((segment) => segment[0])
        .join("")
        .toUpperCase()
        .slice(0, 2),
    avatarUrl: raw.avatarUrl,
    role: raw.role || "Member",
    plan: raw.plan || "free",
    org: raw.org || "",
  };
}

export function adaptChatMessage(msg: CamelCase<ApiChatMessage>): ChatMessage {
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
    hitlData: undefined,
    clarificationData: undefined,
    reasoningData: undefined,
  };
}

export function adaptMemoryEntry(
  entry: ApiMemoryEntry | CamelCase<ApiMemoryEntry>,
): MemoryEntry {
  const input = keysToCamel<CamelCase<ApiMemoryEntry>>(entry);
  return {
    id: input.id,
    type: input.type,
    content: input.content,
    source: input.source || "Unknown",
    createdAt: input.createdAt || new Date().toISOString(),
    updatedAt: input.updatedAt || input.createdAt || new Date().toISOString(),
    relevance: input.relevance ?? 50,
    tags: input.tags || [],
    sessionId: input.sessionId,
    pinned: input.pinned ?? false,
  };
}

export function adaptMemoryEntries(
  entries: Array<ApiMemoryEntry | CamelCase<ApiMemoryEntry>>,
): MemoryEntry[] {
  return entries.map(adaptMemoryEntry);
}

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

function snakeToCamel(str: string): string {
  return str.replace(/_([a-z0-9])/g, (_, char) => char.toUpperCase());
}

function camelToSnake(str: string): string {
  return str.replace(/[A-Z]/g, (char) => `_${char.toLowerCase()}`);
}

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
