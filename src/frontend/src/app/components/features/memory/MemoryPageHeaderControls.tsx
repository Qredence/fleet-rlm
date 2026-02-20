import { Filter, Plus, Search, Square, X } from "lucide-react";
import { typo } from "../../config/typo";
import type { MemoryType } from "../../data/types";
import { ALL_TYPES, TYPE_META } from "../../../lib/memory/metadata";
import { Button } from "../../ui/button";
import { Input } from "../../ui/input";
import { cn } from "../../ui/utils";

export interface MemoryHeaderStats {
  total: number;
  pinned: number;
  size: string;
  types: Map<MemoryType, number>;
}

export interface MemoryPageHeaderControlsProps {
  isMobile: boolean;
  stats: MemoryHeaderStats;
  search: string;
  onSearchChange: (value: string) => void;
  selectionMode: boolean;
  entriesCount: number;
  activeFilters: Set<MemoryType>;
  onToggleFilter: (type: MemoryType) => void;
  onClearFilters: () => void;
  onEnterSelectionMode: () => void;
  onToggleNewForm: () => void;
  onExitSelectionMode: () => void;
}

export function MemoryPageHeaderControls({
  isMobile,
  stats,
  search,
  onSearchChange,
  selectionMode,
  entriesCount,
  activeFilters,
  onToggleFilter,
  onClearFilters,
  onEnterSelectionMode,
  onToggleNewForm,
  onExitSelectionMode,
}: MemoryPageHeaderControlsProps) {
  return (
    <div className={cn(isMobile && "px-4")}>
      <div className="flex items-center gap-3 mb-3">
        <span className="text-muted-foreground" style={typo.helper}>
          {stats.total} entries
        </span>
        <span className="text-border">&middot;</span>
        <span className="text-muted-foreground" style={typo.helper}>
          {stats.pinned} pinned
        </span>
        <span className="text-border">&middot;</span>
        <span className="text-muted-foreground" style={typo.helper}>
          {stats.size}
        </span>
      </div>

      <div className="flex items-center gap-2 mb-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground pointer-events-none" />
          <Input
            value={search}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder="Search memories…"
            aria-label="Search memories"
            className={cn(
              "pl-9 border-transparent bg-muted dark:bg-muted hover:bg-secondary/80 focus-visible:bg-background focus-visible:border-ring",
              isMobile && "touch-target",
            )}
            style={typo.label}
          />
        </div>
        {!selectionMode && entriesCount > 0 && (
          <Button
            variant="secondary"
            className={cn("gap-1.5 rounded-button shrink-0", isMobile && "touch-target")}
            onClick={onEnterSelectionMode}
            aria-label="Enter selection mode"
          >
            <Square className="w-4 h-4" />
            {!isMobile && <span style={typo.label}>Select</span>}
          </Button>
        )}
        {!selectionMode && (
          <Button
            variant="default"
            className={cn("gap-1.5 rounded-button shrink-0", isMobile && "touch-target")}
            onClick={onToggleNewForm}
          >
            <Plus className="w-4 h-4" />
            {!isMobile && <span style={typo.label}>New</span>}
          </Button>
        )}
        {selectionMode && (
          <Button
            variant="ghost"
            className={cn("gap-1.5 rounded-button shrink-0", isMobile && "touch-target")}
            onClick={onExitSelectionMode}
          >
            <X className="w-4 h-4 text-muted-foreground" />
            <span className="text-muted-foreground" style={typo.label}>
              Cancel
            </span>
          </Button>
        )}
      </div>

      <div className="flex items-center gap-1.5 flex-wrap">
        <Filter className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
        {ALL_TYPES.map((type) => {
          const meta = TYPE_META[type];
          const isActive = activeFilters.has(type);
          const count = stats.types.get(type) ?? 0;
          return (
            <Button
              key={type}
              variant={isActive ? "secondary" : "ghost"}
              className={cn(
                "h-7 gap-1 px-2 rounded-full",
                isActive && "bg-accent/10 text-accent border border-accent/20",
                isMobile && "min-h-[36px]",
              )}
              onClick={() => onToggleFilter(type)}
            >
              <span style={typo.helper}>{meta.label}</span>
              <span className="text-muted-foreground" style={typo.micro}>
                {count}
              </span>
            </Button>
          );
        })}
        {activeFilters.size > 0 && (
          <Button
            variant="ghost"
            className={cn("h-7 px-2 gap-1 rounded-full", isMobile && "min-h-[36px]")}
            onClick={onClearFilters}
          >
            <X className="w-3 h-3 text-muted-foreground" />
            <span className="text-muted-foreground" style={typo.helper}>
              Clear
            </span>
          </Button>
        )}
      </div>
    </div>
  );
}
