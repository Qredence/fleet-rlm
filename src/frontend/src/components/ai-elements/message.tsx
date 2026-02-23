import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "@/components/ui/utils";
import { Streamdown } from "@/components/ui/streamdown";

type MessageFrom = "user" | "assistant" | "system";

interface MessageProps extends HTMLAttributes<HTMLDivElement> {
  from: MessageFrom;
}

function Message({ from, className, ...props }: MessageProps) {
  return (
    <div
      data-slot="message"
      data-from={from}
      className={cn(
        "group/message flex w-full",
        from === "user" ? "justify-end" : "justify-start",
        className,
      )}
      {...props}
    />
  );
}

function MessageContent({
  className,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      data-slot="message-content"
      className={cn(
        "max-w-full",
        "data-[from=user]:max-w-[85%] md:data-[from=user]:max-w-md",
        className,
      )}
      {...props}
    />
  );
}

interface MessageResponseProps extends HTMLAttributes<HTMLDivElement> {
  children: string;
  streaming?: boolean;
}

function MessageResponse({
  children,
  streaming,
  className,
  ...props
}: MessageResponseProps) {
  return (
    <div
      data-slot="message-response"
      className={cn("w-full", className)}
      {...props}
    >
      <Streamdown content={children} streaming={streaming} />
    </div>
  );
}

function MessageActions({
  className,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      data-slot="message-actions"
      className={cn("mt-2 flex items-center gap-2", className)}
      {...props}
    />
  );
}

interface MessageActionProps extends React.ComponentProps<"button"> {
  label: string;
  tooltip?: string;
  children: ReactNode;
}

function MessageAction({
  label,
  children,
  className,
  ...props
}: MessageActionProps) {
  return (
    <button
      type="button"
      aria-label={label}
      title={props.title ?? label}
      className={cn(
        "inline-flex items-center justify-center rounded-md border border-border-subtle bg-card p-1.5 text-muted-foreground hover:text-foreground hover:border-border-strong",
        className,
      )}
      {...props}
    >
      {children}
    </button>
  );
}

export {
  Message,
  MessageContent,
  MessageResponse,
  MessageActions,
  MessageAction,
};
