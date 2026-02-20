/**
 * TaxonomyBrowser — hierarchical views of the skill taxonomy and sandbox filesystem.
 *
 * Two view modes toggled via segmented control:
 *   1. **Taxonomy** — original tree view of skill domains/categories/skills
 *   2. **Filesystem** — sandbox volume browser showing skill files, config, artifacts, data
 *
 * All shared state consumed from NavigationContext — zero props.
 */
import { useState, useCallback, useMemo } from "react";
import { motion, AnimatePresence, useReducedMotion } from "motion/react";
import {
  ChevronRight,
  Folder,
  FolderOpen,
  Search,
  FileText,
  HardDrive,
  GitFork,
  FileCode,
  FileJson,
  FileCog,
  Archive,
  Database,
  TriangleAlert,
} from "lucide-react";
import { typo } from "../components/config/typo";
import type { TaxonomyNode, FsNode } from "../components/data/types";
import { useSkills } from "../components/hooks/useSkills";
import { useTaxonomy } from "../components/hooks/useTaxonomy";
import { useFilesystem } from "../components/hooks/useFilesystem";
import { useNavigation } from "../components/hooks/useNavigation";
import { useAppNavigate } from "../components/hooks/useAppNavigate";
import { useIsMobile } from "../components/ui/use-mobile";
import { LargeTitleHeader } from "../components/shared/LargeTitleHeader";
import { Button } from "../components/ui/button";
import { Badge } from "../components/ui/badge";
import { Input } from "../components/ui/input";
import { ScrollArea } from "../components/ui/scroll-area";
import { Alert, AlertDescription, AlertTitle } from "../components/ui/alert";
import { cn } from "../components/ui/utils";

// ── View mode type ──────────────────────────────────────────────────

type ViewMode = "taxonomy" | "filesystem";

// ── File icon helper ────────────────────────────────────────────────

function fileIcon(name: string, _mime?: string) {
  if (name.endsWith(".md"))
    return <FileText className="w-3.5 h-3.5 text-chart-2" />;
  if (name.endsWith(".py"))
    return <FileCode className="w-3.5 h-3.5 text-chart-1" />;
  if (name.endsWith(".yaml") || name.endsWith(".yml"))
    return <FileCog className="w-3.5 h-3.5 text-chart-4" />;
  if (name.endsWith(".json") || name.endsWith(".jsonl"))
    return <FileJson className="w-3.5 h-3.5 text-chart-3" />;
  if (name.endsWith(".tar.gz") || name.endsWith(".zip"))
    return <Archive className="w-3.5 h-3.5 text-muted-foreground" />;
  if (name.endsWith(".bin") || name.endsWith(".db"))
    return <Database className="w-3.5 h-3.5 text-chart-5" />;
  return <FileText className="w-3.5 h-3.5 text-muted-foreground" />;
}

function formatFileSize(bytes?: number): string {
  if (!bytes) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(iso?: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
  });
}

// ── Taxonomy Item ───────────────────────────────────────────────────

function TaxonomyItem({
  node,
  depth,
  expanded,
  onToggle,
  onSelectSkill,
  isMobile,
  prefersReduced,
  allSkills,
}: {
  node: TaxonomyNode;
  depth: number;
  expanded: Set<string>;
  onToggle: (id: string) => void;
  onSelectSkill: (id: string) => void;
  isMobile?: boolean;
  prefersReduced?: boolean | null;
  allSkills: import("../components/data/types").Skill[];
}) {
  const isOpen = expanded.has(node.id);
  const hasChildren = node.children.length > 0;
  const skills = node.skills
    ? node.skills
        .map((sid) => allSkills.find((s) => s.id === sid))
        .filter(Boolean)
    : [];

  return (
    <div>
      {/* Apple HIG: 44px min touch target height on mobile */}
      <Button
        variant="ghost"
        className={cn(
          "w-full justify-start gap-2 rounded-lg h-auto px-3",
          isMobile ? "py-3 touch-target" : "py-2",
        )}
        style={{ paddingLeft: `${12 + depth * 20}px` }}
        onClick={() => onToggle(node.id)}
      >
        <motion.div
          animate={{ rotate: isOpen ? 90 : 0 }}
          transition={
            prefersReduced
              ? { duration: 0.01 }
              : { duration: 0.15, ease: "easeOut" }
          }
        >
          <ChevronRight className="w-3.5 h-3.5 text-muted-foreground" />
        </motion.div>
        {isOpen ? (
          <FolderOpen className="w-4 h-4 text-accent" />
        ) : (
          <Folder className="w-4 h-4 text-muted-foreground" />
        )}
        <span className="text-foreground flex-1 text-left" style={typo.label}>
          {node.name}
        </span>
        <span className="text-muted-foreground" style={typo.helper}>
          {node.skillCount}
        </span>
      </Button>

      <AnimatePresence>
        {isOpen && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={
              prefersReduced
                ? { duration: 0.01 }
                : { duration: 0.18, ease: "easeOut" }
            }
            className="overflow-hidden"
          >
            {hasChildren &&
              node.children.map((child) => (
                <TaxonomyItem
                  key={child.id}
                  node={child}
                  depth={depth + 1}
                  expanded={expanded}
                  onToggle={onToggle}
                  onSelectSkill={onSelectSkill}
                  isMobile={isMobile}
                  prefersReduced={prefersReduced}
                  allSkills={allSkills}
                />
              ))}

            {skills.map((skill) =>
              skill ? (
                <Button
                  key={skill.id}
                  variant="ghost"
                  className={cn(
                    "w-full justify-start gap-2 rounded-lg h-auto px-3",
                    isMobile ? "py-2.5 touch-target" : "py-1.5",
                  )}
                  style={{
                    paddingLeft: `${12 + (depth + 1) * 20 + 18}px`,
                  }}
                  onClick={() => onSelectSkill(skill.id)}
                >
                  <FileText className="w-3.5 h-3.5 text-accent" />
                  <span
                    className="text-foreground flex-1 text-left"
                    style={typo.caption}
                  >
                    {skill.displayName}
                  </span>
                  <span className="text-muted-foreground" style={typo.helper}>
                    v{skill.version}
                  </span>
                </Button>
              ) : null,
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// ── Filesystem Item ─────────────────────────────────────────────────

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
          "w-full justify-start gap-2 rounded-lg h-auto px-3",
          isMobile ? "py-3 touch-target" : "py-2",
          isVolume && "bg-muted/50",
        )}
        style={{ paddingLeft: `${12 + depth * 20}px` }}
        onClick={() => {
          if (isFile) {
            onSelectFile(node);
          } else {
            onToggle(node.id);
          }
        }}
      >
        {/* Chevron or spacer */}
        {isExpandable ? (
          <motion.div
            animate={{ rotate: isOpen ? 90 : 0 }}
            transition={
              prefersReduced
                ? { duration: 0.01 }
                : { duration: 0.15, ease: "easeOut" }
            }
          >
            <ChevronRight className="w-3.5 h-3.5 text-muted-foreground" />
          </motion.div>
        ) : (
          <div className="w-3.5 h-3.5" />
        )}

        {/* Icon */}
        {isVolume ? (
          <HardDrive
            className={cn(
              "w-4 h-4",
              isOpen ? "text-accent" : "text-muted-foreground",
            )}
          />
        ) : isFile ? (
          fileIcon(node.name, node.mime)
        ) : isOpen ? (
          <FolderOpen className="w-4 h-4 text-accent" />
        ) : (
          <Folder className="w-4 h-4 text-muted-foreground" />
        )}

        {/* Name */}
        <span
          className={cn(
            "flex-1 text-left min-w-0 truncate",
            isVolume
              ? "text-foreground"
              : isFile
                ? "text-foreground"
                : "text-foreground",
          )}
          style={isVolume ? typo.label : typo.caption}
        >
          {isVolume ? `/sandbox/${node.name}` : node.name}
        </span>

        {/* Meta: file size or child count */}
        {isFile && node.size ? (
          <span
            className="text-muted-foreground shrink-0"
            style={{
              ...typo.micro,
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {formatFileSize(node.size)}
          </span>
        ) : isVolume ? (
          <Badge variant="secondary" className="rounded-full shrink-0">
            <span style={typo.micro}>{countFiles(node)} files</span>
          </Badge>
        ) : null}

        {/* Date */}
        {node.modifiedAt && (
          <span
            className="text-muted-foreground shrink-0 hidden md:inline"
            style={typo.micro}
          >
            {formatDate(node.modifiedAt)}
          </span>
        )}
      </Button>

      <AnimatePresence>
        {isOpen && node.children && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={
              prefersReduced
                ? { duration: 0.01 }
                : { duration: 0.18, ease: "easeOut" }
            }
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
        )}
      </AnimatePresence>
    </div>
  );
}

// ── Helper: count files recursively ─────────────────────────────────

function countFiles(node: FsNode): number {
  if (node.type === "file") return 1;
  return (node.children ?? []).reduce((a, c) => a + countFiles(c), 0);
}

// ── Helper: collect all expandable ids ──────────────────────────────

function collectExpandableIds(nodes: FsNode[]): string[] {
  const ids: string[] = [];
  const walk = (list: FsNode[]) => {
    list.forEach((n) => {
      if (n.type !== "file") {
        ids.push(n.id);
        if (n.children) walk(n.children);
      }
    });
  };
  walk(nodes);
  return ids;
}

function collectTaxonomyIds(nodes: TaxonomyNode[]): string[] {
  const ids: string[] = [];
  const walk = (list: TaxonomyNode[]) => {
    list.forEach((n) => {
      ids.push(n.id);
      walk(n.children);
    });
  };
  walk(nodes);
  return ids;
}

// ── Filter filesystem nodes by search ───────────────────────────────

function filterFs(nodes: FsNode[], query: string): FsNode[] {
  if (!query) return nodes;
  const q = query.toLowerCase();
  return nodes
    .map((node) => {
      if (node.type === "file") {
        return node.name.toLowerCase().includes(q) ? node : null;
      }
      const filteredChildren = filterFs(node.children ?? [], query);
      if (filteredChildren.length > 0 || node.name.toLowerCase().includes(q)) {
        return { ...node, children: filteredChildren };
      }
      return null;
    })
    .filter(Boolean) as FsNode[];
}

// ── Main Component ──────────────────────────────────────────────────

export function TaxonomyBrowser() {
  const { openCanvas, selectFile } = useNavigation();
  const { navigateToSkill } = useAppNavigate();
  const isMobile = useIsMobile();
  const {
    skills: allSkills,
    dataSource: skillsDataSource,
    degradedReason: skillsDegradedReason,
  } = useSkills();
  const {
    taxonomy,
    dataSource: taxonomyDataSource,
    degradedReason: taxonomyDegradedReason,
  } = useTaxonomy();
  const {
    volumes: filesystem,
    dataSource: filesystemDataSource,
    degradedReason: filesystemDegradedReason,
  } = useFilesystem();
  const prefersReduced = useReducedMotion();

  // ── View mode ─────────────────────────────────────────────────────
  const [viewMode, setViewMode] = useState<ViewMode>("taxonomy");

  // ── Taxonomy state ────────────────────────────────────────────────
  const [txExpanded, setTxExpanded] = useState<Set<string>>(
    new Set(["tx-dev"]),
  );
  const [txSearch, setTxSearch] = useState("");

  // ── Filesystem state ──────────────────────────────────────────────
  const [fsExpanded, setFsExpanded] = useState<Set<string>>(
    new Set(["vol-skills"]),
  );
  const [fsSearch, setFsSearch] = useState("");

  // ── Handlers: taxonomy ────────────────────────────────────────────

  const handleSelectSkill = useCallback(
    (id: string) => {
      navigateToSkill("taxonomy", id);
      openCanvas();
    },
    [navigateToSkill, openCanvas],
  );

  const toggleTxNode = useCallback((id: string) => {
    setTxExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const expandAllTx = useCallback(() => {
    setTxExpanded(new Set(collectTaxonomyIds(taxonomy)));
  }, [taxonomy]);

  const collapseAllTx = useCallback(() => setTxExpanded(new Set()), []);

  // ── Handlers: filesystem ──────────────────────────────────────────

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
      if (node.skillId) {
        navigateToSkill("taxonomy", node.skillId);
        openCanvas();
      } else {
        selectFile(node);
        openCanvas();
      }
    },
    [navigateToSkill, openCanvas, selectFile],
  );

  // ── Filtered data ─────────────────────────────────────────────────

  const filteredTaxonomy = useMemo(() => {
    if (!txSearch) return taxonomy;
    return taxonomy.filter(
      (n) =>
        n.name.toLowerCase().includes(txSearch.toLowerCase()) ||
        n.children.some((c) =>
          c.name.toLowerCase().includes(txSearch.toLowerCase()),
        ),
    );
  }, [taxonomy, txSearch]);

  const filteredFs = useMemo(
    () => filterFs(filesystem, fsSearch),
    [filesystem, fsSearch],
  );

  // ── Stats ─────────────────────────────────────────────────────────

  const txStats = useMemo(
    () => ({
      domains: taxonomy.length,
      totalSkills: taxonomy.reduce((a, n) => a + n.skillCount, 0),
    }),
    [taxonomy],
  );

  const fsStats = useMemo(
    () => ({
      volumes: filesystem.length,
      totalFiles: filesystem.reduce((a, v) => a + countFiles(v), 0),
    }),
    [filesystem],
  );

  // ── Current search/expand handlers based on view ──────────────────

  const search = viewMode === "taxonomy" ? txSearch : fsSearch;
  const setSearch = viewMode === "taxonomy" ? setTxSearch : setFsSearch;
  const expandAll = viewMode === "taxonomy" ? expandAllTx : expandAllFs;
  const collapseAll = viewMode === "taxonomy" ? collapseAllTx : collapseAllFs;
  const isTaxonomyDegraded =
    skillsDataSource === "fallback" || taxonomyDataSource === "fallback";
  const isFilesystemDegraded = filesystemDataSource === "fallback";
  const degradedReason =
    viewMode === "taxonomy"
      ? (taxonomyDegradedReason ?? skillsDegradedReason)
      : filesystemDegradedReason;

  /* ── Header children ─────────────────────────────────────────────── */
  const headerChildren = (
    <div className={cn(isMobile && "px-4")}>
      {/* View mode toggle — segmented control */}
      <div
        className="inline-flex p-0.5 rounded-lg bg-muted border border-border-subtle mb-3"
        role="tablist"
        aria-label="View mode"
      >
        <button
          role="tab"
          aria-selected={viewMode === "taxonomy"}
          onClick={() => setViewMode("taxonomy")}
          className={cn(
            "flex items-center gap-1.5 px-3 py-1.5 rounded-md transition-colors",
            isMobile && "min-h-[36px]",
            viewMode === "taxonomy"
              ? "bg-background text-foreground shadow-sm"
              : "text-muted-foreground hover:text-foreground",
          )}
          style={typo.label}
        >
          <GitFork className="w-3.5 h-3.5" />
          Taxonomy
        </button>
        <button
          role="tab"
          aria-selected={viewMode === "filesystem"}
          onClick={() => setViewMode("filesystem")}
          className={cn(
            "flex items-center gap-1.5 px-3 py-1.5 rounded-md transition-colors",
            isMobile && "min-h-[36px]",
            viewMode === "filesystem"
              ? "bg-background text-foreground shadow-sm"
              : "text-muted-foreground hover:text-foreground",
          )}
          style={typo.label}
        >
          <HardDrive className="w-3.5 h-3.5" />
          Filesystem
        </button>
      </div>

      {/* Expand / collapse + search */}
      <div className="flex items-center justify-end gap-2 mb-3">
        <Button
          variant="link"
          className={cn(
            "px-0 h-auto text-muted-foreground hover:text-foreground",
            isMobile && "touch-target px-2",
          )}
          style={typo.helper}
          onClick={expandAll}
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
          onClick={collapseAll}
        >
          Collapse
        </Button>
      </div>

      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground pointer-events-none" />
        <Input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={
            viewMode === "taxonomy"
              ? "Search taxonomy\u2026"
              : "Search files\u2026"
          }
          aria-label={
            viewMode === "taxonomy" ? "Search taxonomy" : "Search files"
          }
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
        <LargeTitleHeader title="Skill Taxonomy" isMobile={false}>
          {headerChildren}
        </LargeTitleHeader>
      )}

      {/* Tree / Filesystem */}
      <ScrollArea className="flex-1 min-h-0">
        {/* Mobile: large-title header INSIDE scroll area for collapse behavior */}
        {isMobile && (
          <LargeTitleHeader title="Skill Taxonomy" isMobile>
            {headerChildren}
          </LargeTitleHeader>
        )}

        <div className="py-2 max-w-[800px] w-full mx-auto">
          {(viewMode === "taxonomy"
            ? isTaxonomyDegraded
            : isFilesystemDegraded) && (
            <Alert className={cn("mb-3", isMobile ? "mx-4" : "mx-6")}>
              <TriangleAlert className="text-muted-foreground" />
              <AlertTitle style={typo.label}>
                {viewMode === "taxonomy"
                  ? "Taxonomy API unavailable"
                  : "Filesystem API unavailable"}
              </AlertTitle>
              <AlertDescription style={typo.caption}>
                {degradedReason ??
                  "Showing local mock data so this view remains available while backend endpoints are unavailable."}
              </AlertDescription>
            </Alert>
          )}

          {viewMode === "taxonomy"
            ? /* ── Taxonomy tree ─────────────────────────────────── */
              filteredTaxonomy.map((node) => (
                <TaxonomyItem
                  key={node.id}
                  node={node}
                  depth={0}
                  expanded={txExpanded}
                  onToggle={toggleTxNode}
                  onSelectSkill={handleSelectSkill}
                  isMobile={isMobile}
                  prefersReduced={prefersReduced}
                  allSkills={allSkills}
                />
              ))
            : /* ── Filesystem browser ────────────────────────────── */
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
              ))}
        </div>
      </ScrollArea>

      {/* Footer */}
      <div className="px-4 md:px-6 py-3 border-t border-border-subtle shrink-0">
        <span className="text-muted-foreground" style={typo.helper}>
          {viewMode === "taxonomy" ? (
            <>
              {txStats.domains} domains · {txStats.totalSkills} total skills
            </>
          ) : (
            <>
              {fsStats.volumes} volumes · {fsStats.totalFiles} files
            </>
          )}
        </span>
      </div>
    </div>
  );
}
