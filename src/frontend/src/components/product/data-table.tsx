/**
 * DataTable — Generic sortable table with built-in pagination.
 *
 * Provides a consistent table pattern with column definitions, sorting
 * indicators, pagination controls, and an empty-state fallback.
 *
 * ```tsx
 * <DataTable
 *   columns={[
 *     { header: "Name", accessor: "name", sortable: true },
 *     { header: "Score", accessor: "score" },
 *   ]}
 *   data={rows}
 *   pageSize={10}
 * />
 * ```
 */
import { useMemo, useState, useCallback, type ReactNode } from "react";
import { ArrowUpDown, ArrowUp, ArrowDown, ChevronLeft, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

/* -------------------------------------------------------------------------- */
/*                                   Types                                    */
/* -------------------------------------------------------------------------- */

export interface ColumnDef<T> {
  /** Column header label */
  header: string;
  /** Key to read from the row object, or a render function */
  accessor: keyof T | ((row: T) => ReactNode);
  /** Whether this column supports sorting. Defaults to false. */
  sortable?: boolean;
  /** Optional className applied to every cell in this column */
  className?: string;
}

export type SortDirection = "asc" | "desc";

export interface SortState<T> {
  column: keyof T;
  direction: SortDirection;
}

export interface DataTableProps<T> {
  columns: ColumnDef<T>[];
  data: T[];
  /** Rows per page. 0 or undefined disables pagination. */
  pageSize?: number;
  /** Controlled page index (0-based). */
  page?: number;
  /** Called when the user changes page. */
  onPageChange?: (page: number) => void;
  /** Called when the user clicks a sortable column header. */
  onSort?: (sort: SortState<T>) => void;
  /** Message shown when `data` is empty. */
  emptyMessage?: string;
  className?: string;
  /** Unique key extractor for rows. Falls back to index. */
  rowKey?: (row: T, index: number) => string | number;
}

/* -------------------------------------------------------------------------- */
/*                              Component                                     */
/* -------------------------------------------------------------------------- */

export function DataTable<T extends Record<string, unknown>>({
  columns,
  data,
  pageSize = 0,
  page: controlledPage,
  onPageChange,
  onSort,
  emptyMessage = "No data available.",
  className,
  rowKey,
}: DataTableProps<T>) {
  /* ----- internal sort state (uncontrolled fallback) ----- */
  const [internalSort, setInternalSort] = useState<SortState<T> | null>(null);

  const handleSort = useCallback(
    (col: ColumnDef<T>) => {
      if (!col.sortable || typeof col.accessor === "function") return;
      const key = col.accessor;
      const next: SortState<T> = {
        column: key,
        direction:
          internalSort?.column === key && internalSort.direction === "asc" ? "desc" : "asc",
      };
      setInternalSort(next);
      onSort?.(next);
    },
    [internalSort, onSort],
  );

  /* ----- pagination ----- */
  const [internalPage, setInternalPage] = useState(0);
  const currentPage = controlledPage ?? internalPage;
  const paginationEnabled = pageSize > 0;

  /* ----- client-side sorting (when onSort is not provided externally) ----- */
  const sortedData = useMemo(() => {
    if (!internalSort) return data;
    const { column, direction } = internalSort;
    return [...data].sort((a, b) => {
      const av = a[column];
      const bv = b[column];
      if (av == null && bv == null) return 0;
      if (av == null) return direction === "asc" ? -1 : 1;
      if (bv == null) return direction === "asc" ? 1 : -1;
      if (typeof av === "number" && typeof bv === "number") {
        return direction === "asc" ? av - bv : bv - av;
      }
      const as = String(av);
      const bs = String(bv);
      return direction === "asc" ? as.localeCompare(bs) : bs.localeCompare(as);
    });
  }, [data, internalSort]);

  const totalPages = paginationEnabled ? Math.max(1, Math.ceil(sortedData.length / pageSize)) : 1;
  const safePage = Math.min(currentPage, totalPages - 1);

  const visibleRows = useMemo(() => {
    if (!paginationEnabled) return sortedData;
    const start = safePage * pageSize;
    return sortedData.slice(start, start + pageSize);
  }, [sortedData, safePage, pageSize, paginationEnabled]);

  const goToPage = useCallback(
    (p: number) => {
      const clamped = Math.max(0, Math.min(p, totalPages - 1));
      setInternalPage(clamped);
      onPageChange?.(clamped);
    },
    [totalPages, onPageChange],
  );

  /* ----- cell renderer ----- */
  function renderCell(row: T, col: ColumnDef<T>): ReactNode {
    if (typeof col.accessor === "function") return col.accessor(row);
    const val = row[col.accessor];
    if (val == null) return "—";
    return String(val);
  }

  /* ----- sort icon ----- */
  function sortIcon(col: ColumnDef<T>) {
    if (!col.sortable) return null;
    if (typeof col.accessor !== "function" && internalSort?.column === col.accessor) {
      return internalSort.direction === "asc" ? (
        <ArrowUp className="size-3.5" />
      ) : (
        <ArrowDown className="size-3.5" />
      );
    }
    return <ArrowUpDown className="size-3.5 opacity-40" />;
  }

  /* ----- render ----- */
  return (
    <div className={cn("flex flex-col gap-2", className)}>
      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border bg-muted/40">
              {columns.map((col, ci) => (
                <th
                  key={ci}
                  className={cn(
                    "px-3 py-2 text-left text-xs font-medium text-muted-foreground",
                    col.sortable && "cursor-pointer select-none hover:text-foreground",
                    col.className,
                  )}
                  onClick={() => handleSort(col)}
                >
                  <span className="inline-flex items-center gap-1">
                    {col.header}
                    {sortIcon(col)}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visibleRows.length === 0 ? (
              <tr>
                <td
                  colSpan={columns.length}
                  className="px-3 py-8 text-center text-sm text-muted-foreground"
                >
                  {emptyMessage}
                </td>
              </tr>
            ) : (
              visibleRows.map((row, ri) => (
                <tr
                  key={rowKey ? rowKey(row, ri) : ri}
                  className="border-b border-border/50 last:border-0 hover:bg-muted/20 transition-colors"
                >
                  {columns.map((col, ci) => (
                    <td key={ci} className={cn("px-3 py-2 text-foreground", col.className)}>
                      {renderCell(row, col)}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {paginationEnabled && totalPages > 1 && (
        <div className="flex items-center justify-between px-1">
          <span className="text-xs text-muted-foreground">
            Page {safePage + 1} of {totalPages}
          </span>
          <div className="flex items-center gap-1">
            <Button
              variant="outline"
              size="icon-xs"
              disabled={safePage === 0}
              onClick={() => goToPage(safePage - 1)}
              aria-label="Previous page"
            >
              <ChevronLeft />
            </Button>
            <Button
              variant="outline"
              size="icon-xs"
              disabled={safePage >= totalPages - 1}
              onClick={() => goToPage(safePage + 1)}
              aria-label="Next page"
            >
              <ChevronRight />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
