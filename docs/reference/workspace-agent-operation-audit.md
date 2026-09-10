# Workspace Agent filesystem operation audit

This inventory is the Phase 5D.01 decision boundary for the mounted Workspace
Agent. It documents the guarantees Fleet requires before replacing any of its
bounded, path-safe, or atomic storage behavior with a similarly named Daytona
SDK filesystem method.

The current production path is the versioned Workspace Agent protocol in
`src/fleet_rlm/daytona/workspace_agent/`, reached through the root-bound
adapters in `src/fleet_rlm/workspace/storage.py`. The agent is installed only
after a checksum/version handshake. Its fallback launcher and installed form
execute the same packaged `runtime.py` source.

## Required guarantees

Every operation is scoped to a trusted Volume root and relative workspace root.
The agent rejects traversal, unsafe symlink transitions, and protocol/request
overflows before returning a product-facing response. Requests and responses
are capped at 16 MiB; each caller supplies narrower read/write bounds where its
contract needs them. Transport failures are translated to closed storage errors
outside the agent.

| Operation | Fleet callers | Required guarantee | Current owner | SDK substitution status |
| --- | --- | --- | --- | --- |
| `list` | Workspace/project/memory listing | Root confinement, bounded entries/depth/cursor, no symlink escape | Agent runtime plus `Agent*StorageSession` | Not adopted: SDK list equivalence and pagination/error semantics need a contract test |
| `stat` | Existence and metadata checks | Root confinement; optional checksum from the opened inode rather than path text | Agent runtime | Not adopted: checksum/symlink semantics must be proven |
| `tail_read` | Memory injection | Byte-bounded tail, UTF-8 handling, root confinement | Agent runtime and workspace-memory policy | Retain: SDK download is not a bounded tail-read primitive |
| `read` / `read_page` | Workspace and project tools | Explicit byte/character/page bounds, cursor validation, inode revalidation, UTF-8 classification | Agent runtime and storage adapters | Not adopted: streaming/download needs bound, cancellation, and cursor parity evidence |
| `append` | Append-only workspace updates | Lock ownership, expected checksum/CAS, bounded write, post-write checksum | Agent runtime | Retain: generic SDK upload is not compare-and-swap append |
| `write` | Workspace/project writes | New-versus-overwrite policy, lock ownership, expected checksum/CAS, atomic replacement where supported, cleanup warning on non-atomic fallback | Agent runtime | Retain: upload does not establish Fleet publication ordering or CAS |
| `patch` | Memory record updates | Expected checksum/CAS, bounded patch application, lock/revalidation, atomic replacement | Agent runtime and memory promotion owner | Retain: no equivalent SDK transactional patch contract is certified |
| `unlink` / `delete` | Staged input and owned cleanup | Root confinement, inode revalidation, idempotent absence classification, no broad recursive deletion | Agent runtime plus lifecycle owners | Not adopted: SDK deletion must prove scoped idempotence and cancellation semantics |

## Atomicity and publication boundary

`replace_existing`, `write_new_direct`, `read_existing`, and
`lock_existing` in the agent runtime are intentional custom code. They use
directory descriptors, inode revalidation, and `fcntl.flock`; the handshake
advertises `replace_overwrite_recreate` and explicitly reports a
`non_atomic_overwrite_cleanup_warning` fallback. Fleet’s artifact promotion,
memory promotion, and Session history ownership layers remain responsible for
their database/publish ordering. A file upload succeeding never means a Turn
has committed.

## Adoption rule

Daytona `sandbox.fs` may be adopted selectively only after a provider-backed
contract proves the particular operation preserves the guarantees in its row:
path/symlink confinement, bounds, checksum, lock/CAS or equivalent atomic
replacement, cancellation, duplicate request handling, and per-file batch
error reporting. The proof must identify the SDK version, snapshot, candidate,
and provider behavior. Until then, use the Workspace Agent for semantics and
reserve SDK filesystem calls for its current source installation transport.

The pinned Daytona SDK `0.210.0` capability gate in
`tests/unit/backend/daytona/test_workspace_sdk_parity.py` establishes that
`download_file` has no byte/cursor arguments, `list_files` has no cursor, and
upload/delete offer no append, patch, checksum/CAS, or atomic-publication
contract. That is sufficient negative evidence for the current substitution
candidates: no Workspace Agent operation is retained merely for history.
Provider-backed comparison remains necessary only if a future SDK adds the
missing contract surface.
