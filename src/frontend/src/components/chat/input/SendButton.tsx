import { ArrowUp } from "lucide-react";

import { PromptInputSubmit } from "@/components/prompt-kit/prompt-input";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils/cn";

interface SendButtonProps {
  disabled: boolean;
  isLoading?: boolean;
  isReceiving?: boolean;
}

function SendButton({ disabled, isLoading = false, isReceiving = false }: SendButtonProps) {
  return (
    <PromptInputSubmit
      aria-label={isLoading ? "Sending message" : "Submit"}
      aria-busy={isReceiving}
      className={cn(
        "prompt-composer-submit-button aspect-square size-6.5 min-h-6.5 min-w-6.5 rounded-full first:rounded-full last:rounded-full",
        "transition-[background-color,color,box-shadow,opacity]",
      )}
      disabled={disabled}
      size="icon"
      variant="ghost"
    >
      {isLoading ? <Spinner size="sm" /> : <ArrowUp className="size-4.5" />}
    </PromptInputSubmit>
  );
}

export { SendButton };
