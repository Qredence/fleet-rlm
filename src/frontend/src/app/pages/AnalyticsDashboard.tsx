import { BarChart2 } from "lucide-react";
import { typo } from "@/lib/config/typo";
import { LargeTitleHeader } from "@/components/shared/LargeTitleHeader";
import { useIsMobile } from "@/hooks/useIsMobile";

/**
 * AnalyticsDashboard — placeholder until the analytics backend is ready.
 */
export function AnalyticsDashboard() {
  const isMobile = useIsMobile();

  return (
    <div className="flex flex-col h-full w-full bg-background overflow-hidden">
      {!isMobile && (
        <LargeTitleHeader
          title="Analytics"
          subtitle="Skill Fleet usage and quality overview"
          isMobile={false}
        />
      )}

      <div className="flex-1 flex flex-col items-center justify-center gap-4 text-center px-6">
        {isMobile && (
          <LargeTitleHeader
            title="Analytics"
            subtitle="Skill Fleet usage and quality overview"
            isMobile
          />
        )}
        <div className="w-12 h-12 rounded-2xl bg-muted flex items-center justify-center">
          <BarChart2 className="w-6 h-6 text-muted-foreground" />
        </div>
        <div className="space-y-1">
          <p className="text-foreground" style={typo.h3}>
            Coming soon
          </p>
          <p className="text-muted-foreground max-w-xs" style={typo.caption}>
            Analytics will surface skill usage, quality scores, and execution
            trends once backend reporting is available.
          </p>
        </div>
      </div>
    </div>
  );
}
