from __future__ import annotations

import logging

from fleet_rlm.utils.logging import RedactingLogFilter, sanitize_for_log


def test_sanitize_for_log_redacts_auth_query_params() -> None:
    value = sanitize_for_log("ws://localhost/ws?session_id=s&ticket=secret&access_token=jwt")

    assert "secret" not in value
    assert "jwt" not in value
    assert "ticket=%3Credacted%3E" in value
    assert "access_token=%3Credacted%3E" in value


def test_redacting_log_filter_sanitizes_uvicorn_access_args() -> None:
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=(
            "127.0.0.1:1234",
            "GET",
            "/api/v1/ws/execution?session_id=s&ticket=secret",
            "1.1",
            101,
        ),
        exc_info=None,
    )

    assert RedactingLogFilter().filter(record)

    message = record.getMessage()
    assert "secret" not in message
    assert "ticket=%3Credacted%3E" in message
    assert "101" in message
