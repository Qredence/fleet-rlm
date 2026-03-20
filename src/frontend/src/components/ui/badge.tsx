import * as React from "react";

import { cn } from "@/lib/utils/cn";
import { badgeVariants, type BadgeVariantProps } from "./badge-variants";

export interface BadgeProps extends React.HTMLAttributes<HTMLDivElement>, BadgeVariantProps {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
