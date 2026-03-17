import * as React from "react";
import { Avatar as BaseAvatar } from "@base-ui/react";

import { cn } from "@/lib/utils/cn";

function Avatar({ className, ...props }: React.ComponentProps<typeof BaseAvatar.Root>) {
  return (
    <BaseAvatar.Root
      className={cn("relative flex size-10 shrink-0 overflow-hidden rounded-full", className)}
      {...props}
    />
  );
}

function AvatarImage({ className, ...props }: React.ComponentProps<typeof BaseAvatar.Image>) {
  return <BaseAvatar.Image className={cn("aspect-square size-full", className)} {...props} />;
}

function AvatarFallback({ className, ...props }: React.ComponentProps<typeof BaseAvatar.Fallback>) {
  return (
    <BaseAvatar.Fallback
      className={cn("bg-muted flex size-full items-center justify-center rounded-full", className)}
      {...props}
    />
  );
}

export { Avatar, AvatarImage, AvatarFallback };
