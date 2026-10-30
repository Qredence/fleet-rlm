# F2 acceptance: child-output preflight and directory publication

## Status

**Preflight accepted offline. Atomic directory publication blocked by the Daytona Volume qualification gate.**

## Preflight evidence

`collect_child_artifacts` enumerates the complete changed child-output set before public writes. It rejects more than 100 outputs, aggregate output over 25 MB, duplicate target paths, invalid paths, individual oversize files, and pre-existing destination collisions. Unchanged copied inputs remain excluded. Tests assert zero public writes for the 101-file, aggregate-byte, and collision failures. Provider upload failures remain outside this guarantee.

The full `make check` suite passed: 1,844 passed, 1 skipped, including the F2 preflight regressions. Ruff, formatting, type checking, documentation links, OpenAPI contract, and package build passed as well.

## Implementation coordinates and identities

- Preflight implementation: `src/fleet_rlm/sandbox/workspace_io.py:294-363`; regression coverage includes `tests/sandbox/test_workspace_files.py` and `tests/lite/test_child_inputs.py`.
- No database migration or publication-intent field was added because the Daytona publication gate failed before stronger publication work.
- Dependency identity from `uv.lock`: Daytona 0.224.0, Python runtime 3.13.
- Current working-tree delta for `workspace_io.py` is +34/-7 lines (the file also contains edits outside the F2 preflight). Current combined coverage report for the module is 93% statements and 81% branches.

## Daytona publication probe

The bounded probe used one dedicated disposable sandbox mounted to the configured Daytona Volume. It created two synthetic files under `/workspace/.fleet-publications/<id>/`, then called same-filesystem `os.replace(stage, final)` to publish to `/workspace/artifacts/<agent-id>/` while a remote observer sampled the final path.

The provider accepted staging-file uploads, but the remote process call containing the directory rename remained blocked for several minutes. No final `/workspace/artifacts/` directory became visible. The probe was interrupted; its sandbox was deleted and the exact staging prefix was removed through a second disposable sandbox. A subsequent Daytona label lookup showed no probe sandboxes. This fails the required all-or-nothing provider boundary. Concurrent-observation, lost-acknowledgment resolution, and process-interruption assertions did not complete.

## Remediation update (30 October 2026, evening)

- **Fixed a defect that made every promotion fail on a real provider.** The destination-collision
  check built its remote script with `"\\n"` inside a normal string, emitting a literal
  backslash-n; the remote `compile` raised `SyntaxError`, and `_remote_exec` turned that into
  `RuntimeError("Remote Daytona command failed")` for every child, including one with no
  outputs. The script now comes from `sandbox.runtime.fs_ops.existing_workspace_paths_code`,
  and `tests/sandbox/test_paths.py` compiles every generated remote script so this class of
  bug cannot recur — the previous tests all stubbed `exec_remote_python` and could not see it.
- The preflight read loop now enforces its quotas while reading, so a child that wrote far too
  much is refused after at most 101 reads instead of buffering its whole output on the host.
- A destination already holding identical bytes is a completed publication and is skipped, so a
  retried promotion is idempotent instead of failing on its own earlier work (PRD F2.3
  "retry by publication identity/digest"). A destination holding different bytes is still
  refused. Both the skipped and the newly written destinations are receipted.
- Live: a child asked for 101 outputs was refused with zero promoted files, confirmed both by the
  Turn's stored inventory and by the root's own workspace inspection.

Publication authority remains unimplemented and this stage still does not claim atomicity.

## Decision

Do not add or claim a stronger directory publication protocol on this evidence. Do not introduce a manifest fallback. F2 remains preflight-only; publication is blocked pending a provider-supported boundary and a successful repeat of the full live probe matrix.
