import { memo } from "react";
import { motion, useReducedMotion } from "motion/react";
import { typo } from "../config/typo";

interface Props {
  label?: string;
}

// Hoisted static animation objects to avoid re-creating on every render
const REDUCED_ANIMATE = { opacity: 0.6 };
const FULL_ANIMATE = { opacity: [0.3, 1, 0.3] };
const REDUCED_TRANSITION = { duration: 0.01 };
const DOT_INDICES = [0, 1, 2] as const;

function dotTransition(i: number) {
  return {
    duration: 1.4,
    repeat: Infinity,
    delay: i * 0.2,
    ease: "easeInOut" as const,
  };
}

export const TypingDots = memo(function TypingDots({ label }: Props) {
  const prefersReduced = useReducedMotion();

  return (
    <div className="flex items-center gap-1.5 py-3 px-1">
      {DOT_INDICES.map((i) => (
        <motion.div
          key={i}
          className="w-1.5 h-1.5 rounded-full bg-muted-foreground"
          animate={prefersReduced ? REDUCED_ANIMATE : FULL_ANIMATE}
          transition={
            prefersReduced ? REDUCED_TRANSITION : dotTransition(i)
          }
        />
      ))}
      {label ? (
        <span className="text-muted-foreground ml-1" style={typo.caption}>
          {label}
        </span>
      ) : null}
    </div>
  );
});
