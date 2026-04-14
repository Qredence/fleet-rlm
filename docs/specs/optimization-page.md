# Optimization Page Feature Spec

## Purpose

The Optimization page is the **quality lab** for the recursive DSPy worker stack.

It exists to help contributors and operators:

* inspect evaluation datasets
* inspect and compare metrics
* run evaluations
* run GEPA optimization jobs
* inspect artifacts and manifests
* compare runs
* decide whether a candidate artifact should be promoted

This page is **not** a live task execution surface. It is a **worker-quality iteration surface**.

---

## Product goals

The page should make it easy to answer:

1. **What module are we improving?**
2. **What dataset and metric are we using?**
3. **What runs have been executed recently?**
4. **Did optimization improve or regress the module?**
5. **Which artifact is the best current candidate?**
6. **What should we do next: rerun, compare, promote, or inspect failures?**

---

## Non-goals

This page should **not** be used for:

* live workbench/chat execution
* full volume browsing
* generic app settings
* deep transport/protocol debugging as the primary UX
* editing worker logic directly
* replacing CLI or code-based experimentation entirely

---

## Primary users

### 1. Contributor / engineer

Wants to:

* optimize a DSPy worker module
* inspect metrics
* compare runs
* debug regressions
* promote artifacts

### 2. Research / quality operator

Wants to:

* review dataset quality
* inspect failed examples
* understand score deltas
* verify whether a candidate is actually better

### 3. Technical product owner / maintainer

Wants to:

* see current quality status
* know which artifact is currently promoted
* understand recent optimization progress
* identify stale or failing modules

---

## Primary user journeys

### Journey A: Evaluate a module

1. Open Optimization page
2. Select module
3. Select dataset and metric
4. Run evaluation
5. Inspect results and score breakdown
6. Review failed examples

### Journey B: Run GEPA optimization

1. Open Optimization page
2. Select module
3. Select dataset and metric
4. Run optimization
5. Wait for run completion
6. Inspect validation score and artifact
7. Compare with promoted artifact

### Journey C: Promote a candidate artifact

1. Open Compare or Artifacts section
2. Inspect candidate vs promoted artifact
3. Review score deltas and caveats
4. Promote candidate artifact
5. Confirm promoted status updated

### Journey D: Audit data quality

1. Open Datasets section
2. Select dataset
3. Inspect schema, splits, counts, malformed rows
4. Validate normalization quality
5. Decide whether dataset is usable

---

## Page-level information architecture

### Top-level route

`/optimization`

### Subnavigation

The page should use a left-side subnavigation rail on desktop and a compact selector/tab pattern on smaller screens.

Subsections:

* **Overview**
* **Datasets**
* **Metrics**
* **Runs**
* **Artifacts**
* **Compare**
* **Guide**

Optional future additions:

* **Failures**
* **Promotions**
* **CI / Regression**

---

## Global page layout

### 1. Header / control bar

Always visible at the top of the page.

Contains:

* page title: **Optimization**
* selected module
* selected dataset
* selected metric
* selected artifact context if applicable
* primary actions:

  * **Run evaluation**
  * **Run optimization**
  * **Compare**
  * **Promote artifact**
* secondary actions:

  * refresh
  * export
  * open guide

### 2. Left subnavigation

Contains the subsections listed above.

### 3. Main content area

Changes by selected subsection.

### 4. Optional right-side inspector

Used for:

* run details
* artifact details
* dataset details
* comparison details

On narrow screens this should collapse into a drawer or modal.

---

## Shared page state model

The page should have a normalized frontend state model for optimization operations.

### Query context

* selected module
* selected dataset
* selected metric
* selected run
* selected artifact
* selected comparison target

### Operation state

* idle
* loading
* running_evaluation
* running_optimization
* completed
* failed

### Entity state

* no_data
* has_data
* stale
* invalid
* promoted
* candidate

### User-visible status vocabulary

Prefer human-readable states such as:

* **Ready**
* **Running evaluation**
* **Running optimization**
* **Completed**
* **Failed**
* **Promoted**
* **Candidate**
* **Needs attention**

---

# Subpage specifications

## 1. Overview

### Purpose

The landing page for optimization work.

### User questions answered

* What is the latest quality status?
* What is the current promoted artifact?
* What changed recently?
* Where should I go next?

### Required content

#### A. Selector row

* module selector
* dataset selector
* metric selector

#### B. Summary cards

Show at minimum:

* latest validation score
* best score
* current promoted artifact
* latest run status
* dataset size / split status

#### C. Current best candidate section

Contains:

* artifact name
* module
* score
* validation breakdown
* timestamp
* promote action
* inspect action

#### D. Recent runs section

Compact list/table with:

* run ID
* type
* module
* score
* status
* created time

#### E. Quality trend section

Simple trend view:

* score over time
* optionally split by module or dataset

#### F. Needs attention section

Examples:

* missing dataset split
* metric mismatch
* no promoted artifact
* recent regression
* failed optimization run

### Empty state

If there is no data:

* explain what the page is for
* prompt the user to select a module and run evaluation

---

## 2. Datasets

### Purpose

Inspect and validate the datasets used for optimization and evaluation.

### Required content

#### A. Dataset catalog

List datasets with:

* name
* target module(s)
* train/validation/test counts
* schema version
* status
* last updated

#### B. Dataset detail

For selected dataset show:

* description
* intended use
* schema fields
* split counts
* source path
* normalization status

#### C. Example preview

Show sample rows:

* raw row preview
* normalized example preview

#### D. Data quality checks

Surface:

* malformed rows
* missing fields
* duplicates
* class imbalance or skew if relevant
* too-small validation split

#### E. Actions

* validate dataset
* refresh preview
* export normalized form
* inspect malformed rows

### Success criteria

A contributor should be able to tell whether a dataset is trustworthy enough to use.

---

## 3. Metrics

### Purpose

Make evaluation criteria visible and understandable.

### Required content

#### A. Metric catalog

List metrics with:

* metric name
* target module
* score range
* brief description

#### B. Metric detail

Show:

* human-readable definition
* scoring dimensions
* weighting
* assumptions/caveats
* compatible modules

#### C. Example score breakdown

For one selected example, show:

* expected output
* actual output
* per-dimension scores
* final score

### Success criteria

A contributor should be able to understand *why* a score is high or low.

---

## 4. Runs

### Purpose

Browse and inspect evaluation and optimization runs.

### Required content

#### A. Run list

A filterable table with:

* run ID
* run type
* module
* dataset
* metric
* status
* validation score
* artifact path
* created at
* duration

Filters:

* module
* dataset
* metric
* run type
* status
* date range

#### B. Run detail

For selected run show:

* config summary
* dataset used
* metric used
* train/validation counts
* validation score
* breakdown
* warnings
* artifact path
* manifest path

#### C. Actions

* compare
* rerun
* inspect failures
* promote artifact if applicable
* export summary

### Success criteria

A contributor should be able to review a run without leaving the page.

---

## 5. Artifacts

### Purpose

Manage optimized artifacts and manifests.

### Required content

#### A. Artifact list

Show:

* artifact name
* target module
* validation score
* source run
* created at
* promoted status
* storage path

#### B. Artifact detail

Show:

* linked run
* linked manifest
* dataset
* metric
* score
* compatibility/version info
* provenance notes

#### C. Promotion controls

Show:

* current promoted artifact
* candidate artifact
* delta vs promoted
* promote / rollback actions

### Success criteria

A contributor should be able to decide whether an artifact is worth promoting.

---

## 6. Compare

### Purpose

Compare runs or artifacts directly.

### Required content

#### A. Comparison selector

Choose:

* run A vs run B
* artifact A vs artifact B
* baseline vs candidate
* promoted vs latest

#### B. Comparison summary cards

Show:

* overall score delta
* metric dimension deltas
* dataset mismatch warnings
* config mismatch warnings

#### C. Detailed comparison table

Rows should include:

* module
* dataset
* metric
* score
* key dimensions
* artifact/run metadata

#### D. Example-level comparison

Highlight:

* biggest improvements
* biggest regressions

### Success criteria

A contributor should be able to say whether a candidate is truly better than the baseline.

---

## 7. Guide

### Purpose

Make the page self-explanatory for contributors.

### Required content

* what the Optimization page is for
* optimization workflow
* evaluation workflow
* dataset requirements
* metric structure
* artifact promotion flow
* how to add support for a new DSPy module
* common pitfalls

### Success criteria

A new contributor should understand how to use the page without reading backend code first.

---

# Global interactions

## Primary actions

These actions should be accessible from the header/control bar:

* **Run evaluation**
* **Run optimization**
* **Compare**
* **Promote artifact**

## Secondary actions

* refresh
* export summary
* open guide
* inspect logs/details

## Dangerous or consequential actions

Must require explicit confirmation:

* promote artifact
* rollback promoted artifact
* delete artifact, if supported later

---

# Visual and UX guidance

## Tone

The page should feel:

* analytical
* trustworthy
* operational
* contributor-friendly
* not like a raw debug console

## Copy style

Prefer:

* “Run evaluation”
* “Run optimization”
* “Validation score”
* “Promoted artifact”
* “Failed examples”
* “Compare candidate”

Avoid overly internal wording unless in advanced mode.

## Layout style

* summary cards at top
* tables/lists in the middle
* details in a drawer or lower detail area
* clear spacing and typography hierarchy
* strong visual distinction between candidate, promoted, failed, and stale states

---

# Responsive behavior

## Desktop

* left rail visible
* header/control bar always visible
* main content in center
* optional right inspector drawer

## Narrow screens

* subnav becomes tabs or compact selector
* summary cards collapse to one column
* tables become stacked cards where necessary
* detail drawer becomes modal or lower section

---

# Backend dependencies

The page will likely depend on backend surfaces such as:

* list optimization runs
* inspect run details
* list artifacts
* inspect artifact manifests
* run evaluation
* run optimization
* compare runs/artifacts
* validate dataset
* fetch metric definition/breakdown

The exact API shape may be implemented incrementally, but the frontend spec should assume these concepts exist.

---

# Recommended reusable components

* `OptimizationHeader`
* `OptimizationSubnav`
* `ModuleSelector`
* `DatasetSelector`
* `MetricSelector`
* `SummaryCard`
* `RunTable`
* `ArtifactTable`
* `ComparisonView`
* `DatasetPreview`
* `MetricBreakdown`
* `ValidationIssuesPanel`
* `ArtifactPromotionDialog`
* `RunDetailDrawer`
* `EmptyOptimizationState`

---

# Acceptance criteria

A Phase 19 implementation of this page is successful if:

1. A contributor can choose a module, dataset, and metric quickly.
2. A contributor can run evaluation or optimization from the page.
3. A contributor can inspect results and compare them without leaving the page.
4. A contributor can identify the promoted artifact and candidate artifacts clearly.
5. A contributor can understand dataset and metric quality without reading source code.
6. The page structure is composable and maintainable.
7. The page has a built-in guide or linked guide that explains how it is used.

---

# Out of scope for initial version

* fully generalized experiment tracking platform
* raw trace explorer as a first-class section
* multi-user permissioning
* live collaborative review
* replacing CLI workflows entirely
* solving every future optimization workflow up front

---

# Recommended implementation order

1. Build the page shell and subnavigation
2. Implement Overview and Runs first
3. Add Artifacts and Compare
4. Add Datasets and Metrics
5. Add Guide
6. Polish responsiveness and visual hierarchy
7. Add tests for major interactions

---

# Suggested file to add

* `docs/specs/optimization-page.md`

This spec should remain the source of truth for frontend implementation and future refinements.
