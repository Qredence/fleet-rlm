import { motion, useReducedMotion } from "motion/react";
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from "recharts";
import { TrendingUp, Layers, Play, Award } from "lucide-react";
import { typo } from "@/lib/config/typo";
import { useAnalytics } from "@/hooks/useAnalytics";
import { springs } from "@/lib/config/motion-config";
import { LargeTitleHeader } from "@/components/shared/LargeTitleHeader";
import { AnalyticsSkeleton } from "@/components/shared/AnalyticsSkeleton";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { cn } from "@/components/ui/utils";
import { useIsMobile } from "@/components/ui/use-mobile";

// Chart tick style referencing design system variables
const chartTickStyle = {
  fill: "var(--muted-foreground)",
  fontSize: "var(--text-micro)",
  fontFamily: "var(--font-family)",
};

// iOS 26 spring config for staggered entry
const springIn = (delay: number, reduced?: boolean | null) => ({
  initial: { opacity: 0, y: reduced ? 0 : 6 } as const,
  animate: { opacity: 1, y: 0 } as const,
  transition: reduced ? springs.instant : { ...springs.default, delay },
});

// ── Shared chart tooltip ────────────────────────────────────────────
function ChartTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: Array<{ value?: number | string }>;
  label?: string | number;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-popover border border-border-subtle rounded-lg px-3 py-2 shadow-sm">
      <p className="text-muted-foreground mb-1" style={typo.helper}>
        {label}
      </p>
      <p className="text-foreground" style={typo.label}>
        {(payload[0]?.value ?? 0).toLocaleString()}
      </p>
    </div>
  );
}

// ── KPI card ────────────────────────────────────────────────────────
function KpiCard({
  icon: Icon,
  label,
  value,
  sub,
  delay,
  reduced,
}: {
  icon: typeof TrendingUp;
  label: string;
  value: string;
  sub?: string;
  delay: number;
  reduced?: boolean | null;
}) {
  return (
    <motion.div {...springIn(delay, reduced)}>
      <Card className="border-border-subtle">
        <CardContent className="p-4">
          <div className="flex items-center gap-2 mb-3">
            <div className="w-8 h-8 rounded-lg bg-accent/10 flex items-center justify-center">
              <Icon className="w-4 h-4 text-accent" />
            </div>
          </div>
          <p
            className="text-foreground mb-0.5"
            style={{
              ...typo.h3,
              fontWeight: "var(--font-weight-semibold)",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {value}
          </p>
          <p className="text-muted-foreground" style={typo.helper}>
            {label}
          </p>
          {sub && (
            <p className="text-accent mt-1" style={typo.helper}>
              {sub}
            </p>
          )}
        </CardContent>
      </Card>
    </motion.div>
  );
}

// Chart heights in px — explicit numeric values prevent the
// "width(0) height(0)" Recharts error that occurs when
// ResponsiveContainer height="100%" can't resolve inside
// ScrollArea + motion animation containers.
const CHART_H = 192; // matches h-48
const CHART_H_LG = 208; // matches md:h-52

// ── Chart wrapper ───────────────────────────────────────────────────
function ChartCard({
  title,
  children,
  delay,
  className = "",
  reduced,
}: {
  title: string;
  children: React.ReactNode;
  delay: number;
  className?: string;
  reduced?: boolean | null;
}) {
  return (
    <motion.div {...springIn(delay, reduced)} className={className}>
      <Card>
        <CardHeader className="pb-0">
          <CardTitle style={typo.label}>{title}</CardTitle>
        </CardHeader>
        <CardContent className="overflow-hidden">{children}</CardContent>
      </Card>
    </motion.div>
  );
}

// ── Main component ──────────────────────────────────────────────────
/**
 * AnalyticsDashboard — skill fleet usage and quality overview.
 *
 * Uses `useIsMobile()` hook — zero props.
 */
export function AnalyticsDashboard() {
  const isMobile = useIsMobile();
  const {
    analytics: analyticsData,
    dataSource,
    degradedReason,
    isLoading: isAnalyticsLoading,
  } = useAnalytics();
  const prefersReduced = useReducedMotion();

  // Use hook loading state
  const isLoading = isAnalyticsLoading;
  const isDegradedData = dataSource === "fallback";

  return (
    <div className="flex flex-col h-full w-full bg-background overflow-hidden">
      {/* Desktop: static header outside scroll */}
      {!isMobile && (
        <LargeTitleHeader
          title="Analytics"
          subtitle="Skill Fleet usage and quality overview"
          isMobile={false}
        />
      )}

      <ScrollArea className="flex-1 min-h-0">
        {/* Mobile: large-title header INSIDE scroll area for collapse behavior */}
        {isMobile && (
          <LargeTitleHeader
            title="Analytics"
            subtitle="Skill Fleet usage and quality overview"
            isMobile
          />
        )}

        {/* Skeleton vs real content */}
        {isLoading ? (
          <AnalyticsSkeleton isMobile={isMobile} />
        ) : (
          <div
            className={cn(
              "space-y-4 md:space-y-6 max-w-[800px] w-full mx-auto",
              isMobile ? "p-4" : "p-6",
            )}
          >
            {isDegradedData && (
              <Alert>
                <TrendingUp className="text-muted-foreground" />
                <AlertTitle style={typo.label}>
                  Analytics API unavailable
                </AlertTitle>
                <AlertDescription style={typo.caption}>
                  {degradedReason ??
                    "Showing local mock analytics so dashboards remain available while backend endpoints are unavailable."}
                </AlertDescription>
              </Alert>
            )}

            {/* KPIs */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 md:gap-4">
              <KpiCard
                icon={Layers}
                label="Total Skills"
                value={analyticsData.totalSkills.toString()}
                delay={0}
                reduced={prefersReduced}
              />
              <KpiCard
                icon={Play}
                label="Active Skills"
                value={analyticsData.activeSkills.toString()}
                sub={`${analyticsData.activeSkills} / ${analyticsData.totalSkills} active`}
                delay={0.03}
                reduced={prefersReduced}
              />
              <KpiCard
                icon={TrendingUp}
                label="Total Executions"
                value={analyticsData.totalExecutions.toLocaleString()}
                sub={`+${analyticsData.weeklyGrowth}% this week`}
                delay={0.06}
                reduced={prefersReduced}
              />
              <KpiCard
                icon={Award}
                label="Avg Quality Score"
                value={`${analyticsData.avgQualityScore}%`}
                delay={0.09}
                reduced={prefersReduced}
              />
            </div>

            {/* Executions chart */}
            <ChartCard
              title="Executions Over Time"
              delay={0.12}
              reduced={prefersReduced}
            >
              <ResponsiveContainer
                width="100%"
                height={isMobile ? CHART_H : CHART_H_LG}
                debounce={1}
              >
                <AreaChart data={analyticsData.executionsByDay}>
                  <defs>
                    <linearGradient id="execGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop
                        offset="5%"
                        stopColor="var(--accent)"
                        stopOpacity={0.15}
                      />
                      <stop
                        offset="95%"
                        stopColor="var(--accent)"
                        stopOpacity={0}
                      />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                  <XAxis
                    dataKey="date"
                    tick={chartTickStyle}
                    axisLine={false}
                    tickLine={false}
                  />
                  <YAxis
                    tick={chartTickStyle}
                    axisLine={false}
                    tickLine={false}
                    width={40}
                  />
                  <Tooltip content={<ChartTooltip />} />
                  <Area
                    type="monotone"
                    dataKey="count"
                    stroke="var(--accent)"
                    strokeWidth={2}
                    fill="url(#execGrad)"
                  />
                </AreaChart>
              </ResponsiveContainer>
            </ChartCard>

            {/* Two-column row */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 md:gap-4">
              <ChartCard
                title="Top Skills by Usage"
                delay={0.15}
                reduced={prefersReduced}
              >
                <ResponsiveContainer width="100%" height={CHART_H} debounce={1}>
                  <BarChart
                    data={analyticsData.topSkills}
                    layout="vertical"
                    barSize={14}
                  >
                    <CartesianGrid
                      strokeDasharray="3 3"
                      stroke="var(--border)"
                      horizontal={false}
                    />
                    <XAxis
                      type="number"
                      tick={chartTickStyle}
                      axisLine={false}
                      tickLine={false}
                    />
                    <YAxis
                      type="category"
                      dataKey="name"
                      tick={chartTickStyle}
                      axisLine={false}
                      tickLine={false}
                      width={isMobile ? 80 : 100}
                    />
                    <Tooltip content={<ChartTooltip />} />
                    <Bar
                      dataKey="uses"
                      fill="var(--accent)"
                      radius={[0, 4, 4, 0]}
                    />
                  </BarChart>
                </ResponsiveContainer>
              </ChartCard>

              <ChartCard
                title="Quality Distribution"
                delay={0.18}
                reduced={prefersReduced}
              >
                <ResponsiveContainer width="100%" height={CHART_H} debounce={1}>
                  <BarChart data={analyticsData.qualityDist} barSize={28}>
                    <CartesianGrid
                      strokeDasharray="3 3"
                      stroke="var(--border)"
                    />
                    <XAxis
                      dataKey="range"
                      tick={chartTickStyle}
                      axisLine={false}
                      tickLine={false}
                    />
                    <YAxis
                      tick={chartTickStyle}
                      axisLine={false}
                      tickLine={false}
                      width={30}
                    />
                    <Tooltip content={<ChartTooltip />} />
                    <Bar
                      dataKey="count"
                      fill="var(--accent)"
                      radius={[4, 4, 0, 0]}
                    />
                  </BarChart>
                </ResponsiveContainer>
              </ChartCard>
            </div>

            {/* Leaderboard */}
            <motion.div {...springIn(0.21, prefersReduced)}>
              <Card className="border-border-subtle">
                <CardHeader className="border-b border-border-subtle pb-4">
                  <CardTitle style={typo.label}>Skill Leaderboard</CardTitle>
                </CardHeader>
                <CardContent className="p-0">
                  <div className="divide-y divide-border-subtle">
                    {analyticsData.topSkills.map((skill, i) => (
                      <div
                        key={skill.name}
                        className="flex items-center gap-4 px-4 md:px-5 py-3 touch-target hover:bg-muted transition-colors"
                      >
                        <span
                          className="text-muted-foreground w-6 text-center"
                          style={{
                            ...typo.label,
                            fontVariantNumeric: "tabular-nums",
                          }}
                        >
                          #{i + 1}
                        </span>
                        <span
                          className="text-foreground flex-1 min-w-0 truncate"
                          style={typo.label}
                        >
                          {skill.name}
                        </span>
                        <span
                          className="text-muted-foreground shrink-0"
                          style={{
                            ...typo.caption,
                            fontVariantNumeric: "tabular-nums",
                          }}
                        >
                          {skill.uses.toLocaleString()} uses
                        </span>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
            </motion.div>
          </div>
        )}
      </ScrollArea>
    </div>
  );
}
