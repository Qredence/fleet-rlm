"""Regression guards for the split WebSocket router modules.

These imports broke during the ws.py decomposition when ws.py started importing
helper modules before they existed in the same commit sequence.
"""


def test_ws_router_split_modules_import():
    import fleet_rlm.server.routers.ws as ws
    import fleet_rlm.server.routers.ws.commands as ws_commands
    import fleet_rlm.server.routers.ws.helpers as ws_helpers
    import fleet_rlm.server.routers.ws.lifecycle as ws_lifecycle
    import fleet_rlm.server.routers.ws.session as ws_session

    # Basic symbol checks ensure imports resolve to the expected modules.
    assert ws.router is not None
    assert ws_commands._handle_command is not None
    assert ws_helpers._error_envelope is not None
    assert ws_lifecycle.ExecutionLifecycleManager is not None
    assert ws_session._manifest_path is not None


def test_ws_router_registers_expected_websocket_routes():
    import fleet_rlm.server.routers.ws as ws

    websocket_paths = {
        route.path for route in ws.router.routes if getattr(route, "path", None)
    }
    assert "/ws/chat" in websocket_paths
    assert "/ws/execution" in websocket_paths
