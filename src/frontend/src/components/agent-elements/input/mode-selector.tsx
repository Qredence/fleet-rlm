"use client";

import { memo, useCallback, useState } from "react";
import type { ComponentType } from "react";
import { Check, ChevronDown } from "lucide-react";

import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

import { cn } from "../utils/cn";

export type ModeOption = {
  id: string;
  label: string;
  icon?: ComponentType<{ className?: string }>;
  description?: string;
};

export type ModeSelectorProps = {
  modes: readonly ModeOption[];
  value?: string;
  defaultValue?: string;
  onChange?: (modeId: string) => void;
  className?: string;
};

export const ModeSelector = memo(function ModeSelector({
  modes,
  value,
  defaultValue,
  onChange,
  className,
}: ModeSelectorProps) {
  const isControlled = value !== undefined;
  const [internalValue, setInternalValue] = useState(defaultValue);
  const activeId = isControlled ? value : internalValue;
  const activeMode = modes.find((mode) => mode.id === activeId) ?? modes[0];
  const [open, setOpen] = useState(false);

  const handleSelect = useCallback(
    (id: string) => {
      if (!isControlled) setInternalValue(id);
      onChange?.(id);
      setOpen(false);
    },
    [isControlled, onChange],
  );

  if (modes.length === 0 || !activeMode) return null;

  const ActiveIcon = activeMode.icon;
  const hasMultiple = modes.length > 1;
  const trigger = (
    <button
      type="button"
      className={cn(
        "inline-flex h-7 items-center gap-1.5 rounded-full px-2.5 typo-caption text-foreground/60 transition-colors hover:bg-foreground/6",
        !hasMultiple && "pointer-events-none",
        className,
      )}
      aria-label="Select mode"
    >
      {ActiveIcon && <ActiveIcon className="size-3.5 shrink-0" />}
      <span className="font-medium">{activeMode.label}</span>
      {hasMultiple && <ChevronDown className="size-3 text-foreground/40" />}
    </button>
  );

  if (!hasMultiple) return trigger;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger render={trigger} />
      <PopoverContent
        align="start"
        side="top"
        className="w-52 rounded-[8px] border-border/80 bg-an-input-background p-1 text-an-foreground shadow-lg"
      >
        {modes.map((mode) => {
          const isActive = mode.id === activeMode.id;
          const Icon = mode.icon;
          return (
            <button
              key={mode.id}
              type="button"
              onClick={() => handleSelect(mode.id)}
              className={cn(
                "flex w-full items-start gap-2 rounded-[6px] px-2 py-1.5 text-left typo-caption transition-colors hover:bg-foreground/6",
                isActive && "bg-foreground/6",
              )}
            >
              {Icon && <Icon className="mt-0.5 size-3.5 shrink-0" />}
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium">{mode.label}</span>
                {mode.description ? (
                  <span className="block truncate text-foreground/40">{mode.description}</span>
                ) : null}
              </span>
              {isActive && <Check className="mt-0.5 size-3.5 shrink-0 text-foreground/60" />}
            </button>
          );
        })}
      </PopoverContent>
    </Popover>
  );
});
