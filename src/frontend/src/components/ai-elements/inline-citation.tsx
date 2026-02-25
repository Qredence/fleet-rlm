import type { ReactNode } from "react";
import { ExternalLink } from "lucide-react";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { cn } from "@/components/ui/utils";

function safeHref(href: string): string | undefined {
  try {
    const parsed = new URL(href);
    if (
      parsed.protocol === "http:" ||
      parsed.protocol === "https:" ||
      parsed.protocol === "file:"
    ) {
      return parsed.toString();
    }
    return undefined;
  } catch {
    return undefined;
  }
}

function InlineCitation({ children }: { children: ReactNode }) {
  return <span className="inline-flex align-baseline">{children}</span>;
}
function InlineCitationText({ children }: { children: ReactNode }) {
  return <>{children}</>;
}
function InlineCitationCard({ children }: { children: ReactNode }) {
  return <Popover>{children}</Popover>;
}
function InlineCitationCardTrigger({
  sources,
  className,
}: {
  sources: string[];
  className?: string;
}) {
  return (
    <PopoverTrigger asChild>
      <button
        type="button"
        className={cn(
          "ml-1 inline-flex items-center rounded-full border border-border-subtle px-1.5 py-0 text-[10px] text-muted-foreground hover:text-foreground",
          className,
        )}
        aria-label={`Citation (${sources.length})`}
      >
        [{sources.length}]
      </button>
    </PopoverTrigger>
  );
}
function InlineCitationCardBody({ children }: { children: ReactNode }) {
  return (
    <PopoverContent align="start" className="w-96 max-w-[90vw] p-3">
      {children}
    </PopoverContent>
  );
}
function InlineCitationCarousel({ children }: { children: ReactNode }) {
  return <div className="space-y-2">{children}</div>;
}
function InlineCitationCarouselHeader({ children }: { children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-2">{children}</div>
  );
}
function InlineCitationCarouselContent({ children }: { children: ReactNode }) {
  return <div>{children}</div>;
}
function InlineCitationCarouselItem({ children }: { children: ReactNode }) {
  return <div className="space-y-2">{children}</div>;
}
function InlineCitationCarouselIndex() {
  return <span className="text-xs text-muted-foreground">1/1</span>;
}
function InlineCitationCarouselPrev() {
  return <span className="text-xs text-muted-foreground">Prev</span>;
}
function InlineCitationCarouselNext() {
  return <span className="text-xs text-muted-foreground">Next</span>;
}
function InlineCitationSource({
  title,
  url,
  description,
}: {
  title: string;
  url: string;
  description?: string;
}) {
  const href = safeHref(url);
  return (
    <div className="space-y-1">
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer nofollow"
        className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline"
      >
        {title}
        <ExternalLink className="size-3.5" />
      </a>
      <div className="text-xs text-muted-foreground break-all">{url}</div>
      {description ? (
        <p className="text-xs text-muted-foreground">{description}</p>
      ) : null}
    </div>
  );
}
function InlineCitationQuote({ children }: { children: ReactNode }) {
  return (
    <blockquote className="rounded-md border border-border-subtle bg-muted/30 p-2 text-xs text-muted-foreground">
      {children}
    </blockquote>
  );
}

export {
  InlineCitation,
  InlineCitationText,
  InlineCitationCard,
  InlineCitationCardTrigger,
  InlineCitationCardBody,
  InlineCitationCarousel,
  InlineCitationCarouselHeader,
  InlineCitationCarouselContent,
  InlineCitationCarouselItem,
  InlineCitationCarouselIndex,
  InlineCitationCarouselPrev,
  InlineCitationCarouselNext,
  InlineCitationSource,
  InlineCitationQuote,
};
