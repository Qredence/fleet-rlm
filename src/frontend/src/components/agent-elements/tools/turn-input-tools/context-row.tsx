import { memo } from "react";
import { IconFileText, IconChevronRight } from "@tabler/icons-react";
import { Collapsible } from "@base-ui/react/collapsible";
import { cn } from "../../utils/cn";
import { TurnInputRowBase } from "./turn-input-row-base";

export type ContextRowProps = {
  part: {
    input?: {
      label?: string;
      value?: string;
      preview?: string;
    };
  };
};

export const ContextRow = memo(function ContextRow({ part }: ContextRowProps) {
  const label = part.input?.label || "Context";
  const value = part.input?.value || "";

  if (!value.trim()) {
    return (
      <TurnInputRowBase icon={<IconFileText className="w-full h-full" />} label={label}>
        <span className="text-sm text-muted-foreground">(empty)</span>
      </TurnInputRowBase>
    );
  }

  return (
    <Collapsible.Root className="flex flex-col gap-1 w-full" defaultOpen={false}>
      <Collapsible.Trigger className="group flex items-center gap-1.5 text-muted-foreground cursor-pointer select-none">
        <span className="flex items-center justify-center size-3 shrink-0" aria-hidden="true">
          <IconFileText className="w-full h-full" />
        </span>
        <span className="font-[450] text-sm whitespace-nowrap shrink-0">{label}</span>
        <IconChevronRight
          className={cn(
            "shrink-0 text-muted-foreground transition-transform duration-150 ease-out",
            "size-3",
            "rotate-0 group-data-panel-open:rotate-90",
          )}
        />
      </Collapsible.Trigger>
      <div className="pl-5">
        <Collapsible.Panel
          className={cn(
            "overflow-hidden",
            "transition-all duration-150 ease-out",
            "data-ending-style:h-0 data-starting-style:h-0",
            "[&[hidden]:not([hidden='until-found'])]:hidden",
          )}
        >
          <div className="max-h-60 overflow-y-auto">
            <pre className="text-sm text-muted-foreground whitespace-pre-wrap break-words overflow-wrap">
              {value}
            </pre>
          </div>
        </Collapsible.Panel>
        <p
          className="text-sm text-muted-foreground whitespace-pre-wrap break-words overflow-wrap line-clamp-2 group-data-panel-open:hidden"
          aria-hidden="true"
        >
          {value}
        </p>
      </div>
    </Collapsible.Root>
  );
});
