import {
  memo,
  type ButtonHTMLAttributes,
  type ReactNode,
  type Ref,
} from "react";
import { IconPaperclip, IconPlus } from "@tabler/icons-react";

import { cn } from "../utils/cn";

export type AttachmentButtonIcon = "plus" | "paperclip";

export type AttachmentButtonProps = Omit<
  ButtonHTMLAttributes<HTMLButtonElement>,
  "children"
> & {
  /**
   * Icon to render inside the button.
   * - "plus" (default): a `+` glyph, matches the generic "add something" affordance.
   * - "paperclip": a paperclip glyph, matches the more literal "attach file" affordance.
   * - Pass any ReactNode to fully override (e.g. a custom svg). The node is
   *   rendered as-is inside the button; size/color from this component's
   *   styling is only applied to the built-in presets.
   */
  icon?: AttachmentButtonIcon | ReactNode;
  ref?: Ref<HTMLButtonElement>;
};

function isIconName(value: unknown): value is AttachmentButtonIcon {
  return value === "plus" || value === "paperclip";
}

export const AttachmentButton = memo(function AttachmentButton({
  icon = "plus",
  className,
  ref,
  ...props
}: AttachmentButtonProps) {
  const iconClassName = "size-3 text-an-input-placeholder-color";
  let iconNode: ReactNode;
  if (isIconName(icon)) {
    iconNode =
      icon === "paperclip" ? (
        <IconPaperclip className={iconClassName} strokeWidth={2} />
      ) : (
        <IconPlus className={iconClassName} strokeWidth={2} />
      );
  } else {
    iconNode = icon;
  }

  return (
    <button
      type="button"
      className={cn(
        "flex size-6 cursor-pointer items-center justify-center rounded-full transition-colors hover:bg-foreground/6",
        className,
      )}
      aria-label="Attach"
      ref={ref}
      {...props}
    >
      {iconNode}
    </button>
  );
});
