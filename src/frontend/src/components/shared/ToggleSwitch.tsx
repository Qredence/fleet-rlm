import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils/cn";

interface ToggleSwitchProps {
  checked?: boolean;
  onChange?: (checked: boolean) => void;
  disabled?: boolean;
  className?: string;
}

/**
 * Settings-facing toggle wrapper.
 *
 * Keeps the existing prop surface stable while delegating rendering to the
 * shared shadcn switch primitive instead of a fully custom inline-styled
 * implementation.
 */
export function ToggleSwitch({
  checked = false,
  onChange,
  disabled = false,
  className,
}: ToggleSwitchProps) {
  return (
    <Switch
      checked={checked}
      className={cn(
        "h-[31px] w-[51px] rounded-full border-transparent bg-[var(--toggle-inactive)] data-[state=checked]:bg-[var(--toggle-active)]",
        "[&_[data-slot=switch-thumb]]:size-[27px] [&_[data-slot=switch-thumb]]:bg-[var(--toggle-knob)]",
        "[&_[data-slot=switch-thumb]]:data-[state=checked]:translate-x-[20px]",
        "[&_[data-slot=switch-thumb]]:data-[state=unchecked]:translate-x-0",
        className,
      )}
      disabled={disabled}
      onCheckedChange={onChange}
    />
  );
}
