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

export type MemoryType = "fact" | "preference" | "session" | "knowledge" | "directive";

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
