from __future__ import annotations

import pytest
from emulator import emulated_sandbox


@pytest.fixture
def sandbox(tmp_path):
    emulated = emulated_sandbox(tmp_path)
    try:
        yield emulated
    finally:
        emulated.code_interpreter.close()
