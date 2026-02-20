"""FastAPI Dependencies for Fleet RLM Server.

This module provides reusable dependency injections for FastAPI routers,
such as database sessions, configuration, and properly initialized
DSPy ReAct agents.
"""

from typing import AsyncGenerator, AsyncIterator
from fastapi import Depends, Request
from sqlmodel.ext.asyncio.session import AsyncSession

from fleet_rlm.server.config import ServerRuntimeConfig
from fleet_rlm.react.agent import RLMReActChatAgent

from .database import get_db_session

# --- Configuration Dependencies ---


def get_server_config(request: Request) -> ServerRuntimeConfig:
    """Retrieve the server runtime configuration.

    This depends on how the config is attached to the app state or
    server state object initialized in main.py.
    """
    from .deps import server_state

    if not server_state.config:
        return ServerRuntimeConfig()
    return server_state.config


# --- Database Dependencies ---


async def get_db(
    session: AsyncSession = Depends(get_db_session),
) -> AsyncGenerator[AsyncSession, None]:
    """Dependency for providing database sessions."""
    yield session


# --- Agent & LLM Dependencies ---


async def get_react_agent(
    config: ServerRuntimeConfig = Depends(get_server_config),
) -> AsyncIterator[RLMReActChatAgent]:
    """Provide a configured RLMReActChatAgent for the request lifecycle.

    Yields the agent to the endpoint and ensures cleanup (interpreter shutdown)
    happens after the request completes.
    """
    from fleet_rlm.core.config import get_planner_lm_from_env
    from fleet_rlm.react.interpreter import ModalInterpreter
    from fleet_rlm.react.tools_rlm_delegate import build_rlm_delegate_tools
    from .deps import server_state
    import dspy

    # Use the globally configured planner_lm if available, otherwise fetch a fresh one
    planner_lm = getattr(server_state, "planner_lm", None) or get_planner_lm_from_env(
        model_name=config.agent_model
    )

    # Initialize the sandbox interpreter
    interpreter = ModalInterpreter(app_name="fleet-rlm")

    # Configure the DSPy globally for this request context
    dspy.settings.configure(lm=planner_lm)

    # Build Agent instance
    agent = RLMReActChatAgent(interpreter=interpreter, max_depth=config.agent_max_depth)

    # Lazily mount tools to prevent circular module dependencies during import
    from fleet_rlm.react import tools_sandbox

    # Inject memory, filesystem, code-gen, and delegation sub-agent tools
    agent.tools.extend(tools_sandbox.build_sandbox_tools(agent))
    agent.tools.extend(build_rlm_delegate_tools(agent))

    try:
        # Yield the fully equipped agent for handler usage
        yield agent
    finally:
        # Ensure Modal cleanup happens
        if agent.interpreter:
            try:
                agent.interpreter.shutdown()
            except Exception as e:
                import logging

                logging.getLogger("fleet_rlm").error(
                    f"Failed to shutdown interpreter: {e}"
                )
