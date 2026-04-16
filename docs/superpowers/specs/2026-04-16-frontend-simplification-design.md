# Frontend Simplification Design

## Summary

Refactor `src/frontend` to make the shadcn/Base UI layering explicit, reduce the number of workspace-owned components, and remove the ambiguous `features/workspace/ui` namespace.

This is a structural cleanup, not a visual redesign and not a frontend/backend contract change. The first target is `features/workspace`; other features keep their current ownership unless they need small follow-up alignment.

## Target Shape

- Keep `src/frontend/src/components/ui/*` as the only `ui` namespace. It remains the canonical shadcn/Base UI primitive layer.
- Keep `src/frontend/src/components/ai-elements/*` as the canonical registry-owned AI layer.
- Keep `src/frontend/src/components/product/*` small and curated. It should contain only proven reusable Fleet compositions built from `ui/*` and `ai-elements/*`.
- Remove `src/frontend/src/features/workspace/ui/`.
- Replace it with responsibility-named workspace modules such as `screen`, `conversation`, `composer`, `inspection`, `workbench`, and `session`.
- Keep `src/frontend/src/lib/workspace/*` as the owner of non-visual runtime/store/event shaping.
- Keep route files thin and continue pointing directly at feature entrypoints.

## Reduction Rules

- Net component count in `features/workspace/*` must go down. The cleanup is not allowed to merely rename folders while increasing or preserving the same wrapper count.
- Do not create feature-local `ui/` folders anywhere in the frontend.
- Do not extract a top-level component for trivial layout anatomy such as header/content/footer wrappers, simple card shells, spacing wrappers, or thin restyles of existing shadcn/Base UI primitives.
- Prefer direct primitive composition over one-off wrappers.
- Prefer private subcomponents inside a file before creating new top-level component files.
- Promote a component to `components/product/*` only when it is already reused across 2+ product surfaces or it replaces duplicated composition already present in multiple places.
- If reuse is uncertain, keep the code local and broader rather than creating speculative reusable components.
- Merge sibling micro-components when they are only consumed together or differ only by small prop variations.
- Candidate consolidation areas include transcript trace-part renderers, inspection shell/anatomy wrappers, and small composer-only helper components.

## Public Boundaries

- `components/ui/*` means primitives only.
- `components/product/*` means reusable app composition only.
- `features/workspace/*` means workspace-specific orchestration, content, and surface assembly only.
- `lib/workspace/*` means workspace runtime/state/types/adapters only.
- Workspace-specific panels such as session/history wiring or inspector data binding stay in `features/workspace/*`.
- Generic panel shells, detail layouts, and reusable section scaffolds must be promoted out of the workspace feature.

## Verification

- Inventory `features/workspace/*` before and after the refactor and confirm the top-level component/file count decreases.
- Confirm `features/workspace/ui/` is gone and no feature-local `ui/` directories remain.
- Add or tighten import-boundary enforcement if needed so `ui` stays reserved for `components/ui/*`.
- Run the standard frontend validation lane:
  - `pnpm run type-check`
  - `pnpm run lint:robustness`
  - `pnpm run test:unit`
  - `pnpm run build`

## Assumptions and Defaults

- The repo continues to use the current shadcn `base-vega` + Base UI baseline from `src/frontend/components.json`.
- No backend route, websocket, or generated OpenAPI contract changes are part of this cleanup.
- Workspace is the only in-scope feature for the first pass.
- The preferred policy is balanced reduction: aggressively delete duplicate and speculative wrappers, but allow a few broader feature modules instead of exploding the tree into many tiny components.
- When execution mode is enabled, write this design to `docs/superpowers/specs/2026-04-16-frontend-simplification-design.md` before creating the implementation plan.
