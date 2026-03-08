import {
  createContext,
  useContext,
  type ReactNode,
  type RefObject,
} from "react";
import { ChevronDown, Download, MessageSquare } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils/cn";

interface ConversationContextValue {
  isAtBottom?: boolean;
  scrollToBottom?: () => void;
}

const ConversationContext = createContext<ConversationContextValue | null>(
  null,
);

interface ConversationProps extends React.HTMLAttributes<HTMLDivElement> {
  children: ReactNode;
  scrollRef?: RefObject<HTMLDivElement | null>;
  contentRef?: RefObject<HTMLDivElement | null>;
  isAtBottom?: boolean;
  scrollToBottom?: () => void;
}

function Conversation({
  children,
  className,
  scrollRef,
  contentRef,
  isAtBottom,
  scrollToBottom,
  ...props
}: ConversationProps) {
  return (
    <ConversationContext.Provider value={{ isAtBottom, scrollToBottom }}>
      <div {...props} className={cn("relative flex-1 min-h-0", className)}>
        <div
          ref={scrollRef}
          className="h-full overflow-y-auto"
          style={{ overscrollBehavior: "contain" }}
          data-slot="conversation-viewport"
        >
          <div ref={contentRef} data-slot="conversation-content-root">
            {children}
          </div>
        </div>
      </div>
    </ConversationContext.Provider>
  );
}

interface ConversationContentProps extends React.HTMLAttributes<HTMLDivElement> {
  children: ReactNode;
}

function ConversationContent({
  children,
  className,
  ...props
}: ConversationContentProps) {
  return (
    <div {...props} className={cn("px-3 py-4 md:px-5 md:py-6", className)}>
      <div className="mx-auto w-full max-w-[800px] space-y-5 md:space-y-6">
        {children}
      </div>
    </div>
  );
}

interface ConversationEmptyStateProps extends React.HTMLAttributes<HTMLDivElement> {
  title?: string;
  description?: string;
  icon?: ReactNode;
  children?: ReactNode;
}

function ConversationEmptyState({
  title,
  description,
  icon,
  children,
  className,
  ...props
}: ConversationEmptyStateProps) {
  const resolvedIcon =
    icon === undefined ? (
      <MessageSquare className="size-10" aria-hidden />
    ) : (
      icon
    );

  return (
    <div
      {...props}
      className={cn(
        "flex flex-col items-center justify-center py-12 text-center",
        className,
      )}
      data-slot="conversation-empty-state"
    >
      {resolvedIcon ? (
        <div className="mb-4 text-muted-foreground">{resolvedIcon}</div>
      ) : null}
      {title ? (
        <h3 className="text-xl font-semibold text-foreground">{title}</h3>
      ) : null}
      {description ? (
        <p className="mt-2 max-w-xl text-sm text-muted-foreground">
          {description}
        </p>
      ) : null}
      {children ? <div className="mt-6 w-full">{children}</div> : null}
    </div>
  );
}

function ConversationScrollButton({
  className,
  ...props
}: React.ComponentProps<typeof Button>) {
  const ctx = useContext(ConversationContext);
  const visible = ctx?.isAtBottom === false;
  if (!visible) return null;

  return (
    <Button
      type="button"
      size="icon"
      variant="secondary"
      className={cn(
        "absolute bottom-3 right-3 z-20 rounded-full border-subtle shadow-none",
        className,
      )}
      onClick={ctx?.scrollToBottom}
      aria-label="Scroll to latest message"
      {...props}
    >
      <ChevronDown className="size-4" />
    </Button>
  );
}

type ConversationMessageForDownload = {
  role: string;
  content: string;
};

function messagesToMarkdown(
  messages: ConversationMessageForDownload[],
  formatMessage?: (
    message: ConversationMessageForDownload,
    index: number,
  ) => string,
): string {
  return messages
    .map((m, i) =>
      formatMessage ? formatMessage(m, i) : `## ${m.role}\n\n${m.content}`,
    )
    .join("\n\n");
}

interface ConversationDownloadProps extends Omit<
  React.ComponentProps<typeof Button>,
  "onClick"
> {
  messages: ConversationMessageForDownload[];
  filename?: string;
  formatMessage?: (
    message: ConversationMessageForDownload,
    index: number,
  ) => string;
}

function ConversationDownload({
  messages,
  filename = "conversation.md",
  formatMessage,
  className,
  ...props
}: ConversationDownloadProps) {
  const handleDownload = () => {
    const markdown = messagesToMarkdown(messages, formatMessage);
    const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      className={cn("absolute top-3 right-3 z-10", className)}
      onClick={handleDownload}
      {...props}
    >
      <Download className="mr-2 size-4" />
      Download
    </Button>
  );
}

export {
  Conversation,
  ConversationContent,
  ConversationDownload,
  ConversationEmptyState,
  ConversationScrollButton,
};
