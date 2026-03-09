import * as React from "react";
import { cn } from "./utils";

interface InputGroupProps extends React.HTMLAttributes<HTMLDivElement> {}

const InputGroup = React.forwardRef<HTMLDivElement, InputGroupProps>(
  ({ className, ...props }, ref) => {
    return (
      <div
        ref={ref}
        className={cn("flex items-center rounded-md border bg-background", className)}
        {...props}
      />
    );
  }
);
InputGroup.displayName = "InputGroup";

interface InputGroupInputProps extends React.InputHTMLAttributes<HTMLInputElement> {}

const InputGroupInput = React.forwardRef<HTMLInputElement, InputGroupInputProps>(
  ({ className, ...props }, ref) => {
    return (
      <input
        ref={ref}
        className={cn(
          "flex-1 bg-transparent px-3 py-2 text-sm outline-none placeholder:text-muted-foreground",
          className
        )}
        {...props}
      />
    );
  }
);
InputGroupInput.displayName = "InputGroupInput";

export { InputGroup, InputGroupInput };
