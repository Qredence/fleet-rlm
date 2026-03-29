import type { NavItem } from "@/stores/navigation-types";

// ── Nav section support ─────────────────────────────────────────────

export const SUPPORTED_SECTIONS = new Set<NavItem>([
  "workspace",
  "settings",
  "volumes",
]);

export const UNSUPPORTED_SECTION_REASON =
  "This surface is not part of the current Workbench product contract.";

export function isSectionSupported(nav: NavItem): boolean {
  return SUPPORTED_SECTIONS.has(nav);
}

// ── Shared API data-source type ─────────────────────────────────────

export type DataSource = "api" | "mock" | "fallback";

export interface TaskListParams {
  page?: number;
  pageSize?: number;
  domain?: string;
  category?: string;
  status?: string;
  search?: string;
  sortBy?: string;
  sortOrder?: "asc" | "desc";
}

export interface MemoryListParams {
  type?: string;
  search?: string;
  pinned?: boolean;
  sortBy?: "relevance" | "createdAt" | "updatedAt";
  sortOrder?: "asc" | "desc";
}
