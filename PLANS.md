# Fleet RLM Skills Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `src/fleet_rlm/skills/` with a small, fixed, DSPy-native bundled Skill system that supports discovery, exact selection, progressive instruction/resource loading, and at most one optional typed DSPy Signature per Turn—without a general plugin or capability framework.

**Architecture:** Fleet ships a fixed catalog of trusted bundled Skills. A Skill may contribute concise instructions, explicitly listed UTF-8 resources, and optionally one DSPy Signature that preserves Fleet's standard inputs and required `answer: str` output. Runtime tools remain host-owned and are never registered by Skill Markdown. The existing native `dspy.RLM` remains the execution Module; this plan adds no parallel agent loop and no custom Skill Module abstraction.

**Tech Stack:** Python 3.11-3.13, DSPy 3.3.0b1, FastAPI 0.139.0, Pydantic v2, pytest.

## Global Constraints

- Scope this plan to `src/fleet_rlm/skills/` and the minimal integration points required to consume it.
- Keep `dspy==3.3.0b1` pinned.
- Keep native `dspy.RLM` as the only recursive execution Module.
- Use DSPy Signatures only for stable typed task output shapes.
- Do not introduce a custom Skill execution Module, router model, plugin loader, or capability registry.
- Bundled Skills are trusted package resources, not user-uploaded extensions.
- Support only system-visible bundled Skills in this plan.
- Support only UTF-8 `SKILL.md` and explicitly declared UTF-8 resources.
- Do not support binary Skill assets, executable Skill scripts, workspace-scoped Skills, hidden Skills, trust tiers, dynamic registration, arbitrary capability references, input adapters, output validators, knowledge registries, or RLM requirement negotiation.
- Keep exactly two host-mediated Skill tools: `load_skill` and `read_skill_resource`.
- Ship exactly four bundled Skills in the PyPI 0.7.0 catalog.
- Core tools such as workspace, Attachment, Artifact, and Session History tools remain bound by the runtime profile; Skill files cannot register executable host behavior.
- Explicit Skill selections remain version-pinned and limited to four unique Skills per Turn.
- At most one selected Skill may provide a custom Signature.
- Every custom Signature must preserve Fleet's standard inputs and define a required `answer: str` output.
- Every task must leave the backend runnable and independently testable.

---

## Why this change

The current Skills implementation supports a broad future extension platform:

- registry mutation;
- multiple scopes, trust states, and visibility states;
- generic authorization;
- recursive directory discovery;
- YAML metadata parsing;
- binary assets and MIME inference;
- capability references;
- task-contract references;
- tools, event views, input adapters, validators, knowledge, and RLM minimum requirements;
- dynamic composition into a large Turn blueprint.

Fleet currently ships only a very small bundled catalog. The runtime does not need a general plugin platform yet. The simplified design keeps the product behavior that is useful now and removes machinery whose only purpose is hypothetical extensibility.

## Product behavior preserved

The refactor must preserve these behaviors:

1. The API can list bounded Skill Cards.
2. A Turn starts with all bundled Skill Cards as metadata.
3. The model can call `load_skill` to receive one authorized `SKILL.md`.
4. The model can call `read_skill_resource` after loading that Skill.
5. The API may explicitly select up to four exact Skill ID/version pairs.
6. Explicitly selected Skills are preloaded before RLM execution.
7. Skill instruction bodies and resource contents do not appear in public cards or tool-event projections.
8. Skill Markdown cannot create tools or bypass host authorization.

---

## Target repository tree

```text
src/fleet_rlm/
├── skills/
│   ├── __init__.py
│   ├── errors.py
│   ├── models.py
│   ├── catalog.py
│   ├── resolver.py
│   ├── tools.py
│   ├── signatures.py
│   └── bundled/
│       ├── README.md
│       ├── long-context/
│       │   ├── SKILL.md
│       │   ├── scripts/
│       │   │   ├── semantic_chunk.py
│       │   │   └── rank_chunks.py
│       │   └── references/
│       │       └── chunking-strategies.md
│       └── workspace-files/
│       │   ├── SKILL.md
│       │   └── references/
│       │       └── filesystem-contract.md
│       ├── data-analysis/
│       │   └── SKILL.md
│       └── report-builder/
│           └── SKILL.md
│
├── app.py
├── api/
│   ├── dependencies.py
│   └── routes/skills.py
├── chat/
│   └── turn_preparation.py
├── daytona/
│   └── run_environment.py
└── rlm/
    ├── factory.py
    ├── inputs.py
    └── signature.py

tests/
├── unit/backend/
│   ├── test_skill_catalog.py
│   ├── test_skill_resolver.py
│   ├── test_skill_tools.py
│   └── test_skill_signatures.py
└── contracts/backend/
    ├── test_skill_turn_contract.py
    └── test_skills_api.py
```

## Files removed after migration

```text
src/fleet_rlm/skills/authorize.py
src/fleet_rlm/skills/cards.py
src/fleet_rlm/skills/capabilities.py
src/fleet_rlm/skills/loader.py
src/fleet_rlm/skills/paths.py
src/fleet_rlm/skills/ranking.py
src/fleet_rlm/skills/registry.py
src/fleet_rlm/skills/skills/
```

These files are removed atomically in Phase 4 after their callers migrate and
the replacement contract tests pass.

---

## Target dependency flow

```text
FastAPI app
  -> build_bundled_skill_catalog()
      -> immutable SkillCatalog

Turn preparation
  -> catalog.cards()
  -> resolve_selected_skills(catalog, selections)
  -> SkillToolHost(catalog)
  -> ResolvedSkills(instructions, signature)

RLM factory
  -> native dspy.RLM(
         signature=resolved.signature or FleetRLMSignature,
         tools=runtime_tools + skill_tools,
     )
```

There is no `CapabilityRegistry`, `CapabilityResolver`, `TaskContract`, or Skill-defined tool registration in the target design.

---

## Target public interfaces

### Minimal models

```python
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping
from uuid import UUID

import dspy


@dataclass(frozen=True, slots=True)
class SkillSelectionRef:
    id: UUID
    expected_version: str


@dataclass(frozen=True, slots=True)
class SkillCard:
    id: UUID
    name: str
    description: str
    version: str
    resources_available: bool


@dataclass(frozen=True, slots=True)
class SkillResource:
    path: str
    media_type: str
    content: str


@dataclass(frozen=True, slots=True)
class SkillDefinition:
    card: SkillCard
    instructions: str
    resources: Mapping[str, SkillResource] = MappingProxyType({})
    signature: type[dspy.Signature] | None = None
```

### Immutable catalog

```python
class SkillCatalog:
    def cards(self) -> tuple[SkillCard, ...]: ...
    def get(self, skill_id: UUID) -> SkillDefinition | None: ...
    def require(self, skill_id: UUID) -> SkillDefinition: ...
```

### Selection result

```python
@dataclass(frozen=True, slots=True)
class ResolvedSkills:
    cards: tuple[SkillCard, ...]
    selected: tuple[SkillDefinition, ...]
    instructions: tuple[str, ...]
    signature: type[dspy.Signature] | None
```

### Resolver

```python
def resolve_selected_skills(
    catalog: SkillCatalog,
    selections: tuple[SkillSelectionRef, ...],
    *,
    max_selections: int = 4,
) -> ResolvedSkills: ...
```

Resolver rules:

- reject more than four selections;
- reject duplicate IDs;
- reject unknown IDs;
- reject version mismatches;
- reject selection sets containing more than one custom Signature;
- return all cards for discovery and selected definitions for preload;
- perform no model call and no async work.

### Skill tools

```python
class SkillToolHost:
    def __init__(self, catalog: SkillCatalog, *, max_loaded_skills: int = 4) -> None: ...
    def mark_preloaded(self, skill: SkillDefinition) -> None: ...
    def load_skill(self, skill_id: str, expected_version: str | None = None) -> dict: ...
    def read_skill_resource(
        self,
        skill_id: str,
        resource_path: str,
        expected_version: str | None = None,
    ) -> dict: ...
    def as_tools(self) -> tuple[dspy.Tool, dspy.Tool]: ...
```

### DSPy Signature rule

```python
class DataAnalysisSignature(dspy.Signature):
    """Analyze supplied data with deterministic verification and report concise findings."""

    request: str = dspy.InputField()
    session_context: dict = dspy.InputField()
    skill_cards: list[dict] = dspy.InputField()
    attachments: list[dict] = dspy.InputField()

    answer: str = dspy.OutputField(desc="User-facing analysis")
    findings: list[str] = dspy.OutputField(desc="Verified key findings")
    metrics: list[dict] = dspy.OutputField(desc="Named computed metrics")
    anomalies: list[str] = dspy.OutputField(desc="Qualified anomalies or an empty list")
```

`answer` remains the canonical public text field. The other fields are typed Prediction data; they do not require a new Skill serializer framework.

---

# Phase 0: Baseline and repair the characterization plan

### Task 0: Reconcile the plan with the live Skills seams

**Evidence:**
- Branch/tip: `dev-0.7` at `d09366ebb2ec322f767ce688535da094ed5cb0df`
- Pre-existing working tree: modified `.gitignore`; untracked local `PLANS.md`
- Bundled catalog: `long-context` and `workspace-files`
- Public seams: Skills HTTP routes, Turn request/preparation, host Skill tools, and Runtime Event/SSE projection
- Focused baseline: 28 passing tests across Skills cards/tools, Turn selections, and Deno preparation

- [x] **Step 1: Inspect the current branch, dirty tree, bundled catalog, API routes, and Deno/Daytona preparation**
- [x] **Step 2: Run the focused existing Skills suite**
- [x] **Step 3: Replace future-only fixtures and paths with current public seams**
- [x] **Step 4: Keep target-only behavior in its later TDD phase; Phase 1 has no xfails**
- [x] **Step 5: Preserve the user-owned `.gitignore` and local `PLANS.md` state**

**Phase 0 exit gate:** The plan names only existing public seams, separates current behavior from future design, and requires no expected failures.

---

# Phase 1: Characterize the behavior to preserve

### Task 1: Add focused Skills contract tests

**Files:**
- Create: `tests/contracts/backend/test_skill_turn_contract.py`
- Create: `tests/contracts/backend/test_skills_api.py`
- Modify: no production files

**Interfaces:**
- Consumes: current Skills API and Turn preparation behavior
- Produces: behavioral tests that remain valid after simplification

- [x] **Step 1: Characterize the bundled Skills HTTP contract**

Exercise `create_app()` through `TestClient`: list/get return the two bounded bundled cards, ranking only reorders results, missing IDs return a generic 404, and no instruction, resource, or executable body appears.

- [x] **Step 2: Characterize the exact Turn-selection contract**

Exercise `CreateTurnRequest` and the Turn route: accept zero to four unique version-pinned selections, reject duplicates/overflow, and translate unknown IDs or version mismatches to the existing generic pre-stream response without reflecting supplied version text.

- [x] **Step 3: Characterize Deno and Daytona preparation and progressive loading**

Use in-memory Deno preparation and injected Daytona fakes only. Both profiles expose the two host-owned Skill tools; explicit selections preload and restrict later loading; resources require a prior load; public tool and Skill lifecycle projections remain metadata-only.

- [x] **Step 4: Run the characterization tests against the unchanged production implementation**

Run:

```bash
uv run pytest \
  tests/contracts/backend/test_skill_turn_contract.py \
  tests/contracts/backend/test_skills_api.py -v
```

Receipt: 7 passed with no xfail or skip; the complete `tests/contracts/backend` lane also passed. Target-only Signature behavior begins in its later TDD phase.

- [x] **Step 5: Preserve the requested uncommitted scope**

No files were staged or committed. The pre-existing `.gitignore` modification and local `PLANS.md` remain user-owned.

**Phase 1 exit gate:** Current user-visible behavior is characterized by passing tests without changing production code or generated contracts.

---

# Phase 2–4: Immutable Skills cutover for PyPI 0.7.0

**Compatibility boundary:** `v0.6.2` is the latest released package and has no
current Skills package. The unreleased `0.7.0` Python interfaces are cut over
atomically without a compatibility shim. Existing deterministic selections remain
valid: `long-context@2.0.0` and `workspace-files@1.0.0` retain their IDs
and versions. `data-analysis` and `report-builder` remain Phase 5 work.

### Phase 2: Minimal immutable domain and catalog

**Files:**
- Edit: `src/fleet_rlm/skills/models.py`
- Add: `src/fleet_rlm/skills/catalog.py`
- Add: `src/fleet_rlm/skills/signatures.py`
- Move: `src/fleet_rlm/skills/skills/` to `src/fleet_rlm/skills/bundled/`
- Edit: `pyproject.toml`, `scripts/validate_release.py`

- [x] Replace broad records with frozen `SkillCard`, text-only
  `SkillResource`, `SkillDefinition`, and `ResolvedSkills` while
  retaining validated `SkillSelectionRef`.
- [x] Validate safe names and versions, nonblank instructions, canonical
  POSIX-relative paths, text content, and immutable resource mappings.
- [x] Build one deterministic immutable catalog from a static two-Skill manifest
  and `importlib.resources`; read full UTF-8 `SKILL.md` bodies without
  runtime frontmatter parsing, scanning, MIME inference, or binary decoding.
- [x] Preserve stable IDs and versions for exactly `long-context@2.0.0`
  (three explicit resources) and `workspace-files@1.0.0` (one resource).
- [x] Remove the PDF marker and binary package-data patterns.
- [x] Add `validate_skill_signature()` and the future
  `DataAnalysisSignature` contract without installing it in the catalog.

**Phase 2 exit gate:** Fleet has an eagerly validated immutable two-Skill catalog;
malformed trusted package content prevents application creation.

### Phase 3: Pure selection and neutral execution

**Files:**
- Add: `src/fleet_rlm/skills/resolver.py`
- Edit: `src/fleet_rlm/rlm/context.py`
- Edit: `src/fleet_rlm/rlm/dspy_contract.py`

- [x] Resolve zero to four exact selections synchronously, preserving input
  order and rejecting duplicates, overflow, unknown IDs, version mismatches,
  and more than one Signature-providing Skill with one generic error.
- [x] Replace capability blueprints with typed `RLMExecutionSpec` containing
  only cards, Signature, schema identity, host tools/event views, and Workspace
  metadata.
- [x] Use `FleetRLMSignature` with `fleet.default@1` by default; apply
  selected instruction bodies with `Signature.with_instructions(...)`.
- [x] Validate Prediction outputs directly from the selected Signature and
  explicit schema metadata. Every accepted Signature has the four standard
  inputs and required `answer: str` output.

**Phase 3 exit gate:** Selection is pure and deterministic, and the optional
validated Signature is the only Skill-specific DSPy execution extension.

### Phase 4: Atomic application and runtime cutover

**Files:**
- Edit: `src/fleet_rlm/skills/tools.py`, `skills/__init__.py`
- Edit: `src/fleet_rlm/app.py`, `api/routes/skills.py`,
  `api/schemas.py`
- Edit: Deno, Daytona, testing composition, Runner, inputs, and affected tests
- Delete: `authorize.py`, `cards.py`, `capabilities.py`,
  `loader.py`, `paths.py`, `registry.py`, and `ranking.py`

- [x] Bind `SkillToolHost` directly to the catalog while retaining exactly
  `load_skill` and `read_skill_resource`, preload state, four-load bound,
  load-before-resource enforcement, and metadata-only event views.
- [x] Install one fail-fast immutable catalog in `create_app()` and expose it
  through an annotated FastAPI dependency. Preserve the eight-field HTTP card,
  ranking behavior, generic 404, and generic invalid-selection envelope.
- [x] Compose runtime-specific core tools plus the two Skill tools directly into
  `RLMExecutionSpec` for Deno and injected Daytona resources without changing
  provider SDK calls or lifecycle ownership.
- [x] Make Runner pass the selected Signature to native `dspy.RLM`, always
  build standard Fleet inputs, and validate using the selected schema identity.
- [x] Replace composition registry state with one `skill_catalog` and retain
  explicit unavailable-catalog degradation only for private tests.
- [x] Remove the obsolete registry/capability framework after migrating all
  production callers; do not add adapters, task contracts, knowledge injection,
  capability-defined tools, or requirement negotiation.
- [x] Complete the focused, backend, Deno, generated-contract, docs, security,
  release-package, and full `make check` receipts below.

Validation receipt: 81 focused tests passed; the complete unit/backend contract
lane passed; `make test-deno`, `make api-sync`, `make api-check`,
`make check-docs`, `make check-security`, `make build-release`,
`make check-release`, `make check`, and `git diff --check` passed. Live Daytona
tests were collection-checked only; no provider, credential, Sandbox, Volume, or
network operation ran.

No intermediate commit is part of this cutover. The checkout remains uncommitted
unless a separate request authorizes Git publication.

**Phase 4 exit gate:** The complete backend runs through one immutable two-Skill
catalog, one pure resolver, one optional Signature seam, native `dspy.RLM`,
and no registry/capability framework.

# Phase 5: Add only the two justified new Skills

The final PyPI 0.7.0 bundled catalog contains exactly four trusted system
Skills: `data-analysis@1.0.0`, `long-context@2.0.0`, `report-builder@1.0.0`,
and `workspace-files@1.0.0`. Existing IDs and versions remain unchanged.
`data-analysis` is the only bundled custom Signature; `report-builder` is
instruction-only. Neither Skill contributes executable tools.

### Task 8: Add `data-analysis`

**Files:**
- Modify: `src/fleet_rlm/skills/catalog.py` (static manifest and stable IDs)
- Create: `src/fleet_rlm/skills/bundled/data-analysis/SKILL.md`
- Modify: `tests/unit/backend/test_skill_catalog.py`
- Modify: `tests/contracts/backend/test_skills_api.py`
- Modify: `tests/contracts/backend/test_skill_turn_contract.py`
- Modify: `scripts/validate_release.py` (required wheel files)

**Interfaces:**
- Consumes: Python REPL, `DataAnalysisSignature`
- Produces: verified descriptive analysis workflow

- [x] **Step 1: Write the Skill body**

```markdown
# Data analysis

Use Python for deterministic calculations. Inspect schema and missing values before computing results. Report the requested metrics, state the statistical convention used, and verify the final numbers from the original data.

For anomaly claims:

1. State the rule used.
2. Report the relevant comparison value.
3. Do not call a point anomalous when it does not cross the rule.
4. For very small samples, qualify the conclusion and prefer descriptive language.

Before submitting, verify all reported counts, extrema, sums, means, medians, and dispersion values with Python.
```

- [x] **Step 2: Add a contract test based on the observed CSV workflow**

The test must assert that a selected `data-analysis` Skill:

- resolves `DataAnalysisSignature`;
- preloads its instructions;
- preserves `answer` as the required public text output;
- does not add executable tools.

- [x] **Step 3: Run the contract test**

```bash
uv run pytest tests/contracts/backend/test_skill_turn_contract.py -v -k data_analysis
```

Expected: PASS. Catalog, API, package, and Turn assertions project the
four-Skill product contract while keeping bodies and resources private.

### Task 9: Add `report-builder`

**Files:**
- Modify: `src/fleet_rlm/skills/catalog.py` (static manifest)
- Create: `src/fleet_rlm/skills/bundled/report-builder/SKILL.md`
- Modify: `tests/unit/backend/test_skill_catalog.py`
- Modify: `tests/contracts/backend/test_skills_api.py`
- Modify: `tests/contracts/backend/test_skill_turn_contract.py`

**Interfaces:**
- Consumes: runtime-bound workspace and Artifact tools when available
- Produces: write-read-verify report workflow

- [x] **Step 1: Write the Skill body**

```markdown
# Report builder

Create the requested report from verified source data.

1. Build the complete report in memory.
2. Check that every required section and requested value is present.
3. Save it with the bound Session Workspace tool when a workspace path is requested.
4. Read the same path through the same Workspace tool.
5. Verify the read-back content, required headings, and requested values.
6. Create an Artifact only when the user asks for a downloadable public output.
7. Never treat Python-local files as Session Workspace files.
8. Submit only after verification succeeds.
```

- [x] **Step 2: Add a contract test**

The test must assert that `report-builder`:

- is instruction-only;
- does not define a custom Signature;
- does not register workspace or Artifact tools;
- can be selected together with `data-analysis` without a Signature conflict.

- [x] **Step 3: Run the contract test**

```bash
uv run pytest tests/contracts/backend/test_skill_turn_contract.py -v -k report_builder
```

Expected: PASS. The two new Skills can be selected together, with the single
Signature rule and exactly two host-owned Skill tools preserved.

**Phase 5 exit gate:** The static catalog contains exactly four focused Skills;
the catalog, API, Turn, Deno, package, and Signature tests pass. This remains
one coherent uncommitted cutover; no intermediate commit is required.

---

# Phase 6: Documentation follow-through after the Phase 5 catalog expansion

Phase 4 already removes the registry/capability framework, updates application
wiring, and establishes the immutable runtime contract. Phase 6 must not repeat
those changes.

### Task 10: Document the final four-Skill product catalog

**Files:**
- Modify only user and maintainer documentation that changes when Phase 5 adds
  `data-analysis` and `report-builder`.

- [x] Describe the fixed four-Skill catalog after Phase 5 lands.
- [x] Document that cards are bounded metadata, bodies and explicit UTF-8
  resources load progressively, selections are ID/version pinned, and at most
  one selected Skill may supply a validated Signature.
- [x] Keep executable tools host-owned and remove any newly stale two-Skill
  release wording.
- [x] Run `make check-docs`, `make api-check`, and `git diff --check`.

**Phase 6 exit gate:** Documentation matches the Phase 5 four-Skill catalog
without reopening the catalog, tool, application-wiring, or runtime architecture.

# Phase 7: Full validation and measurable acceptance

### Task 12: Run the release-quality validation matrix

**Files:**
- Modify only if a validation failure identifies a defect within this plan's scope

**Interfaces:**
- Consumes: final implementation
- Produces: exact-SHA verification evidence

- [x] **Step 1: Run the full offline suite**

```bash
make check
make test-deno
make api-sync
make api-check
make check-docs
make check-security
make build-release
make check-release
git diff --check
```

Expected: all commands PASS.

- [x] **Step 2: Run focused package import and wheel checks**

```bash
uv build
uv run python - <<'PY'
from fleet_rlm.skills.catalog import build_bundled_skill_catalog

catalog = build_bundled_skill_catalog()
print([(card.name, card.version) for card in catalog.cards()])
assert [(card.name, card.version) for card in catalog.cards()] == [
    ("data-analysis", "1.0.0"),
    ("long-context", "2.0.0"),
    ("report-builder", "1.0.0"),
    ("workspace-files", "1.0.0"),
]
PY
```

Expected output contains exactly:

```text
data-analysis
long-context
report-builder
workspace-files
```

- [x] **Step 3: Run the provider-free Deno preparation contract**

Verify:

1. all four cards appear in bounded Turn input metadata;
2. `load_skill` returns one selected `SKILL.md`;
3. `read_skill_resource` rejects an unloaded Skill resource;
4. a selected `data-analysis` preparation uses its typed output schema;
5. no Skill adds a host tool;
6. the deterministic testing composition preserves the same catalog-bound
   selection, preload, Signature, and two-tool behavior.

- [x] **Step 4: Run the injected Daytona preparation contract**

Use injected provider-free resources to verify the runtime-specific core tools,
Workspace metadata, catalog-bound Skill tools, and progressive preloading.
Select `report-builder` with `workspace-files` and verify Workspace write/read
operations remain runtime-owned while an unselected Skill remains unavailable.
Credentialed provider execution remains a separate opt-in live gate.

Expected: PASS.

- [x] **Step 5: Verify complexity reduction**

```bash
find src/fleet_rlm/skills -maxdepth 1 -name '*.py' -print | sort
rg -n 'os\.walk|yaml|base64|mimetypes|CapabilityRegistry|TaskContract|InputAdapter|OutputValidator' src/fleet_rlm/skills -g '*.py'
```

Expected:

- only `__init__.py`, `errors.py`, `models.py`, `catalog.py`, `resolver.py`, `tools.py`, and `signatures.py` exist at package root;
- the `rg` command returns no matches;
- exactly two Skill tools exist;
- exactly four bundled Skills exist;
- at most one custom Signature can resolve per Turn.

- [ ] **Step 6: Record the exact candidate SHA**

```bash
git rev-parse HEAD
```

Attach the SHA and validation command outputs to the review or release evidence.

**Phase 7 exit gate:** The simplified Skills system passes offline and provider-free Deno/Daytona preparation workflows. Live Daytona remains pending until an opt-in credentialed exact-tip receipt exists.

---

## Final acceptance criteria

The plan is complete only when all statements below are true:

- `src/fleet_rlm/skills/` contains seven small Python modules and one `bundled/` directory.
- The runtime catalog contains exactly four trusted system Skills.
- The catalog is immutable after startup.
- No runtime directory scanning or YAML frontmatter parsing occurs.
- No Skill binary assets or base64 resource responses exist.
- No Skill scope, trust, visibility, authorizer, mutable registry, capability registry, task-contract registry, adapters, validators, knowledge registry, or RLM requirement negotiation exists.
- Explicit selections remain unique, version-pinned, and limited to four.
- Progressive loading remains available through exactly two host tools.
- Skills cannot register host tools.
- Native `dspy.RLM` remains the execution Module.
- Custom task shape is expressed only through a validated DSPy Signature with Fleet's standard inputs and required `answer: str`.
- `data-analysis` is the only initial Skill with a custom Signature.
- `data-analysis` and `report-builder` may be selected together.
- FastAPI lists only bounded cards.
- Skill bodies and resource content never appear in cards or public tool-event projections.
- All focused tests, `make check`, `make test-deno`, `make api-check`, and `git diff --check` pass.

## Explicitly deferred

The following require a future product spec and are not extension points to preserve preemptively:

- user-created or uploaded Skills;
- workspace-scoped Skills;
- untrusted Skills;
- remote Skill registries;
- Skill marketplace support;
- hot reload;
- binary Skill assets;
- Skill-defined executable plugins;
- multiple custom Signatures in one Turn;
- model-based Skill routing;
- optimizer/GEPA compilation of Skill programs;
- persistent Skill analytics or ranking.
