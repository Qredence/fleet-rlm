import { Skeleton } from "@/components/ui/skeleton";
import { Card } from "@/components/ui/card";

/**
 * Skeleton placeholder matching the SkillCard layout.
 *
 * Three rows: title + badge, description line, domain + version + score.
 * Uses `bg-muted` via the Skeleton primitive so shimmer inherits
 * the design system's muted palette.
 */
function SkillCardSkeleton() {
  return (
    <Card>
      <div className="px-4 py-3 flex flex-col gap-2">
        {/* Row 1: title + status badge */}
        <div className="flex items-center justify-between gap-2">
          <Skeleton className="h-4 w-36" />
          <Skeleton className="h-5 w-16 rounded-full" />
        </div>

        {/* Row 2: description */}
        <Skeleton className="h-3.5 w-full" />

        {/* Row 3: domain badge + version + quality */}
        <div className="flex items-center gap-2">
          <Skeleton className="h-5 w-20 rounded-full" />
          <Skeleton className="h-3 w-10" />
          <div className="ml-auto">
            <Skeleton className="h-3 w-8" />
          </div>
        </div>
      </div>
    </Card>
  );
}

export { SkillCardSkeleton };
