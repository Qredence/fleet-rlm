import type * as React from "react";
import { cn } from "@/lib/utils/cn";

interface SpinnerProps extends React.HTMLAttributes<HTMLDivElement> {
  size?: "sm" | "md" | "lg";
  ref?: React.Ref<HTMLDivElement>;
}

const sizeClasses = {
  sm: "size-4",
  md: "size-6",
  lg: "size-8",
};

function Spinner({ className, size = "md", ref, ...props }: SpinnerProps) {
  return (
    <div
      ref={ref}
      role="status"
      aria-label="Loading"
      className={cn(
        "animate-spin rounded-full border-2 border-muted border-t-primary",
        sizeClasses[size],
        className,
      )}
      {...props}
    />
  );
}

export { Spinner };
