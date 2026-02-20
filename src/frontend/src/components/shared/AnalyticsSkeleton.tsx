import { Skeleton } from "@/components/ui/skeleton";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { cn } from "@/components/ui/utils";

/**
 * Skeleton placeholder for the Analytics Dashboard.
 *
 * Mirrors the real layout: 4 KPI cards, an area chart card,
 * two side-by-side chart cards, and a leaderboard card.
 *
 * All shimmer colours inherit from `bg-muted` via the Skeleton
 * primitive so the user can restyle from CSS alone.
 */
function KpiSkeleton() {
  return (
    <Card>
      <CardContent className="p-4">
        <Skeleton className="w-8 h-8 rounded-lg mb-3" />
        <Skeleton className="h-5 w-16 mb-1" />
        <Skeleton className="h-3 w-24" />
      </CardContent>
    </Card>
  );
}

function ChartSkeleton({ className }: { className?: string }) {
  return (
    <Card className={className}>
      <CardHeader className="pb-0">
        <Skeleton className="h-4 w-32" />
      </CardHeader>
      <CardContent>
        <Skeleton className="h-48 w-full rounded-md" />
      </CardContent>
    </Card>
  );
}

function AnalyticsSkeleton({ isMobile }: { isMobile?: boolean }) {
  return (
    <div
      className={cn(
        "space-y-4 md:space-y-6 max-w-[800px] w-full mx-auto",
        isMobile ? "p-4" : "p-6",
      )}
    >
      {/* KPIs */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 md:gap-4">
        <KpiSkeleton />
        <KpiSkeleton />
        <KpiSkeleton />
        <KpiSkeleton />
      </div>

      {/* Executions chart */}
      <ChartSkeleton />

      {/* Two-column row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 md:gap-4">
        <ChartSkeleton />
        <ChartSkeleton />
      </div>

      {/* Leaderboard */}
      <Card>
        <CardHeader className="border-b border-border-subtle pb-4">
          <Skeleton className="h-4 w-28" />
        </CardHeader>
        <CardContent className="p-0">
          <div className="divide-y divide-border-subtle">
            {Array.from({ length: 5 }).map((_, i) => (
              <div
                key={i}
                className="flex items-center gap-4 px-4 md:px-5 py-3 touch-target"
              >
                <Skeleton className="h-4 w-6" />
                <Skeleton className="h-4 flex-1 max-w-[160px]" />
                <Skeleton className="h-3 w-16 ml-auto" />
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

export { AnalyticsSkeleton };
