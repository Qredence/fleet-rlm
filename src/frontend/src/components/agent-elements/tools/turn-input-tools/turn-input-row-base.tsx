import type { ReactNode } from "react";
import { cn } from "../../utils/cn";

export type TurnInputRowBaseProps = {
  icon: ReactNode;
  label: string;
  children: ReactNode;
  className?: string;
};

export function TurnInputRowBase({ icon, label, children, className }: TurnInputRowBaseProps) {
  return (
    <div className={cn("flex flex-col gap-1 w-full", className)}>
      <div className="flex items-center gap-1.5 text-muted-foreground">
        <span className="flex items-center justify-center size-3 shrink-0" aria-hidden="true">
          {icon}
        </span>
        <span className="font-[450] text-sm whitespace-nowrap shrink-0">{label}</span>
      </div>
      {children}
    </div>
  );
}
