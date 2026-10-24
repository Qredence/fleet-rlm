# FastAPI Health and Improvement Plan — fleet-rlm API Layer

## Overview

**Goal:** Improve the correctness, maintainability, and idiomatic quality of the
FastAPI API layer in fleet-rlm. The upgrade from `0.151.0` to `0.162.0` is one
item in this plan; the remainder address concrete patterns found during a full
line-by-line review of `src/fleet_rlm/api/` cross-referenced against the
official FastAPI skill and live FastAPI documentation.

**Scope:**
- All files in [`src/fleet_rlm/api/`](src/fleet_rlm/api/)
- Dependency manifest: [`pyproject.toml`](pyproject.toml) / [`uv.lock`](uv.lock)
- Test infrastructure: [`tests/api/`](tests/api/)

**Out of scope:** Application logic in DSPy, Daytona, Store, or evaluation
layers. No database migrations. No new endpoints.

**Current locked versions (confirmed from `pyproject.toml` and `uv.lock`):**

| Package | Locked version |
|---|---|
| `fastapi[standard]` | `0.151.0` |
| `starlette` | `1.7.0` |
| `pydantic` | `2.13.5` |
| `pydantic-core` | `2.46.5` |
| `anyio` | `4.15.1` |
| `uvicorn[standard]` | `0.54.0` |

---

## Sub-task 1 — Upgrade `fastapi[standard]` to 0.162.0

**Status:** [x] done

### Intent

Advance the locked version of `fastapi[standard]` from `0.151.0` to `0.162.0`
to pick up 11 releases worth of bug fixes, performance improvements, and new
features (Pydantic validation options on parameters, improved form semantics,
improved SSE support). All three breaking changes introduced across this range
were confirmed non-impacting on this codebase (see Reference — Release Notes
Analysis below). No application code changes are required.

### Expected Outcomes

- `pyproject.toml` line 40 pins `fastapi[standard]==0.162.0`
- `uv.lock` is regenerated and consistent
- `uv run python -c "import fastapi; print(fastapi.__version__)"` prints `0.162.0`
- Full test suite passes without new failures
- Live `/health` and `/openapi.json` respond correctly

### Todo List

1. Edit [`pyproject.toml`](pyproject.toml) line 40:
   ```diff
   -    "fastapi[standard]==0.151.0",
   +    "fastapi[standard]==0.162.0",
   ```
2. Regenerate the lock file:
   ```bash
   uv lock
   uv sync --dev
   ```
3. Verify import:
   ```bash
   uv run python -c "import fastapi; print(fastapi.__version__)"
   # Expected: 0.162.0
   ```
4. Confirm no `**extra` kwargs, boolean form fields, or `extra="forbid"` query
   models were added since the audit (grep sanity check):
   ```bash
   grep -rn "Query(\|Header(\|Cookie(\|Form(\|File(\|Path(" src/fleet_rlm/api/ \
     | grep -v "ge=\|le=\|gt=\|lt=\|min_length\|max_length\|pattern\|default\|description\|alias\|title\|deprecated\|auto_error\|File()"
   ```
5. Run the API test suite:
   ```bash
   uv run pytest tests/api/ -v --tb=short
   ```
6. Run the full check:
   ```bash
   make check
   ```
7. Regenerate and review `openapi.yaml` if it is a checked-in artifact:
   ```bash
   uv run python -c "
   import json
   from fleet_rlm.api.app import create_app
   app = create_app(initialize_db=False, configure_observability=False)
   print(json.dumps(app.openapi(), indent=2))
   " > openapi_new.json
   diff openapi.yaml openapi_new.json
   ```
   Commit only intentional schema changes.

### Rollback

If regressions surface that cannot be resolved:
```bash
git checkout pyproject.toml uv.lock
uv sync --dev
make check
```
No application code changes are needed for the rollback because no application
code changes are needed for the upgrade.

### Relevant Context

- [`pyproject.toml` line 40](pyproject.toml)
- [`uv.lock`](uv.lock)
- [`tests/api/fakes.py`](tests/api/fakes.py) — `TestClient` factory with `dependency_overrides`
- See _Reference — Release Notes Analysis_ section at the end of this file for
  the full breaking-change breakdown and why each is non-impacting.

---

## Sub-task 2 — Replace `response_model=` with typed return annotations

**Status:** [x] done

### Intent

Every route handler in the codebase currently uses the older pattern:

```python
@router.get("/...", response_model=Sessions, ...)
async def list_sessions(...) -> dict:
```

The idiomatic FastAPI pattern (reinforced by the official skill) is to drop
`response_model=` and annotate the return type directly:

```python
@router.get("/...", ...)
async def list_sessions(...) -> Sessions:
```

The typed-return form is:
- More legible — the contract is visible without reading the decorator
- Statically checkable by `ty` (the project's type checker, confirmed in
  `Makefile`)
- Less prone to divergence: the response contract is enforced at the type level,
  not only at FastAPI's runtime serialization step

Affected handlers: every `-> dict` route handler in the four router files. The
`-> None` handlers (delete routes that genuinely return nothing) and `-> AsyncIterator[ServerSentEvent]` SSE handlers are already correctly typed and are out of scope.

### Expected Outcomes

- No route handler carries both `response_model=X` in its decorator and `-> dict`
  as its return type
- `response_model=` in decorators is removed where redundant with the typed
  return annotation
- `ty check` continues to pass (or improves — fewer `dict` escapes)
- Existing tests pass unchanged

### Todo List

For each of the four router files listed below, for each qualifying route:

1. Remove `response_model=SomeModel` from the `@router.verb(...)` decorator.
2. Change `-> dict` on the function signature to `-> SomeModel`.
3. Confirm that the function body `return {...}` is still valid — FastAPI will
   serialise the dict through the declared model, so dicts are fine as return
   values when the model is declared on the function.
4. Run `uv run pytest tests/api/ -v` after editing each file.

**Files and handlers to update:**

| File | Handlers affected |
|---|---|
| [`src/fleet_rlm/api/app.py`](src/fleet_rlm/api/app.py) | `health` (`response_model=Health`, `-> dict`) |
| [`src/fleet_rlm/api/routes/sessions.py`](src/fleet_rlm/api/routes/sessions.py) | All 18 handlers — `-> dict` throughout; `response_model=` on each |
| [`src/fleet_rlm/api/routes/setup.py`](src/fleet_rlm/api/routes/setup.py) | All non-SSE, non-`None` handlers |
| [`src/fleet_rlm/api/routes/workspace.py`](src/fleet_rlm/api/routes/workspace.py) | Non-streaming handlers |

**Do not change:**
- [`src/fleet_rlm/api/routes/turns.py`](src/fleet_rlm/api/routes/turns.py) — already correctly typed
- SSE handlers that return `EventSourceResponse` — the `response_class=` decorator
  kwarg is separate from `response_model=` and must be kept
- Delete handlers genuinely returning `None` — keep `-> None` or `status_code=204`

### Relevant Context

- Official FastAPI skill: _"Prefer return types over `response_model=`"_
- [`src/fleet_rlm/api/routes/sessions.py`](src/fleet_rlm/api/routes/sessions.py)
- [`src/fleet_rlm/api/routes/setup.py`](src/fleet_rlm/api/routes/setup.py)
- [`src/fleet_rlm/api/routes/workspace.py`](src/fleet_rlm/api/routes/workspace.py)
- [`src/fleet_rlm/api/app.py`](src/fleet_rlm/api/app.py)
- [`src/fleet_rlm/api/schemas.py`](src/fleet_rlm/api/schemas.py) — the model types to use as return annotations

---

## Sub-task 3 — Split the stacked `@router.get` decorators in `workspace.py`

**Status:** [x] done

### Intent

[`src/fleet_rlm/api/routes/workspace.py` lines 25–26](src/fleet_rlm/api/routes/workspace.py)
registers two routes on a single function using stacked decorators:

```python
@router.get("/{rel_path:path}")
@router.get("/")
async def list_files(rel_path: str = "", ...) -> ...:
    ...
```

The official FastAPI skill states: **one HTTP operation per function**. Stacking
two `@router.get` decorators on one function is unsupported: FastAPI will
register both paths but the OpenAPI schema may only reflect one, and future
FastAPI versions may warn or reject this pattern.

The fix is to extract one of the two routes into a thin wrapper that delegates
to a shared implementation function.

### Expected Outcomes

- Two separate route handler functions exist: one for `GET /workspace/` and one
  for `GET /workspace/{rel_path:path}`
- The shared logic is extracted into a private function (e.g. `_list_files(...)`)
  called by both handlers
- `GET /workspace/` and `GET /workspace/{rel_path:path}` both appear correctly
  in `openapi.json`
- Existing tests pass unchanged

### Todo List

1. Open [`src/fleet_rlm/api/routes/workspace.py`](src/fleet_rlm/api/routes/workspace.py).
2. Extract the current `list_files` body into a private helper, e.g.:
   ```python
   async def _list_files(rel_path: str, runtime: Runtime, settings: Settings) -> Files:
       ...  # existing implementation
   ```
3. Replace the stacked handler with two single-decorator functions:
   ```python
   @router.get("/")
   async def list_files_root(runtime: Runtime, settings: Settings) -> Files:
       return await _list_files("", runtime, settings)

   @router.get("/{rel_path:path}")
   async def list_files_path(rel_path: str, runtime: Runtime, settings: Settings) -> Files:
       return await _list_files(rel_path, runtime, settings)
   ```
4. Run `uv run pytest tests/api/ -v` and confirm both routes are exercised.
5. Verify both paths appear in `openapi.json`:
   ```bash
   uv run python -c "
   import json
   from fleet_rlm.api.app import create_app
   app = create_app(initialize_db=False, configure_observability=False)
   paths = list(app.openapi()['paths'].keys())
   print([p for p in paths if 'workspace' in p])
   "
   ```

### Relevant Context

- [`src/fleet_rlm/api/routes/workspace.py` lines 25–26](src/fleet_rlm/api/routes/workspace.py)
- Official FastAPI skill: _"One HTTP operation per function"_

---

## Sub-task 4 — Wrap bare query parameters in `Annotated[..., Query(...)]`

**Status:** [x] done

### Intent

Two route handlers in [`src/fleet_rlm/api/routes/sessions.py`](src/fleet_rlm/api/routes/sessions.py)
declare query parameters as plain defaults without a `Query()` constructor:

```python
# Line 47
async def create_session(scope: str = "system.ai", ...):

# Line 192
async def search_memories(q: str = "", ...):
```

The official FastAPI skill (and all other query parameters in the codebase,
including [`src/fleet_rlm/api/routes/setup.py` line 254](src/fleet_rlm/api/routes/setup.py))
use the `Annotated[T, Query(...)]` form for all explicit query parameters:

```python
async def create_session(
    scope: Annotated[str, Query(description="Runtime scope")] = "system.ai",
    ...
):
```

Benefits:
- `description=` is surfaced in `openapi.json` and the auto-generated docs UI
- Consistent style with every other query parameter in the project
- `Query(min_length=...)` or `Query(pattern=...)` constraints can be added without
  refactoring the declaration later
- Matches the FastAPI skill's preferred pattern

### Expected Outcomes

- `scope` in `create_session` is declared as
  `Annotated[str, Query(description="...")]`
- `q` in `search_memories` is declared as
  `Annotated[str, Query(description="...")]`
- Both parameters still accept their defaults (`"system.ai"` and `""`)
- The OpenAPI schema for those two routes gains a `description` field for the
  query parameters
- Tests pass unchanged

### Todo List

1. Open [`src/fleet_rlm/api/routes/sessions.py`](src/fleet_rlm/api/routes/sessions.py).
2. Add `Query` to the `from fastapi import ...` import if not already present.
3. On line 47, change:
   ```python
   scope: str = "system.ai",
   ```
   to:
   ```python
   scope: Annotated[str, Query(description="Runtime scope tag for the session")] = "system.ai",
   ```
4. On line 192, change:
   ```python
   q: str = "",
   ```
   to:
   ```python
   q: Annotated[str, Query(description="Search query string")] = "",
   ```
5. Run `uv run pytest tests/api/ -v`.
6. Spot-check the schema:
   ```bash
   uv run python -c "
   import json
   from fleet_rlm.api.app import create_app
   app = create_app(initialize_db=False, configure_observability=False)
   schema = app.openapi()
   # Check create_session path for scope description
   for path, item in schema['paths'].items():
       for method, op in item.items():
           for p in op.get('parameters', []):
               if p['name'] in ('scope', 'q'):
                   print(path, p)
   "
   ```

### Relevant Context

- [`src/fleet_rlm/api/routes/sessions.py` line 47](src/fleet_rlm/api/routes/sessions.py)
- [`src/fleet_rlm/api/routes/sessions.py` line 192](src/fleet_rlm/api/routes/sessions.py)
- [`src/fleet_rlm/api/routes/setup.py` line 254](src/fleet_rlm/api/routes/setup.py) — example of existing correct `Annotated[int, Query(ge=0)]` usage
- Official FastAPI skill: _"Use `Annotated` for all parameter declarations"_

---

## Sub-task 5 — Convert `async def apply` to `def apply`

**Status:** [x] done

### Intent

[`src/fleet_rlm/api/routes/setup.py` line 268](src/fleet_rlm/api/routes/setup.py)
declares a route handler as `async def` but contains **no `await` expressions**
in its body:

```python
@router.post("/setup/apply", ...)
async def apply(request: Request, service: Operator) -> ...:
    # body has no await calls
    ...
```

The FastAPI documentation and official skill state: _"use `def` unless the logic
is genuinely async-compatible"_. A function declared `async def` with no actual
I/O awaits:
- Blocks Uvicorn's event loop while executing (it is not yielded to the
  executor the way a `def` function is via `run_in_executor`)
- Misleads readers and type checkers into thinking I/O suspension points exist

Fix: change to `def apply(...)` so FastAPI routes the call correctly through its
thread-pool fallback.

**Precondition:** Verify the body truly contains no `await` — do not make this
change if the file has been modified since the audit.

### Expected Outcomes

- `apply` is declared as `def apply(...)` (synchronous)
- FastAPI correctly routes it through the thread executor
- Tests pass unchanged
- `ty check` and Ruff pass

### Todo List

1. Open [`src/fleet_rlm/api/routes/setup.py`](src/fleet_rlm/api/routes/setup.py)
   line 268.
2. Confirm there are no `await` expressions in the function body:
   ```bash
   grep -n "await" src/fleet_rlm/api/routes/setup.py
   ```
   If any `await` appears inside `apply`, do not make this change and mark the
   sub-task blocked.
3. If confirmed, change `async def apply(` to `def apply(`.
4. Run:
   ```bash
   uv run pytest tests/api/ -v
   make check
   ```

### Relevant Context

- [`src/fleet_rlm/api/routes/setup.py` line 268](src/fleet_rlm/api/routes/setup.py)
- Official FastAPI skill: _"Use `def` unless the logic is genuinely async"_
- [`src/fleet_rlm/api/routes/turns.py`](src/fleet_rlm/api/routes/turns.py) — example of correctly declared `async def` (uses `await` throughout)

---

## Implementation Instructions (for Agent Mode)

Process sub-tasks in the order listed. Before starting each sub-task, re-read
this file for context. After completing each sub-task, update its status to
`[x] done` here.

Sub-tasks 3, 4, and 5 are independent of each other and can be done in any
order. Sub-task 2 (return-type refactor) should follow Sub-task 1 (upgrade) so
the type checker runs against the final installed version.

Use `start_subtask` for each sub-task. Confirm the status with the user before
proceeding to the next.

---

## Reference — Release Notes Analysis (0.152.0 – 0.162.0)

Retained from original analysis. All 11 versions between the current pin
(exclusive) and the target (inclusive) were reviewed. The table below
summarises every breaking change and notable fix.

| Version | Category | Summary |
|---|---|---|
| **0.152.0** | Fix / behaviour | Header parameter model validation errors now report the HTTP header name (`int-val`) instead of the Python field name (`int_val`). |
| **0.153.0** | Fix / behaviour | Unions of form models now apply browser-form semantics: empty scalars use declared defaults; omitted booleans become `False`; unselected files are missing. JSON bodies unchanged. |
| **0.154.0** | Feature | Pydantic validation options (`strict`, `context`) can be passed directly to request parameters. No breaking change. |
| **0.155.0** | **Breaking** | `**extra` kwargs removed from `Path`, `Query`, `Header`, `Cookie`, `Body`, `Form`, `File`. Unknown kwargs now raise `TypeError`. Replacement: `json_schema_extra={"custom": "value"}`. |
| **0.156.0–0.156.8** | Fixes | Regression fixes for the 0.155.0 removal. No additional API changes. |
| **0.157.0** | Feature | Feature addition only. No breaking change. |
| **0.158.0–0.158.2** | Features + Fix | No breaking changes. |
| **0.159.0–0.159.1** | Features | No breaking changes. |
| **0.160.0** | **Breaking** | Query/header/cookie/form parameter models now apply `extra` config to sibling fields in the same request location. |
| **0.161.0** | Feature | No breaking change. |
| **0.161.1** | Fix | Regression fix only. |
| **0.162.0** | **Breaking** | Omitted boolean form fields no longer infer `False`. They use the declared default or raise a missing-field error. |

### Breaking Change Impact Table

| # | Breaking Change | Introduced | Affects fleet-rlm? | Reason |
|---|---|---|---|---|
| BC-1 | `**extra` kwargs removed from parameter constructors | 0.155.0 | **No** | No undocumented kwargs anywhere — `Query(ge=0)` uses only documented args; `File()` takes no args |
| BC-2 | Parameter model `extra` applied to sibling fields | 0.160.0 | **No** | All `extra="forbid"` models (`_Request`, `SetupRequest` subclasses) are JSON body models — BC-2 is scoped to query/header/cookie parameter models |
| BC-3 | Omitted boolean form fields no longer default to `False` | 0.162.0 | **No** | No boolean form parameters exist; `MemoryRequest.reviewed: bool` and `TurnRequest.record_replay: bool` are JSON body fields |

**No code changes are required in `src/` or `tests/` to migrate from 0.151.0
to 0.162.0.** The migration is a dependency pin bump only.

---

## Reference — File-by-File Codebase Impact Analysis

Full line-by-line review of every file in `src/fleet_rlm/api/`. Each file was
read in its entirety and cross-referenced against all three breaking changes.

| File | Patterns Examined | BC-1 | BC-2 | BC-3 | Upgrade action |
|---|---|---|---|---|---|
| `__init__.py` | Docstring only | ✅ N/A | ✅ N/A | ✅ N/A | None |
| `routes/__init__.py` | Docstring only | ✅ N/A | ✅ N/A | ✅ N/A | None |
| `app.py` | `FastAPI(...)`, `@app.exception_handler`, `@app.get("/health")` | ✅ No extra kwargs | ✅ No param models | ✅ No form fields | None |
| `deps.py` | `Depends(...)`, `Request`, `HTTPException`, `Annotated` aliases | ✅ No constructors | ✅ No param models | ✅ No form fields | None |
| `schemas.py` | `_Request(extra="forbid")`, 6 subclasses, 15+ response models, `bool` fields | ✅ No constructors | ✅ Body models only | ✅ JSON body `bool`, not form | None |
| `routes/sessions.py` | 18 routes; `scope: str`, `q: str` bare query params | ✅ No constructors | ✅ No param models | ✅ No form fields | None |
| `routes/turns.py` | SSE route; `TurnRequest` JSON body; `hold_turn_lock` async dep | ✅ No constructors | ✅ Body model only | ✅ No form fields | None |
| `routes/setup.py` | `Query(ge=0)`, `HTTPBearer(auto_error=False)`, `SetupRequest(extra="forbid")` hierarchy | ✅ `ge` is documented | ✅ Body models only | ✅ No form fields | None |
| `routes/workspace.py` | `File()` (no args), `UploadFile`, `FileResponse`, `BackgroundTask` | ✅ `File()` no kwargs | ✅ No param models | ✅ No boolean form fields | None |
| `events.py` | No FastAPI imports; DSPy callback; `asyncio.Queue` | ✅ N/A | ✅ N/A | ✅ N/A | None |

### Transitive Dependency Assessment

The three breaking changes are isolated to FastAPI's request-parameter handling
layer. No transitive impacts were found across shared abstractions.

No transitive dependency conflicts are expected from the version bump:
- `starlette>=1.3.1` floor override in `[tool.uv].override-dependencies`
  covers the range; no Starlette bumps noted in the reviewed release notes.
- `pydantic>=2.13.5,<3` is compatible with all FastAPI versions in this range.
- `uvicorn[standard]>=0.54.0,<1` is unaffected.
