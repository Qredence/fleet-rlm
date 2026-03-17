import * as React from "react";
import { Switch as BaseSwitch } from "@base-ui/react";

import { cn } from "@/lib/utils/cn";

function Switch({ className, ...props }: React.ComponentProps<typeof BaseSwitch.Root>) {
  return (
    <BaseSwitch.Root
      className={cn(
        "peer inline-flex h-7.75 w-12.75 shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent shadow-xs transition-all outline-none",
        "bg-toggle-inactive data-checked:bg-toggle-active",
        "focus-visible:ring-ring/50 focus-visible:ring-[3px]",
        "disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    >
      <BaseSwitch.Thumb
        className={cn(
          "pointer-events-none block size-6.75 rounded-full bg-toggle-knob shadow-(--toggle-knob-shadow) ring-0 transition-transform",
          "data-checked:translate-x-5 translate-x-0",
        )}
      />
    </BaseSwitch.Root>
  );
}

export { Switch };
