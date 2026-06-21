"use client";

import { memo, useMemo, useState } from "react";
import { Check, ChevronDown } from "lucide-react";
import { popoverSurfaceClass } from "./popover-surface";

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
        "an-popover-text-medium inline-flex h-an-input-toolbar-height min-w-0 max-w-44 items-center gap-1 rounded-full bg-card px-2 text-an-foreground-muted transition-colors hover:text-an-foreground",
        disabled &&
          "cursor-not-allowed opacity-60 hover:bg-transparent hover:text-foreground/60",
        className,
      )}
      disabled={disabled}
    >
      <span className="truncate font-medium">{activeModel.label}</span>
      <ChevronDown className="size-3 shrink-0 text-an-input-placeholder-color" />
    </button>
  );

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger render={trigger} />
      <PopoverContent
        align="end"
        side="top"
        className={cn("w-44", popoverSurfaceClass)}
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
                  "an-popover-option-compact an-popover-text flex w-full text-left transition-colors",
                  isActive && "an-popover-option-active",
                  (model.disabled || !canSelect) && "cursor-default",
                )}
              >
                <span className="flex min-w-0 flex-1 items-center gap-1 overflow-hidden">
                  <span className="truncate">{model.label}</span>
                  {model.description ? (
                    <span className="truncate text-an-foreground-muted">
                      {model.description}
                    </span>
                  ) : null}
                </span>
                {isActive && (
                  <Check className="size-3.5 shrink-0 text-an-foreground-muted" />
                )}
              </button>
            );
          })}
        </div>
        {onConfigure ? (
          <div>
            <button
              type="button"
              className="an-popover-option-compact an-popover-text flex w-full text-left transition-colors"
              onClick={() => {
                setOpen(false);
                onConfigure();
              }}
            >
              <span className="flex min-w-0 flex-1 items-center gap-1 overflow-hidden">
                <span className="truncate">{configureLabel}</span>
              </span>
            </button>
          </div>
        ) : null}
      </PopoverContent>
    </Popover>
  );
});
