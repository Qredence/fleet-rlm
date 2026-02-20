import type { TaxonomyNode } from "../types";

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
