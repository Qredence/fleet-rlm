/**
 * TaxonomyBrowser — Modal Volume filesystem browser.
 *
 * Displays the real file tree of the configured Modal Volume
 * fetched from GET /api/v1/runtime/volume/tree.
 *
 * All shared state consumed from NavigationContext — zero props.
 */
import { useState, useCallback, useMemo } from "react";
import { useReducedMotion } from "motion/react";
import { Search, HardDrive, TriangleAlert, RefreshCw } from "lucide-react";
import { typo } from "@/lib/config/typo";
import type { FsNode } from "@/lib/data/types";
import { useFilesystem } from "@/hooks/useFilesystem";
import { useNavigation } from "@/hooks/useNavigation";
import { useIsMobile } from "@/components/ui/use-mobile";
import { LargeTitleHeader } from "@/components/shared/LargeTitleHeader";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { cn } from "@/components/ui/utils";
import { FsItem } from "@/features/taxonomy/TaxonomyBrowserSections";
import {
  collectExpandableIds,
  countFiles,
  filterFs,
} from "@/lib/taxonomy/browser";

export function TaxonomyBrowser() {
  const { openCanvas, selectFile } = useNavigation();
  const isMobile = useIsMobile();
  const {
    volumes: filesystem,
    dataSource: filesystemDataSource,
    degradedReason: filesystemDegradedReason,
    isLoading,
    refetch,
  } = useFilesystem();
  const prefersReduced = useReducedMotion();

  // ── Filesystem state ──────────────────────────────────────────────
  const [fsExpanded, setFsExpanded] = useState<Set<string>>(new Set());
  const [fsSearch, setFsSearch] = useState("");

  // ── Handlers ──────────────────────────────────────────────────────

  const toggleFsNode = useCallback((id: string) => {
    setFsExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const expandAllFs = useCallback(() => {
    setFsExpanded(new Set(collectExpandableIds(filesystem)));
  }, [filesystem]);

  const collapseAllFs = useCallback(() => setFsExpanded(new Set()), []);

  const handleSelectFile = useCallback(
    (node: FsNode) => {
      selectFile(node);
      openCanvas();
    },
    [openCanvas, selectFile],
  );

  // ── Filtered data ─────────────────────────────────────────────────

  const filteredFs = useMemo(
    () => filterFs(filesystem, fsSearch),
    [filesystem, fsSearch],
  );

  // ── Stats ─────────────────────────────────────────────────────────

  const fsStats = useMemo(
    () => ({
      volumes: filesystem.length,
      totalFiles: filesystem.reduce((a, v) => a + countFiles(v), 0),
    }),
    [filesystem],
  );

  const isDegraded = filesystemDataSource === "fallback";

  /* ── Header children ─────────────────────────────────────────────── */
  const headerChildren = (
    <div className={cn(isMobile && "px-4")}>
      {/* Expand / collapse + refresh */}
      <div className="flex items-center justify-between mb-3">
        <div
          className="flex items-center gap-1 text-muted-foreground"
          style={typo.helper}
        >
          <HardDrive className="w-3.5 h-3.5" />
          <span>Modal Volume</span>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            onClick={() => refetch()}
            aria-label="Refresh volume tree"
          >
            <RefreshCw
              className={cn("w-3.5 h-3.5", isLoading && "animate-spin")}
            />
          </Button>
          <Button
            variant="link"
            className={cn(
              "px-0 h-auto text-muted-foreground hover:text-foreground",
              isMobile && "touch-target px-2",
            )}
            style={typo.helper}
            onClick={expandAllFs}
          >
            Expand
          </Button>
          <span className="text-border">|</span>
          <Button
            variant="link"
            className={cn(
              "px-0 h-auto text-muted-foreground hover:text-foreground",
              isMobile && "touch-target px-2",
            )}
            style={typo.helper}
            onClick={collapseAllFs}
          >
            Collapse
          </Button>
        </div>
      </div>

      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground pointer-events-none" />
        <Input
          value={fsSearch}
          onChange={(e) => setFsSearch(e.target.value)}
          placeholder="Search files…"
          aria-label="Search files"
          className={cn("pl-9", isMobile && "touch-target")}
          style={typo.label}
        />
      </div>
    </div>
  );

  return (
    <div className="flex flex-col h-full w-full bg-background overflow-hidden">
      {/* Desktop: static header outside scroll */}
      {!isMobile && (
        <LargeTitleHeader title="Volume Browser" isMobile={false}>
          {headerChildren}
        </LargeTitleHeader>
      )}

      {/* Tree */}
      <ScrollArea className="flex-1 min-h-0">
        {/* Mobile: large-title header INSIDE scroll area for collapse behavior */}
        {isMobile && (
          <LargeTitleHeader title="Volume Browser" isMobile>
            {headerChildren}
          </LargeTitleHeader>
        )}

        <div className="py-2 max-w-200 w-full mx-auto">
          {isDegraded && (
            <Alert className={cn("mb-3", isMobile ? "mx-4" : "mx-6")}>
              <TriangleAlert className="text-muted-foreground" />
              <AlertTitle style={typo.label}>Volume API unavailable</AlertTitle>
              <AlertDescription style={typo.caption}>
                {filesystemDegradedReason ??
                  "Showing local mock data while the volume endpoint is unavailable."}
              </AlertDescription>
            </Alert>
          )}

          {isLoading && filesystem.length === 0 ? (
            <div
              className="flex items-center justify-center py-12 text-muted-foreground"
              style={typo.label}
            >
              Loading volume tree…
            </div>
          ) : (
            filteredFs.map((node) => (
              <FsItem
                key={node.id}
                node={node}
                depth={0}
                expanded={fsExpanded}
                onToggle={toggleFsNode}
                onSelectFile={handleSelectFile}
                isMobile={isMobile}
                prefersReduced={prefersReduced}
              />
            ))
          )}
        </div>
      </ScrollArea>

      {/* Footer */}
      <div className="px-4 md:px-6 py-3 border-t border-border-subtle shrink-0">
        <span className="text-muted-foreground" style={typo.helper}>
          {fsStats.volumes} volumes · {fsStats.totalFiles} files
          {filesystemDataSource !== "mock" &&
            filesystemDataSource !== "fallback" && <> · Live</>}
        </span>
      </div>
    </div>
  );
}
