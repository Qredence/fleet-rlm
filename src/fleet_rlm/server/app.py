"""Optional FastAPI server surface for ReAct + RLM chat."""

from __future__ import annotations

from dataclasses import dataclass

from fleet_rlm import runners


@dataclass
class ServerRuntimeConfig:
    secret_name: str = "LITELLM"
    volume_name: str | None = None
    timeout: int = 900
    react_max_iters: int = 10
    rlm_max_iterations: int = 30
    rlm_max_llm_calls: int = 50


def create_app(*, config: ServerRuntimeConfig | None = None):
    """Create and return FastAPI app (imported lazily for optional extras)."""
    from fastapi import FastAPI, WebSocket
    from pydantic import BaseModel

    cfg = config or ServerRuntimeConfig()
    app = FastAPI(title="fleet-rlm server", version="0.1.0")

    class ChatRequest(BaseModel):
        message: str
        docs_path: str | None = None
        trace: bool = False

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.post("/chat")
    async def chat(request: ChatRequest):
        result = runners.run_react_chat_once(
            message=request.message,
            docs_path=request.docs_path,
            react_max_iters=cfg.react_max_iters,
            rlm_max_iterations=cfg.rlm_max_iterations,
            rlm_max_llm_calls=cfg.rlm_max_llm_calls,
            timeout=cfg.timeout,
            secret_name=cfg.secret_name,
            volume_name=cfg.volume_name,
            include_trajectory=request.trace,
        )
        return result

    @app.websocket("/chat/ws")
    async def chat_ws(websocket: WebSocket):
        await websocket.accept()
        with runners.build_react_chat_agent(
            react_max_iters=cfg.react_max_iters,
            rlm_max_iterations=cfg.rlm_max_iterations,
            rlm_max_llm_calls=cfg.rlm_max_llm_calls,
            timeout=cfg.timeout,
            secret_name=cfg.secret_name,
            volume_name=cfg.volume_name,
        ) as agent:
            while True:
                payload = await websocket.receive_json()
                message = str(payload.get("message", "")).strip()
                if not message:
                    await websocket.send_json({"error": "message cannot be empty"})
                    continue
                result = agent.chat_turn(message)
                await websocket.send_json(result)

    return app
