import type {
  Skill,
  TaxonomyNode,
  PlanStep,
  SkillMetadataItem,
  MemoryEntry,
  FsNode,
} from "./types";

// ── Mock Plan Steps (for Queue in Plan tab) ─────────────────────────
export const mockPlanSteps: PlanStep[] = [
  {
    id: "step-1",
    label: "Analyze intent & extract requirements",
    description:
      "Parse user prompt to identify skill domain, scope, and constraints",
    completed: false,
  },
  {
    id: "step-2",
    label: "Search taxonomy for related skills",
    description:
      "Check /development/testing/* for naming conflicts and dependencies",
    completed: false,
  },
  {
    id: "step-3",
    label: "Identify dependencies",
    description:
      "code-analysis (AST parsing), assertion-patterns (test assertions)",
    completed: false,
  },
  {
    id: "step-4",
    label: "Determine taxonomy placement",
    description: "/development/testing/test-generation \u2014 no conflicts",
    completed: false,
  },
  {
    id: "step-5",
    label: "Draft skill parameters",
    description: "language, framework, coverage_target, include_edge_cases",
    completed: false,
  },
  {
    id: "step-6",
    label: "Prepare plan for user approval",
    description: "Compile summary with dependency graph",
    completed: false,
  },
];

// ── Mock Skill Metadata (resolved after plan completes) ─────────────
export const mockSkillMetadata: SkillMetadataItem[] = [
  { id: "meta-domain", label: "Domain", value: "Development" },
  {
    id: "meta-category",
    label: "Category",
    value: "Testing / Quality Assurance",
  },
  {
    id: "meta-taxonomy",
    label: "Taxonomy",
    value: "/development/testing/test-generation",
  },
  {
    id: "meta-deps",
    label: "Dependencies",
    value: "code-analysis, assertion-patterns",
  },
];

// ── Clarification question sets ─────────────────────────────────────
export const phase1ClarificationQuestions = [
  {
    question: "What's the primary scope of this skill?",
    options: [
      {
        id: "p1q1-a",
        label: "Unit testing only",
        description: "Focus on isolated function and method testing",
      },
      {
        id: "p1q1-b",
        label: "Integration testing only",
        description: "Test interactions between modules and services",
      },
      {
        id: "p1q1-c",
        label: "End-to-end testing",
        description: "Full workflow and user journey validation",
      },
      {
        id: "p1q1-d",
        label: "All testing types",
        description: "Comprehensive coverage across all test levels",
      },
      { id: "p1q1-custom", label: "Write your own\u2026" },
    ],
    customOptionId: "p1q1-custom",
  },
  {
    question: "Which programming languages should be supported?",
    options: [
      {
        id: "p1q2-a",
        label: "Python only",
        description: "pytest, unittest frameworks",
      },
      {
        id: "p1q2-b",
        label: "JavaScript / TypeScript",
        description: "Jest, Vitest, Mocha frameworks",
      },
      {
        id: "p1q2-c",
        label: "Python + JS/TS",
        description: "Both ecosystems with framework auto-detection",
      },
      {
        id: "p1q2-d",
        label: "All major languages",
        description: "Python, JS/TS, Go, Rust, Java, and more",
      },
      { id: "p1q2-custom", label: "Write your own\u2026" },
    ],
    customOptionId: "p1q2-custom",
  },
  {
    question: "What coverage model do you prefer?",
    options: [
      {
        id: "p1q3-a",
        label: "Standard (70\u201380%)",
        description: "Balanced between speed and thoroughness",
      },
      {
        id: "p1q3-b",
        label: "High (80\u201390%)",
        description: "Strong coverage with reasonable generation time",
      },
      {
        id: "p1q3-c",
        label: "Very high (90%+)",
        description: "Maximum coverage including edge cases",
      },
      {
        id: "p1q3-d",
        label: "Configurable per-project",
        description: "Let users set their own target at runtime",
      },
      { id: "p1q3-custom", label: "Write your own\u2026" },
    ],
    customOptionId: "p1q3-custom",
  },
];

export const phase2ClarificationQuestions = [
  {
    question: "Which part of the generated content needs changes?",
    options: [
      {
        id: "p2q1-a",
        label: "Documentation needs more detail",
        description: "Expand SKILL.md with additional sections and examples",
      },
      {
        id: "p2q1-b",
        label: "Code examples need a different framework",
        description: "Swap testing framework in generated examples",
      },
      {
        id: "p2q1-c",
        label: "Parameters need adjustment",
        description: "Add, remove, or modify skill parameters",
      },
      {
        id: "p2q1-d",
        label: "Missing capability definitions",
        description: "Add more capability entries to the manifest",
      },
      { id: "p2q1-custom", label: "Write your own\u2026" },
    ],
    customOptionId: "p2q1-custom",
  },
  {
    question: "How should the output format be structured?",
    options: [
      {
        id: "p2q2-a",
        label: "Keep current format",
        description: "Markdown with YAML frontmatter as-is",
      },
      {
        id: "p2q2-b",
        label: "Add more code samples",
        description: "Include examples for additional languages",
      },
      {
        id: "p2q2-c",
        label: "Simplify for quick start",
        description: "Shorter docs focused on immediate usage",
      },
      {
        id: "p2q2-d",
        label: "Add troubleshooting section",
        description: "Common issues and their resolutions",
      },
      { id: "p2q2-custom", label: "Write your own\u2026" },
    ],
    customOptionId: "p2q2-custom",
  },
];

// ── Mock Reasoning Data ─────────────────────────────────────────────
export const mockReasoningPhase1 = {
  parts: [
    {
      type: "text" as const,
      text: 'Analyzing the user request for a test generation skill. Identifying domain as "Development" with sub-category "Testing / Quality Assurance".',
    },
    {
      type: "text" as const,
      text: "Searching the existing taxonomy for related skills... Found 2 potential dependencies: `code-analysis` (prerequisite for AST parsing) and `assertion-patterns` (complementary for test assertions).",
    },
    {
      type: "text" as const,
      text: "Evaluating taxonomy placement. Best fit: `/development/testing/test-generation`. No naming conflicts detected. Version would be 1.0.0 for a new skill entry.",
    },
    {
      type: "text" as const,
      text: "Assessing coverage model: Standard unit test generation covers ~70% of code paths. Need to clarify with user whether integration and E2E testing are in scope to set appropriate quality targets.",
    },
    {
      type: "text" as const,
      text: "Preparing intent analysis summary and dependency graph. Ready to present plan for user approval.",
    },
  ],
  duration: 4.2,
};

export const mockReasoningPhase2 = {
  parts: [
    {
      type: "text" as const,
      text: "Generating SKILL.md documentation with YAML frontmatter. Including capability manifest with input/output schemas for test generation parameters.",
    },
    {
      type: "text" as const,
      text: "Building code examples using pytest framework. Generating 3 sample test suites: basic function tests, parameterized tests, and mock-based integration tests.",
    },
    {
      type: "text" as const,
      text: "Cross-referencing generated content against the approved plan. All parameters from Phase 1 are accounted for. Adding trigger phrases and edge case handlers.",
    },
    {
      type: "text" as const,
      text: "Validating YAML schema compliance and markdown structure. All 8 required sections present. Estimated quality score: 93/100.",
    },
  ],
  duration: 6.8,
};

export const mockReasoningPhase3 = {
  parts: [
    {
      type: "text" as const,
      text: "Running compliance checks: file structure validation, YAML frontmatter parsing, required section verification. All checks passed.",
    },
    {
      type: "text" as const,
      text: "Executing quality assessment algorithms. Completeness: 94% — all major sections present. Clarity: 91% — language is precise and unambiguous. Technical Accuracy: 96% — code examples compile and assertions are valid.",
    },
    {
      type: "text" as const,
      text: "Running trigger phrase coverage test: 8/8 phrases recognized. Edge case handler test: 12/12 scenarios handled. Code example output validation: all outputs match expected results.",
    },
  ],
  duration: 3.1,
};

// ── Mock Skills ─────────────────────────────────────────────────────
export const mockSkills: Skill[] = [
  {
    id: "sk-001",
    name: "data-analysis",
    displayName: "Data Analysis",
    version: "1.2.0",
    domain: "analytics",
    category: "data-processing",
    status: "published",
    description:
      "Comprehensive data analysis and visualization capabilities including statistical modeling, trend detection, and automated insight generation.",
    tags: ["data", "analysis", "visualization", "statistics"],
    dependencies: ["data-cleaning", "statistical-methods"],
    taxonomyPath: "/analytics/data-processing/data-analysis",
    usageCount: 1247,
    lastUsed: "2026-02-06",
    qualityScore: 94,
    author: "system",
    createdAt: "2025-11-15",
  },
  {
    id: "sk-002",
    name: "code-review",
    displayName: "Code Review",
    version: "2.0.1",
    domain: "development",
    category: "quality-assurance",
    status: "published",
    description:
      "Automated code review with best practice enforcement, security vulnerability detection, and performance optimization suggestions.",
    tags: ["code", "review", "quality", "security"],
    dependencies: ["code-analysis", "security-patterns"],
    taxonomyPath: "/development/quality-assurance/code-review",
    usageCount: 892,
    lastUsed: "2026-02-07",
    qualityScore: 91,
    author: "system",
    createdAt: "2025-10-22",
  },
  {
    id: "sk-003",
    name: "document-summarization",
    displayName: "Document Summarization",
    version: "1.0.0",
    domain: "nlp",
    category: "text-processing",
    status: "validated",
    description:
      "Intelligent document summarization supporting multiple formats, extractive and abstractive methods, with configurable detail levels.",
    tags: ["nlp", "summarization", "text", "documents"],
    dependencies: ["text-extraction"],
    taxonomyPath: "/nlp/text-processing/document-summarization",
    usageCount: 634,
    lastUsed: "2026-02-05",
    qualityScore: 88,
    author: "jchen",
    createdAt: "2025-12-01",
  },
  {
    id: "sk-004",
    name: "api-integration",
    displayName: "API Integration",
    version: "3.1.0",
    domain: "development",
    category: "integration",
    status: "published",
    description:
      "Dynamic API integration builder with schema inference, authentication handling, rate limiting, and automatic retry logic.",
    tags: ["api", "integration", "rest", "graphql"],
    dependencies: ["auth-patterns", "schema-validation"],
    taxonomyPath: "/development/integration/api-integration",
    usageCount: 2104,
    lastUsed: "2026-02-07",
    qualityScore: 96,
    author: "system",
    createdAt: "2025-09-18",
  },
  {
    id: "sk-005",
    name: "test-generation",
    displayName: "Test Generation",
    version: "1.4.2",
    domain: "development",
    category: "testing",
    status: "published",
    description:
      "Automated test suite generation from code analysis including unit tests, integration tests, and edge case identification.",
    tags: ["testing", "automation", "quality", "coverage"],
    dependencies: ["code-analysis", "assertion-patterns"],
    taxonomyPath: "/development/testing/test-generation",
    usageCount: 1589,
    lastUsed: "2026-02-07",
    qualityScore: 93,
    author: "system",
    createdAt: "2025-10-05",
  },
  {
    id: "sk-006",
    name: "sentiment-analysis",
    displayName: "Sentiment Analysis",
    version: "1.1.0",
    domain: "nlp",
    category: "analysis",
    status: "validated",
    description:
      "Multi-dimensional sentiment analysis supporting aspect-based analysis, emotion detection, and trend monitoring across text corpora.",
    tags: ["nlp", "sentiment", "analysis", "emotions"],
    dependencies: ["text-extraction", "language-detection"],
    taxonomyPath: "/nlp/analysis/sentiment-analysis",
    usageCount: 478,
    lastUsed: "2026-02-04",
    qualityScore: 86,
    author: "mlee",
    createdAt: "2025-12-15",
  },
  {
    id: "sk-007",
    name: "deployment-automation",
    displayName: "Deployment Automation",
    version: "2.2.0",
    domain: "devops",
    category: "automation",
    status: "published",
    description:
      "End-to-end deployment automation with blue-green deployments, canary releases, rollback strategies, and health monitoring.",
    tags: ["devops", "deployment", "automation", "ci-cd"],
    dependencies: ["infra-provisioning", "health-checks"],
    taxonomyPath: "/devops/automation/deployment-automation",
    usageCount: 756,
    lastUsed: "2026-02-06",
    qualityScore: 92,
    author: "system",
    createdAt: "2025-11-01",
  },
  {
    id: "sk-008",
    name: "knowledge-extraction",
    displayName: "Knowledge Extraction",
    version: "1.0.3",
    domain: "nlp",
    category: "knowledge-management",
    status: "draft",
    description:
      "Extract structured knowledge from unstructured text including entity recognition, relation extraction, and knowledge graph construction.",
    tags: ["nlp", "knowledge", "extraction", "graph"],
    dependencies: ["text-extraction", "entity-recognition"],
    taxonomyPath: "/nlp/knowledge-management/knowledge-extraction",
    usageCount: 203,
    lastUsed: "2026-01-28",
    qualityScore: 79,
    author: "jchen",
    createdAt: "2026-01-10",
  },
  {
    id: "sk-009",
    name: "performance-monitoring",
    displayName: "Performance Monitoring",
    version: "1.3.0",
    domain: "devops",
    category: "observability",
    status: "published",
    description:
      "Real-time performance monitoring with anomaly detection, SLO tracking, and automated alerting across distributed systems.",
    tags: ["devops", "monitoring", "performance", "observability"],
    dependencies: ["metrics-collection", "alerting-rules"],
    taxonomyPath: "/devops/observability/performance-monitoring",
    usageCount: 945,
    lastUsed: "2026-02-07",
    qualityScore: 90,
    author: "system",
    createdAt: "2025-10-12",
  },
  {
    id: "sk-010",
    name: "data-cleaning",
    displayName: "Data Cleaning",
    version: "1.5.0",
    domain: "analytics",
    category: "preprocessing",
    status: "published",
    description:
      "Automated data cleaning pipeline with outlier detection, missing value imputation, type inference, and deduplication.",
    tags: ["data", "cleaning", "preprocessing", "quality"],
    dependencies: [],
    taxonomyPath: "/analytics/preprocessing/data-cleaning",
    usageCount: 1823,
    lastUsed: "2026-02-07",
    qualityScore: 95,
    author: "system",
    createdAt: "2025-08-20",
  },
];

// ── Mock Taxonomy ───────────────────────────────────────────────────
export const mockTaxonomy: TaxonomyNode[] = [
  {
    id: "tx-analytics",
    name: "analytics",
    path: "/analytics",
    skillCount: 4,
    children: [
      {
        id: "tx-dp",
        name: "data-processing",
        path: "/analytics/data-processing",
        skillCount: 1,
        children: [],
        skills: ["sk-001"],
      },
      {
        id: "tx-pp",
        name: "preprocessing",
        path: "/analytics/preprocessing",
        skillCount: 1,
        children: [],
        skills: ["sk-010"],
      },
      {
        id: "tx-viz",
        name: "visualization",
        path: "/analytics/visualization",
        skillCount: 2,
        children: [],
      },
    ],
  },
  {
    id: "tx-dev",
    name: "development",
    path: "/development",
    skillCount: 5,
    children: [
      {
        id: "tx-qa",
        name: "quality-assurance",
        path: "/development/quality-assurance",
        skillCount: 1,
        children: [],
        skills: ["sk-002"],
      },
      {
        id: "tx-test",
        name: "testing",
        path: "/development/testing",
        skillCount: 2,
        children: [],
        skills: ["sk-005"],
      },
      {
        id: "tx-int",
        name: "integration",
        path: "/development/integration",
        skillCount: 2,
        children: [],
        skills: ["sk-004"],
      },
    ],
  },
  {
    id: "tx-nlp",
    name: "nlp",
    path: "/nlp",
    skillCount: 5,
    children: [
      {
        id: "tx-tp",
        name: "text-processing",
        path: "/nlp/text-processing",
        skillCount: 2,
        children: [],
        skills: ["sk-003"],
      },
      {
        id: "tx-ana",
        name: "analysis",
        path: "/nlp/analysis",
        skillCount: 1,
        children: [],
        skills: ["sk-006"],
      },
      {
        id: "tx-km",
        name: "knowledge-management",
        path: "/nlp/knowledge-management",
        skillCount: 2,
        children: [],
        skills: ["sk-008"],
      },
    ],
  },
  {
    id: "tx-devops",
    name: "devops",
    path: "/devops",
    skillCount: 4,
    children: [
      {
        id: "tx-auto",
        name: "automation",
        path: "/devops/automation",
        skillCount: 2,
        children: [],
        skills: ["sk-007"],
      },
      {
        id: "tx-obs",
        name: "observability",
        path: "/devops/observability",
        skillCount: 2,
        children: [],
        skills: ["sk-009"],
      },
    ],
  },
];

// ── Analytics mock data ─────────────────────────────────────────────
export const analyticsData = {
  totalSkills: 42,
  activeSkills: 38,
  totalExecutions: 12847,
  avgQualityScore: 91.2,
  weeklyGrowth: 8.3,
  executionsByDay: [
    { date: "Jan 28", count: 320 },
    { date: "Jan 29", count: 410 },
    { date: "Jan 30", count: 380 },
    { date: "Jan 31", count: 520 },
    { date: "Feb 01", count: 480 },
    { date: "Feb 02", count: 290 },
    { date: "Feb 03", count: 560 },
    { date: "Feb 04", count: 610 },
    { date: "Feb 05", count: 530 },
    { date: "Feb 06", count: 690 },
    { date: "Feb 07", count: 740 },
  ],
  topSkills: [
    { name: "API Integration", uses: 2104 },
    { name: "Data Cleaning", uses: 1823 },
    { name: "Test Generation", uses: 1589 },
    { name: "Data Analysis", uses: 1247 },
    { name: "Perf. Monitoring", uses: 945 },
  ],
  qualityDist: [
    { range: "70-79", count: 2 },
    { range: "80-84", count: 3 },
    { range: "85-89", count: 6 },
    { range: "90-94", count: 18 },
    { range: "95-100", count: 13 },
  ],
};

// ── Generated SKILL.md content for preview ──────────────────────────
export const generatedSkillMd = `# Test Generation

## Overview
This skill enables automated generation of comprehensive test suites from code analysis, covering unit tests, integration tests, and edge case identification.

## Capabilities
- **Unit Test Generation** — Analyze function signatures and generate type-safe unit tests
- **Integration Test Scaffolding** — Create test harnesses for service boundaries
- **Edge Case Identification** — Detect boundary conditions and generate targeted tests
- **Coverage Analysis** — Map generated tests to code paths for gap detection

## Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| language | string | 'auto' | Target language for test generation |
| framework | string | 'auto' | Testing framework (jest, pytest, etc.) |
| coverage_target | number | 80 | Minimum coverage percentage |
| include_edge_cases | boolean | true | Generate edge case tests |

## Usage

\`\`\`python
from skill_fleet import SkillRunner

runner = SkillRunner("test-generation")
result = runner.execute(
    source_path="./src",
    coverage_target=90,
    framework="pytest"
)

print(f"Generated {result.test_count} tests")
print(f"Estimated coverage: {result.coverage}%")
\`\`\`

## Dependencies
- \`code-analysis\` — Required for AST parsing and function extraction
- \`assertion-patterns\` — Complementary patterns for assertion generation

## Notes

> **Important**: This skill requires read access to the source code repository. Ensure proper file system permissions are configured.
`;

// ── Mock Memory Entries ─────────────────────────────────────────────
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
export const mockFilesystem: FsNode[] = [
  {
    id: "vol-skills",
    name: "skills",
    path: "/sandbox/skills",
    type: "volume",
    modifiedAt: "2026-02-16T08:00:00Z",
    children: [
      {
        id: "dir-sk001",
        name: "data-analysis",
        path: "/sandbox/skills/data-analysis",
        type: "directory",
        skillId: "sk-001",
        modifiedAt: "2026-02-06T12:00:00Z",
        children: [
          {
            id: "f-sk001-md",
            name: "SKILL.md",
            path: "/sandbox/skills/data-analysis/SKILL.md",
            type: "file",
            size: 4820,
            mime: "text/markdown",
            modifiedAt: "2026-02-06T12:00:00Z",
            skillId: "sk-001",
          },
          {
            id: "f-sk001-yaml",
            name: "manifest.yaml",
            path: "/sandbox/skills/data-analysis/manifest.yaml",
            type: "file",
            size: 1240,
            mime: "text/yaml",
            modifiedAt: "2026-02-06T11:30:00Z",
            skillId: "sk-001",
          },
          {
            id: "f-sk001-py",
            name: "handler.py",
            path: "/sandbox/skills/data-analysis/handler.py",
            type: "file",
            size: 3650,
            mime: "text/x-python",
            modifiedAt: "2026-02-06T11:45:00Z",
            skillId: "sk-001",
          },
        ],
      },
      {
        id: "dir-sk002",
        name: "code-review",
        path: "/sandbox/skills/code-review",
        type: "directory",
        skillId: "sk-002",
        modifiedAt: "2026-02-07T09:00:00Z",
        children: [
          {
            id: "f-sk002-md",
            name: "SKILL.md",
            path: "/sandbox/skills/code-review/SKILL.md",
            type: "file",
            size: 5130,
            mime: "text/markdown",
            modifiedAt: "2026-02-07T09:00:00Z",
            skillId: "sk-002",
          },
          {
            id: "f-sk002-yaml",
            name: "manifest.yaml",
            path: "/sandbox/skills/code-review/manifest.yaml",
            type: "file",
            size: 1580,
            mime: "text/yaml",
            modifiedAt: "2026-02-07T08:45:00Z",
            skillId: "sk-002",
          },
          {
            id: "f-sk002-rules",
            name: ".reviewrc.yaml",
            path: "/sandbox/skills/code-review/.reviewrc.yaml",
            type: "file",
            size: 890,
            mime: "text/yaml",
            modifiedAt: "2026-02-07T08:30:00Z",
            skillId: "sk-002",
          },
        ],
      },
      {
        id: "dir-sk005",
        name: "test-generation",
        path: "/sandbox/skills/test-generation",
        type: "directory",
        skillId: "sk-005",
        modifiedAt: "2026-02-07T14:00:00Z",
        children: [
          {
            id: "f-sk005-md",
            name: "SKILL.md",
            path: "/sandbox/skills/test-generation/SKILL.md",
            type: "file",
            size: 6240,
            mime: "text/markdown",
            modifiedAt: "2026-02-07T14:00:00Z",
            skillId: "sk-005",
          },
          {
            id: "f-sk005-yaml",
            name: "manifest.yaml",
            path: "/sandbox/skills/test-generation/manifest.yaml",
            type: "file",
            size: 1820,
            mime: "text/yaml",
            modifiedAt: "2026-02-07T13:45:00Z",
            skillId: "sk-005",
          },
          {
            id: "f-sk005-py",
            name: "handler.py",
            path: "/sandbox/skills/test-generation/handler.py",
            type: "file",
            size: 4210,
            mime: "text/x-python",
            modifiedAt: "2026-02-07T13:30:00Z",
            skillId: "sk-005",
          },
          {
            id: "f-sk005-test",
            name: "test_handler.py",
            path: "/sandbox/skills/test-generation/test_handler.py",
            type: "file",
            size: 2890,
            mime: "text/x-python",
            modifiedAt: "2026-02-07T13:15:00Z",
            skillId: "sk-005",
          },
        ],
      },
    ],
  },
  {
    id: "vol-config",
    name: "config",
    path: "/sandbox/config",
    type: "volume",
    modifiedAt: "2026-02-14T16:00:00Z",
    children: [
      {
        id: "f-fleet-yaml",
        name: "fleet.yaml",
        path: "/sandbox/config/fleet.yaml",
        type: "file",
        size: 2340,
        mime: "text/yaml",
        modifiedAt: "2026-02-14T16:00:00Z",
      },
      {
        id: "f-taxonomy-json",
        name: "taxonomy.json",
        path: "/sandbox/config/taxonomy.json",
        type: "file",
        size: 8920,
        mime: "application/json",
        modifiedAt: "2026-02-14T15:30:00Z",
      },
      {
        id: "f-auth-yaml",
        name: "auth.yaml",
        path: "/sandbox/config/auth.yaml",
        type: "file",
        size: 640,
        mime: "text/yaml",
        modifiedAt: "2026-02-12T10:00:00Z",
      },
      {
        id: "dir-policies",
        name: "policies",
        path: "/sandbox/config/policies",
        type: "directory",
        modifiedAt: "2026-02-13T14:00:00Z",
        children: [
          {
            id: "f-review-policy",
            name: "review-policy.yaml",
            path: "/sandbox/config/policies/review-policy.yaml",
            type: "file",
            size: 420,
            mime: "text/yaml",
            modifiedAt: "2026-02-13T14:00:00Z",
          },
          {
            id: "f-publish-policy",
            name: "publish-policy.yaml",
            path: "/sandbox/config/policies/publish-policy.yaml",
            type: "file",
            size: 380,
            mime: "text/yaml",
            modifiedAt: "2026-02-13T13:30:00Z",
          },
        ],
      },
    ],
  },
  {
    id: "vol-artifacts",
    name: "artifacts",
    path: "/sandbox/artifacts",
    type: "volume",
    modifiedAt: "2026-02-15T10:30:00Z",
    children: [
      {
        id: "dir-art-sessions",
        name: "sessions",
        path: "/sandbox/artifacts/sessions",
        type: "directory",
        modifiedAt: "2026-02-15T10:30:00Z",
        children: [
          {
            id: "f-session-log",
            name: "session-default.jsonl",
            path: "/sandbox/artifacts/sessions/session-default.jsonl",
            type: "file",
            size: 15600,
            mime: "application/jsonl",
            modifiedAt: "2026-02-15T10:30:00Z",
          },
          {
            id: "f-session-2-log",
            name: "session-2.jsonl",
            path: "/sandbox/artifacts/sessions/session-2.jsonl",
            type: "file",
            size: 8400,
            mime: "application/jsonl",
            modifiedAt: "2026-02-10T11:20:00Z",
          },
        ],
      },
      {
        id: "dir-art-generated",
        name: "generated",
        path: "/sandbox/artifacts/generated",
        type: "directory",
        modifiedAt: "2026-02-15T10:30:00Z",
        children: [
          {
            id: "f-gen-tests",
            name: "test-generation-v1.4.2.tar.gz",
            path: "/sandbox/artifacts/generated/test-generation-v1.4.2.tar.gz",
            type: "file",
            size: 24500,
            mime: "application/gzip",
            modifiedAt: "2026-02-07T14:00:00Z",
          },
          {
            id: "f-gen-review",
            name: "code-review-v2.0.1.tar.gz",
            path: "/sandbox/artifacts/generated/code-review-v2.0.1.tar.gz",
            type: "file",
            size: 18200,
            mime: "application/gzip",
            modifiedAt: "2026-02-07T09:00:00Z",
          },
        ],
      },
    ],
  },
  {
    id: "vol-data",
    name: "data",
    path: "/sandbox/data",
    type: "volume",
    modifiedAt: "2026-02-16T08:00:00Z",
    children: [
      {
        id: "f-embeddings",
        name: "skill-embeddings.bin",
        path: "/sandbox/data/skill-embeddings.bin",
        type: "file",
        size: 524288,
        mime: "application/octet-stream",
        modifiedAt: "2026-02-16T08:00:00Z",
      },
      {
        id: "f-index",
        name: "taxonomy-index.json",
        path: "/sandbox/data/taxonomy-index.json",
        type: "file",
        size: 12400,
        mime: "application/json",
        modifiedAt: "2026-02-14T15:30:00Z",
      },
      {
        id: "dir-cache",
        name: "cache",
        path: "/sandbox/data/cache",
        type: "directory",
        modifiedAt: "2026-02-16T07:00:00Z",
        children: [
          {
            id: "f-cache-lru",
            name: "lru-cache.db",
            path: "/sandbox/data/cache/lru-cache.db",
            type: "file",
            size: 65536,
            mime: "application/octet-stream",
            modifiedAt: "2026-02-16T07:00:00Z",
          },
        ],
      },
    ],
  },
];
