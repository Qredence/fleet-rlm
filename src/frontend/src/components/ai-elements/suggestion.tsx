import { Button } from "@/components/ui/button";
import { ScrollArea, ScrollBar } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import type { ComponentProps } from "react";
import { useCallback } from "react";

export type SuggestionsProps = ComponentProps<typeof ScrollArea> & {
  wrap?: boolean;
};

export const Suggestions = ({
  className,
  children,
  wrap = false,
  ...props
}: SuggestionsProps) => (
  <ScrollArea
    className={cn(
      "w-full whitespace-nowrap",
      wrap ? "overflow-visible whitespace-normal" : "overflow-x-auto",
    )}
    {...props}
  >
    <div
      className={cn(
        "flex items-center gap-2",
        wrap ? "flex-wrap justify-center" : "w-max flex-nowrap",
        className,
      )}
    >
      {children}
    </div>
    {!wrap ? <ScrollBar className="hidden" orientation="horizontal" /> : null}
  </ScrollArea>
);

export type SuggestionProps = Omit<ComponentProps<typeof Button>, "onClick"> & {
  suggestion: string;
  onClick?: (suggestion: string) => void;
};

export const Suggestion = ({
  suggestion,
  onClick,
  className,
  variant = "outline",
  size = "sm",
  children,
  ...props
}: SuggestionProps) => {
  const handleClick = useCallback(() => {
    onClick?.(suggestion);
  }, [onClick, suggestion]);

  return (
    <Button
      className={cn("cursor-pointer rounded-full px-4", className)}
      onClick={handleClick}
      size={size}
      type="button"
      variant={variant}
      {...props}
    >
      {children || suggestion}
    </Button>
  );
};
