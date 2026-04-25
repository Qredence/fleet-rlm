"""FastAPI application entrypoint for ASGI servers and tooling."""

from fleet_rlm.api.main import create_app

app = create_app()
