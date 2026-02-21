# Phase 6: Multi-Agent Orchestration Detailed Plan

This artifact provides the low-level blueprint for implementing the **Supervisor and Sub-Agent** architecture within the DSPy environment.

## 1. Context & Motivation

In previous phases, we successfully built `RLMEngine` to execute Python chunks sequentially inside our Modal Sandbox. However, as task complexity grows, forcing a single monolithic LLM to act as Planner, Researcher, and Python Executer leads to:

1. **Prompt Bloat:** The context window fills with tools and instructions it doesn't need for the _current_ sub-task.
2. **Hallucination:** A model focusing on Python execution forgets higher-level planning constraints.

To solve this (drawing inspiration from PrimeIntellect and Daytona), we are introducing **Context Folding via Sub-Agents**. The Supervisor maintains the big picture while spinning up temporary, stateless Sub-Agents equipped _only_ with the specific tools needed for their narrow chunk of work.

---

## 2. Technical Modifications

### 2.1 Update `dspy.Signatures`

We must redefine our DSPy signatures to clearly demarcate the **Supervisor** from the **Worker**.

**In `src/fleet_rlm/agents/signatures.py`**:

```python
import dspy

class TaskDecomposer(dspy.Signature):
    """Breaks a complex objective into highly isolated, specific sub-tasks."""
    objective = dspy.InputField(desc="The master objective from the user")
    memory_context = dspy.InputField(desc="Relevant rules fetched from Evolutive Memory")
    sub_tasks = dspy.OutputField(desc="List of isolated 1-sentence tasks required.")

class SubAgentDelegator(dspy.Signature):
    """Executes a specific sub-task using available tools."""
    sub_task = dspy.InputField(desc="The highly specific task to accomplish")
    available_tools = dspy.InputField(desc="Names of tools this sub-agent is allowed to use")
    result_summary = dspy.OutputField(desc="The final results of the execution to pass back to Supervisor")
```

### 2.2 Update `dspy.Module` Logic

We need a dedicated `SupervisorModule` that orchestrates the `RLMEngine`.

**In `src/fleet_rlm/agents/supervisor.py`**:

```python
import dspy
from src.fleet_rlm.agents.signatures import TaskDecomposer
from src.fleet_rlm.agents.worker_rlm import RLMEngine

class Supervisor(dspy.Module):
    def __init__(self):
        super().__init__()
        self.decomposer = dspy.ChainOfThought(TaskDecomposer)
        # Instead of self running the loop, we hold a factory for sub-agents

    def forward(self, prompt: str, memory_context: str) -> str:
        # 1. Decompose
        plan = self.decomposer(objective=prompt, memory_context=memory_context)

        results = []
        # 2. Iterate and Spawn Sub-Agents
        for task in plan.sub_tasks:
            # Send WebSocket {"kind": "plan_update"} via callback here

            # 3. Spawn isolated Sandbox/RLM worker
            worker = RLMEngine(tools=self._select_tools_for_task(task))
            worker_result = worker.forward(task, context=" ".join(results))

            results.append(f"Task: {task}\nResult: {worker_result}")

        # 4. Final aggregation
        return self._aggregate(prompt, results)
```

### 2.3 Sub-Agent Tool Isolation

A critical finding from our comparison is that the Supervisor should **not** see the messy stdout payloads of the Python executions.

**Rule:** The `execute_workspace_code` tool is permanently restricted. It can **only** be passed into the `RLMEngine` sub-workers. The Supervisor only sees the `result_summary` output. This fundamentally mitigates context rot globally.

---

## 3. UI/UX Synchronization (Prep for Phase 7)

As the Supervisor iterates over `plan.sub_tasks`, it must yield intermediate states to the WebSocket router.

1. Before starting a loop: Yield `plan_update` containing the list of all decomposed tasks.
2. During `worker.forward()`: Yield `rlm_executing` conveying the depth, e.g., "Research Sub-Agent extracting data...".
3. Upon completion: Update the `plan_update` payload to mark that specific task as `[Done]`.
