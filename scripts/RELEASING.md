# Releasing `fleet-rlm` to PyPI

This project supports both automated and manual release workflows.

## Automated Release (Recommended)

Use the GitHub Actions workflow for a fully automated release:

1. Go to **Actions** → **Release to PyPI** in the GitHub repository
2. Click **Run workflow**
3. Finalize the `CHANGELOG.md` section for the version you are shipping
4. Enter the version number (for this release, `0.4.99` or `v0.4.99`)
5. Approve the TestPyPI environment deployment
6. Once smoke tests pass, approve the PyPI environment deployment

The workflow will:

- Run all preflight checks (tests, linting)
- Run dependency/code security checks
- Build and verify distributions
- Upload to TestPyPI and run smoke tests
- Upload to PyPI after approval
- Create a GitHub release with the git tag
- Build the GitHub release body from the matching `CHANGELOG.md` section

### Prerequisites for Automated Release

Configure GitHub environments and OIDC trusted publishing:

1. **TestPyPI Environment:**
   - Create environment: `testpypi`
   - Configure OIDC trusted publisher at https://test.pypi.org/manage/account/publishing/
   - Set workflow: `release.yml`, environment: `testpypi`

2. **PyPI Environment:**
   - Create environment: `pypi`
   - Add required reviewers for production approval
   - Configure OIDC trusted publisher at https://pypi.org/manage/account/publishing/
   - Set workflow: `release.yml`, environment: `pypi`

OIDC eliminates the need for API tokens - GitHub uses temporary credentials via OpenID Connect.

## Manual Release

If you prefer manual control or need to troubleshoot, follow this token-based publish flow:

1. Build and validate artifacts locally
2. Upload to TestPyPI
3. Smoke test install from TestPyPI
4. Upload the same artifacts to PyPI

All commands below assume zsh and are run from the repository root.

## 1) Preflight

For the single-command local path, run:

```bash
# from repo root
make release-check
```

That target already runs the full validation lane, rebuilds and syncs packaged UI
assets, builds the wheel/sdist, validates the packaged frontend, and runs `twine check`.

If you need to run the phases manually instead of using `make release-check`:

```bash
# from repo root
uv run pytest
uv run ruff check src tests
uv run ruff format --check src tests
uv run ty check src --exclude "src/fleet_rlm/scaffold/**"
uv run python scripts/validate_release.py hygiene
uv run python scripts/validate_release.py metadata
# TODO: Remove this ignore once Pygments ships a patched release for
# GHSA-5239-wwwm-4pmq / CVE-2026-4539.
uvx pip-audit --ignore-vuln GHSA-5239-wwwm-4pmq
uvx bandit -q -r src/fleet_rlm -x tests,src/fleet_rlm/scaffold -lll

if [ -f src/frontend/package.json ]; then
  cd src/frontend
  pnpm install --frozen-lockfile
  pnpm run api:check
  pnpm run type-check
  pnpm run lint:robustness
  pnpm run test:unit
  pnpm run build
  cd ..
fi
```

Confirm package name availability before first public upload:

```bash
# from repo root
curl -sS -o /dev/null -w "%{http_code}\n" https://pypi.org/pypi/fleet-rlm/json
```

`404` means no project currently exists at that name.

## 2) Build and verify distributions

```bash
# from repo root
make release-artifacts
```

`make release-artifacts` is the canonical manual packaging path. It rebuilds the
frontend with `pnpm`, syncs packaged UI assets into `src/fleet_rlm/ui/dist`,
builds the wheel/sdist, validates wheel asset integrity, and runs `twine check`.
In source checkouts, `uv build` also triggers the frontend packaging hook from
`setup.py`, but `make release-artifacts` remains the explicit end-to-end manual
path because it synchronizes assets and validates the resulting artifacts.

Expected outputs:

- `dist/fleet_rlm-0.4.99.tar.gz`
- `dist/fleet_rlm-0.4.99-py3-none-any.whl`

## 3) Upload to TestPyPI

Create a TestPyPI API token and export credentials:

```bash
# from repo root
export TWINE_USERNAME=__token__
export TWINE_PASSWORD="<testpypi-token>"
```

Upload:

```bash
# from repo root
uvx twine upload --repository-url https://test.pypi.org/legacy/ dist/*
```

## 4) Smoke test install from TestPyPI

```bash
# from repo root
uv venv .venv-release-smoke
uv pip install --python .venv-release-smoke/bin/python \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple \
  fleet-rlm==0.4.99
source .venv-release-smoke/bin/activate
python -m uvicorn fleet_rlm.api.main:app --host 127.0.0.1 --port 8765 >/tmp/fleet-release-smoke.log 2>&1 &
SERVER_PID=$!
trap 'kill $SERVER_PID 2>/dev/null || true; wait $SERVER_PID 2>/dev/null || true' EXIT
for i in {1..30}; do
  if curl -fsS http://127.0.0.1:8765/health >/tmp/fleet-release-health.json; then
    break
  fi
  sleep 2
done
curl -fsS http://127.0.0.1:8765/health >/tmp/fleet-release-health.json
curl -fsS http://127.0.0.1:8765/ >/tmp/fleet-release-root.html
grep -qi "<!doctype html" /tmp/fleet-release-root.html
deactivate
trap - EXIT
kill $SERVER_PID 2>/dev/null || true
wait $SERVER_PID 2>/dev/null || true
rm -rf .venv-release-smoke
```

## 5) Upload to PyPI

Create a PyPI API token and export credentials:

```bash
# from repo root
export TWINE_USERNAME=__token__
export TWINE_PASSWORD="<pypi-token>"
```

Upload:

```bash
# from repo root
uvx twine upload dist/*
```

## 6) Tag and document release

Finalize `CHANGELOG.md` and any docs release-notes page before tagging or
running the automated workflow. The GitHub release body is generated directly
from the changelog section for the version being published.

```bash
# from repo root
git tag v0.4.99
git push origin v0.4.99
```

## Important rules

- PyPI versions are immutable. You cannot re-upload a published version.
- If upload fails after partial publish, bump the patch version and rebuild.
- Always upload the exact artifacts validated by `twine check`.
