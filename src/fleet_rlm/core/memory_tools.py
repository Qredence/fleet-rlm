import asyncio
from sqlmodel import select
from litellm import embedding

from fleet_rlm.memory.schema import AgentMemory
from fleet_rlm.memory.db import get_async_session


def search_evolutive_memory(query: str) -> str:
    """
    Searches the long-term agent memory for past observations, rules, or data chunks
    that semantically match the query. Use this to remember persistent rules across isolated
    Modal sessions or project runs.
    """
    # Create an event loop explicitly because DSPy tools are synchronous
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    return loop.run_until_complete(_async_search(query))


async def _async_search(query: str) -> str:
    try:
        # Generate local embedding via LiteLLM
        response = embedding(model="text-embedding-3-small", input=query)
        query_vector = response["data"][0]["embedding"]

        # Query Neon DB with pgvector
        async for session in get_async_session():
            stmt = (
                select(AgentMemory)
                .order_by(AgentMemory.embedding.l2_distance(query_vector))
                .limit(5)
            )
            result = await session.execute(stmt)
            memories = result.scalars().all()

            if not memories:
                return "No relevant memories found."

            formatted = "[EVOLUTIVE MEMORY RESULTS]\n\n"
            for m in memories:
                formatted += f"[{m.memory_type}]: {m.content}\n\n"
            return formatted

        return "Memory Session Failed."
    except Exception as e:
        return f"Memory Search Failed: {str(e)}"
