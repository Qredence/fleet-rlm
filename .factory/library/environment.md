# Environment

Environment variables, external dependencies, and mission setup notes.

**What belongs here:** current local environment facts, secret handling rules, off-limits resources, and setup expectations.
**What does NOT belong here:** service commands or ports beyond high-level constraints (use `.factory/services.yaml`).

---

## Mission environment facts

- Use the existing repo environment with `uv` and the local `.venv`.
- Frontend build assets already exist under `src/frontend/dist`; browser validation should use the API-served shell rather than starting a separate frontend dev server.
- `.env` is gitignored and must never be committed.
- Preserve the pre-existing unrelated working-tree edit in `src/fleet_rlm/api/bootstrap_observability.py` unless a feature explicitly requires changing that file.

## Ports and local services

- Mission validation service port: `8100`
- Avoid interfering with local listeners already using `3000`, `5000`, `5001`, and `8000`.
- A local MLflow server appears to be running on `5001`; treat it as off-limits unless the orchestrator explicitly changes scope.
- An unrelated local service already uses `8000`; do not stop or reuse it during this mission.

## Credential and external dependency posture

- No new external accounts or credentials are planned for this mission.
- Use current local configuration only; if a feature unexpectedly requires new live provider setup, return to the orchestrator.
- Never print or persist secret values from `.env`, Modal secrets, or provider credentials.

## Contract-sensitive workflow notes

- If API/OpenAPI-facing request or response shapes change, regenerate and validate `openapi.yaml`, then run frontend API drift checks.
- Browser validation authority is `agent-browser` against `127.0.0.1:8100`.
- Do not introduce schema migrations or external service changes as part of this simplification mission.
