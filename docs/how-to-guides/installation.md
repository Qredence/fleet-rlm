# Installation Guide

Follow these steps to set up `fleet-rlm` on your local machine.

## Prerequisites

- **Python**: 3.10 or higher.
- **Package Manager**: [`uv`](https://github.com/astral-sh/uv) (strongly recommended) or pip.
- **Operating System**: macOS or Linux (Windows supported via WSL).

## 1. Install the Package

You can clone the repository for development or install it as a library.

### Option A: Development Install (Cloning)

```bash
git clone https://github.com/qredence/fleet-rlm.git
cd fleet-rlm
uv sync --extra dev --extra interactive --extra server
```

### Option B: Library Install (pip)

```bash
pip install fleet-rlm
```

## 2. Configure Local Environment

Create a `.env` file in your project root to configure the local Planner LLM.

```bash
cp .env.example .env
```

Edit the file with your API keys:

```ini
DSPY_LM_MODEL=openai/gpt-4o
DSPY_LLM_API_KEY=sk-...
```

## 3. Configure Cloud Runtime

`fleet-rlm` uses **Modal** to execute code securely in the cloud. You must set up authentication and secrets.

👉 **Step-by-step guide**: [Configuring Modal](configuring-modal.md)

## 4. Install Helper Skills (Optional)

If you use Claude, install the bundled skills and agents to help you develop faster.

👉 **Step-by-step guide**: [Managing Skills](managing-skills.md)

## Verification

Run the basic demo to ensure everything is working:

```bash
uv run fleet-rlm run-basic
```
