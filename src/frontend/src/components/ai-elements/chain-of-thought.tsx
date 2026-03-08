import type { ReactNode } from "react";
import {
  ChevronDown,
  Circle,
  LoaderCircle,
  CheckCircle2,
  AlertCircle,
} from "lucide-react";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { cn } from "@/lib/utils/cn";

type StepStatus = "pending" | "active" | "complete" | "error";
export type ChainOfThoughtDensity = "default" | "compact";
export type ChainOfThoughtDisclosure =
  | "auto"
  | "always_open"
  | "always_collapsed";

function statusIcon(status: StepStatus) {
  if (status === "complete")
    return <CheckCircle2 className="size-3.5 text-muted-foreground" />;
  if (status === "active")
    return <LoaderCircle className="size-3.5 animate-spin text-accent" />;
  if (status === "error")
    return <AlertCircle className="size-3.5 text-destructive" />;
  return <Circle className="size-3 text-muted-foreground" />;
}

function ChainOfThought({
  children,
  density = "default",
  disclosure = "auto",
  defaultOpen,
  className,
  ...props
}: React.ComponentProps<typeof Collapsible> & {
  density?: ChainOfThoughtDensity;
  disclosure?: ChainOfThoughtDisclosure;
}) {
  const resolvedDefaultOpen =
    disclosure === "always_open"
      ? true
      : disclosure === "always_collapsed"
        ? false
        : defaultOpen;
  return (
    <Collapsible
      defaultOpen={resolvedDefaultOpen}
      className={cn(
        density === "compact"
          ? "rounded-xl border-subtle/80 bg-card/70"
          : "rounded-xl border-subtle bg-card",
        className,
      )}
      {...props}
    >
      {children}
    </Collapsible>
  );
}

function ChainOfThoughtHeader({
  density = "default",
  className,
  children,
  ...props
}: React.ComponentProps<typeof CollapsibleTrigger> & {
  density?: ChainOfThoughtDensity;
}) {
  return (
    <CollapsibleTrigger
      className={cn(
        density === "compact"
          ? "group flex w-full items-center justify-between px-2.5 py-2 text-left transition-colors hover:bg-muted/20"
          : "group flex w-full items-center justify-between px-3 py-2 text-left",
        className,
      )}
      {...props}
    >
      <div
        className={cn(
          "font-medium text-muted-foreground",
          density === "compact"
            ? "text-[11px] tracking-[0.01em]"
            : "text-xs tracking-normal",
        )}
      >
        {children ?? "Execution trace"}
      </div>
      <ChevronDown className="size-3.5 text-muted-foreground transition-transform group-data-[state=open]:rotate-180" />
    </CollapsibleTrigger>
  );
}

function ChainOfThoughtContent({
  className,
  ...props
}: React.ComponentProps<typeof CollapsibleContent>) {
  return (
    <CollapsibleContent
      className={cn("border-t border-border-subtle/80", className)}
      {...props}
    />
  );
}

function ChainOfThoughtStep({
  label,
  status = "pending",
  icon: Icon,
  children,
  className,
}: {
  label: string;
  status?: StepStatus;
  icon?: React.ComponentType<{ className?: string }>;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn("px-2.5 py-2", className)}
      data-slot="cot-step"
      data-status={status}
    >
      <div className="flex items-start gap-2">
        <div className="mt-0.5 shrink-0">
          {Icon ? (
            <Icon className="size-3.5 text-muted-foreground" />
          ) : (
            statusIcon(status)
          )}
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-[13px] text-foreground">{label}</div>
          {children ? (
            <div className="mt-1 text-[11px] text-muted-foreground space-y-1 leading-relaxed">
              {children}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function ChainOfThoughtSearchResults({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn("mt-1 flex flex-wrap gap-1.5", className)} {...props} />
  );
}
function ChainOfThoughtSearchResult({
  className,
  ...props
}: React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn(
        "rounded-full border-subtle bg-muted/40 px-2 py-0.5 text-[11px]",
        className,
      )}
      {...props}
    />
  );
}
function ChainOfThoughtImage({
  caption,
  children,
  className,
}: {
  caption?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <figure className={cn("mt-2 space-y-1", className)}>
      {children}
      {caption ? (
        <figcaption className="text-[11px] text-muted-foreground">
          {caption}
        </figcaption>
      ) : null}
    </figure>
  );
}

export {
  ChainOfThought,
  ChainOfThoughtHeader,
  ChainOfThoughtContent,
  ChainOfThoughtStep,
  ChainOfThoughtSearchResults,
  ChainOfThoughtSearchResult,
  ChainOfThoughtImage,
};
