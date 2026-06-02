"use client";

import { memo, useMemo, useState } from "react";
import { Check, ChevronDown, Cpu, Settings2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";

import { cn } from "../utils/cn";

export type ModelOption = {
  id: string;
  label: string;
  description?: string;
  disabled?: boolean;
};

export type ModelPickerProps = {
  models: readonly ModelOption[];
  value?: string;
  defaultValue?: string;
  onChange?: (modelId: string) => void;
  onConfigure?: () => void;
  configureLabel?: string;
  className?: string;
  disabled?: boolean;
};

export const ModelPicker = memo(function ModelPicker({
  models,
  value,
  defaultValue,
  onChange,
  onConfigure,
  configureLabel = "Model settings",
  className,
  disabled = false,
}: ModelPickerProps) {
  const isControlled = value !== undefined;
  const [internalValue, setInternalValue] = useState(defaultValue);
  const [open, setOpen] = useState(false);
  const activeId = isControlled ? value : internalValue;
  const enabledModels = useMemo(
    () => models.filter((model) => !model.disabled),
    [models],
  );
  const activeModel =
    models.find((model) => model.id === activeId) ??
    enabledModels[0] ??
    models[0] ??
    null;

  if (!activeModel) return null;

  const canSelect = Boolean(onChange) && enabledModels.length > 1 && !disabled;
  const trigger = (
    <button
      type="button"
      aria-label={`Active model: ${activeModel.label}`}
      className={cn(
        "inline-flex h-7 min-w-0 max-w-45 items-center gap-1.5 rounded-[6px] px-2 text-[12px] leading-4 text-foreground/60 transition-colors hover:bg-foreground/6 hover:text-foreground",
        disabled &&
          "cursor-not-allowed opacity-60 hover:bg-transparent hover:text-foreground/60",
        className,
      )}
      disabled={disabled}
    >
      <Cpu className="size-3.5 shrink-0" />
      <span className="truncate font-medium">{activeModel.label}</span>
      <ChevronDown className="size-3 shrink-0 text-foreground/40" />
    </button>
  );

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>{trigger}</PopoverTrigger>
      <PopoverContent
        align="end"
        side="top"
        className="w-64 rounded-[8px] border-border/80 bg-an-input-background p-1 text-an-foreground shadow-lg"
      >
        <div className="max-h-64 overflow-y-auto">
          {models.map((model) => {
            const isActive = model.id === activeModel.id;
            return (
              <button
                key={model.id}
                type="button"
                disabled={model.disabled || !canSelect}
                onClick={() => {
                  if (!canSelect || model.disabled) return;
                  if (!isControlled) setInternalValue(model.id);
                  onChange?.(model.id);
                  setOpen(false);
                }}
                className={cn(
                  "flex w-full items-start gap-2 rounded-[6px] px-2 py-1.5 text-left text-[12px] leading-4 transition-colors",
                  canSelect && !model.disabled && "hover:bg-foreground/6",
                  isActive && "bg-foreground/6",
                  (model.disabled || !canSelect) && "cursor-default",
                )}
              >
                <Cpu className="mt-0.5 size-3.5 shrink-0 text-foreground/50" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium">
                    {model.label}
                  </span>
                  {model.description ? (
                    <span className="block truncate text-foreground/40">
                      {model.description}
                    </span>
                  ) : null}
                </span>
                {isActive && (
                  <Check className="mt-0.5 size-3.5 shrink-0 text-foreground/60" />
                )}
              </button>
            );
          })}
        </div>
        {onConfigure ? (
          <div className="mt-1 border-t border-border/70 pt-1">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-8 w-full justify-start gap-2 rounded-[6px] px-2 text-[12px]"
              onClick={() => {
                setOpen(false);
                onConfigure();
              }}
            >
              <Settings2 className="size-3.5" />
              {configureLabel}
            </Button>
          </div>
        ) : null}
      </PopoverContent>
    </Popover>
  );
});
