# Phase 6 promotion and rollback

Fleet promotes a complete, immutable release bundle. The bundle binds one clean
checkout, built artifact manifest, resolved non-secret policy, dependency lock,
Session and SemanticChild image identities, database head, and quality dataset
and scorer identities. It does not contain credentials, infrastructure errors,
or user content.

Build artifacts first, then create a write-once bundle receipt outside tracked
source files:

```bash
make check
make check-security
make build-release
uv run python scripts/phase6_promotion.py prepare \
  --profile daytona-recursive \
  --artifact-manifest dist/artifact-manifest.json \
  --images .fleet-evidence/phase6/images.json \
  --database-head <alembic-head> \
  --dataset-digest <sha256> \
  --scorer-digest <sha256> \
  --output .fleet-evidence/receipts/phase6/candidate.json
```

The candidate checkout must be clean. `images.json` contains only the two
immutable non-secret image names or digests:

```json
{"session":"fleet-rlm-python313-v10","semantic_child":"fleet-rlm-python313-child-v5"}
```

Create and validate an equivalent baseline bundle before opening the maintenance
window. A rollback pair requires distinct revisions and the same additive-
compatible database head:

```bash
uv run python scripts/phase6_promotion.py validate-rollback-pair \
  --baseline .fleet-evidence/receipts/phase6/baseline.json \
  --candidate .fleet-evidence/receipts/phase6/candidate.json \
  --output .fleet-evidence/receipts/phase6/rollback-pair.json
```

During the maintenance window, close new admissions, settle or cancel and fence
active Runs, and verify provider cleanup before switching the complete bundle.
Rehearse baseline → candidate → baseline → candidate. At each stage verify
existing Session history, workspace files, artifacts, and a new public API/SSE
Turn. Do not restore an older database snapshot over newer user data.

The receipt validates identity only. It does not authorize provider activity,
warm-pool creation, service restart, package publication, or promotion. Record
those separately in the ADR 006 evidence ledger.
