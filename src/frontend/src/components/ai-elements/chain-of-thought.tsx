"use client";

import { Badge } from "@/components/ui/badge";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";
import type { LucideIcon } from "lucide-react";
import { BrainIcon, CheckIcon, ChevronDownIcon, DotIcon } from "lucide-react";
import type { ComponentProps, ReactNode } from "react";
import { memo } from "react";

export type ChainOfThoughtProps = ComponentProps<"div"> & {
  open?: boolean;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
};

export const ChainOfThought = memo(
  ({
    className,
    children,
    open: _open,
    defaultOpen: _defaultOpen,
    onOpenChange: _onOpenChange,
    ...props
  }: ChainOfThoughtProps) => (
    <div className={cn("not-prose w-full", className)} {...props}>
      {children}
    </div>
  ),
);

export type ChainOfThoughtHeaderProps = ComponentProps<typeof CollapsibleTrigger>;

export const ChainOfThoughtHeader = memo(
  ({ className, children, ...props }: ChainOfThoughtHeaderProps) => {
    return (
      <CollapsibleTrigger
        className={cn(
          "flex w-full items-center gap-2 text-muted-foreground text-sm transition-colors hover:text-foreground",
          className,
        )}
        {...props}
      >
        <BrainIcon className="size-4" />
        <span className="flex-1 text-left">{children ?? "Chain of Thought"}</span>
        <ChevronDownIcon className="size-4 transition-transform group-data-[state=open]:rotate-180" />
      </CollapsibleTrigger>
    );
  },
);

export type ChainOfThoughtStepProps = ComponentProps<typeof Collapsible> & {
  icon?: LucideIcon;
  label?: ReactNode;
  description?: ReactNode;
  status?: "complete" | "active" | "pending";
  isLast?: boolean;
};

const stepStatusStyles = {
  active: "text-foreground",
  complete: "text-muted-foreground",
  pending: "text-muted-foreground/50",
};

export const ChainOfThoughtStep = memo(
  ({
    className,
    icon: Icon = DotIcon,
    label,
    description,
    status = "complete",
    isLast = false,
    children,
    ...props
  }: ChainOfThoughtStepProps) => (
    <Collapsible
      className={cn(
        "group/chain-step relative grid grid-cols-[1rem_minmax(0,1fr)] gap-3 text-sm",
        stepStatusStyles[status],
        "fade-in-0 slide-in-from-top-2 animate-in",
        className,
      )}
      {...props}
    >
      <div className="relative flex justify-center pt-1.5">
        <Icon className="relative z-10 size-4 bg-background" />
        {!isLast ? (
          <div className="absolute top-7 bottom-0 left-1/2 -mx-px w-px bg-border" />
        ) : null}
      </div>
      <div className="min-w-0 flex-1 space-y-2 overflow-hidden pb-4">
        {label ? <div>{label}</div> : null}
        {description && <div className="text-muted-foreground text-xs">{description}</div>}
        {children}
      </div>
    </Collapsible>
  ),
);

export type ChainOfThoughtTriggerProps = ComponentProps<typeof CollapsibleTrigger> & {
  leftIcon?: ReactNode;
  swapIconOnHover?: boolean;
};

export const ChainOfThoughtTrigger = memo(
  ({
    className,
    children,
    leftIcon,
    swapIconOnHover = true,
    ...props
  }: ChainOfThoughtTriggerProps) => (
    <CollapsibleTrigger
      className={cn(
        "group/chain-trigger flex w-full min-w-0 items-center gap-2 text-left text-sm text-foreground transition-colors hover:text-foreground",
        className,
      )}
      {...props}
    >
      {leftIcon !== null ? (
        <span className="flex size-4 shrink-0 items-center justify-center text-muted-foreground">
          {leftIcon === undefined ? <BrainIcon className="size-4" /> : leftIcon}
        </span>
      ) : null}
      <span className="min-w-0 flex-1">{children}</span>
      <span className="relative size-4 shrink-0 text-muted-foreground">
        <ChevronDownIcon
          className={cn(
            "absolute inset-0 size-4 transition-all group-data-[state=open]/chain-trigger:rotate-180",
            swapIconOnHover &&
              "group-hover/chain-trigger:scale-0 group-data-[state=open]/chain-trigger:scale-100",
          )}
        />
        {swapIconOnHover ? (
          <CheckIcon className="absolute inset-0 size-4 scale-0 transition-transform group-hover/chain-trigger:scale-100 group-data-[state=open]/chain-trigger:scale-0" />
        ) : null}
      </span>
    </CollapsibleTrigger>
  ),
);

export type ChainOfThoughtSearchResultsProps = ComponentProps<"div">;

export const ChainOfThoughtSearchResults = memo(
  ({ className, ...props }: ChainOfThoughtSearchResultsProps) => (
    <div className={cn("flex flex-wrap items-center gap-2", className)} {...props} />
  ),
);

export type ChainOfThoughtSearchResultProps = ComponentProps<typeof Badge>;

export const ChainOfThoughtSearchResult = memo(
  ({ className, children, ...props }: ChainOfThoughtSearchResultProps) => (
    <Badge
      className={cn("gap-1 px-2 py-0.5 font-normal text-xs", className)}
      variant="secondary"
      {...props}
    >
      {children}
    </Badge>
  ),
);

export type ChainOfThoughtContentProps = ComponentProps<typeof CollapsibleContent>;

export const ChainOfThoughtContent = memo(
  ({ className, children, ...props }: ChainOfThoughtContentProps) => (
    <CollapsibleContent
      className={cn(
        "mt-2 space-y-3",
        "data-[state=closed]:fade-out-0 data-[state=closed]:slide-out-to-top-2 data-[state=open]:slide-in-from-top-2 text-popover-foreground outline-none data-[state=closed]:animate-out data-[state=open]:animate-in",
        className,
      )}
      {...props}
    >
      {children}
    </CollapsibleContent>
  ),
);

export type ChainOfThoughtItemProps = ComponentProps<"div">;

export const ChainOfThoughtItem = memo(({ className, ...props }: ChainOfThoughtItemProps) => (
  <div className={cn("text-muted-foreground text-sm leading-6", className)} {...props} />
));

export type ChainOfThoughtImageProps = ComponentProps<"div"> & {
  caption?: string;
};

export const ChainOfThoughtImage = memo(
  ({ className, children, caption, ...props }: ChainOfThoughtImageProps) => (
    <div className={cn("mt-2 space-y-2", className)} {...props}>
      <div className="relative flex max-h-88 items-center justify-center overflow-hidden rounded-lg bg-muted p-3">
        {children}
      </div>
      {caption && <p className="text-muted-foreground text-xs">{caption}</p>}
    </div>
  ),
);

ChainOfThought.displayName = "ChainOfThought";
ChainOfThoughtHeader.displayName = "ChainOfThoughtHeader";
ChainOfThoughtStep.displayName = "ChainOfThoughtStep";
ChainOfThoughtTrigger.displayName = "ChainOfThoughtTrigger";
ChainOfThoughtSearchResults.displayName = "ChainOfThoughtSearchResults";
ChainOfThoughtSearchResult.displayName = "ChainOfThoughtSearchResult";
ChainOfThoughtContent.displayName = "ChainOfThoughtContent";
ChainOfThoughtItem.displayName = "ChainOfThoughtItem";
ChainOfThoughtImage.displayName = "ChainOfThoughtImage";
