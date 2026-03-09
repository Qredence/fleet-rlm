# Validation Contract: Inline Style Consolidation

**Milestone:** styling-consolidation
**Version:** 1.0
**Created:** 2026-03-09

---

## Overview

This contract defines the validation assertions for the "Inline Style Consolidation" milestone, which replaces inline `style={{` patterns with CSS variables across the frontend codebase.

**Scope:**
- ai-elements/ components
- shared/ components
- chat/ components
- ui/ components
- features/shell/
- features/artifacts/
- features/settings/
- app/pages/
- app/layout/

---

## Assertions

### VAL-STYLING-001: No Hardcoded Color Values

**Title:** Inline styles use CSS variables for colors, not hardcoded hex/rgb values

**Behavioral Description:**
All inline style declarations must use CSS variables for color values. Hardcoded color literals (hex, rgb, rgba, hsl) in inline styles indicate incomplete migration.

**Pass Criteria:**
- Grep for `style={{` returns no matches containing hardcoded color patterns
- Patterns to reject: `#[0-9a-fA-F]{3,6}`, `rgb(`, `rgba(`, `hsl(` in style attributes
- Exceptions: CSS keywords like `currentColor`, `transparent` are acceptable

**Evidence Requirements:**
```bash
# Must return zero results
rg "style=\{\{.*#[0-9a-fA-F]" src/frontend/src
rg "style=\{\{.*rgb\(" src/frontend/src
rg "style=\{\{.*rgba\(" src/frontend/src
```

**Validation Type:** static-analysis

---

### VAL-STYLING-002: CSS Variables Used for Static Values

**Title:** Inline styles use defined CSS variables for static design tokens

**Behavioral Description:**
When inline styles are necessary, they must reference CSS variables from theme.css rather than hardcoded numeric or string values for design tokens (spacing, typography, colors, radii, etc.).

**Pass Criteria:**
- Inline style values like `16px`, `1.5`, `#fff` must use CSS variable equivalents
- Static values wrapped in CSS variables are acceptable (e.g., `var(--space-4)`)
- Dynamic calculated values (e.g., `${percent}%`) are exceptions requiring justification

**Evidence Requirements:**
- Manual code review of remaining `style={{` patterns
- Each pattern must either:
  - Use a CSS variable (e.g., `var(--radius-card)`)
  - Be truly dynamic (computed from props/state)
  - Use an acceptable CSS keyword

**Validation Type:** code-review

---

### VAL-STYLING-003: CSS Variables Defined in theme.css

**Title:** All referenced CSS variables exist in theme.css

**Behavioral Description:**
Every CSS variable referenced in inline styles must be defined in the canonical theme.css file under `:root` or `.dark` selectors.

**Pass Criteria:**
- Extract all `var(--xxx)` patterns from inline styles
- Each variable must exist in theme.css
- No undefined CSS variable references

**Evidence Requirements:**
```bash
# Extract CSS variables used in inline styles
rg "var\(--[a-zA-Z0-9-]+\)" src/frontend/src -o | sort -u

# Verify each exists in theme.css
rg "^  --[a-zA-Z0-9-]+:" src/frontend/src/styles/theme.css
```

**Validation Type:** static-analysis

---

### VAL-STYLING-004: Type Check Passes

**Title:** TypeScript compilation succeeds with no errors

**Behavioral Description:**
All inline style migrations must maintain type safety. The TypeScript compiler must complete without errors.

**Pass Criteria:**
- `bun run type-check` exits with code 0
- No TypeScript errors related to style attribute types

**Evidence Requirements:**
```bash
cd src/frontend && bun run type-check
# Exit code: 0
```

**Validation Type:** command

---

### VAL-STYLING-005: Build Succeeds

**Title:** Production build completes without errors

**Behavioral Description:**
The frontend build process must complete successfully after style migrations.

**Pass Criteria:**
- `bun run build` exits with code 0
- No build errors or warnings related to CSS processing
- Output bundle is valid

**Evidence Requirements:**
```bash
cd src/frontend && bun run build
# Exit code: 0
```

**Validation Type:** command

---

### VAL-STYLING-006: Lint Passes

**Title:** ESLint reports no errors

**Behavioral Description:**
All code must pass linting rules. Style migrations should not introduce lint violations.

**Pass Criteria:**
- `bun run lint` exits with code 0
- No lint errors

**Evidence Requirements:**
```bash
cd src/frontend && bun run lint
# Exit code: 0
```

**Validation Type:** command

---

### VAL-STYLING-007: No Empty Style Objects

**Title:** No empty style={{ }} attributes remain

**Behavioral Description:**
Empty style objects (`style={{ }}`) indicate incomplete migration and should be removed entirely.

**Pass Criteria:**
- No `style={{ }}` patterns in component files
- All style attributes either have content or are removed

**Evidence Requirements:**
```bash
# Must return zero results
rg "style=\{\{\s*\}\}" src/frontend/src
```

**Validation Type:** static-analysis

---

### VAL-STYLING-008: Theme.css is Single Source of Truth

**Title:** No duplicate style definitions in other CSS files

**Behavioral Description:**
Design tokens should be defined only in theme.css. No duplicate variable definitions in other CSS files.

**Pass Criteria:**
- CSS variables for design tokens only defined in theme.css
- Other CSS files may use `@theme inline` to expose to Tailwind but not redefine values

**Evidence Requirements:**
```bash
# Check for CSS variable definitions outside theme.css
rg "^  --[a-zA-Z0-9-]+:" src/frontend/src/styles/*.css --glob='!theme.css'
# Should return minimal or no results
```

**Validation Type:** static-analysis

---

### VAL-STYLING-009: Unit Tests Pass

**Title:** All unit tests pass after style migrations

**Behavioral Description:**
Style changes must not break existing unit tests. All component tests should continue to pass.

**Pass Criteria:**
- `bun run test:unit` exits with code 0
- All tests pass

**Evidence Requirements:**
```bash
cd src/frontend && bun run test:unit
# Exit code: 0
# All tests pass
```

**Validation Type:** command

---

### VAL-STYLING-010: Justified Dynamic Styles

**Title:** Remaining inline styles are justified as dynamic or necessary

**Behavioral Description:**
Any remaining `style={{` patterns must have a documented justification for why CSS variables or classes cannot be used.

**Pass Criteria:**
- Each remaining inline style is either:
  - Using CSS variables correctly (pass)
  - Truly dynamic (calculated from props/state) with comment explaining why
  - Using acceptable CSS keywords (currentColor, transparent)
  - Necessary for third-party library integration (documented in code comment)

**Evidence Requirements:**
- Code review of all `style={{` patterns
- Dynamic styles should have inline comment or be self-evident from context

**Validation Type:** code-review

---

## Summary

| ID | Title | Type | Priority |
|----|-------|------|----------|
| VAL-STYLING-001 | No Hardcoded Color Values | static-analysis | critical |
| VAL-STYLING-002 | CSS Variables Used for Static Values | code-review | critical |
| VAL-STYLING-003 | CSS Variables Defined in theme.css | static-analysis | critical |
| VAL-STYLING-004 | Type Check Passes | command | critical |
| VAL-STYLING-005 | Build Succeeds | command | critical |
| VAL-STYLING-006 | Lint Passes | command | critical |
| VAL-STYLING-007 | No Empty Style Objects | static-analysis | medium |
| VAL-STYLING-008 | Theme.css is Single Source of Truth | static-analysis | medium |
| VAL-STYLING-009 | Unit Tests Pass | command | critical |
| VAL-STYLING-010 | Justified Dynamic Styles | code-review | medium |

---

## Acceptable Inline Style Exceptions

The following patterns are acceptable uses of inline styles:

1. **Dynamic calculations** - Values computed from props/state (e.g., `${width}px`, `${percent}%`)
2. **CSS variables** - `var(--xxx)` patterns referencing theme.css
3. **CSS keywords** - `currentColor`, `transparent`, `inherit`, `initial`
4. **Third-party library requirements** - When a library requires inline styles (documented in comment)
5. **Transform animations** - Complex transform strings that cannot be expressed in CSS classes
6. **Pointer events** - `pointerEvents: 'none'` for click-through overlays

---

## Validation Execution Order

1. Run static-analysis assertions first (VAL-STYLING-001, 003, 007, 008)
2. Run command assertions (VAL-STYLING-004, 005, 006, 009)
3. Perform code-review assertions last (VAL-STYLING-002, 010)
4. Document results in synthesis.json
