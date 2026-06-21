"use client";

import { memo, useCallback, useState } from "react";
import type { ComponentType } from "react";
import { Check, ChevronDown } from "lucide-react";
import { popoverSurfaceClass } from "./popover-surface";

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";

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
        "an-popover-text-medium inline-flex h-an-input-toolbar-height items-center gap-1.5 rounded-full px-2 text-an-foreground-muted transition-colors hover:bg-foreground/6 hover:text-an-foreground",
        !hasMultiple && "pointer-events-none",
        className,
      )}
      aria-label="Select mode"
    >
      {ActiveIcon && <ActiveIcon className="size-3.5 shrink-0" />}
      <span className="font-medium">{activeMode.label}</span>
      {hasMultiple && (
        <ChevronDown className="size-3 text-an-input-placeholder-color" />
      )}
    </button>
  );

  if (!hasMultiple) return trigger;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger render={trigger} />
      <PopoverContent
        align="start"
        side="top"
        className={cn("w-44", popoverSurfaceClass)}
      >
        {modes.map((mode) => {
          const isActive = mode.id === activeMode.id;
          return (
            <button
              key={mode.id}
              type="button"
              onClick={() => handleSelect(mode.id)}
              className={cn(
                "an-popover-option-compact an-popover-text flex w-full text-left transition-colors",
                isActive && "an-popover-option-active",
              )}
            >
              <span className="flex min-w-0 flex-1 items-center gap-1 overflow-hidden">
                <span className="truncate">{mode.label}</span>
                {mode.description ? (
                  <span className="truncate text-an-foreground-muted">
                    {mode.description}
                  </span>
                ) : null}
              </span>
              {isActive && (
                <Check className="size-3.5 shrink-0 text-an-foreground-muted" />
              )}
            </button>
          );
        })}
      </PopoverContent>
    </Popover>
  );
});
