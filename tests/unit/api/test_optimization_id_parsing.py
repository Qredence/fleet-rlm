from __future__ import annotations

import uuid

from fleet_rlm.api.routers.optimization._deps import _parse_uuid_id, parse_run_uuid
from fleet_rlm.api.routers.optimization.background import _resolve_run_uuid
from fleet_rlm.api.runtime_services.session_helpers import parse_session_uuid


def test_parse_uuid_id_accepts_local_numeric_ids() -> None:
    assert _parse_uuid_id("2", detail="missing") == uuid.UUID(int=2)


def test_parse_run_uuid_accepts_local_numeric_ids() -> None:
    assert parse_run_uuid("269") == uuid.UUID(int=269)


def test_background_run_uuid_accepts_local_numeric_ids() -> None:
    assert _resolve_run_uuid("269") == uuid.UUID(int=269)


def test_parse_session_uuid_accepts_local_numeric_ids() -> None:
    assert parse_session_uuid("9619") == uuid.UUID(int=9619)
