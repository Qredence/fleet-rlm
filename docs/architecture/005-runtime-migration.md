# ADR 005: Runtime migration discipline

**Status:** accepted
**Decision date:** 2026-09-04

Every major runtime change must support a bounded, temporary legacy/v2
comparison before legacy behavior is removed. The comparison uses the fixed
runtime benchmark corpus, public Runtime Event fixtures, and the same selected
configuration provenance.

Temporary compatibility modes must have all of the following before landing:

1. one bounded user-visible mode selector, rather than flags for each internal
   component;
2. an owner and explicit comparison evidence;
3. a stated deletion phase and acceptance condition; and
4. a test that prevents the mode from silently becoming the new default.

Phase 0 defines the only initial selectors:

```toml
[defaults.runtime]
implementation = "legacy" # legacy | v2

[defaults.daytona]
interpreter = "broker" # broker | native

[defaults.rlm]
recursion_policy = "legacy" # legacy | capsule
```

The shipped Phase-0 selection is `legacy` / `broker` / `legacy`; non-legacy
values are policy declarations for their owning migration and must not be
represented as implemented before that migration lands.
