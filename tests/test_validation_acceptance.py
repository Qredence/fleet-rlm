"""Keep the Smart Deployments acceptance signal isolated from production PyPI."""

import json
import sys
from unittest.mock import patch

import pytest

from scripts import validation_acceptance


def test_acceptance_signal_uses_only_the_isolated_component_and_environment():
    class Response:
        status = 202

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    with (
        patch.dict("os.environ", {"CIRCLECI_VALIDATION_WEBHOOK_TOKEN": "test-token"}),
        patch.object(sys, "argv", ["validation_acceptance.py", "acceptance-00000000-0000-0000-0000-000000000001"]),
        patch("scripts.validation_acceptance.urllib.request.urlopen", return_value=Response()) as post,
    ):
        validation_acceptance.main()

    request = post.call_args.args[0]
    assert json.loads(request.data) == {
        "provider": "custom",
        "component_name": "fleet-rlm-validation-acceptance",
        "env": "pypi-validation-acceptance",
        "signal_name": "pypi_canary",
        "project_id": "122b2cc9-5a9c-4b3b-88c6-9276bd5d5d98",
        "version": "acceptance-00000000-0000-0000-0000-000000000001",
        "alert_transition": "triggered",
    }


@pytest.mark.parametrize("version", ["0.9.0", "acceptance-not-a-uuid"])
def test_acceptance_signal_rejects_nonfixture_versions_before_network(version):
    with (
        patch.dict("os.environ", {"CIRCLECI_VALIDATION_WEBHOOK_TOKEN": "test-token"}),
        patch.object(sys, "argv", ["validation_acceptance.py", version]),
        patch("scripts.validation_acceptance.urllib.request.urlopen") as post,
        pytest.raises(SystemExit, match="isolated acceptance version"),
    ):
        validation_acceptance.main()
    post.assert_not_called()
