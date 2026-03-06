import { ArrowUp, Loader2 } from "lucide-react";

import { IconButton } from "@/components/ui/icon-button";
import { cn } from "@/lib/utils/cn";

import { usePromptInput } from "./usePromptInput";

function PromptInputSubmit() {
  const { value, isLoading, isReceiving, onSubmit } = usePromptInput();
  const hasContent = value.trim().length > 0;
  const isDisabled = isLoading || !hasContent;

  return (
    <IconButton
      type="button"
      onClick={onSubmit}
      disabled={isDisabled}
      className={cn(
        "size-9 min-h-9 min-w-9 rounded-full transition-[background-color,color,box-shadow,opacity]",
        isReceiving
          ? "bg-accent text-accent-foreground"
          : hasContent
            ? "bg-accent text-accent-foreground hover:bg-accent/90"
            : "bg-muted-foreground/20 text-muted-foreground hover:bg-muted-foreground/25",
      )}
      aria-label={isReceiving ? "Receiving response" : "Send message"}
      aria-busy={isReceiving}
    >
      {isReceiving ? (
        <Loader2 className="size-5 animate-spin" aria-hidden="true" />
      ) : (
        <ArrowUp className="size-5" aria-hidden="true" />
      )}
    </IconButton>
  );
}

export { PromptInputSubmit };
