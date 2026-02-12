# Sandbox File System

This document defines the Virtual File System (VFS) structure for the `fleet-rlm` Modal Sandbox. A standard topology ensures that agents can reliably find data and persist state.

## File Tree Visualization

```text
/ (Root)
├── data/                     # [Persistent] Modal Volume Mount
│   ├── knowledge/            # Documentation & Context
│   │   ├── dspy/
│   │   └── rlm/
│   ├── memory/               # Long-term Agent Memory
│   └── output/               # Saved Artifacts
├── src/                      # [Read-Only] Application Code
│   └── fleet_rlm/            # The injected library
└── workspace/                # [Ephemeral] Current Task Directory
    ├── docs/                 # Active documents for analysis
    └── temp/                 # Scratchpad for intermediate files
```

## VFS Root Structure

| Mount Point  | Description             | Persistence            | Source / Mapping                               |
| :----------- | :---------------------- | :--------------------- | :--------------------------------------------- |
| `/data`      | **Persistent Storage**  | Persistent (Volume V2) | Modal Volume (e.g., `rlm-volume-dspy`)         |
| `/src`       | **Source Code**         | Read-Only / Static     | Local `src/fleet_rlm` package                  |
| `/workspace` | **Ephemeral Task Data** | Ephemeral              | Local task-specific files or generated content |
| `/root`      | **Home Directory**      | Ephemeral              | Standard Linux home directory                  |

---

### Detailed Taxonomy

#### 1. `/data` - Persistent Volume

Mounted using Modal Volumes V2. This is the primary location for data that must survive across sandbox restarts.

- `/data/knowledge/`: Standardized location for documentation and research papers.
  - `/data/knowledge/dspy/`: DSPy framework documentation.
  - `/data/knowledge/rlm/`: RLM research papers and implementation notes.
  - `/data/knowledge/skills/`: Agent skills and tools documentation.
- `/data/memory/`: Persistent state/memory for RLM agents (e.g., JSON files storing learned patterns).
- `/data/cache/`: Shared cache for heavy computations or LLM response caching.
- `/data/output/`: Long-term storage for extracted structured data.

#### 2. `/src` - Application Code

Contains the `fleet_rlm` package and any necessary utility scripts. This directory should be treated as read-only by the sandboxed code to ensure execution integrity.

- `/src/fleet_rlm/`: The core package logic.

#### 3. `/workspace` - Task Runtime

The working directory for the current execution. Used for temporary files, intermediate processing steps, and local clones of data being analyzed.

- `/workspace/temp/`: Short-lived temporary files.
- `/workspace/docs/`: Current document(s) being analyzed if not read from `/data`.

---

### Mapping Guidelines

When using `ModalInterpreter`, the following conventions should be applied:

1.  **Volumes**: Use `volume_name` to mount a Modal Volume at `/data`.
2.  **Uploads**: Use `upload_to_volume` to sync local `rlm_content` to `/data/knowledge`.
3.  **Code Injection**: The `sandbox_driver` facilitates execution of code that can interact with these paths.

### Benefits

- **Consistency**: Agents can rely on fixed paths (e.g., always looking for docs in `/data/knowledge`).
- **Isolation**: Ephemeral task data in `/workspace` doesn't clutter persistent storage.
- **Scalability**: New data categories can be added under `/data` without breaking existing RLM signatures.
