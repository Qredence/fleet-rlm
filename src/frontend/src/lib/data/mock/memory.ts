import type { MemoryEntry } from "@/lib/data/types";

export const mockMemoryEntries: MemoryEntry[] = [
  {
    id: "mem-001",
    type: "fact",
    content: "User prefers pytest over unittest for Python test generation.",
    source: "Session: Test Generation Skill",
    createdAt: "2026-02-15T10:12:00Z",
    updatedAt: "2026-02-15T10:12:00Z",
    relevance: 95,
    tags: ["testing", "python", "preference"],
    sessionId: "session-default",
    pinned: true,
  },
  {
    id: "mem-002",
    type: "preference",
    content: "Coverage target should default to 90% for all new skills.",
    source: "Session: Test Generation Skill",
    createdAt: "2026-02-15T10:15:00Z",
    updatedAt: "2026-02-15T10:15:00Z",
    relevance: 92,
    tags: ["coverage", "defaults"],
    sessionId: "session-default",
    pinned: true,
  },
  {
    id: "mem-003",
    type: "knowledge",
    content:
      "The fleet-rlm backend uses FastAPI with Pydantic v2 schemas. Task endpoints follow REST conventions at /api/v1/tasks.",
    source: "System: API Discovery",
    createdAt: "2026-02-14T08:30:00Z",
    updatedAt: "2026-02-14T08:30:00Z",
    relevance: 88,
    tags: ["backend", "api", "architecture"],
  },
  {
    id: "mem-004",
    type: "session",
    content:
      'Created "test-generation" skill (v1.4.2) with dependencies on code-analysis and assertion-patterns. Placed at /development/testing/test-generation.',
    source: "Session: Test Generation Skill",
    createdAt: "2026-02-15T10:30:00Z",
    updatedAt: "2026-02-15T10:30:00Z",
    relevance: 85,
    tags: ["skill-creation", "test-generation"],
    sessionId: "session-default",
  },
  {
    id: "mem-005",
    type: "directive",
    content:
      "Always include edge case tests when coverage_target > 85%. This is a team-wide policy.",
    source: "User: Manual Entry",
    createdAt: "2026-02-13T14:00:00Z",
    updatedAt: "2026-02-13T14:00:00Z",
    relevance: 90,
    tags: ["policy", "testing", "edge-cases"],
    pinned: true,
  },
  {
    id: "mem-006",
    type: "fact",
    content:
      "Organization uses monorepo structure with pnpm workspaces. Skill packages are in packages/skills/*.",
    source: "System: Repository Analysis",
    createdAt: "2026-02-12T09:00:00Z",
    updatedAt: "2026-02-12T09:00:00Z",
    relevance: 82,
    tags: ["infrastructure", "monorepo"],
  },
  {
    id: "mem-007",
    type: "knowledge",
    content:
      "Taxonomy paths follow the pattern /{domain}/{category}/{skill-name}. Maximum depth is 4 levels.",
    source: "System: Schema Analysis",
    createdAt: "2026-02-11T16:45:00Z",
    updatedAt: "2026-02-11T16:45:00Z",
    relevance: 78,
    tags: ["taxonomy", "structure"],
  },
  {
    id: "mem-008",
    type: "session",
    content:
      "Explored NLP taxonomy branch. Identified gap in translation skills — no skill covers multi-language translation yet.",
    source: "Session: Taxonomy Exploration",
    createdAt: "2026-02-10T11:20:00Z",
    updatedAt: "2026-02-10T11:20:00Z",
    relevance: 72,
    tags: ["nlp", "taxonomy", "gap-analysis"],
    sessionId: "session-2",
  },
  {
    id: "mem-009",
    type: "preference",
    content:
      "User prefers YAML frontmatter over TOML for skill manifest files.",
    source: "Session: Document Summarization",
    createdAt: "2026-02-09T13:10:00Z",
    updatedAt: "2026-02-09T13:10:00Z",
    relevance: 68,
    tags: ["format", "preference"],
  },
  {
    id: "mem-010",
    type: "fact",
    content:
      "The deployment-automation skill requires write access to the CI/CD pipeline configuration. Approved for production use on 2026-02-06.",
    source: "System: Audit Log",
    createdAt: "2026-02-06T17:00:00Z",
    updatedAt: "2026-02-06T17:00:00Z",
    relevance: 65,
    tags: ["deployment", "permissions", "production"],
  },
  {
    id: "mem-011",
    type: "directive",
    content:
      'All new skills must include a "Troubleshooting" section in SKILL.md before publishing.',
    source: "User: Manual Entry",
    createdAt: "2026-02-05T10:00:00Z",
    updatedAt: "2026-02-05T10:00:00Z",
    relevance: 88,
    tags: ["policy", "documentation"],
    pinned: true,
  },
  {
    id: "mem-012",
    type: "knowledge",
    content:
      "Code review skill uses AST-based analysis for JavaScript/TypeScript and tree-sitter for Python. Supports custom rulesets via .reviewrc.yaml.",
    source: "System: Skill Introspection",
    createdAt: "2026-02-04T08:00:00Z",
    updatedAt: "2026-02-04T08:00:00Z",
    relevance: 75,
    tags: ["code-review", "ast", "configuration"],
  },
];

// ── Mock Filesystem / Sandbox Volumes ───────────────────────────────
