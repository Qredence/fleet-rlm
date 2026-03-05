import { motion } from "motion/react";
import { Pin, Trash2 } from "lucide-react";
import { springs } from "@/lib/config/motion-config";
import { typo } from "@/lib/config/typo";
import type { MemoryEntry } from "@/lib/data/types";
import { TYPE_META } from "@/lib/memory/metadata";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Progress } from "@/components/ui/progress";
import { SelectableCard } from "@/components/ui/selectable-card";
import { cn } from "@/lib/utils/cn";

const springIn = (delay: number, reduced?: boolean | null) => ({
  initial: { opacity: 0, y: reduced ? 0 : 6 } as const,
  animate: { opacity: 1, y: 0 } as const,
  transition: reduced ? springs.instant : { ...springs.default, delay },
});

function formatDate(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const diffHrs = diffMs / (1000 * 60 * 60);

  if (diffHrs < 1) return "Just now";
  if (diffHrs < 24) return `${Math.floor(diffHrs)}h ago`;
  if (diffHrs < 48) return "Yesterday";
  return d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
  });
}

export interface MemoryEntryCardProps {
  entry: MemoryEntry;
  onPin: (id: string) => void;
  onDelete: (id: string) => void;
  delay: number;
  reduced?: boolean | null;
  isMobile?: boolean;
  selectionMode: boolean;
  isSelected: boolean;
  onToggleSelect: (id: string) => void;
}

export function MemoryEntryCard({
  entry,
  onPin,
  onDelete,
  delay,
  reduced,
  isMobile,
  selectionMode,
  isSelected,
  onToggleSelect,
}: MemoryEntryCardProps) {
  const meta = TYPE_META[entry.type];
  const Icon = meta.icon;

  return (
    <motion.div {...springIn(delay, reduced)} layout>
      <SelectableCard
        selectable={selectionMode}
        selected={isSelected}
        onSelect={() => onToggleSelect(entry.id)}
        highlighted={entry.pinned}
      >
        <CardContent className={cn("p-4", isMobile && "p-3")}>
          <div className="flex items-start gap-3">
            {selectionMode && (
              <div
                className={cn(
                  "flex items-center justify-center shrink-0 pt-0.5",
                  isMobile && "touch-target flex items-center justify-center",
                )}
                onClick={(e) => {
                  e.stopPropagation();
                  onToggleSelect(entry.id);
                }}
              >
                <Checkbox
                  checked={isSelected}
                  onCheckedChange={() => onToggleSelect(entry.id)}
                  aria-label={`Select ${entry.content.slice(0, 30)}`}
                />
              </div>
            )}

            <div
              className={cn(
                "w-8 h-8 rounded-lg flex items-center justify-center shrink-0",
                "bg-muted",
              )}
            >
              <Icon className={cn("w-4 h-4", meta.color)} />
            </div>

            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-1.5">
                <Badge variant="secondary" className="rounded-full">
                  <span style={typo.micro}>{meta.label}</span>
                </Badge>
                {entry.pinned && (
                  <Pin className="w-3 h-3 text-accent fill-accent" />
                )}
                <span
                  className="text-muted-foreground ml-auto shrink-0"
                  style={typo.helper}
                >
                  {formatDate(entry.createdAt)}
                </span>
              </div>

              <p className="text-foreground mb-2" style={typo.caption}>
                {entry.content}
              </p>

              {entry.tags.length > 0 && (
                <div className="flex flex-wrap gap-1 mb-2">
                  {entry.tags.map((tag) => (
                    <span
                      key={tag}
                      className="inline-flex px-1.5 py-0.5 rounded bg-muted text-muted-foreground"
                      style={typo.micro}
                    >
                      {tag}
                    </span>
                  ))}
                </div>
              )}

              <div className="flex items-center gap-3">
                <span
                  className="text-muted-foreground truncate"
                  style={typo.helper}
                >
                  {entry.source}
                </span>

                <div className="flex items-center gap-1.5 ml-auto shrink-0">
                  <div className="flex items-center gap-1">
                    <Progress value={entry.relevance} className="w-10 h-1" />
                    <span
                      className="text-muted-foreground"
                      style={{
                        ...typo.micro,
                        fontVariantNumeric: "tabular-nums",
                      }}
                    >
                      {entry.relevance}
                    </span>
                  </div>

                  {!selectionMode && (
                    <div
                      className={cn(
                        "flex items-center gap-0.5",
                        !isMobile &&
                          "opacity-0 group-hover:opacity-100 transition-opacity",
                      )}
                    >
                      <Button
                        variant="ghost"
                        className={cn(
                          "h-7 w-7 p-0",
                          isMobile && "touch-target",
                        )}
                        onClick={() => onPin(entry.id)}
                        aria-label={entry.pinned ? "Unpin" : "Pin"}
                      >
                        <Pin
                          className={cn(
                            "w-3 h-3",
                            entry.pinned
                              ? "text-accent fill-accent"
                              : "text-muted-foreground",
                          )}
                        />
                      </Button>
                      <Button
                        variant="ghost"
                        className={cn(
                          "h-7 w-7 p-0",
                          isMobile && "touch-target",
                        )}
                        onClick={() => onDelete(entry.id)}
                        aria-label="Delete memory"
                      >
                        <Trash2 className="w-3 h-3 text-muted-foreground" />
                      </Button>
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>
        </CardContent>
      </SelectableCard>
    </motion.div>
  );
}
