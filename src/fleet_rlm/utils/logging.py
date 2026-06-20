"""Log-safe formatting helpers.

Keeps control-character sanitisation in one place so that untrusted values
cannot inject newlines into structured log output.
"""

from __future__ import annotations

import logging
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SECRET_QUERY_KEYS = {
    "access_token",
    "id_token",
    "refresh_token",
    "ticket",
    "token",
}


def _redact_url_query(value: str) -> str:
    try:
        split = urlsplit(value)
    except ValueError:
        return value
    if not split.query:
        return value

    pairs: list[tuple[str, str]] = []
    redacted = False
    for key, raw in parse_qsl(split.query, keep_blank_values=True):
        if key.lower() in _SECRET_QUERY_KEYS:
            pairs.append((key, "<redacted>"))
            redacted = True
        else:
            pairs.append((key, raw))
    if not redacted:
        return value
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(pairs), split.fragment))


def sanitize_for_log(value: object) -> str:
    """Escape CR and LF for safe log interpolation."""
    return _redact_url_query(str(value)).replace("\r", "\\r").replace("\n", "\\n")


def _sanitize_log_arg(value: object) -> object:
    if isinstance(value, str):
        return sanitize_for_log(value)
    return value


class RedactingLogFilter(logging.Filter):
    """Redact auth-like query parameters in log record messages and args."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = sanitize_for_log(record.msg)

        if isinstance(record.args, tuple):
            record.args = tuple(_sanitize_log_arg(arg) for arg in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: _sanitize_log_arg(value) for key, value in record.args.items()}
        return True


def install_log_redaction_filters() -> None:
    """Install auth-query redaction on app and server access loggers."""
    for name in ("fleet_rlm", "uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(name)
        if not any(isinstance(existing, RedactingLogFilter) for existing in logger.filters):
            logger.addFilter(RedactingLogFilter())


__all__ = ["RedactingLogFilter", "install_log_redaction_filters", "sanitize_for_log"]
