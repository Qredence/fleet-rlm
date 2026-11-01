"""Send a failure event for the isolated CircleCI validation acceptance fixture."""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request

ORG_ID = "d0fc62ae-1f4a-4037-8ccb-d357adfdfda6"
PROJECT_ID = "122b2cc9-5a9c-4b3b-88c6-9276bd5d5d98"
COMPONENT = "fleet-rlm-validation-acceptance"
ENVIRONMENT = "pypi-validation-acceptance"


def main() -> None:
    if len(sys.argv) != 2 or not re.fullmatch(r"acceptance-[0-9a-fA-F-]{36}", sys.argv[1]):
        raise SystemExit("expected the isolated acceptance version acceptance-<workflow-uuid>")
    token = os.environ.get("CIRCLECI_VALIDATION_WEBHOOK_TOKEN", "")
    if not token:
        raise SystemExit("CIRCLECI_VALIDATION_WEBHOOK_TOKEN is required")
    payload = {
        "provider": "custom",
        "component_name": COMPONENT,
        "env": ENVIRONMENT,
        "signal_name": "pypi_canary",
        "project_id": PROJECT_ID,
        "version": sys.argv[1],
        "alert_transition": "triggered",
    }
    request = urllib.request.Request(
        f"https://circleci.com/api/v3/deploy/hooks/{ORG_ID}/validate",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        if not 200 <= response.status < 300:
            raise SystemExit(f"CircleCI rejected the isolated failure event (HTTP {response.status})")


if __name__ == "__main__":
    main()
