"use client";

import { forwardRef, useState, useEffect, type HTMLAttributes } from "react";
import { motion, AnimatePresence, useReducedMotion } from "motion/react";
import { cn } from "@/lib/utils";
import { fontWeights } from "@/lib/font-weight";
import { useSize, typeClass, type SizeVariant } from "@/lib/size-context";

// The glyph is one path morphing circle → infinity → circle → infinity →
// circle. All 3 shapes share the same commands (a move, 4 curves, a close),
// so the morph interpolates point for point. circleA and circleB trace the
// same circle in opposite directions: each pass through the infinity sign
// keeps flowing the same way instead of unwinding.
const circleA =
  "M 12 8 C 14.21 8 16 9.79 16 12 C 16 14.21 14.21 16 12 16 C 9.79 16 8 14.21 8 12 C 8 9.79 9.79 8 12 8 Z";

const infinity =
  "M 12 12 C 14 8.5 19 8.5 19 12 C 19 15.5 14 15.5 12 12 C 10 8.5 5 8.5 5 12 C 5 15.5 10 15.5 12 12 Z";

const circleB =
  "M 12 16 C 14.21 16 16 14.21 16 12 C 16 9.79 14.21 8 12 8 C 9.79 8 8 9.79 8 12 C 8 14.21 9.79 16 12 16 Z";

// The label moves to the next word every 4000ms.
const words = ["Thinking", "Moonwalking", "Planning", "Refining"];

interface ThinkingIndicatorProps extends HTMLAttributes<HTMLDivElement> {
  /** Show the morphing circle⇄infinity glyph before the label. Set to `false`
   *  for a text-only indicator (e.g. inline before a streamed reply). */
  showIcon?: boolean;
  /** Step on the size ladder. Wins over the surrounding SizeProvider. */
  size?: SizeVariant;
}

const ThinkingIndicator = forwardRef<HTMLDivElement, ThinkingIndicatorProps>(
  ({ className, showIcon = true, size, ...props }, ref) => {
  const compactStep = useSize(size).variant === "compact";
  const [index, setIndex] = useState(0);
  // Reduced motion drops the infinite glyph morph and the word cycling — a
  // static glyph and label carry the same meaning without the movement.
  const reduceMotion = useReducedMotion() ?? false;

  useEffect(() => {
    if (reduceMotion) return;
    const interval = setInterval(() => {
      setIndex((i) => (i + 1) % words.length);
    }, 4000);
    return () => clearInterval(interval);
  }, [reduceMotion]);

  return (
    <div
      ref={ref}
      role="status"
      className={cn("flex items-center gap-2 px-3 py-2", className)}
      {...props}
    >
      {/* Static announcement — the cycling word display below is aria-hidden
          so screen readers hear one "Thinking…" instead of a re-announcement
          every 4 seconds. */}
      <span className="sr-only">Thinking…</span>
      {showIcon && (
        <motion.svg
          aria-hidden
          width={compactStep ? 18 : 20}
          height={compactStep ? 18 : 20}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={1.5}
          strokeLinecap="round"
          strokeLinejoin="round"
          className="text-muted-foreground shrink-0"
        >
          {/* 4 equal quarters over 6s, easeInOut, forever. */}
          {reduceMotion ? (
            <path d={infinity} />
          ) : (
            <motion.path
              d={circleA}
              initial={{ d: circleA }}
              animate={{
                d: [circleA, infinity, circleB, infinity, circleA],
              }}
              transition={{
                d: {
                  duration: 6,
                  ease: "easeInOut",
                  repeat: Infinity,
                  times: [0, 0.25, 0.5, 0.75, 1.0],
                },
              }}
            />
          )}
        </motion.svg>
      )}
      {/* Every word sits in the same grid cell. overflow-hidden clips them
          as they roll in from below and out the top, like a slot. */}
      <span
        aria-hidden="true"
        className={cn(
          "inline-grid overflow-hidden",
          typeClass("body", compactStep ? "compact" : "default")
        )}
        style={{ fontVariationSettings: fontWeights.normal }}
      >
        {/* An invisible copy of the longest word reserves the width, so the
            cycle never shifts what sits next to the indicator.
            `shimmer-text` ships with this component's CSS: transparent text
            over a 300%-wide gradient clipped to the letters, sweeping every
            1.5s. Its colors are light-dark() pairs, so the bright band
            inverts per theme. */}
        <span className="col-start-1 row-start-1 invisible shimmer-text">
          {words.reduce((a, b) => (a.length >= b.length ? a : b))}
        </span>
        {reduceMotion ? (
          <span className="col-start-1 row-start-1 shimmer-text">
            {words[0]}
          </span>
        ) : (
          // popLayout lets the outgoing word leave layout at once, so both
          // words overlap in the cell. The exit (0.16s) is quicker than the
          // entrance (0.24s): the old word clears before the new one lands.
          // initial={false}: the first word is already there on mount.
          <AnimatePresence mode="popLayout" initial={false}>
            <motion.span
              key={words[index]}
              className="col-start-1 row-start-1 shimmer-text"
              initial={{ y: "80%", opacity: 0 }}
              animate={{ y: 0, opacity: 1, transition: { duration: 0.24, ease: [0.4, 0, 0.2, 1] } }}
              exit={{ y: "-80%", opacity: 0, transition: { duration: 0.16, ease: [0.4, 0, 0.2, 1] } }}
            >
              {words[index]}
            </motion.span>
          </AnimatePresence>
        )}
      </span>
    </div>
  );
});

ThinkingIndicator.displayName = "ThinkingIndicator";

export { ThinkingIndicator };
export type { ThinkingIndicatorProps };
export default ThinkingIndicator;
