# Figma Imports

This directory contains components and SVG path data exported from the
Figma Make environment. These files are **auto-generated** and should
generally **not** be edited by hand — any manual changes will be lost
when the Figma frame is re-exported.

---

## SVG Path Modules

Each `svg-*.ts` file exports a default object whose keys are hashed path
IDs (`p<hex>`) and whose values are SVG `d` attribute strings.

| Module              | Source Frame     | Icon Paths (key &rarr; description)                                                                                                                                                                                                                                                                                                                                                   |
| ------------------- | ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `svg-synwn0xtnf.ts` | QredenceFleet    | `p4dc2a80` Qredence flame logo &bull; `p2fd67200` compose/edit pencil &bull; `p3a006380` + `p20a25d00` + `p1337c9c0` + `pe8ed000` + `p2032bd00` side-panel icon &bull; `p1c3efea0` + `p25877f40` notification bell &bull; `p1d59db00` bolt &bull; `pb3f4d00` + `p2bdb5600` tune &bull; `p874e300` sparkle &bull; `pb803f80` plus &bull; `p16690500` mic &bull; `p262e1200` send arrow |
| `svg-g9pgfbkia.ts`  | Header (legacy)  | _Superseded by `svg-synwn0xtnf.ts`_ — kept for reference only                                                                                                                                                                                                                                                                                                                         |
| `svg-er4mz3hmp1.ts` | ComposeFooter    | `p2b835b70` plus icon &bull; `p3c9c8b00` microphone icon &bull; `p22cb5880` upward arrow (send)                                                                                                                                                                                                                                                                                       |
| `svg-z9gb50zttr.ts` | Frame8 (Sidebar) | `p1f0f5080` sidebar panel icon &bull; `p1f4a7d00` cross/close (X) &bull; `p2d876f80` cleanup/magic-wand &bull; `p3d35000` Qredence flame (large) &bull; `p1e897300` globe &bull; `p22cb5880` upward arrow (send) &bull; `p439f200` chevron down &bull; `p21b06100` / `p2ae67400` / `p37fd4d00` small dots (ellipsis)                                                                  |
| `svg-14cpcjf8e9.ts` | Token            | `p36b52f00` small edit/pencil icon                                                                                                                                                                                                                                                                                                                                                    |

### Which app components consume each module?

| SVG Module          | App Consumer(s)                                                                                                                                                                                 | Import Alias             | Paths Actually Used                                                                                                                                                                               |
| ------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `svg-synwn0xtnf.ts` | `layout/TopHeader.tsx`, `pages/LoginPage.tsx`, `pages/SignupPage.tsx`, `pages/LogoutPage.tsx`, `pages/NotFoundPage.tsx`, `features/LoginDialog.tsx`, `pages/skill-creation/ChatMessageList.tsx` | `headerSvg` / `svgPaths` | `p4dc2a80` (logo), `p2fd67200` (compose), `p3a006380` + `p20a25d00` + `p1337c9c0` + `pe8ed000` + `p2032bd00` (side panel), `p1d59db00` + `pb3f4d00` + `p2bdb5600` + `p874e300` (suggestion icons) |
| `svg-er4mz3hmp1.ts` | `ui/prompt-input.tsx`                                                                                                                                                                           | `composerSvgPaths`       | `p2b835b70` (plus), `p3c9c8b00` (mic), `p22cb5880` (send)                                                                                                                                         |
| `svg-z9gb50zttr.ts` | `layout/BuilderPanel.tsx`                                                                                                                                                                       | `svgPaths`               | `p1f0f5080` (sidebar panel icon)                                                                                                                                                                  |
| `svg-14cpcjf8e9.ts` | _None_ (only used inside `Token.tsx` frame below)                                                                                                                                               | —                        | —                                                                                                                                                                                                 |

---

## Frame Components

Figma frame exports are full React components with hardcoded pixel values,
colors, and layout — they do **not** follow the project design system.
They serve as **visual reference** for the design intent; the actual app
UI is re-implemented as proper components under `src/app/components/`.

| Frame Component      | SVG Module Used     | Graduated To (app component)                                       | Status                      |
| -------------------- | ------------------- | ------------------------------------------------------------------ | --------------------------- |
| `QredenceFleet.tsx`  | `svg-synwn0xtnf.ts` | `layout/TopHeader.tsx`, `pages/skill-creation/ChatMessageList.tsx` | Superseded — reference only |
| `Header.tsx`         | `svg-g9pgfbkia.ts`  | _Legacy, superseded by QredenceFleet.tsx_                          | Superseded — reference only |
| `ComposeFooter.tsx`  | `svg-er4mz3hmp1.ts` | `ui/prompt-input.tsx`                                              | Superseded — reference only |
| `Frame8.tsx`         | `svg-z9gb50zttr.ts` | `layout/BuilderPanel.tsx`                                          | Superseded — reference only |
| `Token.tsx`          | `svg-14cpcjf8e9.ts` | `features/SkillBadge.tsx` (concept, not 1:1 match)                 | Superseded — reference only |
| `TitleContainer.tsx` | _None_              | Inline in `ChatPanel.tsx` welcome state                            | Superseded — reference only |
| `Rectangle.tsx`      | _None_              | Generic border element, unused                                     | Superseded — reference only |

> **No app component imports any frame component.** Only SVG path modules
> are imported from this directory.

---

## Graduating a Figma Import

When a Figma frame is reimplemented as a proper design-system-compliant
component:

1. Create the new component under `src/app/components/` (in `features/`,
   `layout/`, `shared/`, or `ui/` as appropriate).
2. Import **only the SVG path data** you need:
   ```tsx
   import svgPaths from "@/imports/svg-synwn0xtnf";
   ```
3. **Do not** import the Figma frame component itself — it uses hardcoded
   values and doesn't follow `theme.css` tokens or the `typo` helper.
4. Use `fill="var(--foreground)"` or `fill="currentColor"` for theme
   compatibility; never hardcode hex/rgba fills from the Figma export.
5. The original frame component remains in this directory as a visual
   reference and will be overwritten on the next Figma re-export.

---

## Recommended `.gitattributes` Rules

Add the following to your repo root `.gitattributes` so GitHub collapses
these generated files in pull-request diffs:

```gitattributes
src/imports/*.tsx  linguist-generated=true
src/imports/svg-*.ts  linguist-generated=true
```
