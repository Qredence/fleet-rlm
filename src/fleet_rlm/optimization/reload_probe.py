"""Fresh-process validation for state-only DSPy GEPA candidates."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fleet_rlm.optimization.gepa_runner import (
    _construct_student,
    _instruction_sha256,
    _module_state_sha256,
    _validated_factory_kwargs,
)

_RESULT_PREFIX = "FLEET_GEPA_RELOAD_RESULT="
_MAX_STATE_BYTES = 4_000_000


def _run(state_path: Path, factory_path: str, factory_kwargs: Any) -> dict[str, str]:
    if state_path.suffix != ".json" or not state_path.is_file():
        raise ValueError("candidate state must be a JSON file")
    if state_path.stat().st_size > _MAX_STATE_BYTES:
        raise ValueError("candidate state exceeds the reload limit")
    kwargs = _validated_factory_kwargs(factory_path, factory_kwargs)
    student = _construct_student(factory_path, kwargs)
    student.load(str(state_path), allow_pickle=False)
    return {
        "state_sha256": _module_state_sha256(student),
        "instruction_sha256": _instruction_sha256(student),
    }


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--state", required=True)
    parser.add_argument("--factory", required=True)
    try:
        args = parser.parse_args()
        kwargs = json.loads(sys.stdin.read())
        if not isinstance(kwargs, Mapping):
            return 2
        result = _run(Path(args.state), args.factory, kwargs)
    except BaseException:
        return 2
    sys.stdout.write(_RESULT_PREFIX + json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
