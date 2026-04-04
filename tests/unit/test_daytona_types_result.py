from __future__ import annotations

from fleet_rlm.integrations.providers.daytona.types import DaytonaRunResult


def test_daytona_run_result_from_raw_and_public_sources() -> None:
    result = DaytonaRunResult.from_raw(
        {
            "run_id": "run-1",
            "repo": "https://github.com/example/repo.git",
            "ref": "main",
            "task": "Inspect runtime behavior",
            "budget": {
                "max_sandboxes": 3,
                "max_depth": 2,
                "max_iterations": 5,
                "global_timeout": 100,
                "result_truncation_limit": 1000,
                "batch_concurrency": 2,
            },
            "root_id": "root",
            "context_sources": [
                {
                    "source_id": "context-1",
                    "kind": "file",
                    "host_path": "/tmp/spec.md",
                    "staged_path": "/workspace/.fleet-rlm/context/spec.md",
                    "source_type": "markdown",
                    "extraction_method": "text",
                }
            ],
            "nodes": {
                "root": {
                    "node_id": "root",
                    "parent_id": None,
                    "depth": 0,
                    "task": "Inspect runtime behavior",
                    "repo": "https://github.com/example/repo.git",
                    "ref": "main",
                    "context_sources": [],
                    "prompt_handles": [],
                    "prompt_previews": [],
                    "response_previews": ["Reasoned about the request"],
                    "observations": [
                        {
                            "iteration": 1,
                            "code": "print('hello')",
                            "stdout": "hello",
                            "stderr": "",
                            "duration_ms": 4,
                            "callback_count": 1,
                        }
                    ],
                    "child_ids": [],
                    "child_links": [
                        {
                            "child_id": "child-1",
                            "callback_name": "delegate",
                            "iteration": 1,
                            "status": "completed",
                            "result_preview": "summary",
                            "task": {
                                "task": "Inspect src/app.py",
                                "label": "app",
                                "source": {
                                    "kind": "file",
                                    "path": "src/app.py",
                                    "start_line": 10,
                                    "end_line": 20,
                                    "preview": "print('hello')",
                                },
                            },
                        }
                    ],
                    "warnings": [],
                    "final_artifact": {
                        "kind": "structured",
                        "value": {"answer": "ok"},
                        "finalization_mode": "SUBMIT",
                    },
                    "iteration_count": 1,
                    "error": None,
                }
            },
            "final_artifact": {
                "kind": "structured",
                "value": {"answer": "ok"},
                "finalization_mode": "SUBMIT",
            },
            "summary": {
                "duration_ms": 10,
                "sandboxes_used": 1,
                "termination_reason": "completed",
            },
        }
    )

    public = result.to_public_dict()

    assert public["iterations"][0]["callback_count"] == 1
    assert public["sources"][0]["source_id"] == "context-1"
    assert public["sources"][1]["path"] == "src/app.py"
    assert public["final_artifact"]["value"] == {"answer": "ok"}
