# Deterministic chunking helpers

Prefer direct variable inspection for a small number of searches. Create chunk files only when structure-aware partitioning or repeated ranking materially reduces the evidence set.

## Split one explicit input

Load `scripts/semantic_chunk.py`, execute its UTF-8 source in an isolated
namespace, and call its pure functions from generated Python:

```python
namespace = {"__name__": "skill_resource"}
exec(compile(script_source, "scripts/semantic_chunk.py", "exec"), namespace)
chunks = namespace["chunk_content"](document, "markdown", 8000, 0)
```

The content type accepts `auto`, `markdown`, `log`, `json`, `python`, or
`text`. Each returned tuple contains a stable source start, source end, and
text. Call `write_chunks` only when persistent chunk files are actually useful.
Its output directory is the only location the helper writes.

Use a new or empty output directory. The splitter refuses a non-empty directory so stale chunks cannot enter a later ranking pass. Manifest positions are character offsets in the UTF-8 text decoded by the script; re-align them before applying them to a different source variable.

## Rank explicit chunk files

```python
namespace = {"__name__": "skill_resource"}
exec(compile(ranker_source, "scripts/rank_chunks.py", "exec"), namespace)
ranked = namespace["rank_chunk_files"](
    chunks_dir="chunks",
    query="authentication token refresh",
    top_k=8,
)
```

The ranker reads direct `*.txt` children in lexical path order and returns path
and score pairs. Ties are ordered by path. Scores are lexical hints, not
evidence; read and verify each selected excerpt against the source. Do not use
lexical rank to exclude unseen chunks when the task requires high recall.

`read_skill_resource` returns script source, not a host executable. Execute that
source only inside the existing RLM interpreter. Fleet does not add a host shell
or unrestricted script-execution endpoint.

For codebases, build a bounded input containing only relevant source files. Preserve source-path markers and exclude generated output, dependencies, caches, and secrets.
