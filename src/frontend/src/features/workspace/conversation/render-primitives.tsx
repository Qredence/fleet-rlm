import type {
  ComponentProps,
  HTMLAttributes,
  MouseEvent as ReactMouseEvent,
  ReactNode,
} from "react";
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import type { FileUIPart, SourceDocumentUIPart, ToolUIPart } from "ai";
import {
  BrainIcon,
  Check,
  CheckIcon,
  ChevronDownIcon,
  ChevronRight,
  Code,
  CopyIcon,
  EyeIcon,
  EyeOffIcon,
  FileTextIcon,
  GlobeIcon,
  ImageIcon,
  Music2Icon,
  PaperclipIcon,
  VideoIcon,
  XIcon,
} from "lucide-react";

import { getStatusBadge } from "@/components/ai-elements/tool";
import { TimelineStep, type TimelineStepProps } from "@/components/product/timeline";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { HoverCard, HoverCardContent, HoverCardTrigger } from "@/components/ui/hover-card";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useControllableState } from "@/hooks/use-controllable-state";
import { cn } from "@/lib/utils";

type AttachmentData = (FileUIPart & { id: string }) | (SourceDocumentUIPart & { id: string });

type AttachmentMediaCategory = "image" | "video" | "audio" | "document" | "source" | "unknown";
type AttachmentVariant = "grid" | "inline" | "list";

const mediaCategoryIcons: Record<AttachmentMediaCategory, typeof ImageIcon> = {
  audio: Music2Icon,
  document: FileTextIcon,
  image: ImageIcon,
  source: GlobeIcon,
  unknown: PaperclipIcon,
  video: VideoIcon,
};

function getMediaCategory(data: AttachmentData): AttachmentMediaCategory {
  if (data.type === "source-document") {
    return "source";
  }

  const mediaType = data.mediaType ?? "";
  if (mediaType.startsWith("image/")) return "image";
  if (mediaType.startsWith("video/")) return "video";
  if (mediaType.startsWith("audio/")) return "audio";
  if (mediaType.startsWith("application/") || mediaType.startsWith("text/")) return "document";
  return "unknown";
}

function getAttachmentLabel(data: AttachmentData): string {
  if (data.type === "source-document") {
    return data.title || data.filename || "Source";
  }

  const category = getMediaCategory(data);
  return data.filename || (category === "image" ? "Image" : "Attachment");
}

const AttachmentsContext = createContext<{ variant: AttachmentVariant }>({ variant: "grid" });
const AttachmentContext = createContext<{
  data: AttachmentData;
  mediaCategory: AttachmentMediaCategory;
  onRemove?: () => void;
  variant: AttachmentVariant;
} | null>(null);

function useAttachmentContext() {
  const context = useContext(AttachmentContext);
  if (!context) {
    throw new Error("Attachment components must be used within <Attachment>");
  }
  return context;
}

export function Attachments({
  variant = "grid",
  className,
  children,
  ...props
}: HTMLAttributes<HTMLDivElement> & { variant?: AttachmentVariant }) {
  return (
    <AttachmentsContext.Provider value={{ variant }}>
      <div
        className={cn(
          "flex items-start",
          variant === "list" ? "flex-col gap-2" : "flex-wrap gap-2",
          variant === "grid" && "ml-auto w-fit",
          className,
        )}
        {...props}
      >
        {children}
      </div>
    </AttachmentsContext.Provider>
  );
}

export function Attachment({
  data,
  onRemove,
  className,
  children,
  ...props
}: HTMLAttributes<HTMLDivElement> & {
  data: AttachmentData;
  onRemove?: () => void;
}) {
  const { variant } = useContext(AttachmentsContext);
  const mediaCategory = getMediaCategory(data);

  return (
    <AttachmentContext.Provider value={{ data, mediaCategory, onRemove, variant }}>
      <div
        className={cn(
          "group relative",
          variant === "grid" && "size-24 overflow-hidden rounded-lg",
          variant === "inline" && [
            "flex h-8 cursor-pointer select-none items-center gap-1.5 rounded-md border border-border px-1.5",
            "font-medium text-sm transition-all hover:bg-accent hover:text-accent-foreground dark:hover:bg-accent/50",
          ],
          variant === "list" &&
            "flex w-full items-center gap-3 rounded-lg border p-3 hover:bg-accent/50",
          className,
        )}
        {...props}
      >
        {children}
      </div>
    </AttachmentContext.Provider>
  );
}

export function AttachmentPreview({
  fallbackIcon,
  className,
  ...props
}: HTMLAttributes<HTMLDivElement> & { fallbackIcon?: ReactNode }) {
  const { data, mediaCategory, variant } = useAttachmentContext();
  const iconSize = variant === "inline" ? "size-3" : "size-4";
  const Icon = mediaCategoryIcons[mediaCategory];

  return (
    <div
      className={cn(
        "flex shrink-0 items-center justify-center overflow-hidden",
        variant === "grid" && "size-full bg-muted",
        variant === "inline" && "size-5 rounded bg-background",
        variant === "list" && "size-12 rounded bg-muted",
        className,
      )}
      {...props}
    >
      {mediaCategory === "image" && data.type === "file" && data.url ? (
        <img
          src={data.url}
          alt={data.filename || "Image"}
          className={cn("size-full object-cover", variant === "inline" && "rounded")}
          height={variant === "grid" ? 96 : 20}
          width={variant === "grid" ? 96 : 20}
        />
      ) : mediaCategory === "video" && data.type === "file" && data.url ? (
        <video className="size-full object-cover" muted src={data.url} />
      ) : (
        (fallbackIcon ?? <Icon className={cn(iconSize, "text-muted-foreground")} />)
      )}
    </div>
  );
}

export function AttachmentInfo({
  showMediaType = false,
  className,
  ...props
}: HTMLAttributes<HTMLDivElement> & { showMediaType?: boolean }) {
  const { data, variant } = useAttachmentContext();
  if (variant === "grid") return null;

  return (
    <div className={cn("min-w-0 flex-1", className)} {...props}>
      <span className="block truncate">{getAttachmentLabel(data)}</span>
      {showMediaType && data.mediaType ? (
        <span className="block truncate text-xs text-muted-foreground">{data.mediaType}</span>
      ) : null}
    </div>
  );
}

export function AttachmentRemove({
  label = "Remove",
  className,
  children,
  ...props
}: ComponentProps<typeof Button> & { label?: string }) {
  const { onRemove, variant } = useAttachmentContext();

  const handleClick = useCallback(
    (event: ReactMouseEvent) => {
      event.stopPropagation();
      onRemove?.();
    },
    [onRemove],
  );

  if (!onRemove) return null;

  return (
    <Button
      aria-label={label}
      className={cn(
        variant === "grid" && [
          "absolute right-2 top-2 size-6 rounded-full bg-background/80 p-0 opacity-0 backdrop-blur-sm transition-opacity group-hover:opacity-100 hover:bg-background [&>svg]:size-3",
        ],
        variant === "inline" && [
          "size-5 rounded p-0 opacity-0 transition-opacity group-hover:opacity-100 [&>svg]:size-2.5",
        ],
        variant === "list" && "size-8 shrink-0 rounded p-0 [&>svg]:size-4",
        className,
      )}
      onClick={handleClick}
      type="button"
      variant="ghost"
      {...props}
    >
      {children ?? <XIcon />}
      <span className="sr-only">{label}</span>
    </Button>
  );
}

export function AttachmentHoverCard({
  openDelay = 0,
  closeDelay = 0,
  ...props
}: ComponentProps<typeof HoverCard>) {
  return <HoverCard openDelay={openDelay} closeDelay={closeDelay} {...props} />;
}

export const AttachmentHoverCardTrigger = HoverCardTrigger;

export function AttachmentHoverCardContent({
  align = "start",
  className,
  ...props
}: ComponentProps<typeof HoverCardContent>) {
  return <HoverCardContent align={align} className={cn("w-auto p-2", className)} {...props} />;
}

const ChainOfThoughtContext = createContext<{
  isOpen: boolean;
  setIsOpen: (open: boolean) => void;
} | null>(null);

function useChainOfThought() {
  const context = useContext(ChainOfThoughtContext);
  if (!context) {
    throw new Error("ChainOfThought components must be used within ChainOfThought");
  }
  return context;
}

export function ChainOfThought({
  className,
  open,
  defaultOpen = false,
  onOpenChange,
  children,
  ...props
}: ComponentProps<"div"> & {
  open?: boolean;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
}) {
  const [isOpen, setIsOpen] = useControllableState({
    defaultProp: defaultOpen,
    onChange: onOpenChange,
    prop: open,
  });

  return (
    <ChainOfThoughtContext.Provider value={{ isOpen, setIsOpen }}>
      <div className={cn("not-prose flex w-full flex-col gap-4", className)} {...props}>
        {children}
      </div>
    </ChainOfThoughtContext.Provider>
  );
}

export function ChainOfThoughtHeader({
  className,
  children,
  ...props
}: ComponentProps<typeof CollapsibleTrigger>) {
  const { isOpen, setIsOpen } = useChainOfThought();

  return (
    <Collapsible open={isOpen} onOpenChange={setIsOpen}>
      <CollapsibleTrigger
        className={cn(
          "flex w-full items-center gap-2 text-sm text-muted-foreground transition-colors hover:text-foreground",
          className,
        )}
        {...props}
      >
        <BrainIcon className="size-4" />
        <span className="flex-1 text-left">{children ?? "Chain of Thought"}</span>
        <ChevronDownIcon
          className={cn("size-4 transition-transform", isOpen ? "rotate-180" : "rotate-0")}
        />
      </CollapsibleTrigger>
    </Collapsible>
  );
}

export const ChainOfThoughtStep = TimelineStep as (props: TimelineStepProps) => ReactNode;

export function ChainOfThoughtContent({
  className,
  children,
  ...props
}: ComponentProps<typeof CollapsibleContent>) {
  const { isOpen } = useChainOfThought();

  return (
    <Collapsible open={isOpen}>
      <CollapsibleContent
        className={cn(
          "mt-2 flex flex-col gap-3 outline-none data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:slide-out-to-top-2 data-[state=open]:animate-in data-[state=open]:slide-in-from-top-2 text-popover-foreground",
          className,
        )}
        {...props}
      >
        {children}
      </CollapsibleContent>
    </Collapsible>
  );
}

type ToolUIPartApproval =
  | { id: string; approved?: never; reason?: never }
  | { id: string; approved: boolean; reason?: string }
  | undefined;

const ConfirmationContext = createContext<{
  approval: ToolUIPartApproval;
  state: ToolUIPart["state"];
} | null>(null);

function useConfirmation() {
  const context = useContext(ConfirmationContext);
  if (!context) {
    throw new Error("Confirmation components must be used within Confirmation");
  }
  return context;
}

export function Confirmation({
  className,
  approval,
  state,
  ...props
}: ComponentProps<typeof Alert> & {
  approval?: ToolUIPartApproval;
  state: ToolUIPart["state"];
}) {
  if (!approval || state === "input-streaming" || state === "input-available") {
    return null;
  }

  return (
    <ConfirmationContext.Provider value={{ approval, state }}>
      <Alert className={cn("flex flex-col gap-2", className)} {...props} />
    </ConfirmationContext.Provider>
  );
}

export function ConfirmationTitle({
  className,
  ...props
}: ComponentProps<typeof AlertDescription>) {
  return <AlertDescription className={cn("inline", className)} {...props} />;
}

export function ConfirmationRequest({ children }: { children?: ReactNode }) {
  const { state } = useConfirmation();
  return state === "approval-requested" ? children : null;
}

export function ConfirmationAccepted({ children }: { children?: ReactNode }) {
  const { approval, state } = useConfirmation();
  if (
    !approval?.approved ||
    (state !== "approval-responded" && state !== "output-denied" && state !== "output-available")
  ) {
    return null;
  }
  return children;
}

export function ConfirmationRejected({ children }: { children?: ReactNode }) {
  const { approval, state } = useConfirmation();
  if (
    approval?.approved !== false ||
    (state !== "approval-responded" && state !== "output-denied" && state !== "output-available")
  ) {
    return null;
  }
  return children;
}

export function ConfirmationActions({ className, ...props }: ComponentProps<"div">) {
  const { state } = useConfirmation();
  if (state !== "approval-requested") return null;
  return (
    <div className={cn("flex items-center justify-end gap-2 self-end", className)} {...props} />
  );
}

export function ConfirmationAction(props: ComponentProps<typeof Button>) {
  return <Button className="h-8 px-3 text-sm" type="button" {...props} />;
}

const EnvironmentVariablesContext = createContext<{
  showValues: boolean;
  setShowValues: (show: boolean) => void;
}>({
  showValues: false,
  setShowValues: () => undefined,
});

const EnvironmentVariableContext = createContext<{ name: string; value: string }>({
  name: "",
  value: "",
});

export function EnvironmentVariables({
  showValues: controlledShowValues,
  defaultShowValues = false,
  onShowValuesChange,
  className,
  children,
  ...props
}: HTMLAttributes<HTMLDivElement> & {
  showValues?: boolean;
  defaultShowValues?: boolean;
  onShowValuesChange?: (show: boolean) => void;
}) {
  const [internalShowValues, setInternalShowValues] = useState(defaultShowValues);
  const showValues = controlledShowValues ?? internalShowValues;
  const setShowValues = useCallback(
    (show: boolean) => {
      setInternalShowValues(show);
      onShowValuesChange?.(show);
    },
    [onShowValuesChange],
  );

  return (
    <EnvironmentVariablesContext.Provider value={{ showValues, setShowValues }}>
      <div className={cn("rounded-lg border bg-background", className)} {...props}>
        {children}
      </div>
    </EnvironmentVariablesContext.Provider>
  );
}

export function EnvironmentVariablesHeader({
  className,
  children,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("flex items-center justify-between border-b px-4 py-3", className)}
      {...props}
    >
      {children}
    </div>
  );
}

export function EnvironmentVariablesTitle({
  className,
  children,
  ...props
}: HTMLAttributes<HTMLHeadingElement>) {
  return (
    <h3 className={cn("text-sm font-medium", className)} {...props}>
      {children ?? "Environment Variables"}
    </h3>
  );
}

export function EnvironmentVariablesToggle({ className, ...props }: ComponentProps<typeof Switch>) {
  const { showValues, setShowValues } = useContext(EnvironmentVariablesContext);

  return (
    <div className={cn("flex items-center gap-2", className)}>
      <span className="text-xs text-muted-foreground">
        {showValues ? <EyeIcon size={14} /> : <EyeOffIcon size={14} />}
      </span>
      <Switch
        aria-label="Toggle value visibility"
        checked={showValues}
        onCheckedChange={setShowValues}
        {...props}
      />
    </div>
  );
}

export function EnvironmentVariablesContent({
  className,
  children,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn("divide-y", className)} {...props}>
      {children}
    </div>
  );
}

export function EnvironmentVariable({
  name,
  value,
  className,
  children,
  ...props
}: HTMLAttributes<HTMLDivElement> & { name: string; value: string }) {
  return (
    <EnvironmentVariableContext.Provider value={{ name, value }}>
      <div
        className={cn("flex items-center justify-between gap-4 px-4 py-3", className)}
        {...props}
      >
        {children}
      </div>
    </EnvironmentVariableContext.Provider>
  );
}

export function EnvironmentVariableGroup({
  className,
  children,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn("flex items-center gap-2", className)} {...props}>
      {children}
    </div>
  );
}

export function EnvironmentVariableName({
  className,
  children,
  ...props
}: HTMLAttributes<HTMLSpanElement>) {
  const { name } = useContext(EnvironmentVariableContext);
  return (
    <span className={cn("font-mono text-sm", className)} {...props}>
      {children ?? name}
    </span>
  );
}

export function EnvironmentVariableValue({
  className,
  children,
  ...props
}: HTMLAttributes<HTMLSpanElement>) {
  const { value } = useContext(EnvironmentVariableContext);
  const { showValues } = useContext(EnvironmentVariablesContext);
  const displayValue = showValues ? value : "•".repeat(Math.min(value.length, 20));

  return (
    <span
      className={cn(
        "font-mono text-sm text-muted-foreground",
        !showValues && "select-none",
        className,
      )}
      {...props}
    >
      {children ?? displayValue}
    </span>
  );
}

export function EnvironmentVariableCopyButton({
  onCopy,
  onError,
  timeout = 2000,
  copyFormat = "value",
  children,
  className,
  ...props
}: ComponentProps<typeof Button> & {
  onCopy?: () => void;
  onError?: (error: Error) => void;
  timeout?: number;
  copyFormat?: "name" | "value" | "export";
}) {
  const [isCopied, setIsCopied] = useState(false);
  const timeoutRef = useRef<number | null>(null);
  const { name, value } = useContext(EnvironmentVariableContext);

  const clearCopyTimeout = useCallback(() => {
    if (timeoutRef.current !== null) {
      window.clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
  }, []);

  useEffect(() => clearCopyTimeout, [clearCopyTimeout]);

  const getTextToCopy = useCallback(() => {
    if (copyFormat === "name") return name;
    if (copyFormat === "export") return `export ${name}="${value}"`;
    return value;
  }, [copyFormat, name, value]);

  const copyToClipboard = useCallback(async () => {
    if (typeof window === "undefined" || !navigator?.clipboard?.writeText) {
      onError?.(new Error("Clipboard API not available"));
      return;
    }

    try {
      await navigator.clipboard.writeText(getTextToCopy());
      setIsCopied(true);
      onCopy?.();
      clearCopyTimeout();
      timeoutRef.current = window.setTimeout(() => {
        setIsCopied(false);
        timeoutRef.current = null;
      }, timeout);
    } catch (error) {
      onError?.(error as Error);
    }
  }, [clearCopyTimeout, getTextToCopy, onCopy, onError, timeout]);

  return (
    <Button
      className={cn("size-6 shrink-0", className)}
      onClick={copyToClipboard}
      size="icon"
      variant="ghost"
      {...props}
    >
      {children ?? (isCopied ? <CheckIcon size={12} /> : <CopyIcon size={12} />)}
    </Button>
  );
}

export function EnvironmentVariableRequired({
  className,
  children,
  ...props
}: ComponentProps<typeof Badge>) {
  return (
    <Badge className={cn("text-xs", className)} variant="secondary" {...props}>
      {children ?? "Required"}
    </Badge>
  );
}

export function Sandbox({ className, ...props }: ComponentProps<typeof Collapsible>) {
  return (
    <Collapsible
      className={cn("not-prose group mb-4 w-full overflow-hidden rounded-md border", className)}
      defaultOpen
      {...props}
    />
  );
}

export function SandboxHeader({
  title,
  state,
  className,
  ...props
}: {
  title?: string;
  state: ToolUIPart["state"];
  className?: string;
}) {
  return (
    <CollapsibleTrigger
      className={cn("flex w-full items-center justify-between gap-4 p-3", className)}
      {...props}
    >
      <div className="flex items-center gap-2">
        <Code className="size-4 text-muted-foreground" />
        <span className="text-sm font-medium">{title}</span>
        {getStatusBadge(state)}
      </div>
      <ChevronDownIcon className="size-4 text-muted-foreground transition-transform group-data-[state=open]:rotate-180" />
    </CollapsibleTrigger>
  );
}

export function SandboxContent({ className, ...props }: ComponentProps<typeof CollapsibleContent>) {
  return (
    <CollapsibleContent
      className={cn(
        "outline-none data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:slide-out-to-top-2 data-[state=open]:animate-in data-[state=open]:slide-in-from-top-2",
        className,
      )}
      {...props}
    />
  );
}

export function SandboxTabs({ className, ...props }: ComponentProps<typeof Tabs>) {
  return <Tabs className={cn("w-full gap-0", className)} {...props} />;
}

export function SandboxTabsBar({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      className={cn("flex w-full items-center border-b border-t border-border", className)}
      {...props}
    />
  );
}

export function SandboxTabsList({ className, ...props }: ComponentProps<typeof TabsList>) {
  return (
    <TabsList
      className={cn("h-auto rounded-none border-0 bg-transparent p-0", className)}
      {...props}
    />
  );
}

export function SandboxTabsTrigger({ className, ...props }: ComponentProps<typeof TabsTrigger>) {
  return (
    <TabsTrigger
      className={cn(
        "rounded-none border-0 border-b-2 border-transparent px-4 py-2 text-sm font-medium text-muted-foreground transition-colors data-[active]:border-primary data-[active]:bg-transparent data-[active]:text-foreground data-[active]:shadow-none",
        className,
      )}
      {...props}
    />
  );
}

export function SandboxTabContent({ className, ...props }: ComponentProps<typeof TabsContent>) {
  return <TabsContent className={cn("mt-0 text-sm", className)} {...props} />;
}

export function Queue({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div data-slot="queue" className={cn("flex flex-col gap-2", className)}>
      {children}
    </div>
  );
}

export function QueueSection({
  children,
  defaultOpen = true,
  className,
}: {
  children: ReactNode;
  defaultOpen?: boolean;
  className?: string;
}) {
  return (
    <Collapsible defaultOpen={defaultOpen}>
      <div
        data-slot="queue-section"
        className={cn("overflow-hidden rounded-xl bg-card/70 border-subtle/80", className)}
      >
        {children}
      </div>
    </Collapsible>
  );
}

export function QueueSectionTrigger({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <CollapsibleTrigger asChild>
      <button
        type="button"
        data-slot="queue-section-trigger"
        className={cn(
          "group flex w-full items-center gap-2 px-3 py-2.5 transition-colors hover:bg-muted/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50",
          className,
        )}
        aria-label="Toggle section"
      >
        <ChevronRight className="size-3.5 shrink-0 text-muted-foreground transition-transform group-data-[state=open]:rotate-90" />
        {children}
      </button>
    </CollapsibleTrigger>
  );
}

export function QueueSectionLabel({
  label,
  count,
  className,
}: {
  label: string;
  count?: number;
  className?: string;
}) {
  return (
    <span data-slot="queue-section-label" className={cn("flex items-center gap-2", className)}>
      <span className="typo-label text-foreground">{label}</span>
      {count != null ? <span className="typo-helper text-muted-foreground">{count}</span> : null}
    </span>
  );
}

export function QueueSectionContent({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <CollapsibleContent>
      <div
        data-slot="queue-section-content"
        className={cn("border-t border-border-subtle/80", className)}
      >
        {children}
      </div>
    </CollapsibleContent>
  );
}

export function QueueList({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <ul
      data-slot="queue-list"
      role="list"
      className={cn("divide-y divide-border-subtle/80", className)}
    >
      {children}
    </ul>
  );
}

export function QueueItem({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <li
      data-slot="queue-item"
      className={cn("flex flex-wrap items-start gap-2.5 px-3 py-2.5", className)}
    >
      {children}
    </li>
  );
}

export function QueueItemIndicator({
  completed = false,
  className,
}: {
  completed?: boolean;
  className?: string;
}) {
  return (
    <div
      data-slot="queue-item-indicator"
      className={cn(
        "mt-px flex size-4 shrink-0 items-center justify-center rounded-full transition-colors",
        completed ? "bg-muted/40" : "border border-border-strong/70",
        className,
      )}
    >
      {completed ? <Check className="size-2.5 text-muted-foreground" strokeWidth={3} /> : null}
    </div>
  );
}

export function QueueItemContent({
  children,
  className,
}: {
  children: ReactNode;
  completed?: boolean;
  className?: string;
}) {
  return (
    <span
      data-slot="queue-item-content"
      className={cn("typo-label min-w-0 flex-1 text-foreground", className)}
    >
      {children}
    </span>
  );
}

export function QueueItemDescription({
  children,
  completed,
  className,
}: {
  children: ReactNode;
  completed?: boolean;
  className?: string;
}) {
  return (
    <span
      data-slot="queue-item-description"
      className={cn(
        "typo-caption w-full pl-6",
        completed ? "text-muted-foreground" : "text-muted-foreground/70",
        className,
      )}
    >
      {children}
    </span>
  );
}
