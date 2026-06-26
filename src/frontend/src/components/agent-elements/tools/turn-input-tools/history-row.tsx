import { memo } from "react";
import { IconHistory, IconChevronRight } from "@tabler/icons-react";
import { Collapsible } from "@base-ui/react/collapsible";
import { cn } from "../../utils/cn";
import { TurnInputRowBase } from "./turn-input-row-base";

export type HistoryRowProps = {
  part: {
    input?: {
      label?: string;
      turnCount?: number;
      value?: string;
      preview?: string;
    };
  };
};

export const HistoryRow = memo(function HistoryRow({ part }: HistoryRowProps) {
  const label = part.input?.label || "History";
  const turnCount = part.input?.turnCount ?? 0;
  const value = part.input?.value || "";

  const summaryText =
    turnCount === 0 ? "No prior history" : `${turnCount} ${turnCount === 1 ? "turn" : "turns"}`;

  const hasContent = Boolean(value.trim());

  if (!hasContent) {
    return (
      <TurnInputRowBase icon={<IconHistory className="w-full h-full" />} label={label}>
        <span className="text-sm text-muted-foreground">{summaryText}</span>
      </TurnInputRowBase>
    );
  }

  return (
    <Collapsible.Root className="flex flex-col gap-1 w-full" defaultOpen={false}>
      <Collapsible.Trigger className="group flex items-center gap-1.5 text-muted-foreground cursor-pointer select-none">
        <span className="flex items-center justify-center size-3 shrink-0" aria-hidden="true">
          <IconHistory className="w-full h-full" />
        </span>
        <span className="font-[450] text-sm whitespace-nowrap shrink-0">{label}</span>
        <span className="text-sm text-muted-foreground">{summaryText}</span>
        <IconChevronRight
          className={cn(
            "shrink-0 text-muted-foreground transition-transform duration-150 ease-out",
            "size-3",
            "rotate-0 group-data-panel-open:rotate-90",
          )}
        />
      </Collapsible.Trigger>
      <Collapsible.Panel
        className={cn(
          "overflow-hidden",
          "h-[var(--collapsible-panel-height)] transition-all duration-150 ease-out",
          "data-ending-style:h-0 data-starting-style:h-0",
          "[&[hidden]:not([hidden='until-found'])]:hidden",
        )}
      >
        <div className="max-h-60 overflow-y-auto pl-5">
          <pre className="text-sm text-muted-foreground whitespace-pre-wrap break-words overflow-wrap">
            {value}
          </pre>
        </div>
      </Collapsible.Panel>
    </Collapsible.Root>
  );
});
