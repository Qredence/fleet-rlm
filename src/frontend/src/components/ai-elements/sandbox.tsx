import { ChevronDown, TerminalSquare } from "lucide-react";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/components/ui/utils";
import type { ToolState } from "@/components/ai-elements/tool";

function Sandbox(props: React.ComponentProps<typeof Collapsible>) {
  return (
    <Collapsible
      {...props}
      className={cn(
        "rounded-lg border border-border-subtle bg-card",
        props.className,
      )}
    />
  );
}

function SandboxHeader({
  title,
  state,
  className,
  ...props
}: React.ComponentProps<typeof CollapsibleTrigger> & {
  title?: string;
  state: ToolState;
}) {
  return (
    <CollapsibleTrigger
      className={cn(
        "group flex w-full items-center gap-2 px-3 py-2 text-left",
        className,
      )}
      {...props}
    >
      <TerminalSquare className="size-4 text-muted-foreground" />
      <span className="min-w-0 flex-1 text-sm font-medium text-foreground">
        {title ?? "Sandbox"}
      </span>
      <span className="rounded-full border border-border-subtle px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
        {state.replace(/_/g, " ")}
      </span>
      <ChevronDown className="size-4 text-muted-foreground transition-transform group-data-[state=open]:rotate-180" />
    </CollapsibleTrigger>
  );
}

function SandboxContent(
  props: React.ComponentProps<typeof CollapsibleContent>,
) {
  return (
    <CollapsibleContent
      {...props}
      className={cn("border-t border-border-subtle", props.className)}
    />
  );
}

function SandboxTabs(props: React.ComponentProps<typeof Tabs>) {
  return <Tabs {...props} className={cn("w-full", props.className)} />;
}
function SandboxTabsBar(props: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("border-b border-border-subtle px-3 py-2", props.className)}
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
