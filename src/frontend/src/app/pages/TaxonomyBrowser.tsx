/**
 * TaxonomyBrowser — hierarchical views of the skill taxonomy and sandbox filesystem.
 *
 * Two view modes toggled via segmented control:
 *   1. **Taxonomy** — tree view of skill domains/categories/skills
 *   2. **Filesystem** — sandbox volume browser showing skill files, config, artifacts, data
 *
 * All shared state consumed from NavigationContext — zero props.
 */
import { useState, useCallback, useMemo } from "react";
import { useReducedMotion } from "motion/react";
import { Search, HardDrive, GitFork, TriangleAlert } from "lucide-react";
import { typo } from "@/lib/config/typo";
import type { FsNode } from "@/lib/data/types";
import { useSkills } from "@/hooks/useSkills";
import { useTaxonomy } from "@/hooks/useTaxonomy";
import { useFilesystem } from "@/hooks/useFilesystem";
import { useNavigation } from "@/hooks/useNavigation";
import { useAppNavigate } from "@/hooks/useAppNavigate";
import { useIsMobile } from "@/components/ui/use-mobile";
import { LargeTitleHeader } from "@/components/shared/LargeTitleHeader";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { cn } from "@/components/ui/utils";
import {
  FsItem,
  TaxonomyItem,
} from "@/features/taxonomy/TaxonomyBrowserSections";
import {
  collectExpandableIds,
  collectTaxonomyIds,
  countFiles,
  filterFs,
} from "@/lib/taxonomy/browser";

type ViewMode = "taxonomy" | "filesystem";

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
