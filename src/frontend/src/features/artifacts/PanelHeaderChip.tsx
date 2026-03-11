import { type ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils/cn";
import { Badge } from "@/components/ui/badge";

export interface PanelHeaderChipProps {
  icon?: ReactNode;
  label: string;
  version?: string;
  showChevron?: boolean;
  open?: boolean;
  onClick?: () => void;
  interactive?: boolean;
  disabled?: boolean;
  className?: string;
}

export function PanelHeaderChip({
  icon,
  label,
  version,
  showChevron = true,
  open,
  onClick,
  interactive = false,
  disabled = false,
  className,
}: PanelHeaderChipProps) {
  const isInteractive = (!!onClick || interactive) && !disabled;
  const Tag = onClick ? "button" : "div";

  return (
    <Tag
      data-slot="panel-header-chip"
      type={onClick ? "button" : undefined}
      onClick={isInteractive ? onClick : undefined}
      disabled={onClick ? disabled : undefined}
      className={cn(
        "inline-flex items-center gap-2 px-3 py-1.5 min-w-0",
        "bg-secondary rounded-button border-subtle",
        "transition-[background-color,border-color,box-shadow,transform]",
        "duration-150 ease-out",
        isInteractive && [
          "cursor-pointer",
          "hover:border-border hover:shadow-sm hover:bg-secondary/80",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1",
          "active:scale-[var(--haptic-scale)]",
        ],
        open && "border-border shadow-sm bg-secondary/80",
        disabled && "opacity-50 cursor-not-allowed",
        className,
      )}
    >
      {icon}
      <span
        data-slot="panel-header-chip-label"
        className="text-foreground truncate typo-label"
      >
        {label}
      </span>
      {version && (
        <Badge variant="secondary" className="rounded-full typo-mono">
          v{version}
        </Badge>
      )}
      {showChevron && (
        <ChevronDown
          className={cn(
            "size-4 text-muted-foreground shrink-0",
            "transition-transform duration-150",
            open && "rotate-180",
          )}
          aria-hidden="true"
        />
      )}
    </Tag>
  );
}
