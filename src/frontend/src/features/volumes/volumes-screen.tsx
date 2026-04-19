import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import {
  Archive,
  ChevronRight,
  Database,
  FileCode,
  FileCog,
  FileJson,
  FileText,
  Folder,
  FolderOpen,
  HardDrive,
  RefreshCw,
  Search,
  TriangleAlert,
} from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { PageHeader } from "@/components/product/page-header";
import { useIsMobile } from "@/hooks/use-is-mobile";
import { cn } from "@/lib/utils";
import {
  collectExpandableIds,
  countFiles,
  filterFs,
  formatDate,
  formatFileSize,
  type FsNode,
  type VolumeProvider,
  useFilesystem,
  useVolumesSelectionStore,
} from "@/features/volumes/use-volumes";
import { useNavigationStore } from "@/stores/navigation-store";

export function VolumesScreen() {
  return <VolumesBrowser />;
}

export function VolumesBrowser() {
  const openCanvas = useNavigationStore((state) => state.openCanvas);
  const selectFile = useVolumesSelectionStore((state) => state.selectFile);
  const clearSelectedFile = useVolumesSelectionStore((state) => state.clearSelectedFile);
  const isMobile = useIsMobile();
  const prefersReduced = useReducedMotion();
  const activeProvider: VolumeProvider = "daytona";
  const providerLabel = "Daytona";
  const {
    volumes: filesystem,
    dataSource: filesystemDataSource,
    degradedReason: filesystemDegradedReason,
    isLoading,
    refetch,
  } = useFilesystem(activeProvider);

  const [fsExpanded, setFsExpanded] = useState<Set<string>>(new Set());
  const [fsSearch, setFsSearch] = useState("");
  const previousProviderRef = useRef<VolumeProvider | null>(null);

  useEffect(() => {
    if (previousProviderRef.current && previousProviderRef.current !== activeProvider) {
      clearSelectedFile();
      setFsExpanded(new Set());
    }
    previousProviderRef.current = activeProvider;
  }, [activeProvider, clearSelectedFile]);

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

  const filteredFs = useMemo(() => filterFs(filesystem, fsSearch), [filesystem, fsSearch]);
  const fsStats = useMemo(
    () => ({
      volumes: filesystem.length,
      totalFiles: filesystem.reduce((a, v) => a + countFiles(v), 0),
    }),
    [filesystem],
  );

  const isDegraded = filesystemDataSource === "fallback";

  const headerChildren = (
    <div className={cn(isMobile && "px-4")}>
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2 text-muted-foreground typo-helper">
          <HardDrive className="h-3.5 w-3.5" />
          <span>{providerLabel} durable volume</span>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            onClick={() => refetch()}
            aria-label="Refresh volume tree"
          >
            <RefreshCw className={cn("h-3.5 w-3.5", isLoading && "animate-spin")} />
          </Button>
          <Button
            variant="link"
            className={cn(
              "h-auto px-0 text-muted-foreground hover:text-foreground",
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
              "h-auto px-0 text-muted-foreground hover:text-foreground",
              isMobile && "touch-target px-2",
              "typo-helper",
            )}
            onClick={collapseAllFs}
          >
            Collapse
          </Button>
        </div>
      </div>

      <div className="relative">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={fsSearch}
          onChange={(event) => setFsSearch(event.target.value)}
          placeholder="Search files…"
          aria-label="Search files"
          className={cn("pl-9 typo-label", isMobile && "touch-target")}
        />
      </div>
    </div>
  );

  return (
    <div className="flex h-full w-full flex-col overflow-hidden bg-background">
      {!isMobile ? (
        <PageHeader
          isMobile={false}
          title="Volume Browser"
          description={`Browse the ${providerLabel.toLowerCase()} mounted durable volume for this workspace.`}
        >
          {headerChildren}
        </PageHeader>
      ) : null}

      <ScrollArea className="min-h-0 flex-1">
        {isMobile ? (
          <PageHeader
            isMobile
            title="Volume Browser"
            description={`Browse the ${providerLabel.toLowerCase()} mounted durable volume for this workspace.`}
          >
            {headerChildren}
          </PageHeader>
        ) : null}

        <div className="mx-auto w-full max-w-page py-2">
          {isDegraded ? (
            <Alert className={cn("mb-3", isMobile ? "mx-4" : "mx-6")}>
              <TriangleAlert className="text-muted-foreground" />
              <AlertTitle className="typo-label">
                {providerLabel} durable volume unavailable
              </AlertTitle>
              <AlertDescription className="typo-caption">
                {filesystemDegradedReason ??
                  `The ${providerLabel.toLowerCase()} volume endpoint is unavailable right now.`}
              </AlertDescription>
            </Alert>
          ) : null}

          {isLoading && filesystem.length === 0 ? (
            <div className="flex items-center justify-center py-12 text-muted-foreground typo-label">
              Loading {providerLabel.toLowerCase()} durable volume tree…
            </div>
          ) : filteredFs.length === 0 ? (
            <div className="flex items-center justify-center py-12 text-muted-foreground typo-label">
              {isDegraded
                ? `No ${providerLabel.toLowerCase()} durable volume data available.`
                : `No files found in the ${providerLabel.toLowerCase()} durable volume.`}
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

      <div className="shrink-0 border-t border-border-subtle px-4 py-3 md:px-6">
        <span className="text-muted-foreground typo-helper">
          {providerLabel} · {fsStats.volumes} volumes · {fsStats.totalFiles} files
          {filesystemDataSource !== "mock" && filesystemDataSource !== "fallback" ? " · Live" : ""}
        </span>
      </div>
    </div>
  );
}

function fileIcon(name: string, _mime?: string) {
  if (name.endsWith(".md")) {
    return <FileText className="h-3.5 w-3.5 text-chart-2" />;
  }
  if (name.endsWith(".py")) {
    return <FileCode className="h-3.5 w-3.5 text-chart-1" />;
  }
  if (name.endsWith(".yaml") || name.endsWith(".yml")) {
    return <FileCog className="h-3.5 w-3.5 text-chart-4" />;
  }
  if (name.endsWith(".json") || name.endsWith(".jsonl")) {
    return <FileJson className="h-3.5 w-3.5 text-chart-3" />;
  }
  if (name.endsWith(".tar.gz") || name.endsWith(".zip")) {
    return <Archive className="h-3.5 w-3.5 text-muted-foreground" />;
  }
  if (name.endsWith(".bin") || name.endsWith(".db")) {
    return <Database className="h-3.5 w-3.5 text-chart-5" />;
  }
  return <FileText className="h-3.5 w-3.5 text-muted-foreground" />;
}

function getTreeIndentStyle(depth: number) {
  return {
    paddingLeft: `calc(var(--tree-indent-base) + (${depth} * var(--tree-indent-step)))`,
  };
}

const FILE_SIZE_STYLE = {
  fontSize: "var(--font-text-3xs-size)",
  fontWeight: "var(--font-text-3xs-weight)",
  fontFamily: "var(--font-sans)",
  lineHeight: "var(--font-text-3xs-line-height)",
  letterSpacing: "var(--font-text-3xs-tracking)",
  fontVariantNumeric: "tabular-nums",
} as const;

function FsItem({
  node,
  depth,
  expanded,
  onToggle,
  onSelectFile,
  isMobile,
  prefersReduced,
}: {
  node: FsNode;
  depth: number;
  expanded: Set<string>;
  onToggle: (id: string) => void;
  onSelectFile: (node: FsNode) => void;
  isMobile?: boolean;
  prefersReduced?: boolean | null;
}) {
  const isOpen = expanded.has(node.id);
  const isExpandable = node.type !== "file" && (node.children?.length ?? 0) > 0;
  const isVolume = node.type === "volume";
  const isFile = node.type === "file";

  return (
    <div>
      <Button
        variant="ghost"
        className={cn(
          "h-auto w-full justify-start gap-2 rounded-lg px-3",
          isMobile ? "touch-target py-3" : "py-2",
          isVolume && "bg-muted/50",
        )}
        style={getTreeIndentStyle(depth)}
        onClick={() => {
          if (isFile) {
            onSelectFile(node);
          } else {
            onToggle(node.id);
          }
        }}
      >
        {isExpandable ? (
          <motion.div
            animate={{ rotate: isOpen ? 90 : 0 }}
            transition={prefersReduced ? { duration: 0.01 } : { duration: 0.15, ease: "easeOut" }}
          >
            <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
          </motion.div>
        ) : (
          <div className="h-3.5 w-3.5" />
        )}

        {isVolume ? (
          <HardDrive className={cn("h-4 w-4", isOpen ? "text-accent" : "text-muted-foreground")} />
        ) : isFile ? (
          fileIcon(node.name, node.mime)
        ) : isOpen ? (
          <FolderOpen className="h-4 w-4 text-accent" />
        ) : (
          <Folder className="h-4 w-4 text-muted-foreground" />
        )}

        <span
          className={cn(
            "min-w-0 flex-1 truncate text-left text-foreground",
            isVolume ? "typo-label" : "typo-caption",
          )}
        >
          {isVolume ? node.path : node.name}
        </span>

        {isFile && node.size ? (
          <span className="shrink-0 text-muted-foreground" style={FILE_SIZE_STYLE}>
            {formatFileSize(node.size)}
          </span>
        ) : isVolume ? (
          <Badge variant="secondary" className="shrink-0 rounded-full">
            <span className="typo-micro">{countFiles(node)} files</span>
          </Badge>
        ) : null}

        {node.modifiedAt ? (
          <span className="hidden shrink-0 text-muted-foreground typo-micro md:inline">
            {formatDate(node.modifiedAt)}
          </span>
        ) : null}
      </Button>

      <AnimatePresence>
        {isOpen && node.children ? (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={prefersReduced ? { duration: 0.01 } : { duration: 0.18, ease: "easeOut" }}
            className="overflow-hidden"
          >
            {node.children.map((child) => (
              <FsItem
                key={child.id}
                node={child}
                depth={depth + 1}
                expanded={expanded}
                onToggle={onToggle}
                onSelectFile={onSelectFile}
                isMobile={isMobile}
                prefersReduced={prefersReduced}
              />
            ))}
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}
