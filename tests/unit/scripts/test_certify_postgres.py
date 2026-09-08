"""Contention receipts must not promote omitted, failed or ambiguous evidence."""

import json
from xml.etree.ElementTree import Element, SubElement, tostring

import pytest

from scripts.benchmarks.certify_postgres import SCENARIOS, preflight, summarize_report


def _report():
    suite = Element("testsuite")
    properties = SubElement(suite, "properties")
    for name, value in (("server_version_num", "170011"), ("alembic_heads", "019fe0010001")):
        SubElement(properties, "property", name=f"fleet.postgres.{name}", value=value)
    for name in SCENARIOS:
        SubElement(suite, "testcase", name=name)
    return suite


def _summary(suite, exit_code=0):
    return summarize_report(tostring(suite, encoding="unicode"), exit_code=exit_code)


def test_complete_receipt_requires_six_scenarios_and_database_provenance():
    summary = _summary(_report())
    assert summary["result"]["complete_six_scenario_campaign"] is True
    assert summary["result"]["passed"] == 6
    assert summary["query_plans"] == "not_exercised"


@pytest.mark.parametrize("failure", ["missing", "skipped", "failed", "duplicate", "exit", "provenance"])
def test_incomplete_campaign_never_certifies(failure):
    suite = _report()
    case = suite.findall("testcase")[-1]
    if failure == "missing":
        suite.remove(case)
    elif failure in {"skipped", "failed"}:
        SubElement(case, "skipped" if failure == "skipped" else "failure").text = "secret-provider-error"
    elif failure == "duplicate":
        SubElement(suite, "testcase", name=case.get("name"))
    elif failure == "provenance":
        SubElement(suite.find("properties"), "property", name="fleet.postgres.server_version_num", value="160001")
    summary = _summary(suite, exit_code=1 if failure == "exit" else 0)
    assert summary["result"]["complete_six_scenario_campaign"] is False
    assert "secret-provider-error" not in json.dumps(summary)


def test_receipt_drops_unknown_properties_and_test_content():
    suite = _report()
    SubElement(suite.find("properties"), "property", name="database_url", value="credential-sentinel")
    SubElement(suite, "testcase", name="credential-sentinel")
    SubElement(suite, "system-out").text = "credential-sentinel"
    assert "credential-sentinel" not in json.dumps(_summary(suite))


def test_preflight_requires_explicit_exclusive_target(monkeypatch):
    monkeypatch.setenv("FLEET_LIVE", "1")
    monkeypatch.setenv("FLEET_DATABASE_URL", "postgresql://unused")
    monkeypatch.delenv("FLEET_TEST_DATABASE_EXCLUSIVE", raising=False)
    with pytest.raises(ValueError, match="exclusive"):
        preflight()
    monkeypatch.setenv("FLEET_TEST_DATABASE_EXCLUSIVE", "1")
    preflight()


def test_query_plans_retain_structure_without_literals():
    from scripts.benchmarks.certify_postgres import project_query_plan

    raw = {
        "Node Type": "Index Scan",
        "Total Cost": 10.5,
        "Plan Rows": 64,
        "Filter": "secret-sentinel",
        "Index Cond": "private-sentinel",
        "Schema": "tenant-sentinel",
        "Plans": [{"Node Type": "Sort", "Plan Width": 10, "Output": ["private-sentinel"]}],
    }
    safe = project_query_plan(raw)
    assert safe["Total Cost"] == 10.5
    assert safe["Plans"][0]["Node Type"] == "Sort"
    assert "sentinel" not in json.dumps(safe)


def test_query_receipt_requires_passing_case_and_reprojects_payload():
    suite = _report()
    case = SubElement(suite, "testcase", name="test_postgres_repository_query_plan[sessions]")
    SubElement(
        suite.find("properties"),
        "property",
        name="fleet.postgres.query_plan.sessions",
        value=json.dumps(
            {
                "fixture_samples": 64,
                "plans": [
                    {"statement_sha256": "a" * 64, "plan": {"Node Type": "Seq Scan", "Filter": "secret-sentinel"}}
                ],
            }
        ),
    )
    result = _summary(suite)
    assert result["query_plans"]["sessions"]["status"] == "passed"
    assert "secret-sentinel" not in json.dumps(result)
    SubElement(case, "failure")
    assert _summary(suite)["query_plans"]["sessions"]["status"] == "incomplete"
