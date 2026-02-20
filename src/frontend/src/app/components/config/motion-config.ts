/**
 * Centralised motion spring-physics configuration.
 *
 * Every spring constant used in the project is defined here so that a
 * designer can adjust the overall motion feel from a single file.
 *
 * See Guidelines § 7.2 for the rationale behind each profile.
 *
 * @example
 * ```tsx
 * import { springs, useSpring } from '../config/motion-config';
 *
 * // Direct usage (when you need the raw prefersReduced boolean for other things):
 * const prefersReduced = useReducedMotion();
 * <motion.div transition={prefersReduced ? springs.instant : springs.default} />
 *
 * // Hook usage (simplest — handles reduced-motion automatically):
 * const spring = useSpring('default');
 * <motion.div transition={spring} />
 * ```
 */

import type { Transition } from "motion/react";
import { useReducedMotion } from "motion/react";

// ── Spring Profiles ─────────────────────────────────────────────────

export const springs = {
  /** General UI transitions — panels, cards, modals, staggered entrances (380/30/0.8) */
  default: {
    type: "spring",
    stiffness: 380,
    damping: 30,
    mass: 0.8,
  } as Transition,

  /** Navigation tab indicator pill (350/30/0.8) */
  indicator: {
    type: "spring",
    stiffness: 350,
    damping: 30,
    mass: 0.8,
  } as Transition,

  /** Quick interactive feedback — card press, counter pop, selection dot (500/30/0.8) */
  snappy: {
    type: "spring",
    stiffness: 500,
    damping: 30,
    mass: 0.8,
  } as Transition,

  /** Toggle switch positional snap (500/35/0.8) */
  toggleSnap: {
    type: "spring",
    stiffness: 500,
    damping: 35,
    mass: 0.8,
  } as Transition,

  /** Toggle switch shape deformation — width squeeze/stretch (450/28) */
  toggleDeform: { type: "spring", stiffness: 450, damping: 28 } as Transition,

  /** Reduced-motion fallback — effectively instant */
  instant: { duration: 0.01 } as Transition,
} as const;

// ── Easing-based Fades ──────────────────────────────────────────────

export const fades = {
  /** Quick opacity fade for page/content route transitions */
  fast: { duration: 0.12 } as Transition,

  /** Collapse/expand easing for tree nodes, accordions */
  collapse: { duration: 0.18, ease: "easeOut" } as Transition,

  /** Reduced-motion fallback */
  instant: { duration: 0.01 } as Transition,
} as const;

// ── Hook ────────────────────────────────────────────────────────────

/**
 * Returns the spring transition for `key`, automatically falling back
 * to `springs.instant` when the user prefers reduced motion.
 *
 * For sites that also need the raw boolean (e.g. to toggle
 * `initial.y`), import `useReducedMotion` from `motion/react`
 * directly and reference `springs.*` / `springs.instant` inline.
 */
export function useSpring(key: keyof typeof springs = "default"): Transition {
  const prefersReduced = useReducedMotion();
  return prefersReduced ? springs.instant : springs[key];
}
