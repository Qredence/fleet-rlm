import * as React from "react";
import { mergeProps } from "@base-ui/react/merge-props";
import { useRender } from "@base-ui/react/use-render";

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

export function ButtonGroup({ className, orientation = "horizontal", ...props }: ButtonGroupProps) {
  return (
    <ButtonGroupContext.Provider value={{ orientation }}>
      <div
        className={cn("flex", orientation === "horizontal" ? "flex-row" : "flex-col", className)}
        role="group"
        {...props}
      />
    </ButtonGroupContext.Provider>
  );
}

export type ButtonGroupTextProps = useRender.ComponentProps<"span"> & {
  className?: string;
};

export function ButtonGroupText({ className, render, ...props }: ButtonGroupTextProps) {
  return useRender({
    defaultTagName: "span",
    props: mergeProps<"span">(
      { className: cn("flex items-center justify-center px-3 py-1 text-sm", className) },
      props,
    ),
    render,
  });
}
