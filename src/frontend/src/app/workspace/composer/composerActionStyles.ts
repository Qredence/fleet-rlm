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
  "prompt-composer-chip-button h-[26px] rounded-[50px] border-transparent bg-transparent px-3 text-[14px] font-normal leading-4.5 tracking-[-0.3px] shadow-none hover:bg-muted/60 hover:text-foreground active:bg-muted data-[state=open]:bg-muted data-[state=open]:text-foreground focus-visible:ring-1 focus-visible:ring-ring/50";

export const PROMPT_INPUT_ICON_BUTTON_VARIANT: NonNullable<
  ButtonVariantProps["variant"]
> = "ghost";

export const PROMPT_INPUT_ICON_BUTTON_CLASSNAME =
  "prompt-composer-icon-button size-7 min-h-7 min-w-7 rounded-[14px] border-transparent p-0 shadow-none data-[state=open]:bg-[var(--color-background-primary-ghost-hover)] data-[state=open]:text-foreground";

export const PROMPT_INPUT_MENUBAR_CLASSNAME =
  "h-auto gap-0 border-0 bg-transparent p-0 shadow-none";

export const PROMPT_INPUT_MENU_CONTENT_CLASSNAME =
  "prompt-composer-menu rounded-2xl p-1.5";
