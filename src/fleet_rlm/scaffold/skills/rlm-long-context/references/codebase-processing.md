# Processing a Whole Codebase

For analyzing entire codebases (multiple files), use the concatenation approach.

## Contents

- Concatenate Codebase
- Process with RLM
- Semantic Chunking for Code
- Query-Guided File Selection
- Delegation with File Context
- Common Query Types
- NEVER List for Codebases

---

## Concatenate Codebase

```bash
# Concatenate all source files into single processable file
python3 .skills/rlm-long-context/scripts/codebase_concat.py \
    /path/to/your/project \
    -o codebase.txt

# Include only specific file types
python3 .skills/rlm-long-context/scripts/codebase_concat.py \
    /path/to/your/project \
    -o codebase.txt \
    -i '*.py' '*.md' '*.yaml'

# Exclude specific directories
python3 .skills/rlm-long-context/scripts/codebase_concat.py \
    /path/to/your/project \
    -o codebase.txt \
    --exclude-dirs node_modules vendor .git
```

**Output Format:**

```
======== FILE: src/main.py ========
<content of main.py>
======== END FILE: src/main.py ========
```

---

## Process with RLM

```bash
python3 .skills/rlm-long-context/scripts/rlm_repl.py init codebase.txt

python3 .skills/rlm-long-context/scripts/rlm_repl.py exec -c "
import re
files = re.findall(r'FILE: (.+)', content)
print(f'Total files: {len(files)}')
print(f'First 10 files: {files[:10]}')
"
```

---

## Semantic Chunking for Code

Read [`scripts/semantic_chunk.py`](../scripts/semantic_chunk.py) with `--type python` (or appropriate language).

```bash
python3 .skills/rlm-long-context/scripts/semantic_chunk.py \
    --type python \
    --state .claude/rlm_state/state.pkl
```

Manual REPL approach:

```bash
python3 .skills/rlm-long-context/scripts/rlm_repl.py exec <<'PY'
import re, os

file_pattern = r'={60}\nFILE: (.+?)\n={60}\n(.*?)\n={60}\nEND FILE'
files = re.findall(file_pattern, content, re.DOTALL)
print(f"Found {len(files)} files")

chunks_dir = '.claude/rlm_state/chunks'
os.makedirs(chunks_dir, exist_ok=True)

chunk_paths = []
for i, (filepath, filecontent) in enumerate(files):
    chunk_path = f"{chunks_dir}/chunk_{i:04d}.txt"
    with open(chunk_path, 'w') as f:
        f.write(f"FILE: {filepath}\n\n{filecontent}")
    chunk_paths.append(chunk_path)

print(f"Created {len(chunk_paths)} chunks")
PY
```

---

## Query-Guided File Selection

For targeted queries, find relevant files first:

```bash
python3 .skills/rlm-long-context/scripts/rlm_repl.py exec <<'PY'
import re

keywords = ['auth', 'login', 'password']
pattern = re.compile('|'.join(keywords), re.IGNORECASE)

file_pattern = r'={60}\nFILE: (.+?)\n={60}\n(.*?)\n={60}\nEND FILE'
files = re.findall(file_pattern, content, re.DOTALL)

scored_files = []
for filepath, filecontent in files:
    score = len(pattern.findall(filecontent))
    if score > 0:
        scored_files.append((filepath, score, filecontent))

top_files = sorted(scored_files, key=lambda x: x[1], reverse=True)[:10]
print(f"Top {len(top_files)} relevant files:")
for filepath, score, _ in top_files:
    print(f"  {filepath} (matches: {score})")
PY
```

---

## Delegation with File Context

When delegating to subagents, include file path:

```yaml
subagent: rlm-subcall
input:
  query: "Find all authentication-related functions"
  chunk_path: ".claude/rlm_state/chunks/chunk_0005.txt"
  chunk_id: "chunk_0005"
  format: "json"
  context: "This chunk contains src/auth.py"
```

---

## Common Query Types

| Query Type                    | Approach                                    | Example                                   |
| ----------------------------- | ------------------------------------------- | ----------------------------------------- |
| **Find function definitions** | Query-guided: search `def function_name`    | "Find all functions named 'authenticate'" |
| **Cross-file dependencies**   | Process all chunks, aggregate imports       | "What files import the User class?"       |
| **Architecture overview**     | Semantic chunking by file, summarize each   | "Summarize the project structure"         |
| **Security audit**            | Query-guided: `password`, `secret`, `token` | "Find hardcoded secrets"                  |
| **Refactoring candidates**    | Query: `TODO`, `FIXME`, duplicate patterns  | "Find duplicate utility functions"        |

---

## Extracting Specific Files

```bash
python3 .skills/rlm-long-context/scripts/codebase_concat.py \
    codebase.txt \
    --extract src/auth.py \
    -o auth_backup.py
```

---

## NEVER List for Codebases

**NEVER concatenate node_modules, .git, or vendor directories**

- WHY: Massive bloat, irrelevant noise, potential security issues
- FIX: Use `--exclude-dirs` flag

**NEVER lose file path context**

- WHY: Search results without paths are useless for code navigation
- FIX: Always include `FILE: <path>` header in chunks

**NEVER process binary files as text**

- WHY: Binary content corrupts analysis, creates noise
- FIX: Exclude: `*.png`, `*.jpg`, `*.exe`, `*.so`, `*.dll`
