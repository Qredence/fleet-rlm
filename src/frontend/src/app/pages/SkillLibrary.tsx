import { typo } from "../components/config/typo";
import { springs } from "../components/config/motion-config";
import { useState, useMemo, useCallback, useRef } from "react";
import {
  motion,
  LayoutGroup,
  AnimatePresence,
  useReducedMotion,
} from "motion/react";
import { Search, RefreshCw, ArrowUpDown, TriangleAlert } from "lucide-react";
import { toast } from "sonner";
import { usePostHog } from "@posthog/react";
import { useSkills } from "../components/hooks/useSkills";
import type { Skill } from "../components/data/types";
import { useNavigation } from "../components/hooks/useNavigation";
import { useAppNavigate } from "../components/hooks/useAppNavigate";
import { useIsMobile } from "../components/ui/use-mobile";
import { SkillCard } from "../components/features/SkillCard";
import { SkillCardSkeleton } from "../components/shared/SkillCardSkeleton";
import { LargeTitleHeader } from "../components/shared/LargeTitleHeader";
import { AnimatedIndicator } from "../components/ui/animated-indicator";
import { Input } from "../components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../components/ui/select";
import { ScrollArea } from "../components/ui/scroll-area";
import { Alert, AlertDescription, AlertTitle } from "../components/ui/alert";
import { cn } from "../components/ui/utils";

const domains = ["All", "analytics", "development", "nlp", "devops"];

// ── Sort definitions ──────────────────────────────────────────────────
type SortKey =
  | "name-asc"
  | "name-desc"
  | "quality-desc"
  | "usage-desc"
  | "last-used"
  | "created";

const sortOptions: { key: SortKey; label: string }[] = [
  { key: "name-asc", label: "Name (A\u2013Z)" },
  { key: "name-desc", label: "Name (Z\u2013A)" },
  { key: "quality-desc", label: "Quality (High\u2013Low)" },
  { key: "usage-desc", label: "Most Used" },
  { key: "last-used", label: "Recently Used" },
  { key: "created", label: "Newest First" },
];

function sortSkills(skills: Skill[], key: SortKey): Skill[] {
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

// Pull-to-refresh config
const PULL_THRESHOLD = 80; // px to trigger refresh
const MAX_PULL = 120; // max visual displacement

/**
 * SkillLibrary — browsable, filterable skill catalogue.
 *
 * All shared state consumed from NavigationContext — zero props.
 */
export function SkillLibrary() {
  const { selectedSkillId, openCanvas } = useNavigation();
  const { navigateToSkill } = useAppNavigate();
  const isMobile = useIsMobile();
  const posthog = usePostHog();
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
          posthog?.capture("skill_search_performed", {
            search_term: value,
            results_count: allSkills.filter(
              (s) =>
                s.displayName.toLowerCase().includes(value.toLowerCase()) ||
                s.tags.some((t) => t.includes(value.toLowerCase())),
            ).length,
          });
        }, 500);
      }
    },
    [posthog, allSkills],
  );

  // PostHog: Track domain filter changes
  const handleDomainChange = useCallback(
    (domain: string) => {
      setActiveDomain(domain);
      if (domain !== "All") {
        posthog?.capture("skill_filter_applied", { domain });
      }
    },
    [posthog],
  );

  // PostHog: Track skill selection
  const handleSkillSelect = useCallback(
    (skill: Skill) => {
      posthog?.capture("skill_selected", {
        skill_id: skill.id,
        skill_name: skill.displayName,
        skill_domain: skill.domain,
      });
      navigateToSkill("skills", skill.id);
      openCanvas();
    },
    [posthog, navigateToSkill, openCanvas],
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

  const domainCounts = useMemo(() => {
    const searchMatched = allSkills.filter((s) => {
      return (
        !search ||
        s.displayName.toLowerCase().includes(search.toLowerCase()) ||
        s.tags.some((t) => t.includes(search.toLowerCase()))
      );
    });
    const counts: Record<string, number> = {
      All: searchMatched.length,
    };
    for (const s of searchMatched) {
      counts[s.domain] = (counts[s.domain] || 0) + 1;
    }
    return counts;
  }, [search, allSkills]);

  const filtered = useMemo(() => {
    return allSkills.filter((s) => {
      const matchSearch =
        !search ||
        s.displayName.toLowerCase().includes(search.toLowerCase()) ||
        s.tags.some((t) => t.includes(search.toLowerCase()));
      const matchDomain = activeDomain === "All" || s.domain === activeDomain;
      return matchSearch && matchDomain;
    });
  }, [search, activeDomain, allSkills]);

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

  /* ── Search + domain filters ─────────────────────────────────────── */
  const headerChildren = (
    <>
      {/* Search — min 44px touch target on mobile */}
      <div className={cn("relative mb-3", isMobile && "mx-4")}>
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground pointer-events-none" />
        <Input
          value={search}
          onChange={(e) => handleSearchChange(e.target.value)}
          placeholder="Search skills\u2026"
          aria-label="Search skills"
          className={cn("pl-9", isMobile && "touch-target")}
          style={typo.label}
        />
      </div>

      {/* Domain filters — Apple HIG: 44px min touch height on mobile */}
      <LayoutGroup id="domainFilters">
        <div
          className={cn(
            "flex items-center gap-1 overflow-x-auto no-scrollbar",
            isMobile && "px-4",
          )}
        >
          {domains.map((d) => {
            const isActive = activeDomain === d;
            const count = domainCounts[d] || 0;
            return (
              <button
                key={d}
                onClick={() => handleDomainChange(d)}
                className={cn(
                  "relative flex items-center justify-center px-3 gap-1.5 shrink-0 rounded-lg transition-colors",
                  isMobile ? "touch-target" : "h-8",
                  isActive
                    ? "text-foreground"
                    : "text-muted-foreground hover:text-foreground hover:bg-muted",
                )}
              >
                <span className="relative z-10" style={typo.helper}>
                  {d === "All" ? "All" : d.charAt(0).toUpperCase() + d.slice(1)}
                </span>
                <span
                  className={cn(
                    "relative z-10 flex items-center justify-center min-w-[18px] h-[18px] px-1 rounded-full overflow-hidden",
                    isActive ? "bg-foreground/10" : "bg-muted",
                  )}
                  style={typo.micro}
                >
                  <AnimatePresence mode="popLayout" initial={false}>
                    <motion.span
                      key={count}
                      initial={{
                        y: prefersReduced ? 0 : 8,
                        opacity: 0,
                      }}
                      animate={{ y: 0, opacity: 1 }}
                      exit={{
                        y: prefersReduced ? 0 : -8,
                        opacity: 0,
                      }}
                      transition={
                        prefersReduced ? springs.instant : springs.snappy
                      }
                    >
                      {count}
                    </motion.span>
                  </AnimatePresence>
                </span>
                {isActive && <AnimatedIndicator layoutId="domainFilter" />}
              </button>
            );
          })}

          {/* Sort — inline with domain filters, pushed to the end */}
          <div className="ml-auto shrink-0">
            <Select
              value={sortKey}
              onValueChange={(value) => setSortKey(value as SortKey)}
            >
              <SelectTrigger
                className={cn(
                  "w-auto gap-1.5 border-0 bg-transparent shadow-none rounded-lg transition-colors",
                  isMobile ? "touch-target" : "h-8",
                  "text-muted-foreground hover:text-foreground hover:bg-muted",
                )}
                aria-label="Sort skills"
              >
                <ArrowUpDown className="size-3.5 shrink-0" />
                <SelectValue placeholder="Sort" />
              </SelectTrigger>
              <SelectContent align="end" className="w-48">
                {sortOptions.map((option) => (
                  <SelectItem key={option.key} value={option.key}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
      </LayoutGroup>
    </>
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

      {/* Pull-to-refresh indicator (mobile only) */}
      {isMobile && pullDistance > 0 && (
        <div
          className="flex items-center justify-center shrink-0 overflow-hidden"
          style={{
            height: pullDistance,
            transition: isPullingActive
              ? "none"
              : "height 0.3s cubic-bezier(0.4, 0, 0.2, 1)",
          }}
        >
          <motion.div
            animate={{
              rotate: isRefreshing
                ? 360
                : (pullDistance / (MAX_PULL * 0.45)) * 270,
            }}
            transition={
              isRefreshing
                ? {
                    repeat: Infinity,
                    duration: prefersReduced ? 0.01 : 0.8,
                    ease: "linear",
                  }
                : prefersReduced
                  ? springs.instant
                  : springs.default
            }
          >
            <RefreshCw
              className="w-5 h-5 text-muted-foreground"
              style={{
                opacity: Math.min(pullDistance / (PULL_THRESHOLD * 0.45), 1),
              }}
            />
          </motion.div>
        </div>
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
              isMobile ? "px-4 py-4" : "px-6 py-6 max-w-[800px]",
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
