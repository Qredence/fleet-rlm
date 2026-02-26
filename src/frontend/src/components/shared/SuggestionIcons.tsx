/**
 * SuggestionIcons — lightweight icon components used by SuggestionChip
 * in the chat welcome state.
 */
import { SlidersHorizontal, Sparkles, Zap } from "lucide-react";

/** Lightning bolt — "Create / generate" affordance. */
export function SuggestionIconBolt() {
  return <Zap className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />;
}

/** Sliders / tune — "Configure / build" affordance. */
export function SuggestionIconTune() {
  return (
    <SlidersHorizontal
      className="size-4 shrink-0 text-muted-foreground"
      aria-hidden="true"
    />
  );
}

/** Sparkle / magic — "Design / imagine" affordance. */
export function SuggestionIconSparkle() {
  return (
    <Sparkles className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
  );
}
