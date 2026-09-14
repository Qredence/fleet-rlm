#!/usr/bin/env python3
"""Create and compare immutable, non-secret Phase 6 promotion bundles.

This command performs no provider, database, package-publication, or service
switch action. Operators use its receipt as the identity precondition for a
separately authorized maintenance-window promotion or rollback rehearsal.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "fleet.phase6-promotion-bundle/v1"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}$")


class PromotionBundleError(ValueError):
    """A promotion identity is incomplete, unsafe, or incompatible."""


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise PromotionBundleError(f"cannot read required identity file: {path}") from exc


def _safe_identifier(value: str, field: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise PromotionBundleError(f"{field} must be a bounded non-secret identifier")
    return value


def _load_images(path: Path) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PromotionBundleError("image identities file is unreadable") from exc
    if not isinstance(payload, dict) or set(payload) != {"session", "semantic_child"}:
        raise PromotionBundleError("image identities must contain only session and semantic_child")
    return {key: _safe_identifier(value, f"images.{key}") for key, value in payload.items()}


def _git_output(*args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise PromotionBundleError("git identity cannot be determined") from exc


def build_bundle(
    *,
    profile: str,
    config: Path,
    lock: Path,
    artifact_manifest: Path,
    images: Path,
    database_head: str,
    dataset_digest: str,
    scorer_digest: str,
    revision: str | None = None,
) -> dict[str, Any]:
    """Build a verified, non-secret identity bundle for one clean checkout."""
    if _git_output("status", "--porcelain"):
        raise PromotionBundleError("promotion candidate checkout must be clean")
    head = _git_output("rev-parse", "HEAD")
    selected_revision = _safe_identifier(revision or head, "revision")
    if selected_revision != head:
        raise PromotionBundleError("revision must equal the clean checkout HEAD")
    manifest_digest = _sha256_file(artifact_manifest)
    bundle: dict[str, Any] = {
        "schema": SCHEMA,
        "revision": selected_revision,
        "profile": _safe_identifier(profile, "profile"),
        "config_sha256": _sha256_file(config),
        "lock_sha256": _sha256_file(lock),
        "artifact_manifest_sha256": manifest_digest,
        "images": _load_images(images),
        "database_head": _safe_identifier(database_head, "database_head"),
        "dataset_sha256": _safe_identifier(dataset_digest, "dataset_digest"),
        "scorer_sha256": _safe_identifier(scorer_digest, "scorer_digest"),
    }
    bundle["bundle_sha256"] = hashlib.sha256(_canonical_bytes(bundle)).hexdigest()
    return bundle


def validate_bundle(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a persisted bundle and return its canonical representation."""
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise PromotionBundleError("unsupported promotion bundle schema")
    required = {
        "schema",
        "revision",
        "profile",
        "config_sha256",
        "lock_sha256",
        "artifact_manifest_sha256",
        "images",
        "database_head",
        "dataset_sha256",
        "scorer_sha256",
        "bundle_sha256",
    }
    if set(payload) != required:
        raise PromotionBundleError("promotion bundle fields are incomplete")
    unsigned = {key: value for key, value in payload.items() if key != "bundle_sha256"}
    if payload["bundle_sha256"] != hashlib.sha256(_canonical_bytes(unsigned)).hexdigest():
        raise PromotionBundleError("promotion bundle digest does not match")
    for field in required - {"schema", "images", "bundle_sha256"}:
        _safe_identifier(str(payload[field]), field)
    if not isinstance(payload["images"], dict):
        raise PromotionBundleError("promotion bundle images are invalid")
    _load_image_payload(payload["images"])
    return payload


def _load_image_payload(payload: dict[str, Any]) -> None:
    if set(payload) != {"session", "semantic_child"}:
        raise PromotionBundleError("promotion bundle images are incomplete")
    for key, value in payload.items():
        _safe_identifier(str(value), f"images.{key}")


def validate_rollback_pair(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Reject rollback pairs that cannot safely share the deployed database."""
    baseline = validate_bundle(baseline)
    candidate = validate_bundle(candidate)
    if baseline["revision"] == candidate["revision"]:
        raise PromotionBundleError("rollback baseline and candidate must be distinct revisions")
    if baseline["database_head"] != candidate["database_head"]:
        raise PromotionBundleError("rollback pair must share an additive-compatible database head")
    return {
        "schema": "fleet.phase6-rollback-pair/v1",
        "baseline_bundle_sha256": baseline["bundle_sha256"],
        "candidate_bundle_sha256": candidate["bundle_sha256"],
        "database_head": baseline["database_head"],
    }


def _write_once(path: Path, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise PromotionBundleError(f"refusing to overwrite receipt: {path}") from exc
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _read_bundle(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PromotionBundleError(f"cannot read bundle: {path}") from exc
    return validate_bundle(payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--profile", required=True)
    prepare.add_argument("--config", type=Path, default=ROOT / "config" / "fleet.toml")
    prepare.add_argument("--lock", type=Path, default=ROOT / "uv.lock")
    prepare.add_argument("--artifact-manifest", type=Path, required=True)
    prepare.add_argument("--images", type=Path, required=True)
    prepare.add_argument("--database-head", required=True)
    prepare.add_argument("--dataset-digest", required=True)
    prepare.add_argument("--scorer-digest", required=True)
    prepare.add_argument("--revision")
    prepare.add_argument("--output", type=Path, required=True)
    pair = commands.add_parser("validate-rollback-pair")
    pair.add_argument("--baseline", type=Path, required=True)
    pair.add_argument("--candidate", type=Path, required=True)
    pair.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            payload = build_bundle(
                profile=args.profile,
                config=args.config,
                lock=args.lock,
                artifact_manifest=args.artifact_manifest,
                images=args.images,
                database_head=args.database_head,
                dataset_digest=args.dataset_digest,
                scorer_digest=args.scorer_digest,
                revision=args.revision,
            )
            _write_once(args.output, payload)
        else:
            payload = validate_rollback_pair(_read_bundle(args.baseline), _read_bundle(args.candidate))
            _write_once(args.output, payload)
    except PromotionBundleError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
