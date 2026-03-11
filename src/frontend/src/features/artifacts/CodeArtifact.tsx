/**
 * CodeArtifact — sandboxed code editor and execution panel.
 *
 * Shown in the BuilderPanel when "Context Memory" is active.
 * Provides a real CodeMirror 6 editor with:
 *   - Full Python syntax highlighting (via @codemirror/lang-python)
 *   - Editable sandbox with line numbers, bracket matching, undo/redo
 *   - Custom theme using design-system CSS variables (auto light/dark)
 *   - Run / Stop / Copy / Clear toolbar
 *   - Collapsible console output with progressive streaming
 *   - File tab bar for switching between artifacts
 *
 * All typography via `typo` / CSS variables.
 * All colors via design system tokens.
 */
import { useState, useCallback, useRef, useEffect } from "react";
import { motion, AnimatePresence, useReducedMotion } from "motion/react";
import {
  Play,
  Square,
  Trash2,
  Copy,
  Check,
  Terminal,
  FileCode2,
  Circle,
  ChevronDown,
} from "lucide-react";
import { toast } from "sonner";
import { springs, fades } from "@/lib/config/motion-config";
import { Button } from "@/components/ui/button";
import { IconButton } from "@/components/ui/icon-button";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils/cn";
import { useCodeMirror } from "@/hooks/useCodeMirror";

// ── Mock artifacts ──────────────────────────────────────────────────

interface CodeFile {
  id: string;
  name: string;
  language: string;
  code: string;
  output: string;
}

const mockFiles: CodeFile[] = [
  {
    id: "f1",
    name: "analyze.py",
    language: "python",
    code: `import json
from skill_fleet import SkillRunner, ContextMemory

# Initialize context memory with current session
ctx = ContextMemory.from_session()
runner = SkillRunner("data-analysis")

# Load dataset from context
dataset = ctx.get("uploaded_dataset")
print(f"Dataset loaded: {len(dataset)} rows")

# Run analysis with configurable parameters
result = runner.execute(
    data=dataset,
    analysis_type="statistical_summary",
    include_correlations=True,
    confidence_level=0.95
)

# Store results back in context
ctx.set("analysis_result", result)
print(f"\\nStatistical Summary:")
print(f"  Mean:   {result.mean:.4f}")
print(f"  Median: {result.median:.4f}")
print(f"  StdDev: {result.std_dev:.4f}")
print(f"  Correlations: {len(result.correlations)} pairs")
print(f"\\n✓ Results stored in context memory")`,
    output: `Dataset loaded: 1,247 rows

Statistical Summary:
  Mean:   42.8731
  Median: 38.5000
  StdDev: 12.4092
  Correlations: 15 pairs

✓ Results stored in context memory`,
  },
  {
    id: "f2",
    name: "test_gen.py",
    language: "python",
    code: `from skill_fleet import SkillRunner

runner = SkillRunner("test-generation")

# Generate tests from source analysis
result = runner.execute(
    source_path="./src/utils/parser.py",
    framework="pytest",
    coverage_target=90,
    include_edge_cases=True
)

print(f"Generated {result.test_count} tests")
print(f"Estimated coverage: {result.coverage}%")
print(f"Edge cases found: {result.edge_cases}")

for test in result.tests[:3]:
    print(f"\\n--- {test.name} ---")
    print(test.code[:120] + "...")`,
    output: `Generated 24 tests
Estimated coverage: 93%
Edge cases found: 7

--- test_parse_empty_input ---
def test_parse_empty_input():
    """Edge case: empty string should return empty AST node."""
    result = parse("")
    assert result.type == "empty"...

--- test_parse_nested_expressions ---
def test_parse_nested_expressions():
    """Verify deeply nested expressions (depth > 10) are handled."""
    expr = "((((((((((1))))))))))"
    result = pa...

--- test_parse_unicode_identifiers ---
def test_parse_unicode_identifiers():
    """Ensure parser handles unicode variable names correctly."""
    result = parse("café = 42")
    assert result.va...`,
  },
  {
    id: "f3",
    name: "pipeline.py",
    language: "python",
    code: `from skill_fleet import Pipeline, SkillRunner

# Chain multiple skills in a pipeline
pipeline = Pipeline("data-cleaning-analysis")
pipeline.add_step(SkillRunner("data-cleaning"))
pipeline.add_step(SkillRunner("data-analysis"))

result = pipeline.execute(
    source="./data/raw_metrics.csv",
    output_format="json"
)

print(f"Pipeline completed in {result.duration:.1f}s")
print(f"Steps: {result.steps_completed}/{result.steps_total}")
print(f"Output: {result.output_path}")`,
    output: `Pipeline completed in 3.2s
Steps: 2/2
Output: ./data/processed_metrics.json`,
  },
];

const CONSOLE_OUTPUT_STYLE = {
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-text-xs-size)",
  fontWeight: "var(--font-text-xs-weight)",
  lineHeight: "var(--font-text-xs-line-height)",
  letterSpacing: "var(--font-text-xs-tracking)",
  color: "var(--color-text)",
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
} as const;

const EDITOR_TEXTAREA_STYLE = {
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-text-xs-size)",
  fontWeight: "var(--font-text-xs-weight)",
  lineHeight: "var(--font-text-xs-line-height)",
  letterSpacing: "var(--font-text-xs-tracking)",
  color: "var(--color-text)",
  caretColor: "var(--color-background-info-solid)",
} as const;

// ── Console output sub-component ────────────────────────────────────

function ConsoleOutput({
  output,
  isRunning,
  prefersReduced,
}: {
  output: string;
  isRunning: boolean;
  prefersReduced: boolean | null;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [output]);

  return (
    <div className="flex flex-col h-full">
      {/* Console header */}
      <div className="flex items-center gap-2 border-t border-border-subtle bg-[var(--color-surface-secondary)] px-4 py-2 shrink-0">
        <Terminal
          className="size-3.5 text-muted-foreground"
          aria-hidden="true"
        />
        <span className="text-muted-foreground typo-helper">
          Console Output
        </span>
        {isRunning && (
          <motion.span
            className="flex items-center gap-1 ml-auto"
            animate={
              prefersReduced ? { opacity: 1 } : { opacity: [0.5, 1, 0.5] }
            }
            transition={
              prefersReduced
                ? { duration: 0.01 }
                : { duration: 1.5, repeat: Infinity }
            }
          >
            <Circle className="size-2" fill="var(--chart-3)" stroke="none" />
            <span className="text-chart-3 typo-micro">
              Running
            </span>
          </motion.span>
        )}
      </div>

      {/* Output area */}
      <div
        ref={scrollRef}
        className="flex-1 min-h-0 overflow-auto overscroll-contain bg-card p-4"
      >
        {output ? (
          <pre style={CONSOLE_OUTPUT_STYLE}>{output}</pre>
        ) : (
          <span className="text-muted-foreground typo-mono">
            {">"} Waiting for execution{"\u2026"}
          </span>
        )}
      </div>
    </div>
  );
}

// ── Main component ──────────────────────────────────────────────────

export function CodeArtifact() {
  const defaultFile: CodeFile = mockFiles[0] ?? {
    id: "fallback",
    name: "artifact.py",
    language: "python",
    code: "",
    output: "",
  };

  const [activeFileId, setActiveFileId] = useState(defaultFile.id);
  const [consoleOutput, setConsoleOutput] = useState("");
  const [isRunning, setIsRunning] = useState(false);
  const [copied, setCopied] = useState(false);
  const [showConsole, setShowConsole] = useState(true);
  const prefersReduced = useReducedMotion();
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Track edited code per file
  const [editedCode, setEditedCode] = useState<Record<string, string>>(() => {
    const initial: Record<string, string> = {};
    for (const f of mockFiles) initial[f.id] = f.code;
    return initial;
  });

  const activeFile =
    mockFiles.find((f) => f.id === activeFileId) ?? defaultFile;
  const currentCode = editedCode[activeFileId] ?? activeFile.code;

  // CodeMirror hook
  const { containerRef: editorRef, editorReady } = useCodeMirror({
    value: currentCode,
    onChange: (newCode) => {
      setEditedCode((prev) => ({ ...prev, [activeFileId]: newCode }));
    },
  });

  const handleRun = useCallback(() => {
    if (isRunning) return;
    setIsRunning(true);
    setShowConsole(true);
    setConsoleOutput("");

    const lines = activeFile.output.split("\n");
    let lineIdx = 0;

    intervalRef.current = setInterval(
      () => {
        if (lineIdx < lines.length) {
          setConsoleOutput(
            (prev) => (prev ? prev + "\n" : "") + lines[lineIdx],
          );
          lineIdx++;
        } else {
          if (intervalRef.current) clearInterval(intervalRef.current);
          intervalRef.current = null;
          setIsRunning(false);
        }
      },
      prefersReduced ? 10 : 180,
    );
  }, [activeFile, isRunning, prefersReduced]);

  const handleStop = useCallback(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
    setIsRunning(false);
    setConsoleOutput((prev) => prev + "\n\n[Execution stopped]");
  }, []);

  const handleClear = useCallback(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
    setConsoleOutput("");
    setIsRunning(false);
  }, []);

  const handleCopy = useCallback(() => {
    navigator.clipboard
      .writeText(currentCode)
      .then(() => {
        setCopied(true);
        toast.success("Code copied to clipboard");
        setTimeout(() => setCopied(false), 2000);
      })
      .catch(() => {
        toast.error("Failed to copy");
      });
  }, [currentCode]);

  // Clean up interval on unmount
  useEffect(() => {
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, []);

  // Track if code has been modified from original
  const isModified = currentCode !== activeFile.code;

  return (
    <div className="flex flex-col h-full bg-background">
      {/* File tab bar */}
      <div className="flex items-center overflow-x-auto border-b border-border-subtle bg-[var(--color-surface-secondary)] no-scrollbar shrink-0">
        {mockFiles.map((file) => {
          const isActive = file.id === activeFileId;
          const fileModified = (editedCode[file.id] ?? file.code) !== file.code;
          return (
            <button
              key={file.id}
              type="button"
              onClick={() => setActiveFileId(file.id)}
              className={cn(
                "flex items-center gap-1.5 px-3 py-2 min-h-[36px] shrink-0 transition-colors border-b-2",
                "focus-visible:outline-none focus-visible:ring-[2px] focus-visible:ring-ring/50",
                isActive
                  ? "border-accent text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground hover:bg-muted/50",
                "typo-helper",
              )}
            >
              <FileCode2
                className={cn(
                  "size-3.5 shrink-0",
                  isActive ? "text-accent" : "text-muted-foreground",
                )}
                aria-hidden="true"
              />
              {file.name}
              {fileModified && (
                <span
                  className="h-1.5 w-1.5 rounded-full bg-accent shrink-0"
                  aria-label="Modified"
                />
              )}
            </button>
          );
        })}
      </div>

      {/* Toolbar */}
      <div className="flex items-center gap-2 px-4 py-2 border-b border-border-subtle shrink-0">
        <Button
          size="sm"
          variant={isRunning ? "destructive" : "default"}
          onClick={isRunning ? handleStop : handleRun}
          className="gap-1.5"
        >
          {isRunning ? (
            <>
              <Square className="size-3.5" />
              Stop
            </>
          ) : (
            <>
              <Play className="size-3.5" />
              Run
            </>
          )}
        </Button>
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="inline-flex">
              <IconButton aria-label="Copy code" onClick={handleCopy}>
                {copied ? (
                  <Check className="size-4 text-chart-3" />
                ) : (
                  <Copy className="size-4 text-muted-foreground" />
                )}
              </IconButton>
            </span>
          </TooltipTrigger>
          <TooltipContent side="bottom">Copy code</TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="inline-flex">
              <IconButton aria-label="Clear console" onClick={handleClear}>
                <Trash2 className="size-4 text-muted-foreground" />
              </IconButton>
            </span>
          </TooltipTrigger>
          <TooltipContent side="bottom">Clear console</TooltipContent>
        </Tooltip>

        <div className="ml-auto flex items-center gap-2">
          {isModified && (
            <span className="text-accent typo-micro">
              Modified
            </span>
          )}
          <span className="text-muted-foreground typo-micro">
            Python 3.12
          </span>
          <button
            type="button"
            onClick={() => setShowConsole((p) => !p)}
            className={cn(
              "flex items-center gap-1 px-2 py-1 rounded transition-colors",
              "hover:bg-muted focus-visible:outline-none focus-visible:ring-[2px] focus-visible:ring-ring/50",
            )}
          >
            <Terminal className="size-3 text-muted-foreground" />
            <span className="text-muted-foreground typo-micro">
              Console
            </span>
            <motion.span
              animate={{ rotate: showConsole ? 180 : 0 }}
              transition={prefersReduced ? springs.instant : springs.snappy}
            >
              <ChevronDown className="size-3 text-muted-foreground" />
            </motion.span>
          </button>
        </div>
      </div>

      {/* Editor + Console split */}
      <div className="flex-1 min-h-0 flex flex-col">
        {/* CodeMirror editor container */}
        <div
          ref={editorRef}
          className={cn(
            "min-h-0 flex-1 overflow-hidden bg-card",
            !editorReady && "hidden",
          )}
        />

        {/* Textarea fallback when CodeMirror fails to load */}
        {!editorReady && (
          <div className="flex-1 min-h-0 overflow-hidden bg-card">
            <textarea
              value={currentCode}
              onChange={(e) => {
                setEditedCode((prev) => ({
                  ...prev,
                  [activeFileId]: e.target.value,
                }));
              }}
              className="w-full h-full resize-none bg-transparent text-foreground p-4 outline-none"
              style={EDITOR_TEXTAREA_STYLE}
              spellCheck={false}
              aria-label={`Code editor: ${activeFile.name}`}
            />
          </div>
        )}

        {/* Console (collapsible) */}
        <AnimatePresence initial={false}>
          {showConsole && (
            <motion.div
              key="console"
              initial={prefersReduced ? { height: 180 } : { height: 0 }}
              animate={{ height: 180 }}
              exit={prefersReduced ? { height: 0 } : { height: 0 }}
              transition={
                prefersReduced
                  ? fades.instant
                  : { duration: 0.2, ease: "easeInOut" }
              }
              className="shrink-0 overflow-hidden border-t border-border-subtle"
            >
              <ConsoleOutput
                output={consoleOutput}
                isRunning={isRunning}
                prefersReduced={prefersReduced}
              />
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}
