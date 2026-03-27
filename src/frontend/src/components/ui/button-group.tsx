import * as React from "react";
import { Slot } from "@radix-ui/react-slot";

import { cn } from "@/lib/utils";

const ButtonGroupContext = React.createContext<{
  orientation?: "horizontal" | "vertical";
}>({ orientation: "horizontal" });

export function useButtonGroup() {
  return React.useContext(ButtonGroupContext);
}

export type ButtonGroupProps = React.HTMLAttributes<HTMLDivElement> & {
  orientation?: "horizontal" | "vertical";
};

export function ButtonGroup({
  className,
  orientation = "horizontal",
  ...props
}: ButtonGroupProps) {
  return (
    <ButtonGroupContext.Provider value={{ orientation }}>
      <div
        className={cn(
          "flex",
          orientation === "horizontal" ? "flex-row" : "flex-col",
          className,
        )}
        role="group"
        {...props}
      />
    </ButtonGroupContext.Provider>
  );
}

export type ButtonGroupTextProps = React.HTMLAttributes<HTMLSpanElement> & {
  asChild?: boolean;
};

export function ButtonGroupText({
  className,
  asChild = false,
  ...props
}: ButtonGroupTextProps) {
  const Comp = asChild ? Slot : "span";
  return (
    <Comp
      className={cn(
        "flex items-center justify-center px-3 py-1 text-sm",
        className,
      )}
      {...props}
    />
  );
}
