/**
 * TreeView — Generic, accessible tree-view primitive.
 *
 * Provides a recursive tree with keyboard navigation (ARIA tree pattern),
 * expand/collapse state, and leaf-node selection. Uses token-based
 * indentation via the `tree-row` utility and `--tree-depth` custom property.
 *
 * Animation is limited to `transform` (chevron rotation) and `opacity`
 * (children reveal) — never layout properties like `height`.
 *
 * ```tsx
 * <TreeView
 *   nodes={fsNodes}
 *   renderLabel={(node) => node.name}
 *   renderIcon={(node, expanded) => expanded ? <FolderOpen /> : <Folder />}
 *   onSelect={(node) => console.log(node)}
 * />
 * ```
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

/* -------------------------------------------------------------------------- */
/*                                   Types                                    */
/* -------------------------------------------------------------------------- */

export interface TreeViewNode {
  id: string;
  children?: TreeViewNode[];
}

export interface TreeViewProps<T extends TreeViewNode> {
  nodes: T[];
  /** Controlled set of expanded node ids. If omitted, state is internal. */
  expandedIds?: Set<string>;
  /** Called when the expanded set changes (controlled mode). */
  onExpandedChange?: (ids: Set<string>) => void;
  /** Currently selected node id. */
  selectedId?: string | null;
  /** Called when a leaf or node is clicked. */
  onSelect?: (node: T) => void;
  /** Render the label text/content for a node. */
  renderLabel: (node: T) => ReactNode;
  /** Render the leading icon for a node. Receives expanded state. */
  renderIcon?: (node: T, isExpanded: boolean) => ReactNode;
  /** Render trailing content (badge, size, date, etc.). */
  renderTrailing?: (node: T) => ReactNode;
  /** Override whether a node can expand. Defaults to having children. */
  isExpandable?: (node: T) => boolean;
  /** Override whether a node is a leaf (selectable on click). Defaults to no children. */
  isLeaf?: (node: T) => boolean;
  /** Optional className for the root container. */
  className?: string;
  /** Whether to animate chevron rotation. Defaults to true. */
  animateChevron?: boolean;
}

/* -------------------------------------------------------------------------- */
/*                              Flat list helpers                             */
/* -------------------------------------------------------------------------- */

interface FlatItem<T extends TreeViewNode> {
  node: T;
  depth: number;
  parentId: string | null;
  isExpanded: boolean;
  isExpandable: boolean;
  isLeaf: boolean;
}

function defaultIsExpandable<T extends TreeViewNode>(node: T): boolean {
  return (node.children?.length ?? 0) > 0;
}

function defaultIsLeaf<T extends TreeViewNode>(node: T): boolean {
  return (node.children?.length ?? 0) === 0;
}

function buildFlatList<T extends TreeViewNode>(
  nodes: T[],
  expanded: Set<string>,
  isExpandableFn: (node: T) => boolean,
  isLeafFn: (node: T) => boolean,
): FlatItem<T>[] {
  const result: FlatItem<T>[] = [];

  function walk(list: T[], depth: number, parentId: string | null) {
    for (const node of list) {
      const expandable = isExpandableFn(node);
      const isExpanded = expandable && expanded.has(node.id);
      result.push({
        node,
        depth,
        parentId,
        isExpanded,
        isExpandable: expandable,
        isLeaf: isLeafFn(node),
      });
      if (isExpanded && node.children) {
        walk(node.children as T[], depth + 1, node.id);
      }
    }
  }

  walk(nodes, 0, null);
  return result;
}

/* -------------------------------------------------------------------------- */
/*                              Component                                     */
/* -------------------------------------------------------------------------- */

export function TreeView<T extends TreeViewNode>({
  nodes,
  expandedIds,
  onExpandedChange,
  selectedId,
  onSelect,
  renderLabel,
  renderIcon,
  renderTrailing,
  isExpandable = defaultIsExpandable,
  isLeaf = defaultIsLeaf,
  className,
  animateChevron = true,
}: TreeViewProps<T>) {
  const [internalExpanded, setInternalExpanded] = useState<Set<string>>(new Set());
  const expanded = expandedIds ?? internalExpanded;
  const itemRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const [focusedId, setFocusedId] = useState<string | null>(null);

  const flatList = useMemo(
    () => buildFlatList(nodes, expanded, isExpandable, isLeaf),
    [nodes, expanded, isExpandable, isLeaf],
  );

  const toggleExpand = useCallback(
    (id: string) => {
      const next = new Set(expanded);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      if (expandedIds) {
        onExpandedChange?.(next);
      } else {
        setInternalExpanded(next);
        onExpandedChange?.(next);
      }
    },
    [expanded, expandedIds, onExpandedChange],
  );

  const focusItem = useCallback((id: string) => {
    setFocusedId(id);
    itemRefs.current.get(id)?.focus();
  }, []);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent, item: FlatItem<T>) => {
      const index = flatList.findIndex((f) => f.node.id === item.node.id);
      if (index === -1) return;

      switch (e.key) {
        case "ArrowDown": {
          e.preventDefault();
          const next = flatList[index + 1];
          if (next) focusItem(next.node.id);
          break;
        }
        case "ArrowUp": {
          e.preventDefault();
          const prev = flatList[index - 1];
          if (prev) focusItem(prev.node.id);
          break;
        }
        case "ArrowRight": {
          e.preventDefault();
          if (item.isExpandable && !item.isExpanded) {
            toggleExpand(item.node.id);
          } else if (item.isExpanded) {
            const firstChild = flatList[index + 1];
            if (firstChild && firstChild.depth > item.depth) {
              focusItem(firstChild.node.id);
            }
          }
          break;
        }
        case "ArrowLeft": {
          e.preventDefault();
          if (item.isExpandable && item.isExpanded) {
            toggleExpand(item.node.id);
          } else if (item.parentId) {
            focusItem(item.parentId);
          }
          break;
        }
        case "Enter":
        case " ": {
          e.preventDefault();
          if (item.isExpandable && !item.isLeaf) {
            toggleExpand(item.node.id);
          }
          onSelect?.(item.node);
          break;
        }
        case "Home": {
          e.preventDefault();
          if (flatList[0]) focusItem(flatList[0].node.id);
          break;
        }
        case "End": {
          e.preventDefault();
          const last = flatList[flatList.length - 1];
          if (last) focusItem(last.node.id);
          break;
        }
      }
    },
    [flatList, focusItem, toggleExpand, onSelect],
  );

  /* sync focusedId when flatList changes */
  useEffect(() => {
    if (focusedId && !flatList.some((f) => f.node.id === focusedId)) {
      setFocusedId(null);
    }
  }, [flatList, focusedId]);

  return (
    <div role="tree" aria-label="Tree view" className={cn("flex flex-col", className)}>
      {flatList.map((item) => {
        const isSelected = selectedId === item.node.id;
        return (
          <div
            key={item.node.id}
            role="treeitem"
            aria-expanded={item.isExpandable ? item.isExpanded : undefined}
            aria-selected={isSelected || undefined}
            aria-level={item.depth + 1}
            tabIndex={focusedId === item.node.id || (!focusedId && item === flatList[0]) ? 0 : -1}
            ref={(el) => {
              if (el) itemRefs.current.set(item.node.id, el);
              else itemRefs.current.delete(item.node.id);
            }}
            className={cn(
              "tree-row flex items-center gap-2 rounded-lg py-2 pr-3 transition-colors duration-150",
              "hover:bg-muted/20 focus-visible:bg-muted/30 focus-visible:outline-none",
              isSelected && "bg-muted/40",
            )}
            style={{ "--tree-depth": item.depth } as React.CSSProperties}
            onClick={() => {
              setFocusedId(item.node.id);
              if (item.isExpandable && !item.isLeaf) {
                toggleExpand(item.node.id);
              }
              onSelect?.(item.node);
            }}
            onKeyDown={(e) => handleKeyDown(e, item)}
          >
            {item.isExpandable ? (
              <ChevronRight
                className={cn(
                  "size-3.5 shrink-0 text-muted-foreground transition-transform duration-150",
                  item.isExpanded && "rotate-90",
                  !animateChevron && "transition-none",
                )}
              />
            ) : (
              <span className="inline-block size-3.5 shrink-0" />
            )}

            {renderIcon ? renderIcon(item.node, item.isExpanded) : null}

            <span className="min-w-0 flex-1 truncate text-left typo-caption text-foreground">
              {renderLabel(item.node)}
            </span>

            {renderTrailing ? (
              <span className="shrink-0 text-muted-foreground">{renderTrailing(item.node)}</span>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
