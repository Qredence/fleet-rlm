// Compatibility aliases for Daytona-specific imports while the shared repo helpers remain canonical.
export {
  detectRepoContext as detectDaytonaRepoContext,
  normalizeRepoUrl as normalizeDaytonaRepoUrl,
  resolveRepoContext as resolveDaytonaRepoContext,
} from "@/lib/utils/repoContext";
export type {
  DetectedRepoContext as DetectedDaytonaRepoContext,
  RepoDetectionSource as DaytonaRepoDetectionSource,
  ResolvedRepoContext as ResolvedDaytonaRepoContext,
} from "@/lib/utils/repoContext";
