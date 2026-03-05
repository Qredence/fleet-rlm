import { motion } from "motion/react";
import { MinusSquare, Pin, CheckSquare, Trash2, X } from "lucide-react";
import { springs } from "@/lib/config/motion-config";
import { typo } from "@/lib/config/typo";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils/cn";

export interface BulkActionToolbarProps {
  selectedCount: number;
  totalCount: number;
  onSelectAll: () => void;
  onDeselectAll: () => void;
  onBulkPin: () => void;
  onBulkUnpin: () => void;
  onBulkDelete: () => void;
  onCancel: () => void;
  hasUnpinnedSelected: boolean;
  hasPinnedSelected: boolean;
  isMobile?: boolean;
  reduced?: boolean | null;
}

export function BulkActionToolbar({
  selectedCount,
  totalCount,
  onSelectAll,
  onDeselectAll,
  onBulkPin,
  onBulkUnpin,
  onBulkDelete,
  onCancel,
  hasUnpinnedSelected,
  hasPinnedSelected,
  isMobile,
  reduced,
}: BulkActionToolbarProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: reduced ? 0 : 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: reduced ? 0 : 8 }}
      transition={reduced ? springs.instant : springs.default}
      className={cn(
        "sticky bottom-0 z-10 border-t border-border-subtle",
        "bg-card backdrop-blur-sm",
      )}
    >
      <div
        className={cn(
          "flex items-center gap-2 max-w-[800px] w-full mx-auto",
          isMobile ? "px-4 py-3" : "px-6 py-3",
        )}
      >
        <Button
          variant="ghost"
          className={cn(
            "h-8 gap-1.5 px-2 shrink-0",
            isMobile && "touch-target",
          )}
          onClick={selectedCount === totalCount ? onDeselectAll : onSelectAll}
          aria-label={
            selectedCount === totalCount ? "Deselect all" : "Select all"
          }
        >
          {selectedCount === totalCount ? (
            <MinusSquare className="w-4 h-4 text-muted-foreground" />
          ) : (
            <CheckSquare className="w-4 h-4 text-muted-foreground" />
          )}
          <span
            className="text-muted-foreground hidden sm:inline"
            style={typo.helper}
          >
            {selectedCount === totalCount ? "Deselect all" : "Select all"}
          </span>
        </Button>

        <span className="text-foreground shrink-0" style={typo.label}>
          {selectedCount} selected
        </span>

        <div className="flex-1" />

        {hasUnpinnedSelected && (
          <Button
            variant="secondary"
            className={cn(
              "h-8 gap-1.5 px-3 rounded-button",
              isMobile && "touch-target",
            )}
            onClick={onBulkPin}
          >
            <Pin className="w-3.5 h-3.5" />
            <span style={typo.helper}>Pin</span>
          </Button>
        )}
        {hasPinnedSelected && (
          <Button
            variant="secondary"
            className={cn(
              "h-8 gap-1.5 px-3 rounded-button",
              isMobile && "touch-target",
            )}
            onClick={onBulkUnpin}
          >
            <Pin className="w-3.5 h-3.5 text-muted-foreground" />
            <span style={typo.helper}>Unpin</span>
          </Button>
        )}
        <Button
          variant="destructive-ghost"
          className={cn(
            "h-8 gap-1.5 px-3 rounded-button",
            isMobile && "touch-target",
          )}
          onClick={onBulkDelete}
        >
          <Trash2 className="w-3.5 h-3.5" />
          <span style={typo.helper}>Delete</span>
        </Button>
        <Button
          variant="ghost"
          className={cn("h-8 w-8 p-0 shrink-0", isMobile && "touch-target")}
          onClick={onCancel}
          aria-label="Exit selection mode"
        >
          <X className="w-4 h-4 text-muted-foreground" />
        </Button>
      </div>
    </motion.div>
  );
}
