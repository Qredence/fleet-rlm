import { memo } from "react";
import { cn } from "./utils/cn";

export type ErrorMessageProps = {
  title?: string;
  message: string;
  className?: string;
};

export const ErrorMessage = memo(function ErrorMessage({
  title = "Something went wrong",
  message,
  className,
}: ErrorMessageProps) {
  return (
    <div className={cn("flex justify-start", className)}>
      <div className="border border-danger/30 bg-danger/10 px-4 py-2.5 text-sm text-an-foreground rounded-an-action-lg">
        <div className="font-medium text-an-foreground">{title}</div>
        <div className="mt-0.5 text-an-foreground-muted">{message}</div>
      </div>
    </div>
  );
});
