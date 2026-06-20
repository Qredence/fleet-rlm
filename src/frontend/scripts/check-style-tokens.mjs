#!/usr/bin/env node
/**
 * Style token guard — fails when arbitrary Tailwind values or raw palette colors
 * are introduced outside of approved exception files.
 *
 * Banned patterns (in .tsx/.jsx className strings):
 *   1. Typography arbitrary values: text-[Npx], text-[Nrem], text-[Nem]
 *   2. Radius arbitrary values: rounded-[Npx], rounded-[Nrem]
 *   3. Raw palette colors (any shade): bg-(red|emerald|amber|blue|green|yellow)-N,
 *      text-(red|emerald|amber|blue|green|yellow)-N, border-(red|emerald|amber|blue|green|yellow)-N
 *   4. Raw hex/rgb/hsl arbitrary colors: bg-[#fff], text-[rgb(…)], border-[hsl(…)]
 *   5. Dark-mode raw palette: dark:text-(red|emerald|amber|blue|green|yellow)-N
 *   6. Inline style color properties: style={{ color: "…" }}, backgroundColor, borderColor
 *      (CSS custom properties via style={{ "--var": … }} are allowed)
 *
 * Allowed exceptions:
 *   - Token-bridge forms: text-[length:var(--…)], rounded-[calc(var(--…))], rounded-[inherit]
 *   - settings-content.tsx: theme-swatch illustrations use raw bg-zinc-* values
 *   - components/ui/*: shadcn registry components may use token-bridge forms
 *
 * Usage: node scripts/check-style-tokens.mjs
 * Wired into package.json as: pnpm run lint:style-tokens
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const ROOT = new URL("../src", import.meta.url).pathname;
const EXCEPTIONS = new Set([
  "src/features/settings/screen/settings-content.tsx", // theme-swatch illustrations
]);

// Status palette colors that should use semantic tokens instead.
// Matches any shade 50–950 (not just 500).
const STATUS_COLORS = "(red|emerald|amber|blue|green|yellow)";
const SHADE = "(50|100|200|300|400|500|600|700|800|900|950)";

// Patterns that are always banned
const BANNED_PATTERNS = [
  {
    name: "arbitrary typography size",
    regex: /text-\[(?!length:var)(?!calc\(var)(?![0-9]+(px|rem|em)\s*\/)\d+(px|rem|em)\]/g,
    message: "Use typo-* @utility tokens from globals.css instead of text-[Npx]",
  },
  {
    name: "arbitrary radius",
    regex: /rounded-\[(?!calc\(var)(?!inherit)(?![a-z]+:\d)\d+(px|rem|em)\]/g,
    message: "Use rounded-an-action-{sm,md,lg} or rounded-an-* tokens instead of rounded-[Npx]",
  },
  {
    name: "raw palette bg color",
    regex: new RegExp(`bg-${STATUS_COLORS}-${SHADE}\\b`, "g"),
    message: "Use bg-success, bg-warning, bg-danger, bg-destructive tokens instead of bg-{color}-{shade}",
  },
  {
    name: "raw palette text color",
    regex: new RegExp(`text-${STATUS_COLORS}-${SHADE}\\b`, "g"),
    message: "Use text-success, text-warning, text-danger, text-destructive tokens instead of text-{color}-{shade}",
  },
  {
    name: "raw palette border color",
    regex: new RegExp(`border-${STATUS_COLORS}-${SHADE}\\b`, "g"),
    message: "Use border-success, border-warning, border-danger, border-destructive tokens instead of border-{color}-{shade}",
  },
  {
    name: "arbitrary hex color",
    regex: /(?:bg|text|border|fill|stroke)-\[#[0-9a-fA-F]{3,8}\]/g,
    message: "Use semantic color tokens (bg-success, text-danger, etc.) instead of arbitrary hex values",
  },
  {
    name: "arbitrary rgb/hsl color",
    regex: /(?:bg|text|border|fill|stroke)-\[(?:rgb|hsl)\([^)]*\)\]/g,
    message: "Use semantic color tokens instead of arbitrary rgb()/hsl() values",
  },
];

// Dark: prefix variants like dark:text-emerald-400
const BANNED_DARK_PATTERNS = [
  {
    name: "raw palette dark text color",
    regex: new RegExp(`dark:text-${STATUS_COLORS}-${SHADE}\\b`, "g"),
    message: "Use text-success, text-warning, text-danger tokens (auto dark-mode) instead of dark:text-{color}-{shade}",
  },
  {
    name: "raw palette dark bg color",
    regex: new RegExp(`dark:bg-${STATUS_COLORS}-${SHADE}\\b`, "g"),
    message: "Use bg-success, bg-warning, bg-danger tokens (auto dark-mode) instead of dark:bg-{color}-{shade}",
  },
  {
    name: "raw palette dark border color",
    regex: new RegExp(`dark:border-${STATUS_COLORS}-${SHADE}\\b`, "g"),
    message: "Use border-success, border-warning, border-danger tokens (auto dark-mode) instead of dark:border-{color}-{shade}",
  },
];

// Inline style color properties — bans raw color values in style objects.
// Allows `var(--…)` references and CSS custom property keys (per AGENTS.md).
const BANNED_INLINE_STYLE_PATTERNS = [
  {
    name: "inline style raw color value",
    regex: /\b(color|backgroundColor|borderColor|fill|stroke)\s*:\s*["'`](?!var\()(#[0-9a-fA-F]{3,8}|rgb\(|hsl\|[a-z]+)/g,
    message: 'Use CSS custom properties via style={{ "--var": … }} or semantic Tailwind tokens instead of inline raw color values',
  },
];

function walk(dir) {
  const results = [];
  for (const entry of readdirSync(dir)) {
    const fullPath = join(dir, entry);
    const stat = statSync(fullPath);
    if (stat.isDirectory()) {
      results.push(...walk(fullPath));
    } else if (fullPath.endsWith(".tsx") || fullPath.endsWith(".jsx")) {
      results.push(fullPath);
    }
  }
  return results;
}

const files = walk(ROOT);
const violations = [];

for (const file of files) {
  const relPath = relative(process.cwd(), file);
  const isException = EXCEPTIONS.has(relPath);

  if (isException) continue;

  const content = readFileSync(file, "utf-8");

  for (const pattern of [...BANNED_PATTERNS, ...BANNED_DARK_PATTERNS, ...BANNED_INLINE_STYLE_PATTERNS]) {
    const matches = content.matchAll(pattern.regex);
    for (const match of matches) {
      const lineNum = content.slice(0, match.index).split("\n").length;
      const line = content.split("\n")[lineNum - 1]?.trim() ?? "";
      violations.push({
        file: relPath,
        line: lineNum,
        pattern: pattern.name,
        match: match[0],
        message: pattern.message,
        context: line.slice(0, 120),
      });
    }
  }
}

if (violations.length === 0) {
  console.log("✓ Style token guard passed — no arbitrary values or raw palette colors found.");
  process.exit(0);
} else {
  console.error(`✗ Style token guard failed — ${violations.length} violation(s) found:\n`);
  for (const v of violations) {
    console.error(`  ${v.file}:${v.line}`);
    console.error(`    pattern: ${v.pattern} — "${v.match}"`);
    console.error(`    fix: ${v.message}`);
    console.error(`    context: ${v.context}`);
    console.error();
  }
  process.exit(1);
}
