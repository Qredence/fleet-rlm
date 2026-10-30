"""The served and exported contracts share version-valid native schemas."""

from openapi_spec_validator import validate

from fleet_rlm.api.app import create_app
from fleet_rlm.config import FleetSettings


def test_openapi_is_version_valid_and_deterministic():
    app = create_app(settings=FleetSettings(_env_file=None), initialize_db=False, configure_observability=False)
    schema = app.openapi()
    validate(schema)
    assert schema == app.openapi()
    assert schema["openapi"] == "3.1.0"
    streams = []
    for path in schema["paths"].values():
        for operation in path.values():
            for response in operation.get("responses", {}).values():
                media = response.get("content", {}).get("text/event-stream")
                if media is not None:
                    streams.append(media)
                    assert media["schema"] == {"type": "string"}
                    assert "itemSchema" not in media
    assert len(streams) == 2
