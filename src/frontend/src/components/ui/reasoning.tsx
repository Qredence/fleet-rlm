import { useState, useEffect, useRef } from "react";
import { motion, AnimatePresence, useReducedMotion } from "motion/react";
import { ChevronDown, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils/cn";
import { typo } from "@/lib/config/typo";
import { springs } from "@/lib/config/motion-config";
import { Streamdown } from "@/components/ui/streamdown";

// ── Types ───────────────────────────────────────────────────────────
interface ReasoningPart {
  type: "text";
  text: string;
}

interface ReasoningProps {
  /** Reasoning content parts (text blocks / steps) */
  parts: ReasoningPart[];
  /** Whether the model is still actively thinking */
  isThinking?: boolean;
  /** How long the reasoning took (seconds). Shown when complete. */
  duration?: number;
  /** Whether the content is expanded by default */
  defaultOpen?: boolean;
  /** Additional className for the outer wrapper */
  className?: string;
}

// ── Thinking dots (inline, lightweight) ─────────────────────────────
function ThinkingDots({ reduced }: { reduced?: boolean | null }) {
  return (
    <span className="inline-flex items-center gap-0.5 ml-1">
      {[0, 1, 2].map((i) => (
        <motion.span
          key={i}
          className="inline-block w-1 h-1 rounded-full bg-accent"
          animate={reduced ? { opacity: 0.6 } : { opacity: [0.3, 1, 0.3] }}
          transition={
            reduced
              ? { duration: 0.01 }
              : {
                  duration: 1.4,
                  repeat: Infinity,
                  delay: i * 0.2,
                  ease: "easeInOut",
                }
          }
        />
      ))}
    </span>
  );
}

// ── Elapsed timer ───────────────────────────────────────────────────
function useElapsedTime(isActive: boolean) {
  const [elapsed, setElapsed] = useState(0);
  const startRef = useRef(Date.now());

  useEffect(() => {
    if (!isActive) return;
    startRef.current = Date.now();
    const interval = setInterval(() => {
      setElapsed(Math.floor((Date.now() - startRef.current) / 1000));
    }, 1000);
    return () => clearInterval(interval);
  }, [isActive]);

  return elapsed;
}

// ── Main component ──────────────────────────────────────────────────
function Reasoning({
  parts,
  isThinking = false,
  duration,
  defaultOpen = false,
  className,
}: ReasoningProps) {
  const [isOpen, setIsOpen] = useState(defaultOpen);
  const prefersReduced = useReducedMotion();
  const elapsed = useElapsedTime(isThinking);

  const spring = prefersReduced ? springs.instant : springs.default;

  // Auto-open while streaming so live reasoning is visible, but keep
  // manual toggle control after streaming completes.
  useEffect(() => {
    if (isThinking) {
      setIsOpen(true);
    }
  }, [isThinking]);

  // Format display duration
  const displayDuration = isThinking
    ? `${elapsed}s`
    : duration != null
      ? `${duration.toFixed(1)}s`
      : null;

  // Build summary label
  const summaryLabel = isThinking ? "Thinking" : "Reasoned";

  return (
    <div className={cn("w-full", className)}>
      {/* Trigger */}
      <button
        type="button"
        onClick={() => setIsOpen((prev) => !prev)}
        className={cn(
          "group flex items-center gap-1.5 w-full py-1.5 cursor-pointer",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 rounded-sm",
          "transition-colors duration-150",
        )}
        aria-expanded={isOpen}
        aria-label={
          isThinking ? "Thinking in progress" : "Toggle reasoning details"
        }
      >
        <Sparkles
          className={cn(
            "size-3.5 shrink-0",
            isThinking ? "text-accent" : "text-muted-foreground",
          )}
          aria-hidden="true"
        />
        <span
          className={cn(isThinking ? "text-accent" : "text-muted-foreground")}
          style={typo.caption}
        >
          {summaryLabel}
        </span>
        {isThinking && <ThinkingDots reduced={prefersReduced} />}
        {displayDuration && !isThinking && (
          <span className="text-muted-foreground" style={typo.helper}>
            for {displayDuration}
          </span>
        )}
        <motion.span
          className="ml-auto text-muted-foreground"
          animate={{ rotate: isOpen ? 180 : 0 }}
          transition={spring}
        >
          <ChevronDown className="size-3.5" aria-hidden="true" />
        </motion.span>
      </button>

      {/* Collapsible content */}
      <AnimatePresence initial={false}>
        {isOpen && (
          <motion.div
            key="reasoning-content"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={
              prefersReduced
                ? { duration: 0.01 }
                : { duration: 0.2, ease: "easeInOut" }
            }
            className="overflow-hidden"
          >
            <div className="border-l-2 border-border-subtle ml-1.5 pl-3 pb-1 pt-1 space-y-2">
              {parts.map((part, i) => (
                <div
                  key={i}
                  className="text-muted-foreground"
                  style={typo.caption}
                >
                  <Streamdown
                    content={part.text}
                    streaming={isThinking && i === parts.length - 1}
                  />
                </div>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export { Reasoning };
export type { ReasoningProps, ReasoningPart };
