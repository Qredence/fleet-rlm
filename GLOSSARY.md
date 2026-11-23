# Fleet-RLM

Fleet-RLM is a stateless, recursive agentic workspace where an agent solves tasks by executing Python over isolated session files, delegating bounded subtasks, and retaining curated long-term knowledge.

## Language

### Sessions & Workspaces

**Session**:
A durable, user-owned conversation thread that binds its episode history, runs, files, and optional model override together.
_Avoid_: Thread, conversation, chat room

**Workspace**:
The isolated, persistent file environment belonging to a single Session, divided into uploaded inputs and generated artifacts.
_Avoid_: Session workspace, project directory, scratchpad

**Input File**:
A user-uploaded file stored in a Session's Workspace for inspection and analysis without mutation.
_Avoid_: Attachment, upload, source doc

**Artifact**:
An output file created by the agent inside a Session's Workspace during a Turn and surfaced to the user for download.
_Avoid_: Output file, workspace artifact, deliverable

**File Manifest**:
The authoritative inventory of Input Files and Artifacts belonging to a Session, including content digests and sizes.
_Avoid_: File index, directory listing, catalog

**Sandbox**:
An isolated remote compute environment attached to a Session's Workspace where agent-authored Python executes safely.
_Avoid_: Container, runner, VM, worker

**Snapshot**:
A pre-built, immutable runtime image containing the Python packages and read-only Skills used to boot new Sandboxes.
_Avoid_: Base image, template, environment build

**Deployment Settings**:
Global runtime defaults and mutable workspace-wide overrides shared across all Sessions in a deployment, distinct from read-only infrastructure configuration.
_Avoid_: Workspace settings, global config, environment settings

### Execution & Recursion

**Turn**:
A single user prompt and its corresponding streamed assistant response within a Session, guarded so only one Turn executes per Session at a time.
_Avoid_: Invocation, request, message exchange

**Run**:
A single recorded execution node inside a Turn's recursive execution tree, classified as a Root Run, Subagent Run, or Sub-LM Query.
_Avoid_: Job, execution, task run

**Root Run**:
The top-level Run started for a Turn that owns the Session's single-writer lock and commits final history upon completion.
_Avoid_: Parent turn, main job, top run

**Subagent Run**:
A recursive child Run delegated by a parent Run into a fresh Execution Context within the same Workspace and recursion depth limit.
_Avoid_: Child agent, nested agent, helper worker

**Sub-LM Query**:
A direct single-step or batched language model call invoked from inside a Run to perform semantic map-reduce or structured extraction.
_Avoid_: Sub-call, prompt query, model call

**Execution Context**:
An ephemeral Python interpreter memory space created fresh for a single Run and discarded afterward so in-memory variables never leak across Runs.
_Avoid_: REPL session, kernel state, runtime cache

**Reasoning Trace**:
The live and persisted sequence of thoughts, Python executions, recursive Subagent Runs, and Artifacts produced during a Run.
_Avoid_: Trajectory log, debug stream, event feed

**Episode History**:
The bounded, replayable multi-turn conversation memory retained on a Session to keep subsequent Turns within token budgets.
_Avoid_: Chat log, transcript, context buffer

**Call Budget**:
The shared upper bound on total Sub-LM Queries and Subagent Runs permitted across an entire Root Run tree.
_Avoid_: Token limit, rate limit, quota

**Recursion Depth**:
The nesting level of a Run within a Turn's delegation tree, starting at zero for a Root Run and capped by a configured maximum.
_Avoid_: Nesting level, hop count, stack depth

### Skills & Memory

**Skill**:
A read-only, packaged bundle of domain instructions and optional supporting resources loaded progressively when relevant to a Turn.
_Avoid_: Plugin, prompt template, tool pack

**Skill Selection**:
The per-Turn decision step that scores available Skills against the user's task and activates only the most relevant ones.
_Avoid_: Skill routing, prompt matching, skill lookup

**Pinned Skill**:
A Skill explicitly requested by the user for a Turn, bypassing automatic Skill Selection.
_Avoid_: Forced skill, mentioned skill, locked skill

**Memory**:
A concise, durable fact distilled from a completed Turn that can be retrieved in future Turns once approved.
_Avoid_: Note, knowledge entry, fact snippet

**Memory Scope**:
The visibility boundary of a Memory, either restricted to its originating Session or shared globally across all Sessions.
_Avoid_: Visibility, namespace, tenant scope

**Memory Consolidation**:
The asynchronous two-stage post-Turn process that judges whether a Turn produced durable knowledge, distills it into a candidate Memory, and auto-approves high-confidence facts or queues lower-confidence ones for human review.
_Avoid_: Memory extraction, background indexing, auto-save

### Optimization

**Compiled Program**:
A versioned, read-only prompt and reasoning state produced offline by optimizing the workspace agent against labeled examples and verified on a Holdout Split.
_Avoid_: Optimization artifact, model checkpoint, tuned agent

**Calibrated Gate**:
A versioned, read-only decision threshold and label-weight configuration produced offline for Skill Selection or Memory Consolidation and tied to a pinned decision-model snapshot.
_Avoid_: Gate artifact, classifier weights, routing config

**Holdout Split**:
A disjoint evaluation subset withheld from training and validation to verify whether an offline optimization run improves real generalization before saving.
_Avoid_: Test file, eval set, benchmark split
