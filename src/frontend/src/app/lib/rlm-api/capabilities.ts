import type { NavItem } from "../../components/data/types";

export const SUPPORTED_SECTIONS = new Set<NavItem>(["new", "settings"]);

export const UNSUPPORTED_SECTION_REASON =
  "This section requires backend endpoints that are not currently exposed by FastAPI.";

export function isSectionSupported(nav: NavItem): boolean {
  return SUPPORTED_SECTIONS.has(nav);
}
