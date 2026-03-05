import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import {
  AnimatePresence,
  animate,
  motion,
  useMotionValue,
  useReducedMotion,
} from "motion/react";
import { springs } from "@/lib/config/motion-config";
import { cn } from "@/components/ui/utils";

/* ── iOS 26 Authentic Dimensions ──────────────────────────────── */
const TRACK_W = 51;
const TRACK_H = 31;
const KNOB_SIZE = 27;
const MARGIN = 2;
const TRAVEL = TRACK_W - KNOB_SIZE - MARGIN * 2; // 20px
const EXPAND_SCALE = 1.22; // Knob widens to ~33px on press/drag

/* ── Spring Configurations (from centralised motion-config) ───── */
const SNAP_SPRING = springs.toggleSnap;
const SHAPE_SPRING = springs.toggleDeform;
const INSTANT = springs.instant;

/* ── Props ────────────────────────────────────────────────────── */
interface ToggleSwitchProps {
  checked?: boolean;
  onChange?: (checked: boolean) => void;
  disabled?: boolean;
  className?: string;
}

/**
 * iOS 26 Liquid Glass Toggle Switch
 *
 * Authentic Apple-style toggle with:
 * - Liquid glass material on the knob (multi-layered highlights, rim light, depth)
 * - Spring-based snap animation
 * - Press-and-hold knob width deformation (scaleX)
 * - Draggable knob with elastic constraints
 * - Light/dark mode via CSS custom properties (--toggle-*)
 * - Full accessibility (role="switch", aria-checked, keyboard nav)
 * - prefers-reduced-motion support
 */
export function ToggleSwitch({
  checked = false,
  onChange,
  disabled = false,
  className = "",
}: ToggleSwitchProps) {
  const prefersReduced = useReducedMotion();

  /* ── State ──────────────────────────────────────────────── */
  const [isOn, setIsOn] = useState(checked);
  const [isDragging, setIsDragging] = useState(false);
  const [isPressed, setIsPressed] = useState(false);
  const [showFlash, setShowFlash] = useState(false);
  const justDraggedRef = useRef(false);

  /* ── Sync external prop ─────────────────────────────────── */
  useEffect(() => {
    setIsOn(checked);
  }, [checked]);

  /* ── Computed ────────────────────────────────────────────── */
  const isExpanded = isPressed || isDragging;

  /* ── Motion value for knob x position ───────────────────── */
  const x = useMotionValue(isOn ? TRAVEL : 0);

  /* ── Animate position on state change ───────────────────── */
  useEffect(() => {
    if (isDragging) return;
    const target = isOn ? TRAVEL : 0;
    if (prefersReduced) {
      x.set(target);
    } else {
      animate(x, target, SNAP_SPRING);
    }
  }, [isOn, isDragging, prefersReduced, x]);

  /* ── Flash timer ────────────────────────────────────────── */
  useEffect(() => {
    if (showFlash) {
      const t = setTimeout(() => setShowFlash(false), 400);
      return () => clearTimeout(t);
    }
  }, [showFlash]);

  /* ── Handlers ───────────────────────────────────────────── */
  const doToggle = useCallback(() => {
    if (disabled) return;
    const next = !isOn;
    setIsOn(next);
    setShowFlash(true);
    onChange?.(next);
  }, [isOn, onChange, disabled]);

  const handleClick = useCallback(() => {
    if (justDraggedRef.current) {
      justDraggedRef.current = false;
      return;
    }
    doToggle();
  }, [doToggle]);

  const handleDragStart = useCallback(() => {
    if (disabled) return;
    setIsDragging(true);
  }, [disabled]);

  const handleDragEnd = useCallback(() => {
    if (disabled) return;
    const current = x.get();
    const shouldBeOn = current > TRAVEL / 2;

    if (shouldBeOn !== isOn) {
      setIsOn(shouldBeOn);
      setShowFlash(true);
      onChange?.(shouldBeOn);
    }

    justDraggedRef.current = true;
    setIsDragging(false);
    setIsPressed(false);

    // Clear after click event would have fired
    setTimeout(() => {
      justDraggedRef.current = false;
    }, 200);
  }, [disabled, isOn, onChange, x]);

  const handleKnobPointerDown = useCallback(() => {
    if (disabled) return;
    setIsPressed(true);
  }, [disabled]);

  const handleKnobPointerUp = useCallback(() => {
    if (!isDragging) setIsPressed(false);
  }, [isDragging]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === " " || e.key === "Enter") {
        e.preventDefault();
        doToggle();
      }
    },
    [doToggle],
  );

  /* ── Transition helper ──────────────────────────────────── */
  const springConfig = prefersReduced ? INSTANT : SHAPE_SPRING;

  /* ── Render ─────────────────────────────────────────────── */
  return (
    <button
      type="button"
      role="switch"
      aria-checked={isOn}
      aria-label={isOn ? "On" : "Off"}
      disabled={disabled}
      tabIndex={0}
      onClick={handleClick}
      onKeyDown={handleKeyDown}
      className={cn(
        "relative inline-flex shrink-0 cursor-pointer select-none",
        "focus-visible:outline-2 focus-visible:outline-offset-2",
        "disabled:opacity-50 disabled:cursor-not-allowed",
        className,
      )}
      style={{
        width: TRACK_W,
        height: TRACK_H,
        WebkitTapHighlightColor: "transparent",
        outline: "none",
        fontFamily: "var(--font-family)",
      }}
    >
      {/* ── Track ───────────────────────────────────────────── */}
      <motion.div
        className="absolute inset-0 rounded-full overflow-hidden"
        animate={{
          backgroundColor: isOn
            ? "var(--toggle-active)"
            : "var(--toggle-inactive)",
        }}
        transition={{ duration: 0.25, ease: "easeInOut" }}
      >
        {/* Track glass: top shimmer gradient */}
        <div
          className="absolute inset-0 rounded-full pointer-events-none"
          style={{
            background:
              "linear-gradient(180deg, var(--toggle-track-shimmer) 0%, transparent 50%)",
          }}
        />
        {/* Track glass: inner shadow for depth */}
        <div
          className="absolute inset-0 rounded-full pointer-events-none"
          style={{
            boxShadow: "inset 0 0.5px 2px var(--toggle-track-shadow)",
          }}
        />
        {/* Track glass: inner top highlight (active state) */}
        <motion.div
          className="absolute inset-0 rounded-full pointer-events-none"
          animate={{
            boxShadow: isOn
              ? "inset 0 1px 1px var(--toggle-track-highlight-on)"
              : "inset 0 1px 1px var(--toggle-track-highlight-off)",
          }}
          transition={{ duration: 0.25 }}
        />
      </motion.div>

      {/* ── Toggle flash ────────────────────────────────────── */}
      <AnimatePresence>
        {showFlash && !prefersReduced && (
          <motion.div
            key="toggle-flash"
            className="absolute inset-0 rounded-full z-5 pointer-events-none"
            initial={{ opacity: 0 }}
            animate={{ opacity: [0, 0.25, 0] }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.35, ease: "easeOut" }}
            style={{
              background: `radial-gradient(circle at ${
                isOn ? "65%" : "35%"
              } 50%, var(--toggle-flash) 0%, transparent 55%)`,
            }}
          />
        )}
      </AnimatePresence>

      {/* ── Knob ────────────────────────────────────────────── */}
      <motion.div
        className="absolute z-10 touch-none"
        drag={disabled ? false : "x"}
        dragConstraints={{ left: 0, right: TRAVEL }}
        dragElastic={0.08}
        dragMomentum={false}
        onDragStart={handleDragStart}
        onDragEnd={handleDragEnd}
        onPointerDown={handleKnobPointerDown}
        onPointerUp={handleKnobPointerUp}
        onPointerCancel={() => setIsPressed(false)}
        style={{
          x,
          top: MARGIN,
          left: MARGIN,
          width: KNOB_SIZE,
          height: KNOB_SIZE,
          transformOrigin: isOn ? "right center" : "left center",
        }}
        animate={{
          scaleX: isExpanded ? EXPAND_SCALE : 1,
        }}
        transition={{
          scaleX: springConfig,
        }}
      >
        {/* ── Knob body: Liquid Glass Material ─────────────── */}
        <div
          className="w-full h-full rounded-full relative overflow-hidden"
          style={{
            backgroundColor: "var(--toggle-knob)",
            boxShadow: "var(--toggle-knob-shadow)",
          }}
        >
          {/* Glass Layer 1: Top specular highlight
              Simulates light catching the curved top surface of a glass sphere. */}
          <div
            className="absolute pointer-events-none"
            style={{
              top: "6%",
              left: "14%",
              right: "14%",
              height: "44%",
              borderRadius: "50%",
              background:
                "linear-gradient(180deg, var(--toggle-glass-highlight) 0%, var(--toggle-glass-specular-stop) 75%, transparent 100%)",
            }}
          />

          {/* Glass Layer 2: Rim highlight (top edge light catch) */}
          <div
            className="absolute inset-[0.5px] rounded-full pointer-events-none"
            style={{
              boxShadow: "inset 0 0.5px 0 0 var(--toggle-glass-rim)",
            }}
          />

          {/* Glass Layer 3: Bottom depth shadow
              Simulates the subtle shadowing at the bottom of a glass bead. */}
          <div
            className="absolute pointer-events-none"
            style={{
              bottom: 0,
              left: "15%",
              right: "15%",
              height: "35%",
              borderRadius: "0 0 50% 50%",
              background:
                "linear-gradient(0deg, var(--toggle-glass-depth) 0%, transparent 100%)",
            }}
          />

          {/* Glass Layer 4: Directional caustic / refraction shimmer
              Shifts subtly when the toggle state changes. */}
          <motion.div
            className="absolute inset-0 rounded-full pointer-events-none"
            animate={{
              opacity: isOn ? 1 : 0.65,
            }}
            transition={{ duration: 0.3 }}
            style={{
              background:
                "linear-gradient(145deg, var(--toggle-glass-caustic-bright) 0%, transparent 45%, var(--toggle-glass-caustic-dim) 100%)",
            }}
          />

          {/* Glass Layer 5: Liquid color bleed from track
              The glass picks up a faint tint of the track color underneath. */}
          <motion.div
            className="absolute inset-0 rounded-full pointer-events-none"
            style={{ backgroundColor: "var(--toggle-active)" }}
            animate={{ opacity: isOn ? 0.045 : 0 }}
            transition={{ duration: 0.4 }}
          />

          {/* Glass Layer 6: Frosted glass backdrop (very subtle) */}
          <div
            className="absolute inset-0 rounded-full pointer-events-none"
            style={{
              backdropFilter: "blur(1px)",
              WebkitBackdropFilter: "blur(1px)",
            }}
          />
        </div>
      </motion.div>
    </button>
  );
}
