# Daytona v2 migration runbook

Rehearse on isolated staging resources before applying the cutover to an existing deployment.

## Verify the staged application

- **Zero-VM files.** In a new Session, upload a CSV and XLSX, list, preview, download, and verify exact bytes. Confirm the sandbox ID remains null and the provider inventory shows no allocation for the Session. Repeat download and artifact preview after manually deleting its test VM.
- **Replacement and concurrent admission.** Rehydrate the two-turn fixture after VM deletion and verify a new ID and matching input and artifact hashes. Submit two concurrent roots against one Session using separate backend workers. Expect one admitted turn and one conflict, one surviving VM binding, and no orphan VM. Run a second Session concurrently and verify workspace and trace isolation.
- **Artifacts, Skills, and history.** In the browser, observe nested tool activity, stop a live recursive turn, retry, download a published chart, and reload the Session. Verify the nested tree replays with terminal statuses. Publish and install a generated `SKILL.md` from S3 and reject a stale SHA. Preserve existing approval and memory-scope checks. Use one existing large-file fixture to verify the transfer path below the upload cap and reject an above-limit upload.
- **Failure ordering and delete.** Inject a storage failure after partial artifact upload. Verify the API emits no successful completion or durable artifact entries. Retry successfully. Delete an idle Session with multiple files, verify its prefix and VM are gone, and check a neighboring Session remains intact. Inject a failed object batch and verify a subsequent delete finishes cleanup.

Reuse `test_phase2_engine.py` and `fleet_workflow_baseline.py` for live coverage. Use the existing `FLEET_RUN_PHASE2_LIVE=1` gate for the scripted cases and the migration suite in the HTTP runner using its `--url`, `--repeats 5`, and `--output-dir` options. Run it against the baseline and candidate endpoints and record any skips. Reuse the existing DB, API, and optimizer tests for CAS, SSE, file caps, cancellation, and settings. Run `make check-backend` after the worker canary and `make check` before cutover. Run the browser checks against the same staged build. [Fleet live workflow runner](../scripts/fleet_workflow_baseline.py)

From the repository root, run the suite with the selected arm's staging environment. Start both staging services with DSPy's memory and disk model caches disabled. The v2 arm deliberately deletes the Session's test VM between turns.

```bash
uv run --project backend --env-file "$FLEET_STAGING_ENV" python \
  scripts/fleet_workflow_baseline.py --daytona-migration v2 \
  --url "$FLEET_STAGING_URL" --repeats 5 --output-dir "$FLEET_EVIDENCE_DIR"
```

Run the comparison with `--daytona-migration v0` and the reference service's environment and URL. The runner writes the submitted answers, SSE events, trace IDs, cleanup status, and verdict for each repetition.

## Cutover and release gates

- **Go/no-go.** Require all sandbox, host S3, scripted trajectory, and full-stack correctness checks to pass with no missing trace or surviving test worker. Require five successful candidate repetitions per real-model workflow with no paired regression. Use the 120-second startup bound and flag a warm execution median more than 25% slower than the fresh paired baseline for review before rollout. Report the Volume experiment and Spot capability alongside the required gates. A skipped required run leaves its gate pending.
- **Data rehearsal.** On a disposable Neon branch, apply Alembic with the direct connection and verify sample v0 binds and stale file metadata are cleared, old artifact controls are removed from saved run snapshots, and titles, textual history, run relationships, memories, and server-side Skills survive. Retire old Daytona workspace override keys and validate the new region in deployment configuration. Compare before and after counts and exercise the same-path upload case to catch stale links.
- **Live cutover.** Drain v0 writes and workers. Before applying revision `0004_daytona_v2_cutover`, archive legacy Volume files that users need to keep and verify the archive is readable. Save a Postgres restore point, apply the rehearsed migration, and deploy the verified v2 image and host configuration. Re-upload retained files from the archive into the corresponding v2 Sessions. Use a live smoke session to verify create, recursive execution, MLflow trace, artifact download, forced VM replacement, and clean delete before reopening traffic. Preserve the old Volume and deployment checkpoint during verification. Roll back by restoring the checkpoint before reopening if a gate fails. After live v2 writes, preserve the new Postgres and S3 state and fix forward.
- **After reopening.** Check provisioning and hydration errors, warm and cold latency, VM allocations and cleanup, file publication failures, and missing trace correlations. Retire v0 runtime credentials and resources once smoke and cleanup evidence are recorded. Keep the run IDs, trace IDs, and evidence location in the release record.
