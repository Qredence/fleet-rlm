import { cn } from "@/components/ui/utils";

// ── Types ───────────────────────────────────────────────────────────
interface IconButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  /** When true, renders the active/toggled visual state (accent tint). */
  isActive?: boolean;
}

// ── Component ───────────────────────────────────────────────────────
/**
 * Token-driven icon button.
 *
 * Plain function declaration — `const + forwardRef` crashes HMR in
 * Figma Make preview. When used inside `<TooltipTrigger asChild>`,
 * wrap in a native `<span className="inline-flex">` so Radix can
 * attach refs to the span instead.
 */
function IconButton({
  className,
  isActive = false,
  children,
  ...props
}: IconButtonProps) {
  return (
    <button
      data-slot="icon-button"
      className={cn(
        "flex items-center justify-center p-1 rounded-lg transition-colors",
        "focus-visible:outline-none focus-visible:ring-[2px] focus-visible:ring-ring/50",
        "disabled:pointer-events-none disabled:opacity-50",
        isActive
          ? "bg-accent/10 text-accent"
          : "text-foreground hover:bg-muted",
        className,
      )}
      {...props}
    >
      {children}
    </button>
  );
}

export { IconButton };
export type { IconButtonProps };
