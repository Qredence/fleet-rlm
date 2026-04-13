import type { ReactNode } from "react";
import { PanelRight, type LucideIcon } from "lucide-react";

import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { cn } from "@/lib/utils";

type EmptyPanelProps = {
  title: string;
  description: string;
  icon?: LucideIcon;
  className?: string;
  children?: ReactNode;
};

export function EmptyPanel({
  title,
  description,
  icon: Icon = PanelRight,
  className,
  children,
}: EmptyPanelProps) {
  return (
    <Empty className={cn(className)}>
      <EmptyMedia variant="icon">
        <Icon />
      </EmptyMedia>
      <EmptyContent>
        <EmptyTitle>{title}</EmptyTitle>
        <EmptyDescription>{description}</EmptyDescription>
      </EmptyContent>
      {children}
    </Empty>
  );
}
