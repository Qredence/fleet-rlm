import { AnimatedIndicator } from "@/components/ui/animated-indicator";
import { cn } from "@/lib/utils/cn";

interface NavTabProps {
  label: string;
  isActive: boolean;
  onClick: () => void;
  onPointerEnter?: () => void;
  onFocus?: () => void;
  layoutId?: string;
  disabled?: boolean;
}

export function NavTab({
  label,
  isActive,
  onClick,
  onPointerEnter,
  onFocus,
  layoutId = "navActive",
  disabled = false,
}: NavTabProps) {
  return (
    <button
      type="button"
      onClick={disabled ? undefined : onClick}
      onPointerEnter={disabled ? undefined : onPointerEnter}
      onFocus={disabled ? undefined : onFocus}
      aria-disabled={disabled || undefined}
      disabled={disabled}
      className={cn(
        "relative flex items-center justify-center h-9 px-2 shrink-0 rounded-lg transition-colors",
        disabled
          ? "text-muted-foreground/50 cursor-not-allowed"
          : isActive
            ? "text-foreground"
            : "text-muted-foreground hover:text-foreground hover:bg-muted",
      )}
    >
      <span className="relative z-10 font-app text-[length:var(--font-text-sm-size)] font-normal leading-[var(--font-text-sm-line-height)] tracking-[var(--font-text-sm-tracking)]">
        {label}
      </span>
      {isActive && <AnimatedIndicator layoutId={layoutId} />}
    </button>
  );
}
