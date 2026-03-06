import { motion } from "motion/react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils/cn";

export interface AnimatedTabItem<T extends string = string> {
  id: T;
  label: string;
  icon?: ReactNode;
  disabled?: boolean;
}

interface AnimatedTabsProps<T extends string = string> {
  tabs: AnimatedTabItem<T>[];
  value: T;
  onValueChange: (tabId: T) => void;
  className?: string;
  indicatorLayoutId?: string;
}

export function AnimatedTabs<T extends string = string>({
  tabs,
  value,
  onValueChange,
  className,
  indicatorLayoutId = "animated-tabs-indicator",
}: AnimatedTabsProps<T>) {
  return (
    <div
      role="tablist"
      aria-orientation="horizontal"
      data-slot="tabs-list"
      className={cn(
        "text-muted-foreground w-fit items-center justify-center flex h-8 max-w-full self-start overflow-x-auto rounded-md border border-border-subtle/70 bg-muted/45 p-0.5",
        className,
      )}
      style={{ height: "2rem", minHeight: "2rem" }}
      tabIndex={0}
    >
      {tabs.map((tab) => {
        const active = value === tab.id;

        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={active}
            aria-disabled={tab.disabled || undefined}
            disabled={tab.disabled}
            tabIndex={active ? 0 : -1}
            onClick={() => {
              if (!tab.disabled) {
                onValueChange(tab.id);
              }
            }}
            className={cn(
              "relative inline-flex h-6 flex-none items-center justify-center gap-1.5 rounded-sm border border-transparent px-3 py-1 text-[11px] whitespace-nowrap transition-[color,background-color,box-shadow]",
              "focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:outline-ring focus-visible:ring-[3px] focus-visible:outline-1",
              "disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
              active
                ? "text-foreground"
                : "text-muted-foreground hover:bg-white/8 hover:text-foreground/95",
            )}
          >
            {active ? (
              <motion.span
                layoutId={indicatorLayoutId}
                className="absolute inset-0 z-0 rounded-sm border border-white/20 bg-white/12"
                transition={{ type: "spring", bounce: 0.16, duration: 0.34 }}
              />
            ) : null}
            <span className="relative z-10 inline-flex items-center gap-1.5">
              {tab.icon}
              {tab.label}
            </span>
          </button>
        );
      })}
    </div>
  );
}
