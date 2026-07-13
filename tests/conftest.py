"""Root test configuration — markers, env isolation, fixture registration."""

from __future__ import annotations

import logging
import os
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

# Prevent remote model-cost fetch during test collection.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "true")


def _suite_from_path(path: Path) -> str | None:
    """Derive the test-suite marker from the file path."""
    parts = path.parts
    if "tests" not in parts:
        return None
    idx = parts.index("tests")
    if idx + 1 >= len(parts):
        return None
    suite = parts[idx + 1]
    if suite in {"unit", "integration", "contracts", "e2e"}:
        return suite
    return None


def pytest_make_parametrize_id(config: pytest.Config, val: object, argname: str) -> str | None:
    """Sanitize parametrize IDs to avoid spaces in node IDs for CI tooling."""
    _ = config, argname
    if isinstance(val, str) and " " in val:
        return val.replace(" ", "_")
    return None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Auto-apply suite markers and skip live tests unless opted-in."""
    _ = config
    for item in items:
        item_path = Path(str(item.fspath))
        suite = _suite_from_path(item_path)
        if suite is not None:
            item.add_marker(getattr(pytest.mark, suite))

        # Auto-mark DB-dependent integration tests.
        if suite == "integration" and item_path.name.startswith("test_db_"):
            item.add_marker(pytest.mark.db)


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Post-process the JUnit XML report to align classnames and names for Smarter Testing."""
    _ = exitstatus
    junit_xml_path = session.config.getoption("--junitxml")
    if not junit_xml_path:
        return

    path = Path(junit_xml_path)
    if not path.is_file():
        logger.debug("[conftest] junit xml path does not exist: %s", junit_xml_path)
        return

    try:
        tree = ET.parse(path)
        root = tree.getroot()
        modified_count = 0

        for testcase in root.iter("testcase"):
            classname = testcase.get("classname")
            name = testcase.get("name")
            file_attr = testcase.get("file")

            if file_attr:
                module_path = file_attr.replace("/", ".")
                if module_path.endswith(".py"):
                    module_path = module_path[:-3]

                if classname and classname.startswith(module_path) and len(classname) > len(module_path):
                    class_name = classname[len(module_path) + 1 :]
                    logger.debug(
                        "[conftest] Adjusting classname '%s' -> '%s', name '%s' -> '%s::%s'",
                        classname,
                        module_path,
                        name,
                        class_name,
                        name,
                    )
                    testcase.set("classname", module_path)
                    testcase.set("name", f"{class_name}::{name}")
                    modified_count += 1

        if modified_count:
            tree.write(path, encoding="utf-8", xml_declaration=True)
            logger.info("[conftest] Adjusted %d JUnit XML test cases.", modified_count)
    except Exception as e:
        logger.exception("[conftest] Error post-processing JUnit XML for Smarter Testing: %s", e)
