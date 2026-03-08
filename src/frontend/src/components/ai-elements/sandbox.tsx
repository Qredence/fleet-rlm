import { ChevronDown, TerminalSquare } from "lucide-react";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils/cn";
import type { ToolState } from "@/components/ai-elements/tool";

export type SandboxDensity = "default" | "compact";
export type SandboxDisclosure = "auto" | "always_open" | "always_collapsed";

function Sandbox({
  density = "default",
  disclosure = "auto",
  defaultOpen,
  className,
  ...props
}: React.ComponentProps<typeof Collapsible> & {
  density?: SandboxDensity;
  disclosure?: SandboxDisclosure;
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
      {...props}
      className={cn(
        density === "compact"
          ? "rounded-xl border-subtle/80 bg-card/70"
          : "rounded-xl border-subtle bg-card",
        className,
      )}
    />
  );
}

function SandboxHeader({
  title,
  state,
  density = "default",
  className,
  ...props
}: React.ComponentProps<typeof CollapsibleTrigger> & {
  title?: string;
  state: ToolState;
  density?: SandboxDensity;
}) {
  return (
    <CollapsibleTrigger
      className={cn(
        density === "compact"
          ? "group flex w-full items-center gap-2 px-2.5 py-2 text-left transition-colors hover:bg-muted/20"
          : "group flex w-full items-center gap-2 px-3 py-2 text-left",
        className,
      )}
      {...props}
    >
      <TerminalSquare className="size-3.5 text-muted-foreground" />
      <span
        className={cn(
          "min-w-0 flex-1 font-medium text-foreground",
          density === "compact" ? "text-[13px]" : "text-sm",
        )}
      >
        {title ?? "Sandbox"}
      </span>
      <span className="rounded-full border-subtle px-1.5 py-0.5 text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
        {state.replace(/_/g, " ")}
      </span>
      <ChevronDown className="size-3.5 text-muted-foreground transition-transform group-data-[state=open]:rotate-180" />
    </CollapsibleTrigger>
  );
}

function SandboxContent(
  props: React.ComponentProps<typeof CollapsibleContent>,
) {
  return (
    <CollapsibleContent
      {...props}
      className={cn("border-t border-border-subtle/80", props.className)}
    />
  );
}

function SandboxTabs(props: React.ComponentProps<typeof Tabs>) {
  return <Tabs {...props} className={cn("w-full", props.className)} />;
}
function SandboxTabsBar(props: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "border-b border-border-subtle/80 px-2.5 py-1.5",
        props.className,
      )}
      {...props}
    />
  );
}
function SandboxTabsList(props: React.ComponentProps<typeof TabsList>) {
  return (
    <TabsList
      {...props}
      className={cn("grid w-fit grid-cols-2", props.className)}
    />
  );
}
function SandboxTabsTrigger(props: React.ComponentProps<typeof TabsTrigger>) {
  return <TabsTrigger {...props} />;
}
function SandboxTabContent(props: React.ComponentProps<typeof TabsContent>) {
  return (
    <TabsContent {...props} className={cn("m-0 px-3 py-2", props.className)} />
  );
}

export {
  Sandbox,
  SandboxHeader,
  SandboxContent,
  SandboxTabs,
  SandboxTabsBar,
  SandboxTabsList,
  SandboxTabsTrigger,
  SandboxTabContent,
};
