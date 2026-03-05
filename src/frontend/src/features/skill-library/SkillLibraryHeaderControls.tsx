import { AnimatePresence, LayoutGroup, motion } from "motion/react";
import { ArrowUpDown, Search } from "lucide-react";
import { springs } from "@/lib/config/motion-config";
import { typo } from "@/lib/config/typo";
import { AnimatedIndicator } from "@/components/ui/animated-indicator";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils/cn";
import { type SortKey, sortOptions } from "@/lib/skills/library";

export function SkillLibraryHeaderControls({
  search,
  onSearchChange,
  domains,
  activeDomain,
  onDomainChange,
  domainCounts,
  sortKey,
  onSortChange,
  isMobile,
  prefersReduced,
}: {
  search: string;
  onSearchChange: (value: string) => void;
  domains: string[];
  activeDomain: string;
  onDomainChange: (domain: string) => void;
  domainCounts: Record<string, number>;
  sortKey: SortKey;
  onSortChange: (next: SortKey) => void;
  isMobile: boolean;
  prefersReduced: boolean | null;
}) {
  return (
    <>
      <div className={cn("relative mb-3", isMobile && "mx-4")}>
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground pointer-events-none" />
        <Input
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          placeholder="Search skills…"
          aria-label="Search skills"
          className={cn("pl-9", isMobile && "touch-target")}
          style={typo.label}
        />
      </div>

      <LayoutGroup id="domainFilters">
        <div
          className={cn(
            "flex items-center gap-1 overflow-x-auto no-scrollbar",
            isMobile && "px-4",
          )}
        >
          {domains.map((domain) => {
            const isActive = activeDomain === domain;
            const count = domainCounts[domain] || 0;
            return (
              <button
                key={domain}
                onClick={() => onDomainChange(domain)}
                className={cn(
                  "relative flex items-center justify-center px-3 gap-1.5 shrink-0 rounded-lg transition-colors",
                  isMobile ? "touch-target" : "h-8",
                  isActive
                    ? "text-foreground"
                    : "text-muted-foreground hover:text-foreground hover:bg-muted",
                )}
              >
                <span className="relative z-10" style={typo.helper}>
                  {domain === "All"
                    ? "All"
                    : domain.charAt(0).toUpperCase() + domain.slice(1)}
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

          <div className="ml-auto shrink-0">
            <Select
              value={sortKey}
              onValueChange={(value) => onSortChange(value as SortKey)}
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
}
