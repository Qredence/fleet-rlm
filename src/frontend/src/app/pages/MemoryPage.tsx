/**
 * MemoryPage — agent memory browser and manager.
 *
 * Displays all stored memory entries (facts, preferences, directives,
 * session history, knowledge) with filtering by type, search, and
 * pinned-first sorting. Each entry shows relevance score, source,
 * tags, and timestamp.
 *
 * Supports multi-select mode with bulk pin and bulk delete actions.
 *
 * Data is fetched via the `useMemory()` hook (React Query) which
 * falls back to mock data when no backend is configured.
 *
 * Uses `useIsMobile()` hook — zero props.
 */
import { useState, useMemo, useCallback } from "react";
import { motion, AnimatePresence, useReducedMotion } from "motion/react";
import {
  Search,
  Pin,
  Brain,
  BookOpen,
  MessageSquare,
  Compass,
  Lightbulb,
  Filter,
  Trash2,
  Plus,
  X,
  Check,
  CheckSquare,
  Square,
  MinusSquare,
  TriangleAlert,
} from "lucide-react";
import { toast } from "sonner";
import { typo } from "../components/config/typo";
import { springs } from "../components/config/motion-config";
import { useMemory } from "../components/hooks/useMemory";
import type { MemoryEntry, MemoryType } from "../components/data/types";
import { LargeTitleHeader } from "../components/shared/LargeTitleHeader";
import { Button } from "../components/ui/button";
import { Badge } from "../components/ui/badge";
import { Input } from "../components/ui/input";
import { Card, CardContent } from "../components/ui/card";
import { Progress } from "../components/ui/progress";
import { ScrollArea } from "../components/ui/scroll-area";
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from "../components/ui/select";
import { Checkbox } from "../components/ui/checkbox";
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogFooter,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogAction,
  AlertDialogCancel,
} from "../components/ui/alert-dialog";
import { Alert, AlertDescription, AlertTitle } from "../components/ui/alert";
import { cn } from "../components/ui/utils";
import { useIsMobile } from "../components/ui/use-mobile";
import { SelectableCard } from "../components/ui/selectable-card";

// ── Type metadata ───────────────────────────────────────────────────

const TYPE_META: Record<
  MemoryType,
  { label: string; icon: typeof Brain; color: string }
> = {
  fact: {
    label: "Fact",
    icon: Lightbulb,
    color: "text-chart-1",
  },
  preference: {
    label: "Preference",
    icon: Compass,
    color: "text-chart-2",
  },
  session: {
    label: "Session",
    icon: MessageSquare,
    color: "text-chart-3",
  },
  knowledge: {
    label: "Knowledge",
    icon: BookOpen,
    color: "text-chart-4",
  },
  directive: {
    label: "Directive",
    icon: Brain,
    color: "text-chart-5",
  },
};

const ALL_TYPES: MemoryType[] = [
  "fact",
  "preference",
  "session",
  "knowledge",
  "directive",
];

// ── Creatable types (exclude 'session' — those are system-generated) ─

const CREATABLE_TYPES: MemoryType[] = [
  "fact",
  "preference",
  "knowledge",
  "directive",
];

// ── Spring animation ────────────────────────────────────────────────

const springIn = (delay: number, reduced?: boolean | null) => ({
  initial: { opacity: 0, y: reduced ? 0 : 6 } as const,
  animate: { opacity: 1, y: 0 } as const,
  transition: reduced ? springs.instant : { ...springs.default, delay },
});

// ── Helpers ─────────────────────────────────────────────────────────

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

function formatSize(entries: { content: string }[]): string {
  const chars = entries.reduce((a, e) => a + e.content.length, 0);
  if (chars < 1000) return `${chars} chars`;
  return `${(chars / 1000).toFixed(1)}K chars`;
}

// ── Memory Entry Card ───────────────────────────────────────────────

function MemoryEntryCard({
  entry,
  onPin,
  onDelete,
  delay,
  reduced,
  isMobile,
  selectionMode,
  isSelected,
  onToggleSelect,
}: {
  entry: MemoryEntry;
  onPin: (id: string) => void;
  onDelete: (id: string) => void;
  delay: number;
  reduced?: boolean | null;
  isMobile?: boolean;
  selectionMode: boolean;
  isSelected: boolean;
  onToggleSelect: (id: string) => void;
}) {
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
          {/* Header */}
          <div className="flex items-start gap-3">
            {/* Selection checkbox */}
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
              {/* Type badge + pin */}
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

              {/* Content */}
              <p className="text-foreground mb-2" style={typo.caption}>
                {entry.content}
              </p>

              {/* Tags */}
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

              {/* Footer: source + relevance + actions */}
              <div className="flex items-center gap-3">
                <span
                  className="text-muted-foreground truncate"
                  style={typo.helper}
                >
                  {entry.source}
                </span>

                <div className="flex items-center gap-1.5 ml-auto shrink-0">
                  {/* Relevance */}
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

                  {/* Actions (visible on hover / always on mobile, hidden in selection mode) */}
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

// ── New Memory Inline Form ──────────────────────────────────────────

function NewMemoryForm({
  onSubmit,
  onCancel,
  isMobile,
  reduced,
}: {
  onSubmit: (data: {
    type: MemoryType;
    content: string;
    tags: string[];
  }) => void;
  onCancel: () => void;
  isMobile?: boolean;
  reduced?: boolean | null;
}) {
  const [type, setType] = useState<MemoryType>("fact");
  const [content, setContent] = useState("");
  const [tagsStr, setTagsStr] = useState("");

  const handleSubmit = () => {
    if (!content.trim()) {
      toast.error("Content is required");
      return;
    }
    const tags = tagsStr
      .split(",")
      .map((t) => t.trim().toLowerCase())
      .filter(Boolean);
    onSubmit({ type, content: content.trim(), tags });
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: reduced ? 0 : -8, height: 0 }}
      animate={{ opacity: 1, y: 0, height: "auto" }}
      exit={{ opacity: 0, y: reduced ? 0 : -8, height: 0 }}
      transition={reduced ? springs.instant : springs.default}
      className="overflow-hidden"
    >
      <Card className="border-accent/30 bg-accent/[0.02]">
        <CardContent className={cn("p-4 space-y-3", isMobile && "p-3")}>
          <div className="flex items-center justify-between">
            <span className="text-foreground" style={typo.label}>
              New Memory Entry
            </span>
            <Button
              variant="ghost"
              className={cn("h-7 w-7 p-0", isMobile && "touch-target")}
              onClick={onCancel}
              aria-label="Cancel"
            >
              <X className="w-4 h-4 text-muted-foreground" />
            </Button>
          </div>

          {/* Type selector */}
          <div>
            <label
              className="text-muted-foreground mb-1.5 block"
              style={typo.helper}
            >
              Type
            </label>
            <Select
              value={type}
              onValueChange={(v) => setType(v as MemoryType)}
            >
              <SelectTrigger
                className={cn("w-full", isMobile && "touch-target")}
                style={typo.label}
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {CREATABLE_TYPES.map((t) => {
                  const meta = TYPE_META[t];
                  const MIcon = meta.icon;
                  return (
                    <SelectItem key={t} value={t}>
                      <div className="flex items-center gap-2">
                        <MIcon className={cn("w-3.5 h-3.5", meta.color)} />
                        <span>{meta.label}</span>
                      </div>
                    </SelectItem>
                  );
                })}
              </SelectContent>
            </Select>
          </div>

          {/* Content */}
          <div>
            <label
              className="text-muted-foreground mb-1.5 block"
              style={typo.helper}
            >
              Content
            </label>
            <textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              placeholder="What should the agent remember?"
              rows={3}
              className={cn(
                "w-full resize-none rounded-lg border border-border-subtle bg-background p-3 text-foreground",
                "placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring",
                isMobile && "min-h-[88px]",
              )}
              style={{
                fontFamily: "var(--font-family)",
                fontSize: "var(--text-caption)",
                fontWeight: "var(--font-weight-regular)",
                lineHeight: "1.5",
              }}
              autoFocus
            />
          </div>

          {/* Tags */}
          <div>
            <label
              className="text-muted-foreground mb-1.5 block"
              style={typo.helper}
            >
              Tags{" "}
              <span className="text-muted-foreground/60">
                (comma-separated)
              </span>
            </label>
            <Input
              value={tagsStr}
              onChange={(e) => setTagsStr(e.target.value)}
              placeholder="testing, policy, preference"
              className={cn(isMobile && "touch-target")}
              style={typo.caption}
            />
          </div>

          {/* Actions */}
          <div className="flex items-center gap-2 pt-1">
            <Button
              variant="default"
              className={cn(
                "gap-1.5 rounded-button",
                isMobile && "touch-target",
              )}
              onClick={handleSubmit}
              disabled={!content.trim()}
            >
              <Check className="w-4 h-4" />
              <span style={typo.label}>Save</span>
            </Button>
            <Button
              variant="ghost"
              className={cn("rounded-button", isMobile && "touch-target")}
              onClick={onCancel}
            >
              <span style={typo.label}>Cancel</span>
            </Button>
          </div>
        </CardContent>
      </Card>
    </motion.div>
  );
}

// ── Bulk Action Toolbar ─────────────────────────────────────────────

function BulkActionToolbar({
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
}: {
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
}) {
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

// ── Main Component ──────────────────────────────────────────────────

export function MemoryPage() {
  const isMobile = useIsMobile();
  const prefersReduced = useReducedMotion();

  const {
    entries,
    dataSource,
    degradedReason,
    isLoading,
    create,
    remove,
    togglePin,
    bulkPin,
    bulkRemove,
  } = useMemory();

  const [search, setSearch] = useState("");
  const [activeFilters, setActiveFilters] = useState<Set<MemoryType>>(
    new Set(),
  );
  const [showNewForm, setShowNewForm] = useState(false);
  const [selectionMode, setSelectionMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const isDegradedData = dataSource === "fallback";

  const toggleFilter = useCallback((type: MemoryType) => {
    setActiveFilters((prev) => {
      const next = new Set(prev);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
  }, []);

  const clearFilters = useCallback(() => setActiveFilters(new Set()), []);

  const filteredEntries = useMemo(() => {
    let result = entries;
    if (activeFilters.size > 0) {
      result = result.filter((e) => activeFilters.has(e.type));
    }
    if (search) {
      const q = search.toLowerCase();
      result = result.filter(
        (e) =>
          e.content.toLowerCase().includes(q) ||
          e.source.toLowerCase().includes(q) ||
          e.tags.some((t) => t.includes(q)),
      );
    }
    return [...result].sort((a, b) => {
      if (a.pinned && !b.pinned) return -1;
      if (!a.pinned && b.pinned) return 1;
      return b.relevance - a.relevance;
    });
  }, [entries, search, activeFilters]);

  const toggleSelect = useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const selectAllVisible = useCallback(() => {
    setSelectedIds(new Set(filteredEntries.map((e) => e.id)));
  }, [filteredEntries]);

  const deselectAll = useCallback(() => {
    setSelectedIds(new Set());
  }, []);
  const exitSelectionMode = useCallback(() => {
    setSelectionMode(false);
    setSelectedIds(new Set());
  }, []);
  const enterSelectionMode = useCallback(() => {
    setSelectionMode(true);
    setShowNewForm(false);
  }, []);

  const selectedEntries = useMemo(
    () => filteredEntries.filter((e) => selectedIds.has(e.id)),
    [filteredEntries, selectedIds],
  );
  const hasPinnedSelected = selectedEntries.some((e) => e.pinned);
  const hasUnpinnedSelected = selectedEntries.some((e) => !e.pinned);

  const handlePin = useCallback(
    (id: string) => {
      togglePin(id);
    },
    [togglePin],
  );
  const handleDelete = useCallback(
    (id: string) => {
      remove(id);
      toast.success("Memory entry removed");
    },
    [remove],
  );
  const handleCreate = useCallback(
    (data: { type: MemoryType; content: string; tags: string[] }) => {
      create({
        type: data.type,
        content: data.content,
        tags: data.tags,
        pinned: false,
      });
      setShowNewForm(false);
      toast.success("Memory entry created");
    },
    [create],
  );

  const handleBulkPin = useCallback(() => {
    const ids = Array.from(selectedIds);
    bulkPin(ids, true);
    toast.success(`Pinned ${ids.length} entries`);
    exitSelectionMode();
  }, [selectedIds, bulkPin, exitSelectionMode]);
  const handleBulkUnpin = useCallback(() => {
    const ids = Array.from(selectedIds);
    bulkPin(ids, false);
    toast.success(`Unpinned ${ids.length} entries`);
    exitSelectionMode();
  }, [selectedIds, bulkPin, exitSelectionMode]);
  const handleBulkDelete = useCallback(() => {
    const ids = Array.from(selectedIds);
    bulkRemove(ids);
    toast.success(`Deleted ${ids.length} entries`);
    exitSelectionMode();
  }, [selectedIds, bulkRemove, exitSelectionMode]);

  const stats = useMemo(() => {
    const pinned = entries.filter((e) => e.pinned).length;
    const types = new Map<MemoryType, number>();
    entries.forEach((e) => types.set(e.type, (types.get(e.type) ?? 0) + 1));
    return {
      total: entries.length,
      pinned,
      types,
      size: formatSize(entries),
    };
  }, [entries]);

  const headerChildren = (
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
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search memories\u2026"
            aria-label="Search memories"
            className={cn(
              "pl-9 border-transparent bg-muted dark:bg-muted hover:bg-secondary/80 focus-visible:bg-background focus-visible:border-ring",
              isMobile && "touch-target",
            )}
            style={typo.label}
          />
        </div>
        {!selectionMode && entries.length > 0 && (
          <Button
            variant="secondary"
            className={cn(
              "gap-1.5 rounded-button shrink-0",
              isMobile && "touch-target",
            )}
            onClick={enterSelectionMode}
            aria-label="Enter selection mode"
          >
            <Square className="w-4 h-4" />
            {!isMobile && <span style={typo.label}>Select</span>}
          </Button>
        )}
        {!selectionMode && (
          <Button
            variant="default"
            className={cn(
              "gap-1.5 rounded-button shrink-0",
              isMobile && "touch-target",
            )}
            onClick={() => setShowNewForm((v) => !v)}
          >
            <Plus className="w-4 h-4" />
            {!isMobile && <span style={typo.label}>New</span>}
          </Button>
        )}
        {selectionMode && (
          <Button
            variant="ghost"
            className={cn(
              "gap-1.5 rounded-button shrink-0",
              isMobile && "touch-target",
            )}
            onClick={exitSelectionMode}
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
              onClick={() => toggleFilter(type)}
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
            className={cn(
              "h-7 px-2 gap-1 rounded-full",
              isMobile && "min-h-[36px]",
            )}
            onClick={clearFilters}
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

  return (
    <div className="flex flex-col h-full w-full bg-background overflow-hidden">
      {!isMobile && (
        <LargeTitleHeader
          title="Memory"
          subtitle="Agent context, facts, preferences, and directives"
          isMobile={false}
        >
          {headerChildren}
        </LargeTitleHeader>
      )}

      <ScrollArea className="flex-1 min-h-0">
        {isMobile && (
          <LargeTitleHeader
            title="Memory"
            subtitle="Agent context, facts, preferences, and directives"
            isMobile
          >
            {headerChildren}
          </LargeTitleHeader>
        )}

        <div
          className={cn(
            "space-y-3 max-w-[800px] w-full mx-auto pb-8",
            isMobile ? "p-4 pt-2" : "p-6 pt-4",
          )}
        >
          {isDegradedData && (
            <Alert>
              <TriangleAlert className="text-muted-foreground" />
              <AlertTitle style={typo.label}>Memory API unavailable</AlertTitle>
              <AlertDescription style={typo.caption}>
                {degradedReason ??
                  "Showing local mock memory entries so you can keep working while backend endpoints are unavailable."}
              </AlertDescription>
            </Alert>
          )}

          <AnimatePresence>
            {showNewForm && !selectionMode && (
              <NewMemoryForm
                onSubmit={handleCreate}
                onCancel={() => setShowNewForm(false)}
                isMobile={isMobile}
                reduced={prefersReduced}
              />
            )}
          </AnimatePresence>

          {isLoading && (
            <div className="flex items-center justify-center py-12">
              <div className="w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin motion-reduce:animate-none" />
            </div>
          )}

          {!isLoading && (
            <AnimatePresence>
              {filteredEntries.map((entry, i) => (
                <MemoryEntryCard
                  key={entry.id}
                  entry={entry}
                  onPin={handlePin}
                  onDelete={handleDelete}
                  delay={Math.min(i * 0.02, 0.2)}
                  reduced={prefersReduced}
                  isMobile={isMobile}
                  selectionMode={selectionMode}
                  isSelected={selectedIds.has(entry.id)}
                  onToggleSelect={toggleSelect}
                />
              ))}
            </AnimatePresence>
          )}

          {!isLoading && filteredEntries.length === 0 && (
            <div className="flex flex-col items-center justify-center py-16 text-center">
              <div className="w-12 h-12 rounded-lg bg-muted flex items-center justify-center mb-4">
                <Brain className="w-6 h-6 text-muted-foreground" />
              </div>
              <p className="text-foreground mb-1" style={typo.label}>
                No memories found
              </p>
              <p className="text-muted-foreground mb-4" style={typo.caption}>
                {search || activeFilters.size > 0
                  ? "Try adjusting your search or filters"
                  : "Memories will appear as you interact with the assistant"}
              </p>
              {!(search || activeFilters.size > 0) && !selectionMode && (
                <Button
                  variant="secondary"
                  className={cn(
                    "gap-1.5 rounded-button",
                    isMobile && "touch-target",
                  )}
                  onClick={() => setShowNewForm(true)}
                >
                  <Plus className="w-4 h-4" />
                  <span style={typo.label}>Add your first memory</span>
                </Button>
              )}
            </div>
          )}
        </div>
      </ScrollArea>

      <AnimatePresence mode="wait">
        {selectionMode && selectedIds.size > 0 ? (
          <BulkActionToolbar
            key="bulk-toolbar"
            selectedCount={selectedIds.size}
            totalCount={filteredEntries.length}
            onSelectAll={selectAllVisible}
            onDeselectAll={deselectAll}
            onBulkPin={handleBulkPin}
            onBulkUnpin={handleBulkUnpin}
            onBulkDelete={() => setShowDeleteConfirm(true)}
            onCancel={exitSelectionMode}
            hasUnpinnedSelected={hasUnpinnedSelected}
            hasPinnedSelected={hasPinnedSelected}
            isMobile={isMobile}
            reduced={prefersReduced}
          />
        ) : (
          <motion.div
            key="footer"
            initial={false}
            className="px-4 md:px-6 py-3 border-t border-border-subtle shrink-0"
          >
            <span className="text-muted-foreground" style={typo.helper}>
              {filteredEntries.length} of {entries.length} entries
              {activeFilters.size > 0 &&
                ` \u00b7 ${activeFilters.size} filter${activeFilters.size > 1 ? "s" : ""} active`}
              {selectionMode && ` \u00b7 Select entries to act on`}
            </span>
          </motion.div>
        )}
      </AnimatePresence>

      <AlertDialog open={showDeleteConfirm} onOpenChange={setShowDeleteConfirm}>
        <AlertDialogContent
          className="border-border-subtle"
          style={{ borderRadius: "var(--radius-card)" }}
        >
          <AlertDialogHeader>
            <AlertDialogTitle style={typo.h4}>
              Delete {selectedIds.size}{" "}
              {selectedIds.size === 1 ? "entry" : "entries"}?
            </AlertDialogTitle>
            <AlertDialogDescription style={typo.caption}>
              This will permanently remove the selected memory
              {selectedIds.size === 1 ? " entry" : " entries"} from the
              agent&apos;s context. This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel
              className="rounded-button"
              style={typo.label}
              onClick={() => setShowDeleteConfirm(false)}
            >
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              className="rounded-button bg-destructive text-destructive-foreground hover:bg-destructive/90"
              style={typo.label}
              onClick={() => {
                setShowDeleteConfirm(false);
                handleBulkDelete();
              }}
            >
              <Trash2 className="w-4 h-4 mr-1.5" />
              Delete {selectedIds.size}{" "}
              {selectedIds.size === 1 ? "entry" : "entries"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
