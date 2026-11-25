"""Memory prompt entries and post-turn memory consolidation."""

import logging
from uuid import UUID

from . import agent
from . import db as store
from .runtime import Runtime, effective_settings, request_decision_lm

_LOG = logging.getLogger(__name__)


def memory_entry(record: store.Memory) -> dict:
    return {
        "id": str(record.id),
        "content": record.content,
        "scope": "global" if record.session_id is None else "session",
        "approved": bool(record.approved),
    }


async def consolidate(
    runtime: Runtime,
    session_id: UUID,
    run_id: UUID,
    prompt: str,
    answer: str,
    memories: list[dict],
) -> None:
    async def save(candidate: agent.MemoryCandidate) -> None:
        if candidate.content is None:
            return
        await store.save_or_update_memory(
            runtime.sessionmaker,
            session_id=session_id if candidate.scope == "session" else None,
            content=candidate.content,
            approved=candidate.approved,
            memory_id=UUID(candidate.supersedes_id) if candidate.supersedes_id else None,
        )

    try:
        effective = await effective_settings(runtime)
        decision_lm = request_decision_lm(runtime, effective)
        candidates = await store.search_memories(
            runtime.sessionmaker,
            session_id,
            f"{prompt}\n{answer}",
            limit=3,
            include_unapproved=True,
        )
        merged = [memory_entry(record) for record in candidates]
        seen_ids = {item["id"] for item in merged}
        for memory in memories:
            if memory["id"] not in seen_ids:
                merged.append(memory)
                seen_ids.add(memory["id"])
        await agent.consolidate_memory(
            effective,
            prompt,
            answer,
            merged[:3],
            on_save=save,
            decision_lm=decision_lm,
        )
    except Exception as error:
        _LOG.warning("Memory consolidation failed for %s (%s)", run_id, type(error).__name__)
