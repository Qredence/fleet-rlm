import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type PointerEvent,
} from "react";
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
import { AnimatePresence, motion, useReducedMotion } from "motion/react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { EmptyPanel } from "@/components/product/empty-panel";
import { VolumeFileDetail } from "@/features/volumes/components/file-detail";
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
import { cn } from "@/lib/utils";

export const WORKSPACE_VOLUME_TREE_DEFAULT_WIDTH = 220;
export const WORKSPACE_VOLUME_TREE_MIN_WIDTH = 160;
export const WORKSPACE_VOLUME_TREE_MAX_WIDTH = 520;
export const WORKSPACE_VOLUME_PREVIEW_MIN_WIDTH = 160;

function fileIcon(name: string) {
  if (name.endsWith(".md")) return <FileText className="h-3.5 w-3.5 text-chart-2" />;
  if (name.endsWith(".py")) return <FileCode className="h-3.5 w-3.5 text-chart-1" />;
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
    "--tree-depth": depth,
  } as CSSProperties;
}

export function useResizableColumns({
  initialWidth,
  minWidth,
  maxWidth,
  minPreviewWidth,
  isMobile,
  containerRef,
}: {
  initialWidth: number;
  minWidth: number;
  maxWidth: number;
  minPreviewWidth: number;
  isMobile: boolean;
  containerRef: React.RefObject<HTMLDivElement | null>;
}) {
  const [width, setWidth] = useState(initialWidth);

  const clampWidth = useCallback((targetWidth: number, containerWidth: number) => {
    const computedMax = Math.min(maxWidth, Math.max(minWidth, containerWidth - minPreviewWidth));
    return Math.min(computedMax, Math.max(minWidth, targetWidth));
  }, [minWidth, maxWidth, minPreviewWidth]);

  useEffect(() => {
    if (isMobile || !containerRef.current) return;

    const observer = new ResizeObserver((entries) => {
      const rectWidth = entries[0]?.contentRect.width;
      if (!rectWidth) return;
      setWidth((current) => clampWidth(current, rectWidth));
    });
    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, [isMobile, clampWidth, containerRef]);

  const onPointerDown = useCallback((event: PointerEvent<HTMLDivElement>) => {
    if (!containerRef.current) return;
    event.preventDefault();

    const handleMove = (moveEvent: globalThis.PointerEvent) => {
      const rect = containerRef.current?.getBoundingClientRect();
      if (!rect) return;
      const nextWidth = clampWidth(moveEvent.clientX - rect.left, rect.width);
      setWidth(Math.round(nextWidth));
    };

    const handleUp = () => {
      window.removeEventListener("pointermove", handleMove);
      window.removeEventListener("pointerup", handleUp);
    };

    window.addEventListener("pointermove", handleMove);
    window.addEventListener("pointerup", handleUp);
  }, [clampWidth, containerRef]);

  return { width, onPointerDown };
}

function VolumeTreeItem({
  node,
  depth,
  expanded,
  onToggle,
  onSelectFile,
  prefersReduced,
}: {
  node: FsNode;
  depth: number;
  expanded: Set<string>;
  onToggle: (id: string) => void;
  onSelectFile: (node: FsNode) => void;
  prefersReduced?: boolean | null;
}) {
  const isOpen = expanded.has(node.id);
  const isExpandable = node.type !== "file" && (node.children?.length ?? 0) > 0;
  const isFile = node.type === "file";
  const isVolume = node.type === "volume";

  return (
    <div>
      <Button
        type="button"
        variant="ghost"
        className={cn(
          "volume-tree-row h-auto w-full justify-start gap-2 rounded-lg py-1.5 pr-2",
          isVolume && "bg-muted/40",
        )}
        style={getTreeIndentStyle(depth)}
        onClick={() => {
          if (isFile) onSelectFile(node);
          else onToggle(node.id);
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
          fileIcon(node.name)
        ) : isOpen ? (
          <FolderOpen className="h-4 w-4 text-accent" />
        ) : (
          <Folder className="h-4 w-4 text-muted-foreground" />
        )}
        <span className="min-w-0 flex-1 truncate text-left text-foreground typo-caption">
          {isVolume ? node.path : node.name}
        </span>
        {isFile && node.size ? (
          <span className="shrink-0 text-muted-foreground typo-micro">
            {formatFileSize(node.size)}
          </span>
        ) : isVolume ? (
          <Badge variant="secondary" className="shrink-0 rounded-full">
            <span className="typo-micro">{countFiles(node)}</span>
          </Badge>
        ) : null}
        {node.modifiedAt ? (
          <span className="hidden shrink-0 text-muted-foreground typo-micro xl:inline">
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
              <VolumeTreeItem
                key={child.id}
                node={child}
                depth={depth + 1}
                expanded={expanded}
                onToggle={onToggle}
                onSelectFile={onSelectFile}
                prefersReduced={prefersReduced}
              />
            ))}
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}

export function VolumeTab({ isMobile }: { isMobile: boolean }) {
  const activeProvider: VolumeProvider = "daytona";
  const prefersReduced = useReducedMotion();
  const splitRef = useRef<HTMLDivElement | null>(null);
  const selectedFileNode = useVolumesSelectionStore((state) => state.selectedFileNode);
  const selectFile = useVolumesSelectionStore((state) => state.selectFile);
  const { volumes, dataSource, degradedReason, isLoading, isFetching, refetch } =
    useFilesystem(activeProvider, { maxDepth: 3, maxEntries: 80 });
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");

  const { width: treeWidth, onPointerDown: handleTreeResizePointerDown } = useResizableColumns({
    initialWidth: WORKSPACE_VOLUME_TREE_DEFAULT_WIDTH,
    minWidth: WORKSPACE_VOLUME_TREE_MIN_WIDTH,
    maxWidth: WORKSPACE_VOLUME_TREE_MAX_WIDTH,
    minPreviewWidth: WORKSPACE_VOLUME_PREVIEW_MIN_WIDTH,
    isMobile,
    containerRef: splitRef,
  });

  const filtered = useMemo(() => filterFs(volumes, query), [query, volumes]);
  const totalFiles = useMemo(
    () => volumes.reduce((count, node) => count + countFiles(node), 0),
    [volumes],
  );

  const toggleNode = useCallback((id: string) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const treePane = (
    <div
      data-workspace-volume-tree-panel
      className="flex h-full min-h-0 w-full flex-col"
    >
      <div className="shrink-0 space-y-2 p-3">
        <div className="flex items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2 text-muted-foreground typo-caption">
            <HardDrive className="h-3.5 w-3.5 shrink-0" />
            <span className="truncate">Daytona volume</span>
          </div>
          <div className="flex items-center gap-1">
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-7 rounded-lg"
              onClick={() => refetch()}
              aria-label="Refresh volume tree"
            >
              <RefreshCw className={cn("size-3.5", (isLoading || isFetching) && "animate-spin")} />
            </Button>
            <Button
              type="button"
              variant="ghost"
              className="h-7 px-2 typo-helper"
              onClick={() => setExpanded(new Set(collectExpandableIds(volumes)))}
            >
              Expand
            </Button>
            <Button
              type="button"
              variant="ghost"
              className="h-7 px-2 typo-helper"
              onClick={() => setExpanded(new Set())}
            >
              Collapse
            </Button>
          </div>
        </div>
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search files..."
            aria-label="Search volume files"
            className="h-8 pl-8 typo-caption"
          />
        </div>
      </div>
      {degradedReason ? (
        <Alert className="mx-3 mb-2">
          <TriangleAlert className="text-muted-foreground" />
          <AlertTitle className="typo-label">Volume unavailable</AlertTitle>
          <AlertDescription className="typo-caption">{degradedReason}</AlertDescription>
        </Alert>
      ) : null}
      <ScrollArea className="min-h-0 flex-1 px-2 pb-2">
        {isLoading && volumes.length === 0 ? (
          <div className="flex items-center justify-center py-8 text-muted-foreground typo-caption">
            Loading durable volume tree...
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex items-center justify-center py-8 text-muted-foreground typo-caption">
            No files found.
          </div>
        ) : (
          filtered.map((node) => (
            <VolumeTreeItem
              key={node.id}
              node={node}
              depth={0}
              expanded={expanded}
              onToggle={toggleNode}
              onSelectFile={selectFile}
              prefersReduced={prefersReduced}
            />
          ))
        )}
      </ScrollArea>
      <div className="shrink-0 border-t border-border-subtle/70 px-3 py-2 text-muted-foreground typo-helper">
        {volumes.length} volumes · {totalFiles} files{dataSource !== "mock" ? " · Live" : ""}
      </div>
    </div>
  );

  const previewPane = (
    <div
      data-workspace-volume-preview-panel
      className="h-full min-h-0 w-full overflow-hidden"
    >
      {selectedFileNode ? (
        <VolumeFileDetail file={selectedFileNode} />
      ) : (
        <EmptyPanel
          title="No file selected"
          description="Choose a file from the Daytona volume tree to preview it here."
          icon={FileText}
          className="h-full"
        />
      )}
    </div>
  );

  if (isMobile) {
    return (
      <div
        data-workspace-volume-layout="stacked"
        className="flex h-full min-h-0 flex-col"
      >
        <div className="min-h-0 basis-2/5 border-b border-border-subtle/70">{treePane}</div>
        <div className="min-h-0 flex-1 overflow-hidden">{previewPane}</div>
      </div>
    );
  }

  return (
    <div
      ref={splitRef}
      data-workspace-volume-layout="horizontal"
      className="workspace-volume-split h-full min-h-0"
      style={{ "--volume-tree-width": `${treeWidth}px` } as CSSProperties}
    >
      <div className="min-w-0 overflow-hidden">
        {treePane}
      </div>
      <div
        data-workspace-volume-resize-handle
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize volume file tree"
        tabIndex={0}
        className="workspace-volume-resize-handle bg-border-subtle/70 transition-colors hover:bg-border focus-visible:bg-border focus-visible:outline-hidden"
        onPointerDown={handleTreeResizePointerDown}
      />
      <div className="min-w-0 overflow-hidden">
        {previewPane}
      </div>
    </div>
  );
}
