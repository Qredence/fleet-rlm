import { Brain, Plus } from "lucide-react";
import { typo } from "@/lib/config/typo";
import { Button } from "@/components/ui/button";
import { cn } from "@/components/ui/utils";

export interface MemoryPageEmptyStateProps {
  search: string;
  activeFilterCount: number;
  selectionMode: boolean;
  isMobile: boolean;
  onAddFirstMemory: () => void;
}

export function MemoryPageEmptyState({
  search,
  activeFilterCount,
  selectionMode,
  isMobile,
  onAddFirstMemory,
}: MemoryPageEmptyStateProps) {
  const hasSearchOrFilters = Boolean(search) || activeFilterCount > 0;

  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <div className="w-12 h-12 rounded-lg bg-muted flex items-center justify-center mb-4">
        <Brain className="w-6 h-6 text-muted-foreground" />
      </div>
      <p className="text-foreground mb-1" style={typo.label}>
        No memories found
      </p>
      <p className="text-muted-foreground mb-4" style={typo.caption}>
        {hasSearchOrFilters
          ? "Try adjusting your search or filters"
          : "Memories will appear as you interact with the assistant"}
      </p>
      {!hasSearchOrFilters && !selectionMode && (
        <Button
          variant="secondary"
          className={cn("gap-1.5 rounded-button", isMobile && "touch-target")}
          onClick={onAddFirstMemory}
        >
          <Plus className="w-4 h-4" />
          <span style={typo.label}>Add your first memory</span>
        </Button>
      )}
    </div>
  );
}
