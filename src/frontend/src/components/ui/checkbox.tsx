import * as React from "react";
import { Checkbox as BaseCheckbox } from "@base-ui/react";
import { CheckIcon } from "lucide-react";

import { cn } from "@/lib/utils/cn";

function Checkbox({ className, ...props }: React.ComponentProps<typeof BaseCheckbox.Root>) {
  return (
    <BaseCheckbox.Root
      className={cn(
        "peer border bg-input-background dark:bg-input/30 data-[checked]:bg-primary data-[checked]:text-primary-foreground dark:data-[checked]:bg-primary data-[checked]:border-primary focus-visible:border-ring focus-visible:ring-ring/50 aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 aria-invalid:border-destructive size-4 shrink-0 rounded-lg shadow-xs transition-shadow outline-none focus-visible:ring-[3px] disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    >
      <BaseCheckbox.Indicator className="flex items-center justify-center text-current transition-none">
        <CheckIcon className="size-3.5" />
      </BaseCheckbox.Indicator>
    </BaseCheckbox.Root>
  );
}

export { Checkbox };
