# Daytona repository acquisition comparison

This is a one-off comparison for the source-acquisition delay in trace
`tr-6f7bc07d95733c60b28d80af03f9323e`. It is not a runtime policy or an
end-to-end performance benchmark. **A live Daytona run needs explicit user
authorization.** Do not execute this runbook merely by reading it.

Use the existing configured Fleet snapshot and the pinned Daytona SDK. The
comparison target is the public repository `https://github.com/Qredence/fleet-rlm.git`
at commit `7b26fd9ecbb8c2dfd55be729d3a8465de705991b`, which was the branch
`refactor/engine-optimize-consolidation` tip when the comparison was prepared.
This is a reproducible comparison target, not a claim about the commit downloaded
by the historical trace. Confirm the commit is still accessible before starting.

## Procedure

1. Create one disposable sandbox from the configured, preflighted snapshot, with
   no Session Volume attached. Record a unique run ID, snapshot identity and
   sandbox ID immediately. Put its ID and each completed attempt's method,
   duration, outcome and verification result in a local report before proceeding.
2. Alternate Git API and archive acquisition, three attempts each, using a new
   sandbox-local scratch directory for every attempt. For Git, clone the named
   branch with `sandbox.git.clone`, then use `sandbox.git.checkout_branch` with
   the full commit SHA and verify `HEAD`; include both calls in the acquisition
   duration. The archive URL is
   `https://codeload.github.com/Qredence/fleet-rlm/tar.gz/7b26fd9ecbb8c2dfd55be729d3a8465de705991b`.
   Extract directly into its scratch directory using Python's safe tar extraction
   filter; do not move an extracted tree onto a Daytona Volume. Record elapsed
   time from the start of each acquisition through its successful completion.
3. Verify the Git checkout's `HEAD` equals the requested commit. For both methods,
   compare file paths, file modes, symlink targets and Git blob object IDs with
   the commit's tree using a trusted local copy of that Git object. Do not treat
   the archive URL or successful HTTP status alone as verification. This target
   has no `.gitmodules` or `.gitattributes` at the selected commit. Keep verification
   time separate from acquisition time.
4. Select the Git tool only if all three Git attempts succeed and verify, all
   three archive attempts succeed and verify, and the Git median is no greater
   than the archive median. Otherwise select the archive Skill only when its
   attempts succeeded and verified; if either method's outcome is ambiguous, or
   the archive is not verified, stop and investigate instead of selecting a path.
   Three trials provide only a small provider-specific comparison.

Do not blindly retry a timed-out provider request: its timeout does not cancel
the operation. Inspect the saved sandbox and attempt state first. Once operations
are confirmed stopped, delete only the disposable sandbox created for this run
and record the cleanup outcome. Keep the report and sandbox ID if cleanup or an
operation is unconfirmed. Credentials and sensitive provider responses do not
belong in the report.

## Recorded result

The authorized comparison ran on 2026-10-26 using the public repository and
commit above, snapshot `fleet-rlm-python313-v14`
(`b3c974d4-2799-4630-8d60-fee5f9eb435a`), run `repo-acq-155299a13387`, and
sandbox `f6d635d0-bfd6-4d2e-a921-d13d8cf9f612`. The local recovery report is
`/private/tmp/fleet-daytona-acquisition-repo-acq-155299a13387.json`.

| Method | Attempts | Durations (seconds) | Median | Verification |
| --- | ---: | --- | ---: | --- |
| Daytona Git branch clone + full-SHA checkout | 3/3 succeeded | 10.349, 11.179, 12.628 | 11.179 | All matched the requested tree |
| codeload archive + safe extraction | 3/3 succeeded | 0.599, 0.547, 0.604 | 0.599 | All matched the requested tree |

Each result matched all 205 tracked files by path, mode, symlink target, and Git
blob ID. The archive trials downloaded 915,169 bytes and each contained 254
archive members. Git acquisition was slower in this run, so Fleet selected the
bundled `repository-inspection` Skill. These are source-acquisition timings for
one repository, commit, snapshot, and provider run; they do not establish a
general or end-to-end speedup. The preliminary invalid harness attempts are
retained in the local JSON report and excluded from the six valid trials.
After all attempts completed and their scratch directories were removed, the
disposable sandbox was deleted successfully; no comparison operation remained
in flight.
