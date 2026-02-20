import type { PlanStep, Skill, SkillMetadataItem } from "@/lib/data/types";

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
