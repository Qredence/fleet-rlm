# Using Claude Skills with fleet-rlm

This guide explains how to use the **Claude Code custom skills** included with fleet-rlm to accelerate development, debugging, and testing workflows.

## Overview

Skills are **specialized capability packages** that give Claude Code domain-specific knowledge, workflows, and best practices. When you ask Claude to perform a task that matches a skill's domain, the skill is automatically invoked to provide expert guidance.

Think of skills as "expert modes" — each skill contains:

- **Structured workflows** for common tasks
- **Code examples** and patterns
- **Troubleshooting steps** for domain-specific issues
- **References** to supporting documentation and scripts

## Why Skills Matter for RLM Development

RLM development involves several specialized domains:

1. **Modal Sandbox Management** — Creating, configuring, and debugging cloud sandboxes
2. **DSPy Signature Design** — Defining input/output schemas for RLM tasks
3. **Long-Context Processing** — Techniques for handling documents exceeding token limits
4. **Distributed Execution** — Running parallel tasks across multiple sandboxes
5. **Memory Persistence** — Managing stateful data across RLM sessions

Each skill encapsulates best practices for these domains, ensuring consistent, correct implementations.

---

## Available Skills

The fleet-rlm project includes **10 specialized skills** in `.claude/skills/`:

| Skill                | Purpose                                                            | When to Use                                                            |
| :------------------- | :----------------------------------------------------------------- | :--------------------------------------------------------------------- |
| **rlm**              | Run RLM for long-context tasks using Modal sandboxes               | Processing large files, extracting information, running code in cloud  |
| **rlm-run**          | Execute RLM tasks with proper configuration                        | Running dspy.RLM, configuring ModalInterpreter, managing timeouts      |
| **rlm-debug**        | Debug RLM execution and troubleshoot failures                      | Modal sandbox issues, credential problems, task failures               |
| **rlm-execute**      | Execute Python code in Modal sandboxes with persistence            | Running code in cloud sandbox, stateful execution, persisting results  |
| **rlm-batch**        | Execute multiple tasks in parallel using Modal                     | Batch processing, parameter sweeps, distributed computation            |
| **rlm-memory**       | Long-term memory persistence using Modal Volumes                   | Storing/recalling data across sandbox sessions                         |
| **rlm-test-suite**   | Test and evaluate fleet-rlm workflows                              | Running tests, benchmarks, regression tests, validating connectivity   |
| **rlm-long-context** | (EXPERIMENTAL) Research implementation for long-context processing | Experimentation, evaluation, alternative patterns (use `rlm` for prod) |
| **dspy-signature**   | Generate and validate DSPy signatures for RLM tasks                | Creating input/output field definitions, choosing field names          |
| **modal-sandbox**    | Manage Modal sandboxes and volumes                                 | Creating, inspecting, cleaning up sandboxes and volumes                |

---

## How Skills Are Invoked

Skills are **automatically invoked** by Claude Code when your request matches the skill's `description` field. You don't need to explicitly call them.

### Example: Automatic Invocation

**You ask:** "How do I debug why my Modal sandbox is timing out?"

**Claude automatically:**

1. Reads `rlm-debug/SKILL.md` because your question matches "Debug RLM execution...troubleshoot failures"
2. Follows the troubleshooting workflow in that skill
3. Suggests specific diagnostic commands and checks

### Explicit Invocation (Optional)

You can also **explicitly mention** a skill to ensure it's used:

```
"Use the rlm-debug skill to diagnose my sandbox issue"
"Follow the rlm-batch skill to run these tasks in parallel"
```

---

## Usage Examples

### 1. Running RLM Tasks (`rlm` and `rlm-run`)

**Trigger:** "Process this large document using RLM" or "Run a long-context analysis"

**What Happens:**

- `rlm` skill provides the overall workflow (Navigate → Query → Synthesize)
- `rlm-run` skill shows how to configure `ModalInterpreter` and `dspy.RLM`

**Example Prompt:**

```
"Use RLM to extract all API endpoints from this 500 KB documentation file"
```

**Claude Response:**

- Reads `rlm/SKILL.md` and `rlm-run/SKILL.md`
- Suggests using the `fleet-rlm run-api-endpoints` CLI command
- Or provides programmatic code with proper CLI/Python usage

---

### 2. Debugging Failures (`rlm-debug`)

**Trigger:** "Why is my sandbox crashing?" or "Modal authentication error"

**What Happens:**

- `rlm-debug` skill is invoked automatically
- **Dynamic context injection** runs live diagnostics (Modal version, secret status, volumes)
- Provides targeted fixes for common issues

**Example Prompt:**

```
"My ModalInterpreter times out after 30 seconds. How do I fix this?"
```

**Claude Response:**

- Invokes `rlm-debug` skill
- Checks timeout configuration
- Suggests increasing `timeout` parameter or checking sandbox logs
- Provides diagnostic script: `uv run python .claude/skills/rlm-debug/scripts/diagnose.py`

---

### 3. Designing Task Signatures (`dspy-signature`)

**Trigger:** "Create a DSPy signature for extracting tables from PDFs"

**What Happens:**

- `dspy-signature` skill is invoked
- Provides field naming conventions, type hints, and examples

**Example Prompt:**

```
"I need a DSPy signature for summarizing long documents with key points extraction"
```

**Claude Response:**

- Reads `dspy-signature/SKILL.md` and `references/signature-examples.md`
- Suggests:
  ```python
  class SummarizeLongDocument(dspy.Signature):
      """Summarize a long document with controllable focus."""
      document: str = dspy.InputField(desc="Full document text to summarize")
      focus: str = dspy.InputField(desc="Optional focus area for summarization")
      summary: str = dspy.OutputField(desc="Concise summary of the document")
      key_points: list[str] = dspy.OutputField(desc="Bulleted key points")
  ```

---

### 4. Batch Processing (`rlm-batch`)

**Trigger:** "Run this analysis on 100 files in parallel"

**What Happens:**

- `rlm-batch` skill provides patterns for parallel execution
- Shows how to use Modal's `.map()` or multiple `ModalInterpreter` instances

**Example Prompt:**

```
"Process these 50 log files in parallel and extract error patterns from each"
```

**Claude Response:**

- Reads `rlm-batch/SKILL.md`
- Suggests using `llm_query_batched()` or Modal's `.map()` for parallelization
- Provides code example with proper resource management

---

### 5. Persistent Memory (`rlm-memory`)

**Trigger:** "Store analysis results across sessions" or "Save this to persistent storage"

**What Happens:**

- `rlm-memory` skill shows Modal Volume patterns
- Provides file organization under `/data/memory/`

**Example Prompt:**

```
"Save the extracted entity list so I can retrieve it in the next session"
```

**Claude Response:**

- Reads `rlm-memory/SKILL.md`
- Suggests using `save_to_volume()` and `load_from_volume()`
- Shows VFS taxonomy: `/data/memory/` for long-term state

---

### 6. Testing Workflows (`rlm-test-suite`)

**Trigger:** "Run the test suite" or "Validate my RLM implementation"

**What Happens:**

- `rlm-test-suite` skill provides test commands and evaluation metrics
- Lists all 11 test files and what they validate

**Example Prompt:**

```
"Run integration tests to verify my Modal sandbox setup is working"
```

**Claude Response:**

- Reads `rlm-test-suite/SKILL.md`
- Suggests:
  ```bash
  uv run pytest tests/test_rlm_integration.py -v
  uv run pytest tests/test_context_manager.py -v
  ```
- Provides validation steps for Modal credentials and volumes

---

## Advanced Features

### Dynamic Context Injection

The **rlm-debug** skill includes **live diagnostics** that automatically run when the skill is invoked:

```markdown
## Live Environment Status

Current Modal environment (auto-detected):

- Modal version: !`uv run python -c "import modal; print(modal.__version__)" 2>&1`
- Secret check: !`uv run fleet-rlm check-secret 2>&1 | head -5`
- Active sandboxes: !`uv run modal sandbox list 2>&1 | head -10`
```

The `!`command`` syntax runs shell commands and injects their output into the skill context. This gives Claude **real-time visibility** into your environment.

### Supporting Scripts

Some skills include **executable scripts** in `scripts/` folders:

- **rlm-debug/scripts/diagnose.py** — Comprehensive environment diagnostics
- **rlm-long-context/scripts/** — Experimental codebase processing tools

Run them directly:

```bash
uv run python .claude/skills/rlm-debug/scripts/diagnose.py
```

### Progressive Disclosure via References

Large skills use `references/` folders to keep the main `SKILL.md` concise (<500 lines):

- **dspy-signature/references/signature-examples.md** — Extended signature examples
- **rlm/references/api-reference.md** — Detailed API documentation
- **rlm-long-context/references/** — Advanced techniques and patterns

Claude automatically reads these when needed.

---

## Best Practices

### 1. Let Skills Guide Your Workflow

**Don't:** Ask generic questions like "How do I use DSPy?"

**Do:** Ask task-specific questions: "Extract tables from this PDF using RLM"

Skills work best when triggered by **concrete tasks** that match their domain.

### 2. Check Skill Coverage First

Before implementing a feature, check if a skill exists:

```bash
# List all skills
ls -1 .claude/skills/*/SKILL.md

# Search for a specific domain
grep -r "parallel" .claude/skills/*/SKILL.md
```

If a skill exists, let it guide your implementation.

### 3. Combine Skills for Complex Tasks

Multiple skills can be used together:

**Example:** "Set up a batch processing pipeline with persistent results"

- `rlm-batch` — Parallel execution patterns
- `rlm-memory` — Persistent storage
- `rlm-debug` — Troubleshooting setup issues

### 4. Use Skills for Debugging

When something goes wrong, explicitly invoke the debug skill:

```
"Use rlm-debug to diagnose why my sandbox exits immediately"
```

---

## Skill Development Guidelines

If you want to **create new skills** or **modify existing ones**, follow these rules:

### Frontmatter

Only `name` and `description` are valid:

```yaml
---
name: skill-name
description: Single-line description of purpose. Use when [specific scenarios].
---
```

### Length Limits

- **SKILL.md**: < 500 lines
- If content exceeds 500 lines, split into `SKILL.md` + `references/`

### File Structure

```
.claude/skills/
└── your-skill/
    ├── SKILL.md              # Main skill file (< 500 lines)
    ├── references/           # Optional: Extended content
    │   ├── examples.md
    │   └── api-docs.md
    └── scripts/              # Optional: Executable tools
        └── helper.py
```

### Invocation Patterns

Make the `description` field **specific and actionable**:

- ✅ "Use when creating input/output field definitions for dspy.RLM"
- ❌ "General DSPy help"

### Progressive Disclosure

Keep `SKILL.md` concise:

- **Quick Start** — Common use cases (3-5 examples)
- **Core Patterns** — Essential workflows
- **References** — Link to `references/` for deep dives

---

## Checking What Skills Are Available

### List All Skills

```bash
ls -1 .claude/skills/*/SKILL.md
```

### View a Skill's Description

```bash
head -5 .claude/skills/rlm/SKILL.md
```

### Search Skills by Keyword

```bash
grep -l "parallel" .claude/skills/*/SKILL.md
grep -l "debug" .claude/skills/*/SKILL.md
```

---

## Troubleshooting

### "Claude didn't use the skill I expected"

**Check:**

1. Does the skill's `description` match your task?
2. Try explicitly mentioning the skill: "Use the rlm-batch skill"
3. Verify the skill file exists: `ls .claude/skills/rlm-batch/SKILL.md`

### "Skill references missing file"

**Fix:**

```bash
# Check for broken references
find .claude/skills -type l ! -exec test -e {} \; -print
```

If you find broken symlinks or references, remove them or fix the target path.

### "Skill output is too generic"

Skills work best with **specific, task-oriented prompts**. Instead of:

- ❌ "Help me with RLM"

Try:

- ✅ "Extract API endpoints from this 200 KB doc using RLM"
- ✅ "Debug why my Modal sandbox times out after 30 seconds"
- ✅ "Run 100 analysis tasks in parallel with persistent results"

---

## Skills, Subagents & Agent Teams

fleet-rlm uses three complementary tiers of delegation. They are **not
alternatives** — they work in **synergy**. Skills provide domain knowledge,
subagents provide isolated execution, and agent teams enable collaborative
exploration. All three can be combined.

### Three-Tier Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ Agent Teams (Tier 3 — Collaborative Exploration)            │
│  Multiple Claude instances with shared tasks & messaging.   │
│  Each teammate loads CLAUDE.md + skills automatically.      │
│  Teammates can spawn subagents within their sessions.       │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ Subagents (Tier 2 — Focused Delegation)               │  │
│  │  Isolated context window, reports results back.        │  │
│  │  Own tool restrictions, model, and maxTurns.           │  │
│  │  Loads skills from its `skills:` frontmatter.          │  │
│  │                                                        │  │
│  │  ┌─────────────────────────────────────────────────┐   │  │
│  │  │ Skills (Tier 1 — Domain Knowledge)              │   │  │
│  │  │  Shared expertise modules, always available.    │   │  │
│  │  │  Loaded by main sessions, subagents, AND        │   │  │
│  │  │  agent team teammates alike.                    │   │  │
│  │  └─────────────────────────────────────────────────┘   │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

**Key insight**: Skills are the foundation. Every subagent and every teammate
benefits from skills. A subagent with the right skills is dramatically more
effective than one without.

### How They Work Together

| Tier            | What It Provides                      | Who Uses It                           | Example                                                  |
| --------------- | ------------------------------------- | ------------------------------------- | -------------------------------------------------------- |
| **Skills**      | Domain knowledge, workflows, patterns | Everyone (main, subagents, teammates) | `rlm-debug` skill loaded by `rlm-specialist` subagent    |
| **Subagents**   | Isolated execution, focused workers   | Main session or teammates             | `rlm-subcall` analyzes one chunk and reports back        |
| **Agent Teams** | Parallel collaboration, shared tasks  | Lead + spawned teammates              | Three teammates each reviewing different aspects of a PR |

### Synergy Patterns

#### Pattern 1: Skill-Enhanced Subagent

A subagent loads skills to gain domain expertise for its isolated task:

```
Main Conversation → rlm-specialist subagent
                    ├── loads rlm skill (long-context patterns)
                    ├── loads rlm-debug skill (diagnostics)
                    ├── loads modal-sandbox skill (sandbox management)
                    └── uses all three to debug a pipeline failure
```

#### Pattern 2: Subagent Chain with Skill Guidance

The main conversation uses a skill for strategy, then delegates execution:

```
Main Conversation
  ├── loads rlm skill → learns the Navigate-Query-Synthesize pattern
  ├── delegates to rlm-orchestrator → processes 500KB file
  │     ├── rlm-orchestrator loads rlm + rlm-execute + rlm-memory skills
  │     └── (when main thread) delegates chunks to rlm-subcall
  └── synthesizes results from orchestrator output
```

#### Pattern 3: Agent Team with Shared Skills

When teammates are spawned, they load CLAUDE.md and all project skills:

```
Lead Session
  ├── spawns Teammate A (security reviewer)
  │     ├── loads rlm-debug skill automatically
  │     └── can spawn modal-interpreter-agent subagent
  ├── spawns Teammate B (performance analyst)
  │     ├── loads rlm skill automatically
  │     └── can spawn rlm-specialist subagent
  └── spawns Teammate C (test validator)
        ├── loads rlm-test-suite skill automatically
        └── runs pytest directly
```

#### Pattern 4: Teammate Spawning Subagents

Within an agent team, individual teammates can use subagents for focused tasks:

```
Agent Team Lead
  └── Teammate (research analyst)
        ├── loads rlm + rlm-execute skills
        ├── delegates to rlm-subcall subagent → chunk analysis
        └── reports findings back to lead via message
```

### When to Use Each Tier

| Scenario                                 | Use                         | Why                                                   |
| ---------------------------------------- | --------------------------- | ----------------------------------------------------- |
| Need domain knowledge for a task         | **Skill**                   | Skills inject best practices without context overhead |
| Self-contained task, result only matters | **Subagent**                | Isolated context, lower token cost, focused output    |
| Task needs main context + expertise      | **Skill** (not subagent)    | Skills share the main conversation context            |
| Multiple independent explorations        | **Agent Team**              | Teammates share findings and challenge each other     |
| Large document processing pipeline       | **Subagent** + **Skills**   | Orchestrator subagent with RLM skills                 |
| Parallel review from different angles    | **Agent Team** + **Skills** | Each teammate brings skill expertise to their angle   |
| Quick diagnostic check                   | **Subagent**                | `modal-interpreter-agent` with persistent memory      |
| Complex multi-step debugging             | **Skill** (inline)          | `rlm-debug` skill in the main conversation            |

### Available Subagents

| Subagent                  | Model   | Skills Loaded                              | Purpose                                  |
| ------------------------- | ------- | ------------------------------------------ | ---------------------------------------- |
| `rlm-orchestrator`        | inherit | rlm, rlm-execute, rlm-memory               | Long-context processing pipelines        |
| `rlm-subcall`             | haiku   | —                                          | Chunk analysis (leaf node, minimal cost) |
| `rlm-specialist`          | sonnet  | rlm, rlm-debug, rlm-execute, modal-sandbox | Debug, optimize, build RLM workflows     |
| `modal-interpreter-agent` | sonnet  | modal-sandbox, rlm-debug                   | Modal diagnostics (persistent memory)    |

### Agent Teams for fleet-rlm

Agent teams are experimental (`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`). Use
them for fleet-rlm tasks that benefit from parallel exploration:

| Use Case                            | Team Structure                                                             |
| ----------------------------------- | -------------------------------------------------------------------------- |
| Reviewing a complex RLM pipeline    | Security reviewer + Performance analyst + Test validator                   |
| Debugging a multi-component failure | Hypothesis A investigator + Hypothesis B investigator + Evidence collector |
| Building a new RLM feature          | Signature designer + Implementation developer + Test writer                |
| Analyzing multiple documents        | One teammate per document, synthesize at the end                           |

**Remember**: teammates load skills automatically. You don't need to specify
skills for each teammate — they inherit project context from CLAUDE.md.

### Constraints

- **Subagents cannot spawn subagents** — the main session must chain delegations
- **Exception**: when an agent runs as the main thread (`claude --agent rlm-orchestrator`),
  it can use `Task(rlm-subcall)` to delegate
- **No nested teams** — teammates cannot create their own agent teams
- **Teammates CAN spawn subagents** — within their session, they can delegate to focused workers

### Examples

```
# Subagent with skill synergy
"Use the rlm-specialist to debug this pipeline. It should use its rlm-debug skill."

# Agent team for parallel exploration
"Create a team: one teammate to analyze the logs, another to review the code,
 and a third to check test coverage. Use fleet-rlm skills."

# Direct skill usage (no delegation needed)
"Use the rlm-debug skill to check my Modal credentials."

# Subagent with persistent memory
"Have the modal-interpreter-agent diagnose my sandbox and remember the findings."
```

---

## Installation for Other Projects

fleet-rlm is distributed as a PyPI package that bundles the RLM skills and agents. After installing the package, use the `fleet-rlm init` CLI command to bootstrap them to your user-level Claude directory (`~/.claude/`).

See the **[Skills and Agents Guide](skills-and-agents.md)** for detailed installation instructions, command options, and package structure.

### Quick Start

```bash
# Install fleet-rlm from PyPI
uv add fleet-rlm
# or: pip install fleet-rlm

# Bootstrap skills and agents to ~/.claude/
fleet-rlm init
```

This installs:

- 10 skills to `~/.claude/skills/`
- 4 agents to `~/.claude/agents/`

**User-level placement**: Skills and agents are available in ALL projects on your machine. You don't need to run `init` in each project.

### Updating After Package Upgrade

```bash
# Upgrade the package
uv upgrade fleet-rlm

# Update your installed skills/agents
fleet-rlm init --force
```

For complete installation options and troubleshooting, see the [Skills and Agents Guide](skills-and-agents.md).

## Further Reading

- **[Skills and Agents Guide](skills-and-agents.md)** — Installation details, command options, and package structure
- **[AGENTS.md](../../AGENTS.md)** — Project architecture and RLM patterns
- **[VFS_TAXONOMY.md](../../VFS_TAXONOMY.md)** — Modal Sandbox file system structure
- **[Core Concepts](../concepts.md)** — RLM theory and implementation details
- **[Claude Code Skills Docs](https://code.claude.com/docs/en/skills)** — Official skills documentation
- **[Claude Code Sub-Agents Docs](https://code.claude.com/docs/en/sub-agents)** — Official subagent documentation
- **[Claude Code Agent Teams Docs](https://code.claude.com/docs/en/agent-teams)** — Official agent teams documentation

---

## Summary

| Skill              | Key Use Cases                              | Trigger Phrases                                      |
| :----------------- | :----------------------------------------- | :--------------------------------------------------- |
| `rlm`              | Long-context processing, document analysis | "Process large file", "Extract from docs"            |
| `rlm-run`          | Configuring and executing RLM tasks        | "Run RLM task", "Configure ModalInterpreter"         |
| `rlm-debug`        | Troubleshooting failures                   | "Debug sandbox", "Why is this failing?"              |
| `rlm-execute`      | Running code in cloud sandboxes            | "Execute in sandbox", "Run this code in Modal"       |
| `rlm-batch`        | Parallel processing                        | "Run in parallel", "Batch process"                   |
| `rlm-memory`       | Persistent storage                         | "Save for later", "Persist results across sessions"  |
| `rlm-test-suite`   | Testing and validation                     | "Run tests", "Validate setup"                        |
| `dspy-signature`   | Designing task schemas                     | "Create a signature", "Define input/output fields"   |
| `modal-sandbox`    | Managing sandboxes and volumes             | "Create volume", "List sandboxes", "Inspect sandbox" |
| `rlm-long-context` | Experimental long-context techniques       | "Research pattern", "Alternative implementation"     |

Skills are your **AI pair programmer's expertise modules** — use them to accelerate development and ensure best practices across the fleet-rlm ecosystem.
