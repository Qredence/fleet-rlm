import { useState, useMemo, useCallback, useRef } from "react";
import { motion, useReducedMotion } from "motion/react";
import { Search, TriangleAlert } from "lucide-react";
import { toast } from "sonner";
import { useTelemetry } from "@/lib/telemetry/useTelemetry";
import { springs } from "@/lib/config/motion-config";
import { typo } from "@/lib/config/typo";
import type { Skill } from "@/lib/data/types";
import { SkillLibraryHeaderControls } from "@/features/skill-library/SkillLibraryHeaderControls";
import { PullToRefreshIndicator } from "@/features/skill-library/PullToRefreshIndicator";
import { SkillCard } from "@/features/skill-library/SkillCard";
import { useNavigation } from "@/hooks/useNavigation";
import { useSkills } from "@/hooks/useSkills";
import { useAppNavigate } from "@/hooks/useAppNavigate";
import { SkillCardSkeleton } from "@/components/shared/SkillCardSkeleton";
import { LargeTitleHeader } from "@/components/shared/LargeTitleHeader";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { cn } from "@/components/ui/utils";
import { useIsMobile } from "@/components/ui/use-mobile";
import {
  buildDomainCounts,
  domains,
  filterSkills,
  matchesSkillSearch,
  MAX_PULL,
  PULL_THRESHOLD,
  type SortKey,
  sortSkills,
} from "@/lib/skills/library";

export function SkillLibrary() {
  const { selectedSkillId, openCanvas } = useNavigation();
  const { navigateToSkill } = useAppNavigate();
  const isMobile = useIsMobile();
  const telemetry = useTelemetry();
  const {
    skills: allSkills,
    dataSource,
    degradedReason,
    isLoading: isSkillsLoading,
    refetch: refetchSkills,
  } = useSkills();
  const [search, setSearch] = useState("");
  const [activeDomain, setActiveDomain] = useState("All");
  const [sortKey, setSortKey] = useState<SortKey>("name-asc");
  const prefersReduced = useReducedMotion();

  // PostHog: Track search with debounce ref
  const searchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const handleSearchChange = useCallback(
    (value: string) => {
      setSearch(value);
      if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
      if (value.trim()) {
        searchTimerRef.current = setTimeout(() => {
          telemetry.capture("skill_search_performed", {
            search_term: value,
            results_count: allSkills.filter((s) => matchesSkillSearch(s, value))
              .length,
          });
        }, 500);
      }
    },
    [telemetry, allSkills],
  );

  // PostHog: Track domain filter changes
  const handleDomainChange = useCallback(
    (domain: string) => {
      setActiveDomain(domain);
      if (domain !== "All") {
        telemetry.capture("skill_filter_applied", { domain });
      }
    },
    [telemetry],
  );

  // PostHog: Track skill selection
  const handleSkillSelect = useCallback(
    (skill: Skill) => {
      telemetry.capture("skill_selected", {
        skill_id: skill.id,
        skill_name: skill.displayName,
        skill_domain: skill.domain,
      });
      navigateToSkill("skills", skill.id);
      openCanvas();
    },
    [telemetry, navigateToSkill, openCanvas],
  );

  // ── Pull-to-refresh state (mobile only) ──────────────────────────
  const [pullDistance, setPullDistance] = useState(0);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isPullingActive, setIsPullingActive] = useState(false);
  const touchStartY = useRef(0);
  const isPulling = useRef(false);
  const scrollAreaRef = useRef<HTMLDivElement>(null);

  // Use hook loading state (replaces setTimeout skeleton)
  const isLoading = isSkillsLoading;
  const isDegradedData = dataSource === "fallback";

  const domainCounts = useMemo(
    () => buildDomainCounts(allSkills, search),
    [allSkills, search],
  );

  const filtered = useMemo(
    () => filterSkills(allSkills, search, activeDomain),
    [allSkills, search, activeDomain],
  );

  const sorted = useMemo(() => {
    return sortSkills(filtered, sortKey);
  }, [filtered, sortKey]);

  // ── Pull-to-refresh touch handlers ───────────────────────────────
  const getScrollTop = useCallback(() => {
    if (!scrollAreaRef.current) return 0;
    const viewport = scrollAreaRef.current.querySelector(
      "[data-radix-scroll-area-viewport]",
    ) as HTMLElement | null;
    return viewport?.scrollTop ?? 0;
  }, []);

  const handleTouchStart = useCallback(
    (e: React.TouchEvent) => {
      if (!isMobile || isRefreshing) return;
      const touch = e.touches[0];
      if (!touch) return;
      if (getScrollTop() <= 0) {
        touchStartY.current = touch.clientY;
        isPulling.current = true;
        setIsPullingActive(true);
      }
    },
    [isMobile, isRefreshing, getScrollTop],
  );

  const handleTouchMove = useCallback(
    (e: React.TouchEvent) => {
      if (!isPulling.current || isRefreshing) return;
      const touch = e.touches[0];
      if (!touch) return;
      if (getScrollTop() > 0) {
        isPulling.current = false;
        setIsPullingActive(false);
        setPullDistance(0);
        return;
      }
      const dy = touch.clientY - touchStartY.current;
      if (dy > 0) {
        const damped = Math.min(dy * 0.45, MAX_PULL);
        setPullDistance(damped);
      } else {
        setPullDistance(0);
      }
    },
    [isRefreshing, getScrollTop],
  );

  const handleTouchEnd = useCallback(() => {
    if (!isPulling.current) return;
    isPulling.current = false;
    setIsPullingActive(false);

    if (pullDistance >= PULL_THRESHOLD * 0.45 && !isRefreshing) {
      setIsRefreshing(true);
      setPullDistance(40);
      toast("Refreshing skills\u2026", { duration: 1200 });

      refetchSkills();
      setTimeout(() => {
        setIsRefreshing(false);
        setPullDistance(0);
        toast.success("Skills refreshed");
      }, 1200);
    } else {
      setPullDistance(0);
    }
  }, [pullDistance, isRefreshing, refetchSkills]);

  const headerChildren = (
    <SkillLibraryHeaderControls
      search={search}
      onSearchChange={handleSearchChange}
      domains={domains}
      activeDomain={activeDomain}
      onDomainChange={handleDomainChange}
      domainCounts={domainCounts}
      sortKey={sortKey}
      onSortChange={setSortKey}
      isMobile={isMobile}
      prefersReduced={prefersReduced}
    />
  );

  return (
    <div
      className="flex flex-col h-full w-full bg-background overflow-hidden"
      onTouchStart={handleTouchStart}
      onTouchMove={handleTouchMove}
      onTouchEnd={handleTouchEnd}
    >
      {/* Desktop: static header outside scroll */}
      {!isMobile && (
        <LargeTitleHeader title="Skill Library" isMobile={false}>
          {headerChildren}
        </LargeTitleHeader>
      )}

      {isMobile && (
        <PullToRefreshIndicator
          pullDistance={pullDistance}
          isPullingActive={isPullingActive}
          isRefreshing={isRefreshing}
          prefersReduced={prefersReduced}
        />
      )}

      {/* Scrollable content */}
      <div
        ref={scrollAreaRef}
        className="flex-1 min-h-0 min-w-0 overflow-hidden"
      >
        <ScrollArea className="h-full">
          {/* Mobile: large-title header INSIDE scroll area for collapse behavior */}
          {isMobile && (
            <LargeTitleHeader title="Skill Library" isMobile>
              {headerChildren}
            </LargeTitleHeader>
          )}

          <div
            className={cn(
              "w-full min-w-0 mx-auto",
              isMobile ? "px-4 py-4" : "px-6 py-6 max-w-200",
            )}
          >
            {isDegradedData && (
              <Alert className="mb-4">
                <TriangleAlert className="text-muted-foreground" />
                <AlertTitle style={typo.label}>
                  Skills API unavailable
                </AlertTitle>
                <AlertDescription style={typo.caption}>
                  {degradedReason ??
                    "Showing local mock data so this page stays usable while backend endpoints are unavailable."}
                </AlertDescription>
              </Alert>
            )}

            {/* Skeleton loading state */}
            {isLoading ? (
              <div className="flex flex-col gap-3 md:gap-4">
                {Array.from({ length: 6 }).map((_, i) => (
                  <SkillCardSkeleton key={i} />
                ))}
              </div>
            ) : (
              <>
                <div className="flex flex-col gap-3 md:gap-4">
                  {sorted.map((skill, i) => (
                    <motion.div
                      key={skill.id}
                      initial={{
                        opacity: 0,
                        y: prefersReduced ? 0 : 6,
                      }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={
                        prefersReduced
                          ? springs.instant
                          : {
                              ...springs.default,
                              delay: i * 0.02,
                            }
                      }
                    >
                      <SkillCard
                        skill={skill}
                        isSelected={selectedSkillId === skill.id}
                        onSelect={() => handleSkillSelect(skill)}
                      />
                    </motion.div>
                  ))}
                </div>

                {sorted.length === 0 && (
                  <div className="flex flex-col items-center justify-center py-16 text-center">
                    <Search className="w-8 h-8 text-muted-foreground mb-3" />
                    <p className="text-muted-foreground" style={typo.label}>
                      No skills found
                    </p>
                    <p className="text-muted-foreground" style={typo.caption}>
                      Try adjusting your search or filters
                    </p>
                  </div>
                )}
              </>
            )}
          </div>
        </ScrollArea>
      </div>

      {/* Footer */}
      <div className="px-4 md:px-6 py-3 border-t border-border-subtle shrink-0">
        <span className="text-muted-foreground" style={typo.helper}>
          {isLoading
            ? "\u2026"
            : `${sorted.length} of ${allSkills.length} skills`}
        </span>
      </div>
    </div>
  );
}
