import re

with open("src/fleet_rlm/server/routers/ws.py", "r") as f:
    text = f.read()

start = text.find("EXECUTION_TO_RUN_STEP_TYPE")
end = text.find('@router.websocket("/ws/execution")')

new_imports = """
from .ws_helpers import (
    _sanitize_for_log,
    _sanitize_id,
    _authenticate_websocket,
    _get_execution_emitter,
    _error_envelope,
    _now_iso,
    _map_execution_step_type,
)
from .ws_session import _manifest_path, persist_session_state, _volume_load_manifest
from .ws_lifecycle import (
    ExecutionLifecycleManager,
    PersistenceRequiredError,
    _classify_stream_failure,
)
from .ws_commands import _handle_command
"""

router_decl = 'router = APIRouter(tags=["websocket"])'
router_idx = text.find(router_decl)
top_imports = text[:router_idx] + new_imports + "\n\n" + text[router_idx:start]

bottom = text[end:]

persist_start = bottom.find("            async def persist_session_state(")
persist_end = bottom.find("            try:\n                while True:")

replacement_persist = """            async def local_persist(
                *, include_volume_save: bool = True, latest_user_message: str = ""
            ) -> None:
                await persist_session_state(
                    agent=agent,
                    session_record=session_record,
                    active_manifest_path=active_manifest_path,
                    active_run_db_id=active_run_db_id,
                    interpreter=interpreter,
                    repository=repository,
                    identity_rows=identity_rows,
                    persistence_required=persistence_required,
                    include_volume_save=include_volume_save,
                    latest_user_message=latest_user_message,
                )

"""
if persist_start != -1 and persist_end != -1:
    bottom = bottom[:persist_start] + replacement_persist + bottom[persist_end:]

# Replace calls to persist_session_state with local_persist (within bottom)
bottom = re.sub(r"\bpersist_session_state\(", r"local_persist(", bottom)

# Remove the duplicated _handle_command at the end of the file
handle_cmd_idx = bottom.rfind("async def _handle_command(")
if handle_cmd_idx != -1:
    bottom = bottom[:handle_cmd_idx]

with open("src/fleet_rlm/server/routers/ws.py", "w") as f:
    f.write(top_imports + bottom)
