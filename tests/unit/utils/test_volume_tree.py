from __future__ import annotations

from fleet_rlm.utils.volume_tree import resolve_realpath_within_root


def test_resolve_realpath_within_root_reports_clean_invalid_path_error() -> None:
    resolved, error = resolve_realpath_within_root(
        "../etc/passwd",
        root="/tmp/fleet-root",
        empty_error="missing",
        invalid_error_prefix="invalid path: ",
    )

    assert resolved is None
    assert error == "invalid path: ../etc/passwd"
