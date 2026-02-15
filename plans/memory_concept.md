# Stateful Memory Concept (Letta-inspired)

This document outlines how to implement stateful memory in `fleet-rlm` using Modal Volumes, inspired by [Letta's Core/Archival Memory](https://docs.letta.com/concepts/memory) architecture.

## Concept: "The Agent's Hard Drive"

Just like Letta, we can give the agent persistent storage that outlives individual chat sessions. We leverage **Modal Volumes** as a high-performance, distributed filesystem.

| Letta Concept       | fleet-rlm Implementation                  | Storage Mechanism                       |
| :------------------ | :---------------------------------------- | :-------------------------------------- |
| **Core Memory**     | `agent_state.json` (System Prompt inputs) | In-memory + persisted to Volume on save |
| **Archival Memory** | `/data/memory/documents/`                 | Modal Volume (File System)              |
| **User Memory**     | `/data/memory/user_profile.md`            | Modal Volume (Markdown File)            |

## Implementation Strategy

### 1. Memory Tiers

We will implement a two-tier memory system:

#### Tier 1: Core Memory (In-Context "Memory Blocks")

- **Inspired by**: Letta "Memory Blocks" / Copilot "Just-in-Time"
- **Storage**: `core_memory.json` on Volume (loaded into System Prompt at startup).
- **Mechanism**: Agent can read/update these blocks to change its persona or track critical facts that must _always_ be in context.
- **Structure**:
  ```json
  {
    "persona": "You are a helpful coding assistant...",
    "human": "User is a senior engineer...",
    "scratchpad": "Current focus: Refactoring the API layer."
  }
  ```

#### Tier 2: Archival Memory (File System)

- **Inspired by**: Standard RAG / File Systems
- **Storage**: `/data/memory/` and `/data/docs/` on Volume.
- **Mechanism**: Agent uses `memory_read` / `memory_write` to store large documents, logs, or user history that doesn't fit in context.

### 2. Volume Structure

```text
/data
  ├── core_memory.json  # Tier 1: Loaded into every prompt
  ├── users/            # Tier 2: User specific archives
  │   └── {user_id}/
  │       ├── profile.md
  │       └── history.jsonl
  └── archival/         # Tier 2: General knowledge
      ├── product_specs/
      └── research_papers/
```

### 3. New Tooling

We will expose tools for both tiers.

#### Core Memory Tools

- `core_memory_update(block: str, content: str)`: Updates a block in `core_memory.json` and immediately refreshes the agent's context.
- `core_memory_append(block: str, content: str)`: Appends to a block.

#### Archival Memory Tools (Filesystem)

- `memory_read(path: str)`: Read file from volume.
- `memory_write(path: str, content: str)`: Write/Update file.
- `memory_list(path: str)`: List files.
- _(Future)_ `memory_search(query: str)`: Semantic search.

### 4. Verification (Copilot-style)

When `memory_write` is used for facts (e.g., "API version is 1.2"), the agent should ideally cite a source. Future improvements can include a "verification" step where the agent checks if the "fact" is still true against the codebase before loading it.

### 3. Usage Example

```python
# Initialize agent with a persistent volume
agent = RLMReActChatAgent(
    volume_name="my-agent-memory",
    volume_mount_path="/data"
)

# Agent can now persist information across sessions
response = agent("My name is Alice.")
# Agent tool call: memory_write("users/current/profile.md", "Name: Alice")

# ... Week later, new session ...
response = agent("What is my name?")
# Agent tool call: memory_read("users/current/profile.md")
# Agent Output: "Your name is Alice."
```

## Advantages

1.  **Zero-Infra DB**: No need for a separate Postgres/Redis. The filesystem _is_ the database.
2.  **Multimodal**: Can store images, PDFs, PDFs, code files alongside text.
3.  **Human Readable**: Memory is stored as standard files (MD, JSON) that can be inspected/edited easily.
