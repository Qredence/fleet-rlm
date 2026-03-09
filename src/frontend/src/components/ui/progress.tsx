import * as React from "react";

import { cn } from "@/lib/utils/cn";

function Progress({
  className,
  value = 0,
  max = 100,
  ...props
}: React.ComponentProps<"progress">) {
  return (
    <progress
      data-slot="progress"
      className={cn(
        "bg-primary/20 relative h-2 w-full overflow-hidden rounded-full appearance-none",
        className,
      )}
      max={max}
      value={value ?? 0}
      {...props}
    />
  );
}

export { Progress };
