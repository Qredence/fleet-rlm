import type { VariantProps } from "class-variance-authority";

import { buttonVariants } from "@/components/ui/button";

/**
 * Keep prompt-input controls aligned with the TopHeader action contract:
 * compact round silhouettes and muted dark-surface affordances so the
 * ChatInput footer matches the AI Elements/Figma composer treatment.
 */
type ButtonVariantProps = VariantProps<typeof buttonVariants>;

export const PROMPT_INPUT_ACTION_BUTTON_SIZE: NonNullable<
  ButtonVariantProps["size"]
> = "sm";

export const PROMPT_INPUT_ACTION_BUTTON_CLASSNAME =
  "prompt-composer-chip-button h-8 rounded-full border-transparent bg-transparent px-3 text-[14px] font-normal leading-5 tracking-[-0.2px] text-muted-foreground shadow-none hover:bg-foreground/6 hover:text-foreground active:bg-foreground/8 data-[popup-open]:bg-foreground/8 data-[popup-open]:text-foreground dark:bg-transparent dark:hover:bg-white/8 dark:active:bg-white/10 dark:data-[popup-open]:bg-white/10 focus-visible:ring-1 focus-visible:ring-ring/40";

export const PROMPT_INPUT_ICON_BUTTON_VARIANT: NonNullable<
  ButtonVariantProps["variant"]
> = "ghost";

export const PROMPT_INPUT_ICON_BUTTON_CLASSNAME =
  "prompt-composer-icon-button size-8 min-h-8 min-w-8 rounded-full border-transparent p-0 text-muted-foreground shadow-none hover:bg-foreground/6 hover:text-foreground data-[state=open]:bg-foreground/8 data-[state=open]:text-foreground dark:bg-transparent dark:hover:bg-white/8 dark:data-[state=open]:bg-white/10";

export const PROMPT_INPUT_MENUBAR_CLASSNAME =
  "h-auto gap-0 border-0 bg-transparent p-0 shadow-none";

export const PROMPT_INPUT_MENU_CONTENT_CLASSNAME =
  "prompt-composer-menu rounded-2xl p-1.5";
