# Release preparation task — version 1

Required inputs: target version and previous release tag. Start from protected
`main` on a new branch and produce a draft PR, never a published release.

1. Read `AGENTS.md` and the release runbook, observing secrets-on-read requirements.
   Verify the previous tag exists and is an ancestor of the selected source commit.
   Review the exact commit range, merged PRs, current changelog, and package version.
2. Propose only the requested version change in `pyproject.toml`, the corresponding
   `uv.lock` project version update, and its reviewed `CHANGELOG.md` section.
   Do not upgrade dependencies or change release/publishing configuration.
3. Describe user-visible changes and fixes. Include migration instructions for
   breaking behavior. Link substantive claims to PRs/commits; distinguish offline
   checks, live canaries, and measured gains. Never invent performance claims.
4. Verify the target changelog section is unique and nonempty with the existing
   extractor. Run release-note regression tests, `make check-ci-config`, and
   `make check`; include exact results and remaining uncertainty.
5. Apply `.circleci/tasks/review-checklist.md`. Request human review of version,
   compatibility, source range, notes, and package identity. The eventual release
   workflow captures notes from the reviewed source SHA after this PR is merged.

Do not edit historical release sections unless correcting an explicitly identified
error. Do not run live services or evaluations, invoke release pipelines, create
tags/GitHub releases, upload packages, promote deployment validation, or merge.
Publishing authorization is separate from this draft-preparation task.

Return a PR with the source range, proposed version, evidence links, migration
notes, validation results/run URLs, and any unresolved release-readiness issues.
