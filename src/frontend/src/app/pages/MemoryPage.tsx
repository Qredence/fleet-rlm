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
import { Trash2, TriangleAlert } from "lucide-react";
import { toast } from "sonner";
import { typo } from "../components/config/typo";
import { useMemory } from "../components/hooks/useMemory";
import type { MemoryType } from "../components/data/types";
import { LargeTitleHeader } from "../components/shared/LargeTitleHeader";
import { ScrollArea } from "../components/ui/scroll-area";
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
import {
  MemoryEntryCard,
  NewMemoryForm,
  BulkActionToolbar,
  MemoryPageHeaderControls,
  MemoryPageEmptyState,
} from "../components/features/memory";
import { formatSize } from "../lib/memory/metadata";

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
    <MemoryPageHeaderControls
      isMobile={isMobile}
      stats={stats}
      search={search}
      onSearchChange={setSearch}
      selectionMode={selectionMode}
      entriesCount={entries.length}
      activeFilters={activeFilters}
      onToggleFilter={toggleFilter}
      onClearFilters={clearFilters}
      onEnterSelectionMode={enterSelectionMode}
      onToggleNewForm={() => setShowNewForm((v) => !v)}
      onExitSelectionMode={exitSelectionMode}
    />
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
            <MemoryPageEmptyState
              search={search}
              activeFilterCount={activeFilters.size}
              selectionMode={selectionMode}
              isMobile={isMobile}
              onAddFirstMemory={() => setShowNewForm(true)}
            />
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
