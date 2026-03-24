/**
 * Shared animation presets for the RLM Workspace chat UI.
 *
 * `fadeUp` is the standard entrance (opacity + y-translate via spring).
 * `fadeUpReduced` is the prefers-reduced-motion fallback (opacity only, instant).
 *
 * Usage:
 *   const prefersReduced = useReducedMotion();
 *   <motion.div {...(prefersReduced ? fadeUpReduced : fadeUp)} />
 */
import { springs } from "@/lib/utils/motion";

export const fadeUp = {
  initial: { opacity: 0, y: 12 },
  animate: { opacity: 1, y: 0 },
  transition: springs.default,
} as const;

export const fadeUpReduced = {
  initial: { opacity: 0 },
  animate: { opacity: 1 },
  transition: springs.instant,
} as const;
