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
        "relative flex h-9 shrink-0 items-center justify-center rounded-lg border px-3 transition-colors duration-150",
        disabled
          ? "cursor-not-allowed border-transparent text-muted-foreground/50"
          : isActive
            ? "border-transparent text-foreground"
            : "border-transparent text-muted-foreground/75 hover:bg-background/70 hover:text-foreground",
      )}
    >
      <span
        className={cn(
          "relative z-10 font-app text-[length:var(--font-text-sm-size)] leading-[var(--font-text-sm-line-height)] tracking-[var(--font-text-sm-tracking)]",
          isActive ? "font-medium" : "font-normal",
        )}
      >
        {label}
      </span>
      {isActive && (
        <AnimatedIndicator
          layoutId={layoutId}
          className="border border-border-subtle/80 bg-background/95 shadow-[0_1px_0_rgba(255,255,255,0.04)]"
        />
      )}
    </button>
  );
}
