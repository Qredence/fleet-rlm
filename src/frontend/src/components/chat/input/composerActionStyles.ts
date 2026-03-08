import type { ButtonVariantProps } from "@/components/ui/button-variants";

/**
 * Keep prompt-input controls aligned with the TopHeader action contract:
 * 36px tall, round silhouettes, and compact padding defined by the shared
 * Button primitive instead of per-callsite utility classes.
 */
export const PROMPT_INPUT_ACTION_BUTTON_SIZE: NonNullable<
  ButtonVariantProps["size"]
> = "toolbar";

export const PROMPT_INPUT_ACTION_BUTTON_CLASSNAME = "rounded-full";

export const PROMPT_INPUT_ICON_BUTTON_VARIANT: NonNullable<
  ButtonVariantProps["variant"]
> = "ghost";

export const PROMPT_INPUT_ICON_BUTTON_CLASSNAME =
  "size-9 min-h-9 min-w-9 rounded-full";
