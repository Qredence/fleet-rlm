import { typo } from "@/lib/config/typo";
import { Badge } from "@/components/ui/badge";
import { badgeVariants } from "@/components/ui/badge-variants";
import { cn } from "@/lib/utils/cn";
import type { VariantProps } from "class-variance-authority";

type BadgeVariant = VariantProps<typeof badgeVariants>["variant"];

interface SkillBadgeProps {
  label: string;
  variant?: BadgeVariant;
  className?: string;
}

/**
 * Reusable badge component for SkillCard metadata.
 * Uses --radius-lg from theme for a reduced, consistent border-radius.
 */
export function SkillBadge({
  label,
  variant = "secondary",
  className,
}: SkillBadgeProps) {
  return (
    <Badge
      variant={variant}
      className={cn("rounded-lg shrink-0", className)}
      style={typo.helper}
    >
      {label}
    </Badge>
  );
}
