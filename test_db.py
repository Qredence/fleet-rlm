import asyncio
import logging
from dotenv import load_dotenv
from sqlmodel import select
from fleet_rlm.memory.db import init_db, get_async_session
from fleet_rlm.memory.schema import TaxonomyNode

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main() -> None:
    logger.info("Initializing Database...")
    await init_db()

    logger.info("Database initialized. Inserting dummy taxonomy node...")

    async_session_gen = get_async_session()
    session = await anext(async_session_gen)

    try:
        dummy_vector = [0.1] * 1536

        node = TaxonomyNode(
            name="DummyNode",
            description="A test taxonomy node from Phase 1 scaffolding",
            embedding=dummy_vector,
        )
        session.add(node)
        await session.commit()
        await session.refresh(node)

        logger.info(f"Inserted node with ID: {node.id}")

        stmt = select(TaxonomyNode).where(TaxonomyNode.name == "DummyNode").limit(1)
        result = await session.execute(stmt)
        queried_node = result.scalar_one_or_none()

        if queried_node:
            logger.info(
                f"Successfully queried node: {queried_node.name} (ID: {queried_node.id})"
            )
        else:
            logger.error("Failed to query the inserted node.")

    finally:
        await session.close()


if __name__ == "__main__":
    asyncio.run(main())
