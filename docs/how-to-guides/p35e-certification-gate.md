# P35-E certification gate

The release and contraction boundary is a content-addressed certification
manifest. It binds deterministic, package, security, release, live-Daytona,
and validator-isolation evidence to one Git SHA and one `uv.lock` digest.
Missing, failed, foreign-SHA, cleanup-incomplete, or stale evidence fails
closed.

## Local release lane

The credentialless package checks are safe to run in CI or from a clean
checkout:

```bash
make check-release
make build-release
git diff --check
```

`make build-release` emits exactly one wheel and one sdist, runs
`twine check --strict`, and writes a content-addressed artifact manifest under
`dist/`. It does not publish, start a service, or require provider credentials.
Use `scripts/validate_release.py version --requested vX.Y.Z` to validate a
release input before any upload.

## P35-E gate

The final gate consumes the serial P35-D live receipt and the mission service
manifest. It also verifies the behavior-golden baseline
`tests/fixtures/p35e-golden-baseline.json`; changed golden bytes require an
explicit human decision in that baseline file.

```bash
FLEET_LIVE=1 uv run python scripts/live_p35d_certification.py
uv run python scripts/validate_release.py service-isolation \
  --services /path/to/mission/services.yaml
uv run python scripts/certification_gate.py run \
  --services /path/to/mission/services.yaml
uv run python scripts/certification_gate.py verify
```

The gate is intentionally fail-closed until the live receipt and approved
service manifest exist. The sealed manifest is ignored local evidence and must
not be committed. P36-P41 consumers must verify it at their candidate SHA
before deletion-oriented checks can pass.
