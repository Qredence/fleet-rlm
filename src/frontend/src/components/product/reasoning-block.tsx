import type { ReactNode } from "react";
import { ChevronDown } from "lucide-react";

import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";

export type ReasoningBlockProps = {
  label: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
  className?: string;
  contentClassName?: string;
};

/** Collapsible reasoning panel for workbench/inspection surfaces (not chat transcripts). */
export function ReasoningBlock({
  label,
  children,
  defaultOpen = false,
  className,
  contentClassName,
}: ReasoningBlockProps) {
  return (
    <Collapsible defaultOpen={defaultOpen} className={cn("group", className)}>
      <CollapsibleTrigger className="flex w-full items-center gap-2 text-left text-sm font-medium text-foreground">
        <ChevronDown className="size-4 shrink-0 text-muted-foreground transition-transform group-data-[state=open]:rotate-180" />
        {label}
      </CollapsibleTrigger>
      <CollapsibleContent className={cn("mt-2 text-sm text-muted-foreground", contentClassName)}>
        {children}
      </CollapsibleContent>
    </Collapsible>
  );
}
