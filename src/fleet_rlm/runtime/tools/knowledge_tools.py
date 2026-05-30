from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fleet_rlm.runtime.tools._marker import tool_fn
from fleet_rlm.runtime.tools._volume_paths import knowledge_root, volume_root
from fleet_rlm.runtime.tools.schemas import (
    KnowledgePersistResult,
    KnowledgeResult,
    SearchKnowledgeInput,
    SearchKnowledgeOutput,
)

_KNOWLEDGE_INDEX_SCHEMA_VERSION = 1


def _index_path(volume_mount_path: str | None = None) -> Path | None:
    root = knowledge_root(volume_mount_path)
    if root is None:
        return None
    return root / "index.json"


def _load_index(volume_mount_path: str | None = None) -> dict[str, Any]:
    path = _index_path(volume_mount_path)
    if path is None or not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(loaded, dict):
        return {}
    documents = loaded.get("documents")
    if isinstance(documents, dict):
        return documents
    return loaded


def _write_index(index: dict[str, Any], volume_mount_path: str) -> None:
    path = _index_path(volume_mount_path)
    if path is None:
        raise RuntimeError("Volume mount path is required to write the knowledge index.")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    payload = {
        "schema_version": _KNOWLEDGE_INDEX_SCHEMA_VERSION,
        "documents": index,
    }
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)


def persist_knowledge_document(
    *,
    source: str,
    text: str,
    metadata: dict[str, Any] | None,
    volume_mount_path: str,
    alias: str = "active",
    tags: list[str] | None = None,
) -> KnowledgePersistResult:
    root = knowledge_root(volume_mount_path)
    if root is None:
        raise RuntimeError("Volume mount path is required to persist knowledge.")
    ingested = root / "ingested"
    ingested.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(f"{source}\n{text}".encode("utf-8")).hexdigest()[:16]
    doc_id = f"doc_{digest}"
    file_path = ingested / f"{doc_id}.txt"
    file_path.write_text(text, encoding="utf-8")
    index = _load_index(volume_mount_path)
    index[doc_id] = {
        "source": source,
        "alias": alias,
        "file": f"ingested/{doc_id}.txt",
        "char_count": len(text),
        "tags": tags or [],
        "metadata": metadata or {},
        "ingested_at": datetime.now(UTC).isoformat(),
    }
    _write_index(index, volume_mount_path)
    return KnowledgePersistResult(doc_id=doc_id, knowledge_path=str(file_path), index_path=str(root / "index.json"))


def _search_knowledge_impl(
    query: str, *, volume_mount_path: str | None = None, max_results: int = 20
) -> SearchKnowledgeOutput:
    vol_root = volume_root(volume_mount_path)
    if vol_root is None:
        return SearchKnowledgeOutput(
            status="error",
            query=query,
            results=[],
            count=0,
            error="No volume mount path configured for knowledge search.",
        )
    root = vol_root / "knowledge"
    index = _load_index(str(vol_root))
    safe_root = root.resolve()
    pattern = re.compile(re.escape(query), flags=re.IGNORECASE)
    results: list[KnowledgeResult] = []
    for doc_id, entry in index.items():
        if not isinstance(entry, dict):
            continue
        haystack = json.dumps(entry, sort_keys=True)
        file_rel = entry.get("file", "")
        if not file_rel or file_rel.startswith("/") or ".." in file_rel:
            continue
        file_path = (root / file_rel).resolve()
        if not str(file_path).startswith(str(safe_root)):
            continue
        text_match = False
        if file_path.exists():
            try:
                text_match = pattern.search(file_path.read_text(encoding="utf-8", errors="replace")) is not None
            except OSError:
                text_match = False
        if pattern.search(haystack) or text_match:
            results.append(
                KnowledgeResult(
                    doc_id=str(doc_id),
                    source=str(entry.get("source", "")),
                    path=str(file_path),
                    alias=str(entry.get("alias", "")),
                    tags=list(entry.get("tags", [])),
                )
            )
        if len(results) >= max_results:
            break
    return SearchKnowledgeOutput(status="ok", query=query, results=results, count=len(results))


@tool_fn
def search_knowledge(query: str, max_results: int = 20) -> dict[str, Any]:
    """Search persisted knowledge index metadata and ingested text."""
    validated = SearchKnowledgeInput(query=query, max_results=max_results)
    output = _search_knowledge_impl(validated.query, max_results=validated.max_results)
    return output.model_dump()


__all__ = ["persist_knowledge_document", "search_knowledge", "_search_knowledge_impl"]
