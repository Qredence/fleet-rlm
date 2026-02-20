import { AnimatedIndicator } from "./animated-indicator";
import { cn } from "./utils";

interface NavTabProps {
  label: string;
  isActive: boolean;
  onClick: () => void;
  onPointerEnter?: () => void;
  onFocus?: () => void;
  layoutId?: string;
}

export function NavTab({
  label,
  isActive,
  onClick,
  onPointerEnter,
  onFocus,
  layoutId = "navActive",
}: NavTabProps) {
  return (
    <button
      onClick={onClick}
      onPointerEnter={onPointerEnter}
      onFocus={onFocus}
      className={cn(
        "relative flex items-center justify-center h-9 px-2 shrink-0 rounded-lg transition-colors",
        isActive
          ? "text-foreground"
          : "text-muted-foreground hover:text-foreground hover:bg-muted",
      )}
    >
      <span
        className="relative z-10"
        style={{
          fontFamily: "var(--font-family)",
          fontWeight: "var(--font-weight-regular)",
          fontSize: "var(--text-label)",
          lineHeight: "20px",
          letterSpacing: "-0.33px",
        }}
      >
        {label}
      </span>
      {isActive && <AnimatedIndicator layoutId={layoutId} />}
    </button>
  );
}
