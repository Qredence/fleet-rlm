# Architecture decision records

Architecture decision records capture accepted choices that are costly to
reverse or surprising when reading one module in isolation. Current runtime
behavior remains documented in the architecture and reference guides. ADR
implementation notes identify later refinements without rewriting history.

## Records

- [ADR 0001: Coordinated Session-first Turn contract](0001-coordinated-turn-contract.md)
  — implemented; later notes refine local scope, Skills, native RLM, and terminal details.
- [ADR 0002: Canonical Deno and Ink terminal](0002-canonical-deno-and-ink-terminal.md)
  — Deno/Daytona decision current; renderer and shell-only clauses superseded in part.
- [ADR 0003: pi-tui with native terminal scrollback](0003-pi-tui-native-scrollback.md)
  — current terminal renderer and scrollback decision.
