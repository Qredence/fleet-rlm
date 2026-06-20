import { useCallback, useMemo, useState } from "react";
import {
  Archive,
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
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { EmptyPanel } from "@/components/product/empty-panel";
import { PageHeader } from "@/components/product/page-header";
import { TreeView } from "@/components/product/tree-view";
import { useIsMobile } from "@/hooks/ui/use-is-mobile";
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
} from "@/features/volumes/use-volumes";
import { VolumeFileDetail } from "@/features/volumes/file-preview/file-detail";

export function VolumesScreen() {
  return <VolumesBrowser />;
}

const FILE_SIZE_STYLE = {
  fontSize: "var(--font-text-3xs-size)",
  fontWeight: "var(--font-text-3xs-weight)",
  fontFamily: "var(--font-sans)",
  lineHeight: "var(--font-text-3xs-line-height)",
  letterSpacing: "var(--font-text-3xs-tracking)",
  fontVariantNumeric: "tabular-nums",
} as const;

function fileIcon(name: string) {
  if (name.endsWith(".md")) return <FileText className="size-3.5 text-chart-2" />;
  if (name.endsWith(".py")) return <FileCode className="size-3.5 text-chart-1" />;
  if (name.endsWith(".yaml") || name.endsWith(".yml")) {
    return <FileCog className="size-3.5 text-chart-4" />;
  }
  if (name.endsWith(".json") || name.endsWith(".jsonl")) {
    return <FileJson className="size-3.5 text-chart-3" />;
  }
  if (name.endsWith(".tar.gz") || name.endsWith(".zip")) {
    return <Archive className="size-3.5 text-muted-foreground" />;
  }
  if (name.endsWith(".bin") || name.endsWith(".db")) {
    return <Database className="size-3.5 text-chart-5" />;
  }
  return <FileText className="size-3.5 text-muted-foreground" />;
}

function nodeIcon(node: FsNode, isExpanded: boolean) {
  if (node.type === "volume") {
    return (
      <HardDrive className={cn("size-4", isExpanded ? "text-accent" : "text-muted-foreground")} />
    );
  }
  if (node.type === "file") return fileIcon(node.name);
  return isExpanded ? (
    <FolderOpen className="size-4 text-accent" />
  ) : (
    <Folder className="size-4 text-muted-foreground" />
  );
}

function nodeTrailing(node: FsNode) {
  if (node.type === "file") {
    return (
      <span className="flex items-center gap-2">
        {node.size ? <span style={FILE_SIZE_STYLE}>{formatFileSize(node.size)}</span> : null}
        {node.modifiedAt ? (
          <span className="hidden typo-micro md:inline">{formatDate(node.modifiedAt)}</span>
        ) : null}
      </span>
    );
  }
  if (node.type === "volume") {
    return (
      <Badge variant="secondary" className="shrink-0 rounded-full">
        <span className="typo-micro">{countFiles(node)} files</span>
      </Badge>
    );
  }
  if (node.modifiedAt) {
    return <span className="hidden typo-micro md:inline">{formatDate(node.modifiedAt)}</span>;
  }
  return null;
}

const VOLUMES_LAYOUT = { "volumes-tree": 38, "volumes-preview": 62 } as const;

export function VolumesBrowser() {
  const isMobile = useIsMobile();
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
  const [selectedFile, setSelectedFile] = useState<FsNode | null>(null);

  const expandAllFs = useCallback(() => {
    setFsExpanded(new Set(collectExpandableIds(filesystem)));
  }, [filesystem]);

  const collapseAllFs = useCallback(() => setFsExpanded(new Set()), []);

  const handleSelect = useCallback((node: FsNode) => {
    if (node.type === "file") setSelectedFile(node);
  }, []);

  const filteredFs = useMemo(() => filterFs(filesystem, fsSearch), [filesystem, fsSearch]);
  const fsStats = useMemo(
    () => ({
      volumes: filesystem.length,
      totalFiles: filesystem.reduce((a, v) => a + countFiles(v), 0),
    }),
    [filesystem],
  );

  const isDegraded = Boolean(filesystemDegradedReason);

  const treePane = (
    <div className="flex h-full min-h-0 flex-col">
      <div className="shrink-0 border-b border-border-subtle px-3 py-2.5">
        <div className="mb-2 flex items-center justify-between">
          <div className="flex items-center gap-2 text-muted-foreground typo-helper">
            <HardDrive className="size-3.5" />
            <span>{providerLabel} durable volume</span>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="icon"
              className="size-7"
              onClick={() => refetch()}
              aria-label="Refresh volume tree"
            >
              <RefreshCw className={cn("size-3.5", isLoading && "animate-spin")} />
            </Button>
            <Button
              variant="link"
              className={cn(
                "h-auto px-0 text-muted-foreground hover:text-foreground typo-helper",
                isMobile && "touch-target px-2",
              )}
              onClick={expandAllFs}
            >
              Expand
            </Button>
            <span className="text-border">|</span>
            <Button
              variant="link"
              className={cn(
                "h-auto px-0 text-muted-foreground hover:text-foreground typo-helper",
                isMobile && "touch-target px-2",
              )}
              onClick={collapseAllFs}
            >
              Collapse
            </Button>
          </div>
        </div>

        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={fsSearch}
            onChange={(event) => setFsSearch(event.target.value)}
            placeholder="Search files…"
            aria-label="Search files"
            className={cn("pl-9 typo-label", isMobile && "touch-target")}
          />
        </div>
      </div>

      <ScrollArea className="min-h-0 flex-1">
        <div className="p-2">
          {isDegraded ? (
            <Alert className="mb-3">
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
            <TreeView
              nodes={filteredFs}
              expandedIds={fsExpanded}
              onExpandedChange={setFsExpanded}
              selectedId={selectedFile?.id ?? null}
              onSelect={handleSelect}
              renderLabel={(node) => (node.type === "volume" ? node.path : node.name)}
              renderIcon={nodeIcon}
              renderTrailing={nodeTrailing}
              isExpandable={(node) => node.type !== "file" && (node.children?.length ?? 0) > 0}
              isLeaf={(node) => node.type === "file"}
            />
          )}
        </div>
      </ScrollArea>
    </div>
  );

  const previewPane = selectedFile ? (
    <VolumeFileDetail file={selectedFile} />
  ) : (
    <EmptyPanel
      title="No file selected"
      description="Select a file in the tree to preview its contents."
      className="h-full rounded-none border-0 bg-transparent"
    />
  );

  return (
    <div className="flex h-full w-full flex-col overflow-hidden bg-background">
      <PageHeader
        isMobile={isMobile}
        title="Volume Browser"
        description={`Browse the ${providerLabel.toLowerCase()} mounted durable volume for this workspace.`}
      />

      <div className="min-h-0 flex-1">
        {isMobile ? (
          <div className="flex h-full min-h-0 flex-col">
            <div className="min-h-0 flex-1">{treePane}</div>
            {selectedFile ? (
              <div className="min-h-0 flex-1 border-t border-border-subtle">{previewPane}</div>
            ) : null}
          </div>
        ) : (
          <ResizablePanelGroup
            orientation="horizontal"
            defaultLayout={VOLUMES_LAYOUT}
            className="h-full"
          >
            <ResizablePanel id="volumes-tree" defaultSize={38} minSize={24} className="min-w-0">
              {treePane}
            </ResizablePanel>
            <ResizableHandle withHandle />
            <ResizablePanel id="volumes-preview" defaultSize={62} minSize={30} className="min-w-0">
              {previewPane}
            </ResizablePanel>
          </ResizablePanelGroup>
        )}
      </div>

      <div className="shrink-0 border-t border-border-subtle px-4 py-3 md:px-6">
        <span className="text-muted-foreground typo-helper">
          {providerLabel} · {fsStats.volumes} volumes · {fsStats.totalFiles} files
          {filesystemDataSource !== "mock" ? " · Live" : ""}
        </span>
      </div>
    </div>
  );
}
