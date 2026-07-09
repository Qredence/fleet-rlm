from __future__ import annotations

import socket
from unittest.mock import MagicMock, patch

import pytest

from fleet_rlm.skills.errors import SkillRemoteFetchError
from fleet_rlm.skills.remote_fetch import fetch_url_text
from fleet_rlm.skills.schemas import SkillInstallPolicy


def test_fetch_url_text_rejects_non_https() -> None:
    policy = SkillInstallPolicy(url_install_enabled=True)
    with pytest.raises(SkillRemoteFetchError):
        fetch_url_text("http://example.com/SKILL.md", policy=policy)


def test_fetch_url_text_rejects_private_host() -> None:
    policy = SkillInstallPolicy(url_install_enabled=True)

    def fake_getaddrinfo(host: str, port) -> list[tuple]:
        _ = host, port
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]

    with patch("fleet_rlm.skills.remote_fetch.socket.getaddrinfo", fake_getaddrinfo):
        with pytest.raises(SkillRemoteFetchError):
            fetch_url_text("https://example.com/SKILL.md", policy=policy)


def test_fetch_url_text_enforces_allowlist() -> None:
    policy = SkillInstallPolicy(url_install_enabled=True, allowed_hosts=["allowed.example"])

    def fake_getaddrinfo(host: str, port) -> list[tuple]:
        _ = host, port
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    with patch("fleet_rlm.skills.remote_fetch.socket.getaddrinfo", fake_getaddrinfo):
        with pytest.raises(SkillRemoteFetchError):
            fetch_url_text("https://blocked.example/SKILL.md", policy=policy)


def test_fetch_url_text_returns_content() -> None:
    policy = SkillInstallPolicy(url_install_enabled=True)
    payload = b"---\nname: alpha\ndescription: x\n---\n"

    def fake_getaddrinfo(host: str, port) -> list[tuple]:
        _ = host, port
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    mock_response = MagicMock()
    mock_response.read.side_effect = [payload, b""]
    mock_response.__enter__.return_value = mock_response

    with patch("fleet_rlm.skills.remote_fetch.socket.getaddrinfo", fake_getaddrinfo):
        with patch("fleet_rlm.skills.remote_fetch._FETCH_OPENER.open", return_value=mock_response):
            text = fetch_url_text("https://example.com/SKILL.md", policy=policy)
    assert "name: alpha" in text
