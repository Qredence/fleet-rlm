// ── Navigation & Phases ─────────────────────────────────────────────
export type NavItem =
  | "new"
  | "skills"
  | "taxonomy"
  | "memory"
  | "analytics"
  | "settings";

export type CreationPhase =
  | "idle"
  | "understanding"
  | "generating"
  | "validating"
  | "complete";

// ── Prompt Feature & Mode ───────────────────────────────────────────
/** Toggleable features in the prompt "+" menu */
export type PromptFeature =
  | "library"
  | "contextMemory"
  | "skills"
  | "webSearch";

/** Prompt execution mode selected via toolbar chip */
export type PromptMode = "auto" | "skillCreation" | "webSearch" | "cowork";

// ── Domain Types ────────────────────────────────────────────────────
export interface Skill {
  id: string;
  name: string;
  displayName: string;
  version: string;
  domain: string;
  category: string;
  status: "draft" | "validating" | "validated" | "published" | "deprecated";
  description: string;
  tags: string[];
  dependencies: string[];
  taxonomyPath: string;
  usageCount: number;
  lastUsed: string;
  qualityScore: number;
  author: string;
  createdAt: string;
}

export interface TaxonomyNode {
  id: string;
  name: string;
  path: string;
  children: TaxonomyNode[];
  skillCount: number;
  skills?: string[]; // skill ids
}

export interface ChatMessage {
  id: string;
  type:
    | "user"
    | "assistant"
    | "system"
    | "hitl"
    | "clarification"
    | "reasoning"
    | "plan_update"
    | "rlm_executing"
    | "memory_update";
  content: string;
  phase?: 1 | 2 | 3;
  /** When true, the Streamdown component will animate text streaming in */
  streaming?: boolean;
  hitlData?: {
    question: string;
    actions: { label: string; variant: "primary" | "secondary" }[];
    resolved?: boolean;
    resolvedLabel?: string;
  };
  clarificationData?: {
    question: string;
    stepLabel: string; // e.g. "Question 1 of 3"
    options: { id: string; label: string; description?: string }[];
    customOptionId: string; // the id of the "Write your own" option
    resolved?: boolean;
    resolvedAnswer?: string;
  };
  reasoningData?: {
    parts: { type: "text"; text: string }[];
    isThinking: boolean;
    duration?: number;
  };
}

// ── Plan Steps (Queue component in Plan tab) ────────────────────────
export interface PlanStep {
  id: string;
  label: string;
  description?: string;
  completed: boolean;
}

// ── Skill Metadata (resolved key-value pairs shown after plan completes) ──
export interface SkillMetadataItem {
  id: string;
  label: string;
  value: string;
}

// ── Memory ──────────────────────────────────────────────────────────

export type MemoryType =
  | "fact"
  | "preference"
  | "session"
  | "knowledge"
  | "directive";

export interface MemoryEntry {
  id: string;
  type: MemoryType;
  content: string;
  source: string;
  createdAt: string;
  updatedAt: string;
  relevance: number; // 0-100
  tags: string[];
  sessionId?: string;
  /** Whether this entry is pinned by the user */
  pinned?: boolean;
}

// ── Filesystem / Sandbox Volumes ────────────────────────────────────

export type FsNodeType = "volume" | "directory" | "file";

export interface FsNode {
  id: string;
  name: string;
  path: string;
  type: FsNodeType;
  children?: FsNode[];
  /** File size in bytes (files only) */
  size?: number;
  /** MIME type hint (files only) */
  mime?: string;
  /** Last modified ISO timestamp */
  modifiedAt?: string;
  /** Associated skill id, if any */
  skillId?: string;
}
