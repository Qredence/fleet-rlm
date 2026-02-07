# Releasing `fleet-rlm` to PyPI

This project uses a manual token-based publish flow:

1. Build and validate artifacts locally
2. Upload to TestPyPI
3. Smoke test install from TestPyPI
4. Upload the same artifacts to PyPI

All commands below assume zsh and are run from the repository root.

## 1) Preflight

```bash
# from repo root
uv run pytest
make lint
make format
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
rm -rf dist build
uv build
uvx twine check dist/*
```

Expected outputs:

- `dist/fleet_rlm-0.1.0.tar.gz`
- `dist/fleet_rlm-0.1.0-py3-none-any.whl`

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
  fleet-rlm==0.1.0
source .venv-release-smoke/bin/activate
fleet-rlm --help
deactivate
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

```bash
# from repo root
git tag v0.1.0
git push origin v0.1.0
```

Then update changelog/release notes with:

- Version number
- Date/time of release
- Key changes included
- Any known caveats

## Important rules

- PyPI versions are immutable. You cannot re-upload `0.1.0` after publishing.
- If upload fails after partial publish, bump version (`0.1.1`) and rebuild.
- Always upload the exact artifacts validated by `twine check`.
