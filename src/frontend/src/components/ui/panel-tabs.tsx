import * as React from "react";
import {
  motion,
  LayoutGroup,
  AnimatePresence,
  useReducedMotion,
} from "motion/react";
import type { PanInfo } from "motion/react";
import { typo } from "@/lib/config/typo";
import { springs } from "@/lib/config/motion-config";
import { cn } from "@/components/ui/utils";

// ── Context ─────────────────────────────────────────────────────────
interface PanelTabsContextValue {
  value: string;
  onValueChange: (value: string) => void;
  layoutId: string;
}

const PanelTabsContext = React.createContext<PanelTabsContextValue | null>(
  null,
);

function usePanelTabs(): PanelTabsContextValue {
  const ctx = React.useContext(PanelTabsContext);
  if (!ctx)
    throw new Error("PanelTabs compounds must be rendered inside <PanelTabs>");
  return ctx;
}

// ── Root ─────────────────────────────────────────────────────────────
interface PanelTabsProps {
  /** Currently active tab value */
  value: string;
  /** Called when the user selects a different tab */
  onValueChange: (value: string) => void;
  /** Unique layout animation scope — prevents cross-component conflicts */
  layoutId?: string;
  className?: string;
  children: React.ReactNode;
}

function PanelTabs({
  value,
  onValueChange,
  layoutId = "panelTab",
  className,
  children,
}: PanelTabsProps) {
  const ctx = React.useMemo(
    () => ({ value, onValueChange, layoutId }),
    [value, onValueChange, layoutId],
  );

  return (
    <PanelTabsContext.Provider value={ctx}>
      <LayoutGroup id={layoutId}>
        <div className={cn("flex flex-col", className)}>{children}</div>
      </LayoutGroup>
    </PanelTabsContext.Provider>
  );
}

// ── Tab List ─────────────────────────────────────────────────────────
interface PanelTabListProps {
  className?: string;
  children: React.ReactNode;
}

/**
 * Renders `role="tablist"` with full WAI-ARIA keyboard navigation:
 *  - ArrowRight / ArrowLeft — move focus to next/prev enabled tab (wraps)
 *  - Home / End — jump to first/last enabled tab
 *  - Automatic activation: focused tab is immediately selected
 *  - Disabled tabs are skipped during traversal
 */
function PanelTabList({ className, children }: PanelTabListProps) {
  const listRef = React.useRef<HTMLDivElement>(null);

  function handleKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    const list = listRef.current;
    if (!list) return;

    // Collect only enabled tab buttons
    const tabs = Array.from(
      list.querySelectorAll<HTMLButtonElement>('[role="tab"]:not(:disabled)'),
    );
    if (tabs.length === 0) return;

    const currentIdx = tabs.indexOf(e.target as HTMLButtonElement);
    if (currentIdx === -1) return; // event didn't originate from a tab

    let nextIdx: number | null = null;

    switch (e.key) {
      case "ArrowRight":
        nextIdx = (currentIdx + 1) % tabs.length;
        break;
      case "ArrowLeft":
        nextIdx = (currentIdx - 1 + tabs.length) % tabs.length;
        break;
      case "Home":
        nextIdx = 0;
        break;
      case "End":
        nextIdx = tabs.length - 1;
        break;
      default:
        return; // don't interfere with other keys
    }

    e.preventDefault();
    const nextTab = tabs[nextIdx];
    if (!nextTab) return;
    nextTab.focus();
    nextTab.click(); // automatic activation
  }

  return (
    <div
      ref={listRef}
      role="tablist"
      aria-orientation="horizontal"
      onKeyDown={handleKeyDown}
      className={cn(
        "relative flex items-center gap-0.5 px-4 md:px-6 border-b border-border-subtle shrink-0",
        className,
      )}
    >
      {children}
    </div>
  );
}

// ── Tab Trigger ──────────────────────────────────────────────────────
interface PanelTabTriggerProps {
  /** Value that identifies this tab — must match a PanelTabContent value */
  value: string;
  /** Optional leading icon (render an svg or lucide icon) */
  icon?: React.ReactNode;
  /** Optional trailing badge (e.g. dependency count) */
  badge?: string | number;
  /** Disables the trigger */
  disabled?: boolean;
  children: React.ReactNode;
}

function PanelTabTrigger({
  value,
  icon,
  badge,
  disabled = false,
  children,
}: PanelTabTriggerProps) {
  const { value: activeValue, onValueChange, layoutId } = usePanelTabs();
  const isActive = activeValue === value;
  const prefersReduced = useReducedMotion();

  return (
    <button
      role="tab"
      type="button"
      id={`${layoutId}-tab-${value}`}
      aria-selected={isActive}
      aria-controls={`${layoutId}-panel-${value}`}
      aria-disabled={disabled || undefined}
      tabIndex={isActive ? 0 : -1}
      disabled={disabled}
      onClick={() => onValueChange(value)}
      className={cn(
        "relative flex items-center gap-1.5 px-3 pb-2.5 pt-3 transition-colors",
        "focus-visible:outline-none focus-visible:ring-[2px] focus-visible:ring-ring/50 rounded-t-lg",
        "disabled:pointer-events-none disabled:opacity-40",
        isActive
          ? "text-accent"
          : "text-muted-foreground hover:text-foreground",
      )}
    >
      {icon && (
        <span className="shrink-0 [&_svg]:size-[15px] flex items-center">
          {icon}
        </span>
      )}
      <span style={typo.label}>{children}</span>
      {badge != null && (
        <span
          className={cn(
            "flex items-center justify-center min-w-[18px] h-[18px] px-1 rounded-full transition-colors",
            isActive
              ? "bg-accent/12 text-accent"
              : "bg-muted text-muted-foreground",
          )}
          style={typo.micro}
        >
          {badge}
        </span>
      )}

      {/* ── Animated underline ── */}
      {isActive && (
        <motion.div
          layoutId={`${layoutId}-underline`}
          className="absolute bottom-0 left-2 right-2 h-[2px] rounded-full"
          style={{ backgroundColor: "var(--accent)" }}
          transition={prefersReduced ? springs.instant : springs.indicator}
        />
      )}
    </button>
  );
}

// ── Tab Content (marker) ─────────────────────────────────────────────
interface PanelTabContentProps {
  /** Must match the value on a corresponding PanelTabTrigger */
  value: string;
  className?: string;
  children: React.ReactNode;
}

/**
 * Marker component — does not render on its own.
 * Place inside `<PanelTabPanels>` which reads each child's props
 * and renders only the active panel with AnimatePresence transitions.
 */
function PanelTabContent(_props: PanelTabContentProps) {
  return null as unknown as React.JSX.Element;
}

// ── Directional slide distance (px) ─────────────────────────────────
const SLIDE_PX = 20;

/**
 * Easing curve used for both enter and exit panel transitions.
 * Cubic-bezier optimized for "settle" feel on short durations.
 */
const PANEL_EASE: [number, number, number, number] = [0.25, 0.1, 0.25, 1];

/**
 * Named variants that accept a `custom` direction value (1 = right, -1 = left).
 * AnimatePresence forwards the latest `custom` to the exiting child, so
 * even stale exit animations receive the correct directional offset.
 */
const panelVariants = {
  enter: (dir: number) => ({
    opacity: 0,
    x: dir * SLIDE_PX,
  }),
  center: {
    opacity: 1,
    x: 0,
    transition: { duration: 0.2, ease: PANEL_EASE },
  },
  exit: (dir: number) => ({
    opacity: 0,
    x: dir * -SLIDE_PX,
    transition: { duration: 0.12, ease: PANEL_EASE },
  }),
};

// ── Swipe thresholds ─────────────────────────────────────────────────
/** Minimum drag offset (px) to trigger a tab switch */
const SWIPE_OFFSET = 50;
/** Minimum drag velocity (px/s) to trigger a tab switch even below offset */
const SWIPE_VELOCITY = 500;
/** Elastic resistance when an adjacent tab exists */
const ELASTIC_ACTIVE = 0.35;
/** Elastic resistance at boundaries (first/last tab) — feels "stuck" */
const ELASTIC_BOUNDARY = 0.08;

// ── Tab Panels (AnimatePresence orchestrator) ────────────────────────
interface PanelTabPanelsProps {
  className?: string;
  children: React.ReactNode;
}

/**
 * Wraps `<PanelTabContent>` children with AnimatePresence.
 * Inspects children props to find the active panel, then renders it
 * inside a keyed `motion.div` so the outgoing panel exits and the
 * incoming panel enters with a directional horizontal slide + crossfade.
 *
 * Direction is derived automatically from the **child order** of
 * `PanelTabContent` markers — clicking a tab to the right slides
 * content left-to-right and vice-versa.
 *
 * Supports **swipe gestures** on touch devices: drag horizontally past
 * a 50 px offset (or 500 px/s velocity) to switch to the adjacent tab.
 * Elastic resistance is reduced at list boundaries (first/last tab) to
 * provide a tactile "end-of-list" feel. `dragDirectionLock` prevents
 * diagonal drags from interfering with vertical scrolling.
 */
function PanelTabPanels({ className, children }: PanelTabPanelsProps) {
  const { value, layoutId, onValueChange } = usePanelTabs();
  const prefersReduced = useReducedMotion();

  // ── Build ordered index from children ──
  const tabValues: string[] = [];
  let panelContent: React.ReactNode = null;
  let panelClassName: string | undefined;

  React.Children.forEach(children, (child) => {
    if (!React.isValidElement(child)) return;
    const props = child.props as PanelTabContentProps;
    if (props.value) tabValues.push(props.value);
    if (props.value === value) {
      panelContent = props.children;
      panelClassName = props.className;
    }
  });

  // ── Boundary awareness ──
  const currentIdx = tabValues.indexOf(value);
  const hasPrev = currentIdx > 0;
  const hasNext = currentIdx < tabValues.length - 1;

  // ── Direction: compare previous index to current ──
  const prevValueRef = React.useRef(value);
  const prevIdx = tabValues.indexOf(prevValueRef.current);
  const nextIdx = currentIdx;
  const direction = nextIdx > prevIdx ? 1 : nextIdx < prevIdx ? -1 : 1;

  React.useEffect(() => {
    prevValueRef.current = value;
  }, [value]);

  // ── Swipe handler ──
  function handleDragEnd(
    _event: MouseEvent | TouchEvent | PointerEvent,
    info: PanInfo,
  ) {
    const swipedLeft =
      info.offset.x < -SWIPE_OFFSET || info.velocity.x < -SWIPE_VELOCITY;
    const swipedRight =
      info.offset.x > SWIPE_OFFSET || info.velocity.x > SWIPE_VELOCITY;

    if (swipedLeft && hasNext) {
      const nextValue = tabValues[currentIdx + 1];
      if (nextValue) onValueChange(nextValue);
    } else if (swipedRight && hasPrev) {
      const prevValue = tabValues[currentIdx - 1];
      if (prevValue) onValueChange(prevValue);
    }
    // Otherwise the panel snaps back to x:0 via dragConstraints
  }

  return (
    <div className={cn("overflow-hidden", className)}>
      <AnimatePresence mode="wait" initial={false} custom={direction}>
        {panelContent != null && (
          <motion.div
            key={value}
            role="tabpanel"
            id={`${layoutId}-panel-${value}`}
            aria-labelledby={`${layoutId}-tab-${value}`}
            custom={direction}
            variants={prefersReduced ? undefined : panelVariants}
            initial={prefersReduced ? { opacity: 1 } : "enter"}
            animate={prefersReduced ? { opacity: 1 } : "center"}
            exit={prefersReduced ? { opacity: 1 } : "exit"}
            /* ── Swipe gesture ── */
            drag="x"
            dragDirectionLock
            dragConstraints={{ left: 0, right: 0 }}
            dragElastic={{
              left: hasNext ? ELASTIC_ACTIVE : ELASTIC_BOUNDARY,
              right: hasPrev ? ELASTIC_ACTIVE : ELASTIC_BOUNDARY,
            }}
            onDragEnd={handleDragEnd}
            style={{ willChange: "opacity, transform", touchAction: "pan-y" }}
            className={cn("outline-none", panelClassName)}
          >
            {panelContent}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export {
  PanelTabs,
  PanelTabList,
  PanelTabTrigger,
  PanelTabContent,
  PanelTabPanels,
};
