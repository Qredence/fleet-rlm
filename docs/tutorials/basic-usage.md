# Tutorial: Basic Usage

This tutorial demonstrates the simplest RLM workflow: asking a question that requires code generation to answer.

## The Fibonacci Example

We will ask the agent to compute the first 12 Fibonacci numbers. This is a classic example because while an LLM _knows_ the numbers, calculating them via code is deterministic and verifiable.

### Running the Command

```bash
uv run fleet-rlm run-basic --question "What are the first 12 Fibonacci numbers?"
```

### What Happens Under the Hood?

1.  **Planner receives query**: "What are the first 12 Fibonacci numbers?"
2.  **Code Generation**: The Planner generates Python code to calculate the sequence:
    ```python
    def fib(n):
        a, b = 0, 1
        res = []
        for _ in range(n):
            res.append(a)
            a, b = b, a + b
        return res
    print(fib(12))
    ```
3.  **Sandbox Execution**:
    - The code is sent to the Modal sandbox by `ModalInterpreter`.
    - The `driver.py` inside the sandbox executes it.
    - `stdout` captures `[0, 1, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89]`.
4.  **Result**: The interpreter returns the output to the user.

## Using Persistent Volumes

You can mount a Modal Volume to persist data across different commands.

### 1. Create the Volume (One-time)

```bash
uv run modal volume create rlm-volume-dspy
```

### 2. Run with Volume

```bash
uv run fleet-rlm run-basic \
    --question "Generate a file named 'fib.txt' with the first 12 Fibonacci numbers" \
    --volume-name rlm-volume-dspy
```

Now, the file `fib.txt` exists in the volume `/data/` directory and can be accessed by future runs.
