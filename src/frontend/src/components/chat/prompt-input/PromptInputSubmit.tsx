import { ArrowUp } from "lucide-react";

import { IconButton } from "@/components/ui/icon-button";
import { cn } from "@/components/ui/utils";

import { usePromptInput } from "./usePromptInput";

function PromptInputSubmit() {
  const { value, isLoading, onSubmit } = usePromptInput();
  const hasContent = value.trim().length > 0;

  return (
    <IconButton
      type="button"
      onClick={onSubmit}
      disabled={!hasContent || isLoading}
      className={cn(
        "size-9 min-h-9 min-w-9 rounded-full transition-[background-color,color,box-shadow,opacity]",
        hasContent
          ? "bg-primary text-primary-foreground hover:bg-primary/90"
          : "bg-muted-foreground/20 text-muted-foreground hover:bg-muted-foreground/25",
      )}
      aria-label="Send message"
    >
      <ArrowUp className="size-5" aria-hidden="true" />
    </IconButton>
  );
}

export { PromptInputSubmit };
