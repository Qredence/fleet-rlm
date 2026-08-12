"""Manage Fleet signature prompts in the MLflow Prompt Registry.

The optimizer and the quality gate produce candidate instruction texts, but
promotion is manual and candidates currently live only in ``.scratch/`` files.
This script registers the ``FleetRLMSignature`` instruction text (or any text
file) as a versioned MLflow prompt, links those versions to persisted
``fleet_turn`` traces for lineage, lists registered prompts, and manages
aliases. All commands require ``FLEET_LIVE=1`` and write bounded JSON receipts.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

RECEIPT_SCHEMA = "fleet.prompt-registry/v1"
DEFAULT_MLFLOW_URL = "databricks"
DEFAULT_PROMPT_NAME = "fleet-rlm-signature"
DEFAULT_PROMPT_TAG = "fleet_eval_candidate"
_MAX_LIMIT = 500
_LIVE_VALUES = frozenset({"1", "true", "yes"})


class PromptRegistryError(RuntimeError):
    """A prompt registry precondition or MLflow contract failed."""


def _require_live() -> None:
    """
    Enforce the explicit live opt-in for credentialed MLflow access.

    Raises:
        PromptRegistryError: If ``FLEET_LIVE`` is not enabled.
    """
    if os.environ.get("FLEET_LIVE", "").lower() not in _LIVE_VALUES:
        raise PromptRegistryError("FLEET_LIVE=1 is required for prompt registry operations")


def _experiment_name_default() -> str:
    return os.environ.get("FLEET_MLFLOW_EXPERIMENT_NAME", "fleet-rlm")


def _resolve_experiment_id_str(mlflow_url: str, experiment_id: str, experiment_name: str) -> str:
    """
    Resolve an experiment id from an explicit id or the experiment name.

    Parameters:
        mlflow_url (str): MLflow tracking URI.
        experiment_id (str): Explicit experiment id, or empty.
        experiment_name (str): Experiment name to resolve when no id is given.

    Returns:
        str: The resolved experiment id.

    Raises:
        PromptRegistryError: If the experiment name does not resolve.
    """
    import mlflow

    mlflow.set_tracking_uri(mlflow_url)
    if experiment_id:
        return str(experiment_id)
    experiment = mlflow.get_experiment_by_name(experiment_name)
    if experiment is None:
        raise PromptRegistryError(f"MLflow experiment not found: {experiment_name!r}")
    return str(experiment.experiment_id)


def _resolve_experiment_id(args: argparse.Namespace) -> str:
    """
    Resolve the target experiment id from CLI arguments.

    Parameters:
        args (argparse.Namespace): Parsed CLI arguments.

    Returns:
        str: The resolved experiment id.
    """
    return _resolve_experiment_id_str(args.mlflow_url, args.experiment_id, args.experiment_name)


def _signature_text(args: argparse.Namespace) -> str:
    """
    Load the prompt template from a text file or the Fleet signature module.

    Parameters:
        args (argparse.Namespace): Parsed CLI arguments.

    Returns:
        str: The prompt template text.

    Raises:
        PromptRegistryError: If the text source is unavailable or empty.
    """
    if args.text_file is not None:
        try:
            text = args.text_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise PromptRegistryError(f"could not read prompt text: {args.text_file}") from exc
    else:
        try:
            from fleet_rlm.rlm.signature import FleetRLMSignature
        except Exception as exc:
            raise PromptRegistryError("--text-file is required when the Fleet signature module is unavailable") from exc
        text = str(FleetRLMSignature.instructions)
    if not text.strip():
        raise PromptRegistryError("prompt template must be non-empty")
    return text


def _resolve_prompt_version(args: argparse.Namespace) -> Any:
    """
    Resolve a PromptVersion entity from --version or --alias.

    Parameters:
        args (argparse.Namespace): Parsed CLI arguments.

    Returns:
        Any: The resolved MLflow PromptVersion.

    Raises:
        PromptRegistryError: If neither selector resolves.
    """
    import mlflow
    from mlflow.tracking.client import MlflowClient

    mlflow.set_tracking_uri(args.mlflow_url)
    if args.version:
        prompt_version = mlflow.load_prompt(args.prompt_name, version=args.version)
    elif args.alias:
        prompt_version = MlflowClient().get_prompt_version_by_alias(args.prompt_name, args.alias)
    else:
        raise PromptRegistryError("link-traces requires --version or --alias")
    if prompt_version is None:
        raise PromptRegistryError(f"prompt version not found: {args.prompt_name} {args.version or args.alias}")
    return prompt_version


def register_prompt_text(
    *,
    template: str,
    prompt_name: str = DEFAULT_PROMPT_NAME,
    mlflow_url: str = DEFAULT_MLFLOW_URL,
    experiment_id: str = "",
    experiment_name: str | None = None,
    source: str = "signature",
    commit_message: str | None = None,
    alias: str = "",
) -> dict[str, Any]:
    """
    Register a prompt template as a versioned MLflow prompt (shared core).

    Used by both the ``register`` CLI command and offline callers such as the
    signature optimizer, so promoted candidates land in the same registry.

    Parameters:
        template (str): Prompt template text (non-empty).
        prompt_name (str): Registry name for the prompt.
        mlflow_url (str): MLflow tracking URI.
        experiment_id (str): Explicit experiment id, or empty to resolve by name.
        experiment_name (str | None): Experiment name fallback.
        source (str): Provenance tag value stamped as ``fleet.source``.
        commit_message (str | None): Version commit message.
        alias (str): Optional alias to set on the new version.

    Returns:
        dict[str, Any]: Registration receipt with the new version.

    Raises:
        PromptRegistryError: If the template is empty or registration fails.
    """
    _require_live()
    if not template.strip():
        raise PromptRegistryError("prompt template must be non-empty")
    experiment_name = experiment_name or _experiment_name_default()
    experiment_id = _resolve_experiment_id_str(mlflow_url, experiment_id, experiment_name)
    import mlflow
    from mlflow.tracking.client import MlflowClient

    mlflow.set_tracking_uri(mlflow_url)
    sha256 = hashlib.sha256(template.encode("utf-8")).hexdigest()
    prompt_version = mlflow.register_prompt(
        name=prompt_name,
        template=template,
        commit_message=commit_message or None,
        tags={
            "fleet.source": source,
            "fleet.signature_sha256": sha256,
        },
    )
    version = int(getattr(prompt_version, "version", 0) or 0)
    with contextlib.suppress(Exception):
        # Linking to the experiment is best-effort and backend-dependent;
        # registration itself succeeded and trace linking remains available.
        MlflowClient()._link_prompt_to_experiment(prompt_version, experiment_id)
    if alias and version:
        mlflow.set_prompt_alias(name=prompt_name, alias=alias, version=version)
    return {
        "command": "register",
        "experiment_id": experiment_id,
        "prompt_name": prompt_name,
        "version": version,
        "prompt_alias": alias,
        "signature_sha256": sha256,
        "template_chars": len(template),
    }


def register(args: argparse.Namespace) -> dict[str, Any]:
    """
    Register the Fleet signature instruction text as a versioned MLflow prompt.

    Parameters:
        args (argparse.Namespace): Connection, source, name, and alias options.

    Returns:
        dict[str, Any]: Registration receipt with the new version.
    """
    _require_live()
    text = _signature_text(args)
    return register_prompt_text(
        template=text,
        prompt_name=args.prompt_name,
        mlflow_url=args.mlflow_url,
        experiment_id=args.experiment_id,
        experiment_name=args.experiment_name,
        source=args.source,
        commit_message=args.commit_message,
        alias=args.alias,
    )


def link_traces(args: argparse.Namespace) -> dict[str, Any]:
    """
    Link a registered prompt version to persisted fleet_turn traces.

    Parameters:
        args (argparse.Namespace): Connection, prompt version, and trace filter options.

    Returns:
        dict[str, Any]: Link receipt with the number of linked traces.
    """
    _require_live()
    experiment_id = _resolve_experiment_id(args)
    prompt_version = _resolve_prompt_version(args)
    import mlflow
    from mlflow.tracking.client import MlflowClient

    mlflow.set_tracking_uri(args.mlflow_url)
    filter_string = f"tag.{args.tag} = 'true'" if args.tag else None
    traces = mlflow.search_traces(
        locations=[experiment_id],
        filter_string=filter_string,
        max_results=args.limit,
        return_type="list",
    )
    client = MlflowClient()
    linked = 0
    skipped = 0
    for trace in traces:
        info = getattr(trace, "info", None)
        trace_id = getattr(info, "trace_id", None)
        if not trace_id:
            skipped += 1
            continue
        client.link_prompt_versions_to_trace([prompt_version], str(trace_id))
        linked += 1
    return {
        "command": "link-traces",
        "experiment_id": experiment_id,
        "prompt_name": args.prompt_name,
        "version": int(getattr(prompt_version, "version", 0) or 0),
        "alias": args.alias,
        "tag": args.tag,
        "limit": args.limit,
        "traces_seen": len(traces),
        "traces_linked": linked,
        "traces_skipped": skipped,
    }


def list_prompts(args: argparse.Namespace) -> dict[str, Any]:
    """
    List registered prompts with their prompt-level tags.

    Parameters:
        args (argparse.Namespace): Connection and limit options.

    Returns:
        dict[str, Any]: List receipt with bounded prompt rows.
    """
    _require_live()
    import mlflow

    mlflow.set_tracking_uri(args.mlflow_url)
    prompts = mlflow.search_prompts(max_results=args.limit)
    rows = []
    for prompt in prompts:
        tags = getattr(prompt, "tags", None) or {}
        rows.append(
            {
                "name": str(getattr(prompt, "name", "unknown")),
                "tags": {str(key): str(value) for key, value in list(tags.items())[:32]},
            }
        )
    return {"command": "list", "count": len(rows), "prompts": rows}


def set_alias(args: argparse.Namespace) -> dict[str, Any]:
    """
    Set an alias on a registered prompt version.

    Parameters:
        args (argparse.Namespace): Connection, name, alias, and version options.

    Returns:
        dict[str, Any]: Alias receipt.
    """
    _require_live()
    import mlflow

    mlflow.set_tracking_uri(args.mlflow_url)
    if not args.alias:
        raise PromptRegistryError("set-alias requires --alias")
    if not args.version:
        raise PromptRegistryError("set-alias requires --version")
    mlflow.set_prompt_alias(name=args.prompt_name, alias=args.alias, version=args.version)
    return {
        "command": "set-alias",
        "prompt_name": args.prompt_name,
        "alias": args.alias,
        "version": args.version,
    }


def build_parser() -> argparse.ArgumentParser:
    """
    Create the command-line argument parser for prompt registry management.

    Returns:
        argparse.ArgumentParser: Parser configured with command, connection,
        prompt source, and alias options.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("register", "link-traces", "list", "set-alias"))
    parser.add_argument("--mlflow-url", default=DEFAULT_MLFLOW_URL)
    parser.add_argument("--experiment-id", default="")
    parser.add_argument("--experiment-name", default=_experiment_name_default())
    parser.add_argument("--prompt-name", default=DEFAULT_PROMPT_NAME)
    parser.add_argument("--source", choices=("signature", "text-file"), default="signature")
    parser.add_argument("--text-file", type=Path, default=None, help="Prompt template text file")
    parser.add_argument("--commit-message", default="")
    parser.add_argument("--alias", default="", help="Prompt alias (register optional; set-alias required)")
    parser.add_argument("--version", type=int, default=0, help="Prompt version (link-traces/set-alias)")
    parser.add_argument("--tag", default=DEFAULT_PROMPT_TAG, help="Trace tag selecting traces to link")
    parser.add_argument("--limit", type=int, default=100, help="Maximum prompts/traces (default: 100)")
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """
    Run the selected prompt command and write its result as a JSON receipt.

    Parameters:
        argv (Sequence[str] | None): Optional command-line arguments; uses the
            process arguments when omitted.

    Returns:
        int: `0` when the command succeeds, `1` when it fails.
    """
    load_dotenv(_REPO_ROOT / ".env", override=False)
    args = build_parser().parse_args(argv)
    try:
        if not 1 <= args.limit <= _MAX_LIMIT:
            raise PromptRegistryError(f"--limit must be in [1, {_MAX_LIMIT}]")
        if args.command == "register":
            receipt = register(args)
        elif args.command == "link-traces":
            receipt = link_traces(args)
        elif args.command == "list":
            receipt = list_prompts(args)
        else:
            receipt = set_alias(args)
    except Exception as exc:
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "generated_at": datetime.now(UTC).isoformat(),
            "command": args.command,
            "status": "failed",
            "error_category": type(exc).__name__,
        }
        exit_code = 1
    else:
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "generated_at": datetime.now(UTC).isoformat(),
            "status": "ok",
            **receipt,
        }
        exit_code = 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
