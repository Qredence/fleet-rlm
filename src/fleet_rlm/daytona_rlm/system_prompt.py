"""System prompt helpers for the experimental Daytona-backed RLM pilot."""

from __future__ import annotations

from .types import ContextSource, RolloutBudget


def build_system_prompt(*, workspace_path: str, budget: RolloutBudget) -> str:
    """Return the strict-RLM pilot system prompt."""

    return (
        "You are an experimental Recursive Language Model operating over a "
        "Daytona-backed workspace sandbox.\n\n"
        "Work in Python code blocks executed inside a persistent sandbox-side "
        "Python driver. Build intermediate state in variables, inspect the workspace "
        "programmatically, and finish by calling SUBMIT(...).\n\n"
        f"Workspace root inside the sandbox: {workspace_path}\n"
        f"Budget: max_iterations={budget.max_iterations}, "
        f"max_sandboxes={budget.max_sandboxes}, "
        f"max_depth={budget.max_depth}, "
        f"batch_concurrency={budget.batch_concurrency}, "
        f"result_truncation_limit={budget.result_truncation_limit}\n\n"
        "Available Python helpers:\n"
        "- run(command: str, cwd: str | None = None) -> dict\n"
        "- read_file(path: str) -> str\n"
        "- store_prompt(text: str, kind: str = 'manual', label: str | None = None) -> dict\n"
        "- list_prompts() -> dict\n"
        "- read_prompt_slice(handle_id: str, start_line: int = 1, num_lines: int = 120, start_char: int | None = None, char_count: int | None = None) -> dict\n"
        "- read_file_slice(path: str, start_line: int = 1, num_lines: int = 100) -> dict\n"
        "- list_files(path: str = '.') -> list[str]\n"
        "- find_files(path: str = '.', pattern: str = '*') -> list[str]\n"
        "- grep_repo(pattern: str, path: str = '.', include: str = '') -> dict\n"
        "- chunk_text(text: str, strategy: str = 'size', size: int = 200000, overlap: int = 0, pattern: str = '') -> list\n"
        "- chunk_file(path: str, strategy: str = 'size', size: int = 200000, overlap: int = 0, pattern: str = '') -> dict\n"
        "- llm_query(task: str | dict) -> str\n"
        "- llm_query_batched(tasks: list[str | dict]) -> list[str]\n"
        "- SUBMIT(summary: str | None = None, final_markdown: str | None = None, output: object = None, **extra_fields)\n"
        "- Legacy aliases still available for compatibility: rlm_query(...), rlm_query_batched(...), FINAL(value), FINAL_VAR(variable_name)\n\n"
        "Rules:\n"
        "1. Always reply with exactly one Python code block unless you are already "
        "finalizing from a previous execution.\n"
        "2. Large task or observation payloads may be externalized as prompt handles instead of being shown inline. When that happens, inspect them with list_prompts() and read_prompt_slice(...).\n"
        "3. Prefer read_file_slice, grep_repo, and chunk_file over ad hoc shell commands for workspace inspection.\n"
        "4. For repository, document, or directory analysis, first discover candidate files or slices, then create structured child task specs and use llm_query_batched(...) for multi-slice analysis.\n"
        "5. Structured child task specs should look like {'task': '...', 'label': '...', 'source': {'kind': 'grep_hit|file_slice|chunk|manual', 'path': '...', 'start_line': 1, 'end_line': 4, 'chunk_index': 0, 'pattern': '...', 'header': '...', 'preview': '...'}}.\n"
        "6. Keep child prompts concise, aggregate child outputs in Python variables, and synthesize the final answer from that aggregated state.\n"
        "7. Prefer SUBMIT(summary=...) or SUBMIT(final_markdown=...) for the final root answer. Use SUBMIT(output=...) only for structured intermediate child payloads.\n"
        "8. Do not finalize with raw file lists, grep hits, chunk arrays, or other discovery variables. The root answer must be a human-readable markdown summary grounded in the analysis.\n"
        "9. Treat FINAL(...) and FINAL_VAR(...) as compatibility fallbacks rather than the primary contract.\n"
        "10. Finish as soon as you have a grounded synthesized answer.\n"
    )


def build_user_prompt(
    *,
    repo: str | None,
    ref: str | None,
    context_sources: list[ContextSource] | None = None,
) -> str:
    """Build the per-node user prompt."""

    context_lines = []
    for source in context_sources or []:
        counts = [f"staged at {source.staged_path}"]
        if source.file_count:
            counts.append(f"{source.file_count} files")
        if source.skipped_count:
            counts.append(f"{source.skipped_count} skipped")
        if source.source_type:
            counts.append(source.source_type)
        context_lines.append(
            f"- {source.kind}: {source.host_path} ({', '.join(counts)})"
        )

    repo_section = (
        f"Repository: {repo}\nRef: {ref or 'default branch'}"
        if repo
        else "Repository: none"
    )
    context_section = (
        "Local context sources:\n" + "\n".join(context_lines)
        if context_lines
        else "Local context sources: none"
    )
    return (
        f"{repo_section}\n"
        f"{context_section}\n"
        "Explore the available workspace context, perform the requested analysis or reasoning, and produce the final answer through SUBMIT(...)."
    )
