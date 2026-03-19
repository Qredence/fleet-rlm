/**
 * VolumesBrowser — provider-scoped runtime volume browser.
 *
 * Displays the real file tree of the configured Modal or Daytona volume
 * fetched from GET /api/v1/runtime/volume/tree.
 */
import { useState, useCallback, useEffect, useMemo, useRef } from "react";
import { useReducedMotion } from "motion/react";
import { Search, HardDrive, TriangleAlert, RefreshCw } from "lucide-react";
import type { FsNode, VolumeProvider } from "@/screens/volumes/model/volumes-types";
import { useFilesystem } from "@/screens/volumes/hooks/use-volumes-filesystem";
import { useNavigationStore } from "@/stores/navigationStore";
import { useVolumesSelectionStore } from "@/screens/volumes/model/volumes-selection-store";
import { useIsMobile } from "@/hooks/useIsMobile";
import { useRuntimeStatus } from "@/hooks/useRuntimeStatus";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { cn } from "@/lib/utils/cn";
import { FsItem } from "@/screens/volumes/volumes-browser-sections";
import {
  collectExpandableIds,
  countFiles,
  filterFs,
} from "@/screens/volumes/model/volumes-browser-utils";

export function VolumesBrowser() {
  const openCanvas = useNavigationStore((state) => state.openCanvas);
  const selectFile = useVolumesSelectionStore((state) => state.selectFile);
  const clearSelectedFile = useVolumesSelectionStore((state) => state.clearSelectedFile);
  const isMobile = useIsMobile();
  const { data: runtimeStatus } = useRuntimeStatus();
  const prefersReduced = useReducedMotion();
  const defaultProvider: VolumeProvider =
    runtimeStatus?.sandbox_provider === "daytona" ? "daytona" : "modal";
  const [selectedProvider, setSelectedProvider] = useState<VolumeProvider | null>(null);
  const activeProvider = selectedProvider ?? defaultProvider;
  const {
    volumes: filesystem,
    dataSource: filesystemDataSource,
    degradedReason: filesystemDegradedReason,
    isLoading,
    refetch,
  } = useFilesystem(activeProvider);

  // ── Filesystem state ──────────────────────────────────────────────
  const [fsExpanded, setFsExpanded] = useState<Set<string>>(new Set());
  const [fsSearch, setFsSearch] = useState("");
  const previousProviderRef = useRef<VolumeProvider | null>(null);

  useEffect(() => {
    setSelectedProvider((current) => current ?? defaultProvider);
  }, [defaultProvider]);

  useEffect(() => {
    if (previousProviderRef.current && previousProviderRef.current !== activeProvider) {
      clearSelectedFile();
      setFsExpanded(new Set());
    }
    previousProviderRef.current = activeProvider;
  }, [activeProvider, clearSelectedFile]);

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

  const handleProviderChange = useCallback((value: string) => {
    if (value === "modal" || value === "daytona") {
      setSelectedProvider(value);
    }
  }, []);

  // ── Filtered data ─────────────────────────────────────────────────

  const filteredFs = useMemo(() => filterFs(filesystem, fsSearch), [filesystem, fsSearch]);

  // ── Stats ─────────────────────────────────────────────────────────

  const fsStats = useMemo(
    () => ({
      volumes: filesystem.length,
      totalFiles: filesystem.reduce((a, v) => a + countFiles(v), 0),
    }),
    [filesystem],
  );

  const isDegraded = filesystemDataSource === "fallback";
  const providerLabel = activeProvider === "daytona" ? "Daytona" : "Modal";

  /* ── Header children ─────────────────────────────────────────────── */
  const headerChildren = (
    <div className={cn(isMobile && "px-4")}>
      {/* Expand / collapse + refresh */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2 text-muted-foreground typo-helper">
          <HardDrive className="w-3.5 h-3.5" />
          <span>{providerLabel} volume</span>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            onClick={() => refetch()}
            aria-label="Refresh volume tree"
          >
            <RefreshCw className={cn("w-3.5 h-3.5", isLoading && "animate-spin")} />
          </Button>
          <Button
            variant="link"
            className={cn(
              "px-0 h-auto text-muted-foreground hover:text-foreground",
              isMobile && "touch-target px-2",
              "typo-helper",
            )}
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
              "typo-helper",
            )}
            onClick={collapseAllFs}
          >
            Collapse
          </Button>
        </div>
      </div>

      <ToggleGroup
        type="single"
        value={activeProvider}
        onValueChange={handleProviderChange}
        className="mb-3"
        aria-label="Volume provider"
      >
        <ToggleGroupItem value="modal" aria-label="Browse Modal volume">
          Modal
        </ToggleGroupItem>
        <ToggleGroupItem value="daytona" aria-label="Browse Daytona volume">
          Daytona
        </ToggleGroupItem>
      </ToggleGroup>

      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground pointer-events-none" />
        <Input
          value={fsSearch}
          onChange={(e) => setFsSearch(e.target.value)}
          placeholder="Search files…"
          aria-label="Search files"
          className={cn("pl-9 typo-label", isMobile && "touch-target")}
        />
      </div>
    </div>
  );

  return (
    <div className="flex flex-col h-full w-full bg-background overflow-hidden">
      {/* Desktop header */}
      {!isMobile && (
        <div className="pt-4 md:pt-6 pb-4 border-b border-border-subtle shrink-0 max-w-200 w-full mx-auto px-6">
          <h2 className="mb-1 text-balance text-foreground typo-h3">Volume Browser</h2>
          <p className="mb-3 text-muted-foreground typo-helper">
            Browse the {providerLabel.toLowerCase()} runtime volume for this workspace.
          </p>
          {headerChildren}
        </div>
      )}

      {/* Tree */}
      <ScrollArea className="flex-1 min-h-0">
        {/* Mobile header */}
        {isMobile && (
          <div className="px-4 pt-2 pb-4 w-full">
            <h2 className="font-app text-foreground text-balance typo-h2 mb-3">Volume Browser</h2>
            <p className="mb-3 text-muted-foreground typo-helper">
              Browse the {providerLabel.toLowerCase()} runtime volume for this workspace.
            </p>
            {headerChildren}
          </div>
        )}

        <div className="py-2 max-w-[800px] w-full mx-auto">
          {isDegraded ? (
            <Alert className={cn("mb-3", isMobile ? "mx-4" : "mx-6")}>
              <TriangleAlert className="text-muted-foreground" />
              <AlertTitle className="typo-label">{providerLabel} volume unavailable</AlertTitle>
              <AlertDescription className="typo-caption">
                {filesystemDegradedReason ??
                  `The ${providerLabel.toLowerCase()} volume endpoint is unavailable right now.`}
              </AlertDescription>
            </Alert>
          ) : null}

          {isLoading && filesystem.length === 0 ? (
            <div className="flex items-center justify-center py-12 text-muted-foreground typo-label">
              Loading {providerLabel.toLowerCase()} volume tree…
            </div>
          ) : filteredFs.length === 0 ? (
            <div className="flex items-center justify-center py-12 text-muted-foreground typo-label">
              {isDegraded
                ? `No ${providerLabel.toLowerCase()} volume data available.`
                : `No files found in the ${providerLabel.toLowerCase()} volume.`}
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
        <span className="text-muted-foreground typo-helper">
          {providerLabel} · {fsStats.volumes} volumes · {fsStats.totalFiles} files
          {filesystemDataSource !== "mock" && filesystemDataSource !== "fallback" && <> · Live</>}
        </span>
      </div>
    </div>
  );
}
