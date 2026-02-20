import { Skeleton } from "../ui/skeleton";
import { SkillCardSkeleton } from "./SkillCardSkeleton";
import { cn } from "../ui/utils";

/**
 * Suspense fallback for the lazy-loaded SkillLibrary page.
 *
 * Mirrors the real layout: large title, search bar, domain filter
 * pills, and a grid of SkillCardSkeletons.
 */
function SkillLibrarySkeleton({ isMobile }: { isMobile?: boolean }) {
  return (
    <div
      className={cn(
        "flex flex-col gap-4 w-full mx-auto animate-in fade-in duration-150",
        isMobile ? "p-4" : "p-6 max-w-[800px]",
      )}
    >
      {/* Large title header */}
      <Skeleton className="h-7 w-36" />

      {/* Search bar */}
      <Skeleton className="h-10 w-full rounded-lg" />

      {/* Domain filter pills */}
      <div className="flex items-center gap-2 overflow-hidden">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-8 w-20 rounded-button shrink-0" />
        ))}
      </div>

      {/* Skill card grid */}
      <div className="flex flex-col gap-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <SkillCardSkeleton key={i} />
        ))}
      </div>
    </div>
  );
}

export { SkillLibrarySkeleton };
