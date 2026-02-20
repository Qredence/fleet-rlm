/**
 * PromptInput — chat composer with two visual modes.
 *
 * **Collapsed** (default, no features active):
 *   Single-line pill: [+] Ask anything … [mic] [send]
 *
 * **Expanded** (≥1 feature active):
 *   Top:    multiline textarea  "Describe a task…"
 *   Bottom: [+] ModeChip · ContextMemoryChip · SkillsChip  [mic] [send]
 *
 * The transition between modes uses Motion spring physics.
 */
import { useRef, useCallback, useEffect } from "react";
import { motion, useReducedMotion } from "motion/react";
import { springs } from "../config/motion-config";
import { typo } from "../config/typo";
import type { PromptFeature, PromptMode } from "../data/types";
import { cn } from "./utils";
import { IconButton } from "./icon-button";
import { PromptPlusMenu } from "./prompt-plus-menu";
import { PromptToolbar } from "./prompt-toolbar";
import composerSvgPaths from "@/imports/svg-er4mz3hmp1";

// ── Types ───────────────────────────────────────────────────────────

interface PromptInputProps {
  /** Current text value */
  value: string;
  /** Called when input text changes */
  onChange: (value: string) => void;
  /** Called on submit (Enter or send button) */
  onSubmit: () => void;
  /** Placeholder text */
  placeholder?: string;
  /** Disables input and send */
  disabled?: boolean;
  /** Set of currently active prompt features */
  activeFeatures: Set<PromptFeature>;
  /** Toggle a feature on/off */
  onToggleFeature: (feature: PromptFeature) => void;
  /** Current prompt mode */
  mode: PromptMode;
  /** Set the prompt mode */
  onSetMode: (mode: PromptMode) => void;
  /** Array of selected skill IDs */
  selectedSkills: string[];
  /** Toggle a skill in the selection */
  onToggleSkill: (skillId: string) => void;
  /** Optional className on outermost wrapper */
  className?: string;
}

// ── Shared text style ───────────────────────────────────────────────

const inputTextStyle = {
  ...typo.base,
  lineHeight: "24px",
  letterSpacing: "-0.32px",
};

// ── Component ───────────────────────────────────────────────────────

export function PromptInput({
  value,
  onChange,
  onSubmit,
  placeholder = "Ask anything",
  disabled = false,
  activeFeatures,
  onToggleFeature,
  mode,
  onSetMode,
  selectedSkills,
  onToggleSkill,
  className,
}: PromptInputProps) {
  const prefersReduced = useReducedMotion();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const isExpanded = activeFeatures.size > 0;
  const canSend = value.trim().length > 0 && !disabled;

  // ── Auto-resize textarea ────────────────────────────────────────
  const resizeTextarea = useCallback(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, 160)}px`;
  }, []);

  useEffect(() => {
    resizeTextarea();
  }, [value, isExpanded, resizeTextarea]);

  // ── Key handler ─────────────────────────────────────────────────
  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey && canSend) {
      e.preventDefault();
      onSubmit();
    }
  }

  // ── Mic + Send buttons (shared) ─────────────────────────────────
  const actionButtons = (
    <div className="flex items-center gap-0.5 shrink-0">
      <IconButton
        type="button"
        className="touch-target rounded-full"
        aria-label="Voice input"
      >
        <svg className="size-5" fill="none" viewBox="0 0 20 20">
          <path d={composerSvgPaths.p3c9c8b00} fill="var(--foreground)" />
        </svg>
      </IconButton>
      <IconButton
        type="button"
        className="size-9 min-h-9 min-w-9 rounded-full bg-primary hover:bg-primary/90"
        onClick={onSubmit}
        disabled={!canSend}
        aria-label="Send message"
      >
        <svg className="size-5" fill="none" viewBox="0 0 20 20">
          <path
            d={composerSvgPaths.p22cb5880}
            fill="var(--primary-foreground)"
          />
        </svg>
      </IconButton>
    </div>
  );

  // ── Motion spring ───────────────────────────────────────────────
  const transition = prefersReduced ? springs.instant : springs.default;

  // ── Expanded layout ─────────────────────────────────────────────
  if (isExpanded) {
    return (
      <motion.div
        className={cn("flex flex-col w-full", className, "rounded-xl")}
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={transition}
      >
        {/* Textarea */}
        <div className="px-1 pt-1 pb-0.5">
          <textarea
            ref={textareaRef}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Describe a task…"
            aria-label="Message input"
            rows={1}
            className={cn(
              "w-full bg-transparent border-0 outline-none resize-none",
              "text-foreground placeholder:text-muted-foreground",
              "disabled:opacity-50 disabled:cursor-not-allowed",
              "min-h-[32px]",
            )}
            style={inputTextStyle}
            disabled={disabled}
          />
        </div>

        {/* Toolbar row */}
        <div className="flex items-center gap-1 px-0.5 pb-0.5">
          {/* Plus menu */}
          <div className="shrink-0">
            <PromptPlusMenu
              activeFeatures={activeFeatures}
              onToggleFeature={onToggleFeature}
              className="min-h-[36px] min-w-[36px]"
            />
          </div>

          {/* Feature chips */}
          <div className="flex-1 min-w-0">
            <PromptToolbar
              activeFeatures={activeFeatures}
              mode={mode}
              onSetMode={onSetMode}
              selectedSkills={selectedSkills}
              onToggleSkill={onToggleSkill}
            />
          </div>

          {/* Mic + Send */}
          {actionButtons}
        </div>
      </motion.div>
    );
  }

  // ── Collapsed layout (single line) ──────────────────────────────
  return (
    <motion.div
      className={cn("flex items-center gap-1 w-full py-1 px-2", className)}
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={transition}
    >
      {/* Plus menu */}
      <div className="shrink-0">
        <PromptPlusMenu
          activeFeatures={activeFeatures}
          onToggleFeature={onToggleFeature}
        />
      </div>

      {/* Input */}
      <div className="flex-1 min-w-0">
        <input
          ref={inputRef}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          aria-label="Message input"
          className={cn(
            "w-full bg-transparent border-0 outline-none",
            "text-foreground placeholder:text-muted-foreground",
            "disabled:opacity-50 disabled:cursor-not-allowed",
            "touch-target",
          )}
          style={inputTextStyle}
          disabled={disabled}
        />
      </div>

      {/* Mic + Send */}
      {actionButtons}
    </motion.div>
  );
}
