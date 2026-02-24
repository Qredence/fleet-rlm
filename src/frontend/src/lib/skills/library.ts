import type { Skill } from "@/lib/data/types";

export const domains = ["All", "analytics", "development", "nlp", "devops"];

export type SortKey =
  | "name-asc"
  | "name-desc"
  | "quality-desc"
  | "usage-desc"
  | "last-used"
  | "created";

export const sortOptions: { key: SortKey; label: string }[] = [
  { key: "name-asc", label: "Name (A–Z)" },
  { key: "name-desc", label: "Name (Z–A)" },
  { key: "quality-desc", label: "Quality (High–Low)" },
  { key: "usage-desc", label: "Most Used" },
  { key: "last-used", label: "Recently Used" },
  { key: "created", label: "Newest First" },
];

export const PULL_THRESHOLD = 80;
export const MAX_PULL = 120;

export function matchesSkillSearch(skill: Skill, search: string): boolean {
  if (!search) return true;
  const q = search.toLowerCase();
  return (
    skill.displayName.toLowerCase().includes(q) ||
    skill.tags.some((t) => t.includes(q))
  );
}

export function buildDomainCounts(
  skills: Skill[],
  search: string,
): Record<string, number> {
  const searchMatched = skills.filter((s) => matchesSkillSearch(s, search));
  const counts: Record<string, number> = {
    All: searchMatched.length,
  };
  for (const skill of searchMatched) {
    counts[skill.domain] = (counts[skill.domain] || 0) + 1;
  }
  return counts;
}

export function filterSkills(
  skills: Skill[],
  search: string,
  activeDomain: string,
): Skill[] {
  return skills.filter((s) => {
    const matchSearch = matchesSkillSearch(s, search);
    const matchDomain = activeDomain === "All" || s.domain === activeDomain;
    return matchSearch && matchDomain;
  });
}

export function sortSkills(skills: Skill[], key: SortKey): Skill[] {
  const sorted = [...skills];
  switch (key) {
    case "name-asc":
      return sorted.sort((a, b) => a.displayName.localeCompare(b.displayName));
    case "name-desc":
      return sorted.sort((a, b) => b.displayName.localeCompare(a.displayName));
    case "quality-desc":
      return sorted.sort((a, b) => b.qualityScore - a.qualityScore);
    case "usage-desc":
      return sorted.sort((a, b) => b.usageCount - a.usageCount);
    case "last-used":
      return sorted.sort(
        (a, b) =>
          new Date(b.lastUsed).getTime() - new Date(a.lastUsed).getTime(),
      );
    case "created":
      return sorted.sort(
        (a, b) =>
          new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime(),
      );
    default:
      return sorted;
  }
}
