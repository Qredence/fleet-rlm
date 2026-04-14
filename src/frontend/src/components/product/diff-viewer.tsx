/**
 * DiffViewer — Side-by-side or unified text diff display.
 *
 * Performs a simple line-by-line comparison (no external diff library)
 * and highlights added, removed, and unchanged lines with colour coding.
 *
 * ```tsx
 * <DiffViewer
 *   before={oldText}
 *   after={newText}
 *   mode="unified"
 *   title="Prompt Diff"
 * />
 * ```
 */
import { useMemo, useState, useCallback } from "react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

/* -------------------------------------------------------------------------- */
/*                                   Types                                    */
/* -------------------------------------------------------------------------- */

export type DiffMode = "side-by-side" | "unified";

export interface DiffViewerProps {
  before: string;
  after: string;
  mode?: DiffMode;
  title?: string;
  className?: string;
}

/* -------------------------------------------------------------------------- */
/*                            Simple diff engine                              */
/* -------------------------------------------------------------------------- */

interface DiffLine {
  type: "added" | "removed" | "unchanged";
  text: string;
  /** 1-based line number in the respective source */
  lineNo: number;
}

/**
 * Naive line-by-line diff using a longest-common-subsequence approach.
 * Sufficient for prompt / config comparisons without a heavy library.
 * Falls back to a simple sequential comparison when either input exceeds
 * MAX_DIFF_LINES to avoid O(m×n) memory and CPU cost in the browser.
 */

const MAX_DIFF_LINES = 500;

/** Simple sequential fallback for large inputs. */
function computeSimpleDiff(a: string[], b: string[]): DiffLine[] {
  const result: DiffLine[] = [];
  const limit = Math.max(a.length, b.length);
  for (let i = 0; i < limit; i++) {
    if (i < a.length && i < b.length) {
      if (a[i] === b[i]) {
        result.push({ type: "unchanged", text: a[i]!, lineNo: i + 1 });
      } else {
        result.push({ type: "removed", text: a[i]!, lineNo: i + 1 });
        result.push({ type: "added", text: b[i]!, lineNo: i + 1 });
      }
    } else if (i < a.length) {
      result.push({ type: "removed", text: a[i]!, lineNo: i + 1 });
    } else {
      result.push({ type: "added", text: b[i]!, lineNo: i + 1 });
    }
  }
  return result;
}

function computeDiff(before: string, after: string): DiffLine[] {
  const a = before.split("\n");
  const b = after.split("\n");

  // Guard: fall back to simple diff for large inputs to avoid O(m×n) cost.
  if (a.length > MAX_DIFF_LINES || b.length > MAX_DIFF_LINES) {
    return computeSimpleDiff(a, b);
  }

  // Build LCS length table
  const m = a.length;
  const n = b.length;
  const dp: number[][] = Array.from({ length: m + 1 }, () => Array(n + 1).fill(0) as number[]);
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      const prevDiag = dp[i - 1]?.[j - 1] ?? 0;
      const prevUp = dp[i - 1]?.[j] ?? 0;
      const prevLeft = dp[i]?.[j - 1] ?? 0;
      dp[i]![j] = a[i - 1] === b[j - 1] ? prevDiag + 1 : Math.max(prevUp, prevLeft);
    }
  }

  // Back-track to produce diff lines
  const result: DiffLine[] = [];
  let i = m;
  let j = n;
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && a[i - 1] === b[j - 1]) {
      result.push({ type: "unchanged", text: a[i - 1] ?? "", lineNo: i });
      i--;
      j--;
    } else if (j > 0 && (i === 0 || (dp[i]?.[j - 1] ?? 0) >= (dp[i - 1]?.[j] ?? 0))) {
      result.push({ type: "added", text: b[j - 1] ?? "", lineNo: j });
      j--;
    } else {
      result.push({ type: "removed", text: a[i - 1] ?? "", lineNo: i });
      i--;
    }
  }
  return result.reverse();
}

/* -------------------------------------------------------------------------- */
/*                            Style helpers                                   */
/* -------------------------------------------------------------------------- */

const lineStyles: Record<DiffLine["type"], string> = {
  added: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  removed: "bg-destructive/10 text-destructive dark:text-red-300",
  unchanged: "text-foreground",
};

const linePrefix: Record<DiffLine["type"], string> = {
  added: "+",
  removed: "−",
  unchanged: " ",
};

/* -------------------------------------------------------------------------- */
/*                              Component                                     */
/* -------------------------------------------------------------------------- */

export function DiffViewer({
  before,
  after,
  mode: initialMode = "unified",
  title,
  className,
}: DiffViewerProps) {
  const [mode, setMode] = useState<DiffMode>(initialMode);

  const toggleMode = useCallback(
    () => setMode((m) => (m === "unified" ? "side-by-side" : "unified")),
    [],
  );

  const diffLines = useMemo(() => computeDiff(before, after), [before, after]);

  /* Split into left/right columns for side-by-side */
  const { leftLines, rightLines } = useMemo(() => {
    const left: (DiffLine | null)[] = [];
    const right: (DiffLine | null)[] = [];
    for (const line of diffLines) {
      if (line.type === "unchanged") {
        left.push(line);
        right.push(line);
      } else if (line.type === "removed") {
        left.push(line);
        right.push(null);
      } else {
        left.push(null);
        right.push(line);
      }
    }
    return { leftLines: left, rightLines: right };
  }, [diffLines]);

  return (
    <div className={cn("flex flex-col gap-2", className)}>
      {/* Header */}
      <div className="flex items-center justify-between">
        {title ? <h3 className="text-sm font-medium text-foreground">{title}</h3> : <span />}
        <Button variant="outline" size="xs" onClick={toggleMode}>
          {mode === "unified" ? "Side-by-side" : "Unified"}
        </Button>
      </div>

      {/* Diff body */}
      <div className="overflow-x-auto rounded-lg border border-border text-xs font-mono">
        {mode === "unified" ? (
          <table className="w-full">
            <tbody>
              {diffLines.map((line, i) => (
                <tr key={i} className={cn(lineStyles[line.type])}>
                  <td className="w-8 select-none px-2 py-0.5 text-right text-muted-foreground/60">
                    {line.lineNo}
                  </td>
                  <td className="w-4 select-none px-1 py-0.5 text-center text-muted-foreground/60">
                    {linePrefix[line.type]}
                  </td>
                  <td className="px-2 py-0.5 whitespace-pre-wrap break-all">{line.text}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="grid grid-cols-2 divide-x divide-border">
            {/* Left (before) */}
            <table className="w-full">
              <tbody>
                {leftLines.map((line, i) => (
                  <tr key={i} className={cn(line ? lineStyles[line.type] : "")}>
                    <td className="w-8 select-none px-2 py-0.5 text-right text-muted-foreground/60">
                      {line?.lineNo ?? ""}
                    </td>
                    <td className="px-2 py-0.5 whitespace-pre-wrap break-all">
                      {line?.text ?? ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {/* Right (after) */}
            <table className="w-full">
              <tbody>
                {rightLines.map((line, i) => (
                  <tr key={i} className={cn(line ? lineStyles[line.type] : "")}>
                    <td className="w-8 select-none px-2 py-0.5 text-right text-muted-foreground/60">
                      {line?.lineNo ?? ""}
                    </td>
                    <td className="px-2 py-0.5 whitespace-pre-wrap break-all">
                      {line?.text ?? ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
