"""System prompt helpers for the experimental Daytona-backed RLM pilot."""

from __future__ import annotations

from .types import ContextSource, RolloutBudget


def build_system_prompt(*, workspace_path: str, budget: RolloutBudget) -> str:
    """Return the strict-RLM pilot system prompt."""

    return (
        "You are an experimental Recursive Language Model operating over a "
        "Daytona-backed workspace sandbox.\n\n"
        "Follow a host-managed iterative REPL loop. On each iteration you must "
        "respond with exactly one Python code block. That code executes inside a "
        "persistent Daytona sandbox driver, and the resulting observation is fed "
        "back into the next iteration. Finish only by calling SUBMIT(...).\n\n"
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
        "- rlm_query(task: str | dict) -> str\n"
        "- rlm_query_batched(tasks: list[str | dict]) -> list[str]\n"
        "- SUBMIT(summary: str | None = None, final_markdown: str | None = None, output: object = None, **extra_fields)\n"
        "\n"
        "Rules:\n"
        "1. Always reply with exactly one Python code block.\n"
        "2. Large task, observation, or conversation-history payloads may be externalized as prompt handles instead of being shown inline. When that happens, inspect them with list_prompts() and read_prompt_slice(...).\n"
        "3. If a follow-up request depends on prior turns, look for `conversation_history` prompt handles and inspect them before answering.\n"
        "4. Prefer read_file_slice, grep_repo, and chunk_file over ad hoc shell commands for workspace inspection.\n"
        "5. Use llm_query(...) and llm_query_batched(...) for semantic subcalls only. They invoke host-side LLM helpers and return strings; they do not create child Daytona sandboxes.\n"
        "6. Use rlm_query(...) and rlm_query_batched(...) when you need true recursive child Daytona runs. They return synthesized child summaries, not raw child payloads.\n"
        "7. Structured task specs for llm_query(...) or rlm_query(...) may look like {'task': '...', 'label': '...', 'source': {'kind': 'grep_hit|file_slice|chunk|manual', 'path': '...', 'start_line': 1, 'end_line': 4, 'chunk_index': 0, 'pattern': '...', 'header': '...', 'preview': '...'}}.\n"
        "8. The host may also auto-decompose successful observations into recursive child Daytona runs between iterations. Use the child summaries that come back in the next observation block instead of assuming only explicit callbacks can recurse.\n"
        "9. Keep sub-prompts concise, aggregate their outputs in Python variables, and synthesize the final answer from that aggregated state.\n"
        "10. Prefer SUBMIT(summary=...) or SUBMIT(final_markdown=...) for the final root answer. Use SUBMIT(output=...) only for structured intermediate child payloads.\n"
        "11. Do not finalize with raw file lists, grep hits, chunk arrays, or other discovery variables. The root answer must be a human-readable markdown summary grounded in the analysis.\n"
        "12. When you cite or summarize document evidence, keep track of the file path, line span, chunk, or header that supported the conclusion so downstream UI can surface evidence.\n"
        "13. For large-corpus analyst workflows such as diligence or M&A review, prefer iterative chunking, semantic subcalls, recursive child tasks, and synthesis over trying to load the full corpus into one prompt.\n"
        "14. Finish as soon as you have a grounded synthesized answer.\n"
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
        "Explore the available workspace context, perform grounded analysis or reasoning over the staged corpus, and produce the final answer through SUBMIT(...)."
    )
