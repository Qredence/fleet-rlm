# Frontend test-teardown repair evidence — 2026-07-11

## Failure signature

The prior clean-sidecar frontend lane completed its assertions but Vitest
reported two unhandled `ReferenceError: window is not defined` rejections after
jsdom teardown. Both followed this stack:

```text
ReferenceError: window is not defined
  at resolveUpdatePriority (.../react-dom-client.development.js:1308:7)
  at requestUpdateLane (.../react-dom-client.development.js:16345:11)
  at dispatchSetState (.../react-dom-client.development.js:9126:14)
  at highlight (src/components/ui/code-block.tsx:49:7)
    setHighlightedHtml(html)
```

This was an asynchronous component-lifecycle defect, not a Vitest or Shiki
runner defect. The effect started highlight work without cancellation, did not
contain rejected highlighting promises, and could let an older result replace
newer code.

## Repair

`src/frontend/src/components/ui/code-block.tsx` now:

- clears stale highlighted HTML when code, language, or theme changes;
- marks effect work cancelled on dependency cleanup and unmount;
- refuses to call React state setters after cancellation;
- contains dynamic-import and highlighting failures; and
- retains the existing plain `<pre><code>` fallback on failure.

No component props, caller behavior, Shiki dependency, package lock, or test
runner error handling changed.

## Deterministic regression coverage

`src/frontend/src/components/ui/__tests__/code-block.test.tsx` uses a mocked
Shiki module and deferred promises to cover:

1. highlight completion after unmount and temporary removal of jsdom `window`;
2. code A resolving after newer code B without overwriting B;
3. rejected highlighting retaining plain code without an unhandled rejection;
4. normal highlighting replacing the plain fallback.

The real Bash-tool caller remained in the focused validation lane.

### Pre-fix red proof

The new regression file was run once against the original effect before the
repair was restored:

```bash
cd src/frontend
pnpm run test:unit src/components/ui/__tests__/code-block.test.tsx --reporter=verbose
```

Result: exit `1`; `1` failed and `3` passed, with `2` unhandled errors. The
stale-result assertion failed because code A replaced code B. Vitest also
reported the exact `window is not defined` stack above and an unhandled
`Error: Unsupported language` from the rejected Shiki promise. This proves the
tests fail for all three lifecycle defects they are intended to prevent.

## Validation

```bash
cd src/frontend
pnpm run test:unit \
  src/components/ui/__tests__/code-block.test.tsx \
  src/components/agent-elements/tools/__tests__/bash-tool.test.tsx
pnpm run test:unit
pnpm run type-check
pnpm run lint
CI=true pnpm run build
cd ../..
make check-frontend
make quality-gate
make api-check
```

Final results:

- Frontend unit suite: `80` files passed; `430` tests passed and `15` skipped.
- Vitest reported zero unhandled rejections.
- Type checking, linting, OpenAPI drift checking, and client/SSR production
  builds passed.
- `make quality-gate` and `make api-check` passed on 2026-07-11.

The clean full gate also required bounding pytest-xdist auto fan-out to two
workers; the unbounded lane had produced nondeterministic worker deaths under
the available host capacity. The cap is documented and remains explicitly
overridable. The AGENTS freshness check was corrected to ignore Git-ignored
workspace replicas instead of validating stale copies under `.codex/` and
`.worktrees/`.

The production build retained existing informational warnings for large icon
barrels/chunks and third-party `lottie-web` direct `eval`; none was introduced
by this repair.
