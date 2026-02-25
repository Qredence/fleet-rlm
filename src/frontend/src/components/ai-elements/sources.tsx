import { ChevronDown, ExternalLink, Link2 } from "lucide-react";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { cn } from "@/components/ui/utils";

function safeHref(href: string | undefined): string | undefined {
  if (!href) return undefined;
  try {
    const parsed = new URL(href);
    if (
      parsed.protocol === "http:" ||
      parsed.protocol === "https:" ||
      parsed.protocol === "file:"
    ) {
      return href;
    }
    return undefined;
  } catch {
    return undefined;
  }
}

function domainForLabel(href: string): string {
  try {
    return new URL(href).hostname.replace(/^www\./, "");
  } catch {
    return href;
  }
}

function Sources({
  defaultOpen = false,
  className,
  ...props
}: React.ComponentProps<typeof Collapsible>) {
  return (
    <Collapsible
      data-slot="sources"
      defaultOpen={defaultOpen}
      className={cn("rounded-lg border border-border-subtle bg-card", className)}
      {...props}
    />
  );
}

function SourcesTrigger({
  count,
  className,
  ...props
}: React.ComponentProps<typeof CollapsibleTrigger> & { count: number }) {
  return (
    <CollapsibleTrigger
      data-slot="sources-trigger"
      className={cn(
        "group flex w-full items-center justify-between px-3 py-2 text-left",
        className,
      )}
      {...props}
    >
      <span className="inline-flex items-center gap-2 text-xs font-medium text-muted-foreground">
        <Link2 className="size-3.5" />
        Sources
        <span className="rounded-full border border-border-subtle px-1.5 py-0 text-[10px]">
          {count}
        </span>
      </span>
      <ChevronDown className="size-4 text-muted-foreground transition-transform group-data-[state=open]:rotate-180" />
    </CollapsibleTrigger>
  );
}

function SourcesContent({
  className,
  ...props
}: React.ComponentProps<typeof CollapsibleContent>) {
  return (
    <CollapsibleContent
      data-slot="sources-content"
      className={cn("border-t border-border-subtle p-2", className)}
      {...props}
    />
  );
}

function Source({
  href,
  title,
  className,
  children,
  ...props
}: React.AnchorHTMLAttributes<HTMLAnchorElement>) {
  const hrefSafe = safeHref(href);
  const sourceTitle = title || (hrefSafe ? domainForLabel(hrefSafe) : "Source");

  return (
    <a
      data-slot="source"
      href={hrefSafe}
      target="_blank"
      rel="noopener noreferrer nofollow"
      className={cn(
        "block rounded-md border border-border-subtle p-2 transition-colors hover:bg-muted/40",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        className,
      )}
      {...props}
    >
      <div className="flex items-center gap-2">
        <span className="truncate text-xs font-medium text-foreground">
          {sourceTitle}
        </span>
        <ExternalLink className="ml-auto size-3.5 shrink-0 text-muted-foreground" />
      </div>
      {hrefSafe ? (
        <div className="mt-1 truncate text-[11px] text-muted-foreground">
          {domainForLabel(hrefSafe)}
        </div>
      ) : null}
      {children ? (
        <div className="mt-1 text-xs text-muted-foreground">{children}</div>
      ) : null}
    </a>
  );
}

export { Sources, SourcesTrigger, SourcesContent, Source };
