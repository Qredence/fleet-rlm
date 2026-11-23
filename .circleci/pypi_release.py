"""Registry checks and Smart Deployments signaling for the Fleet-RLM wheel."""

import hashlib
import json
import os
import re
import sys
import time
import tomllib
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def registry_files(version):
    with urlopen(
        f"https://pypi.org/pypi/fleet-rlm/{version}/json", timeout=20
    ) as response:
        return json.load(response)["urls"]


def preflight(version):
    project = tomllib.loads(Path("backend/pyproject.toml").read_text())["project"]
    if project["name"] != "fleet-rlm" or project["version"] != version:
        raise SystemExit("Requested release does not match backend/pyproject.toml")
    try:
        registry_files(version)
    except HTTPError as error:
        if error.code != 404:
            raise
    else:
        raise SystemExit(f"fleet-rlm {version} already exists on PyPI")


def download(version):
    for attempt in range(12):
        try:
            files = registry_files(version)
            break
        except HTTPError as error:
            if error.code != 404 or attempt == 11:
                raise
            time.sleep(5)

    expected = {}
    for line in Path("dist/SHA256SUMS").read_text().splitlines():
        digest, path = line.split(maxsplit=1)
        expected[Path(path).name] = digest
    published = {file["filename"]: file["digests"]["sha256"] for file in files}
    if published != expected:
        raise SystemExit("Public PyPI digests do not match the CircleCI build")
    (wheel,) = [file for file in files if file["packagetype"] == "bdist_wheel"]
    with urlopen(wheel["url"], timeout=60) as response:
        contents = response.read()
    if hashlib.sha256(contents).hexdigest() != expected[wheel["filename"]]:
        raise SystemExit("Downloaded wheel checksum does not match the CircleCI build")
    Path("canary").mkdir()
    Path("canary", wheel["filename"]).write_bytes(contents)
    print("Public PyPI wheel and sdist digests match the CircleCI build")


def alert(version):
    request = Request(
        "https://circleci.com/api/v3/deploy/hooks/d0fc62ae-1f4a-4037-8ccb-d357adfdfda6/validate",
        headers={
            "Authorization": f"Bearer {os.environ['CIRCLECI_VALIDATION_WEBHOOK_TOKEN']}",
            "Content-Type": "application/json",
        },
        data=json.dumps(
            {
                "provider": "custom",
                "component_name": "fleet-rlm",
                "env": "pypi",
                "signal_name": "pypi_canary",
                "project_id": "122b2cc9-5a9c-4b3b-88c6-9276bd5d5d98",
                "version": version,
                "alert_transition": "triggered",
            }
        ).encode(),
    )
    with urlopen(request, timeout=20) as response:
        print(f"CircleCI accepted the canary alert (HTTP {response.status})")


if __name__ == "__main__":
    release_version = os.environ["FLEET_RELEASE_VERSION"]
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", release_version):
        raise SystemExit("Expected a stable X.Y.Z release version")
    {"preflight": preflight, "download": download, "alert": alert}[sys.argv[1]](
        release_version
    )
