"""Offline contracts for generated changelog drafts and contributor metadata."""

import pytest

from scripts.release_draft import category, field, render, replace_generated


def pr(number=1, kind="fix", body="## Release notes\n\nFix user-visible behavior.\n\n## Migration\n\nRestart."):
    return {
        "number": number,
        "labels": [{"name": f"release:{kind}"}],
        "body": body,
        "title": "Fix",
        "html_url": f"https://github.com/Qredence/fleet-rlm/pull/{number}",
    }


def test_fields_match_template():
    assert field(pr()["body"], "Release notes") == "Fix user-visible behavior."
    assert category(pr(kind="breaking")) == "breaking"


def test_category_requires_exactly_one_and_migration():
    item = pr()
    item["labels"].append({"name": "release:feature"})
    with pytest.raises(SystemExit, match="exactly one"):
        category(item)
    with pytest.raises(SystemExit, match="Migration"):
        category(pr(kind="breaking", body="## Release notes\nBreak."))
    assert category(pr(kind="none", body="")) == "none"


def test_render_history_exclusion_and_no_execution():
    item = pr(body="## Release notes\n$(touch /tmp/never) `danger`\n")
    historical = pr(2)
    historical["labels"] = []
    rendered = render([historical, pr(3, "none"), item])
    assert "$(touch /tmp/never)" in rendered
    assert "### Other" in rendered
    assert "#3" not in rendered
    assert render([{**item, "head": {"ref": "automation/release-draft"}}]) == ""


def test_generated_region_preserves_humans_and_history():
    text = "# Changelog\n\n## [Unreleased]\n\nHuman note\n\n## [0.1.0]\n\nHistory\n"
    draft = replace_generated(text, "### Fixes\n\n- First")
    changed = replace_generated(draft, "### Fixes\n\n- Next")
    assert "Human note" in changed and "History" in changed
    assert "First" not in changed
    assert replace_generated(changed, "### Fixes\n\n- Next") == changed
    with pytest.raises(SystemExit, match="edited"):
        replace_generated(draft.replace("- First", "- Human edit"), "- Other")


def test_ambiguous_markers_fail():
    with pytest.raises(SystemExit, match="markers"):
        replace_generated("## [Unreleased]\n<!-- fleet-release:start -->", "notes")


def test_frozen_draft_never_mutates(monkeypatch):
    from scripts import release_draft

    calls = []

    def fake_api(path, data=None):
        calls.append((path, data))
        return {"object": {"sha": "a" * 40}}

    monkeypatch.setattr(release_draft, "api", fake_api)
    monkeypatch.setattr(
        release_draft,
        "pages",
        lambda _path: [
            {
                "head": {"ref": release_draft.BRANCH, "repo": {"full_name": "Qredence/fleet-rlm"}},
                "draft": True,
                "labels": [{"name": "release-ready"}],
            }
        ],
    )
    release_draft.update("Qredence/fleet-rlm", "a" * 40)
    assert len(calls) == 1


def test_release_ready_requires_current_main(monkeypatch):
    from scripts import release_draft

    item = {
        "head": {"ref": release_draft.BRANCH, "repo": {"full_name": "Qredence/fleet-rlm"}, "sha": "b" * 40},
        "labels": [{"name": "release-ready"}],
    }
    monkeypatch.setattr(release_draft, "api", lambda path: item if "/pulls/" in path else {"object": {"sha": "a" * 40}})
    monkeypatch.setattr(release_draft, "ancestor", lambda _before, _after: False)
    with pytest.raises(SystemExit, match="stale"):
        release_draft.validate("Qredence/fleet-rlm", 1)


def test_app_token_is_repository_and_permission_scoped(monkeypatch):
    import io
    import json
    from types import SimpleNamespace

    from scripts import github_app_token

    for name, value in {
        "FLEET_GITHUB_APP_ID": "123",
        "FLEET_GITHUB_APP_INSTALLATION_ID": "456",
        "FLEET_GITHUB_APP_PRIVATE_KEY": "test-key",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(
        github_app_token.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(stdout=b"signature")
    )
    captured = []

    def open_request(request, timeout):
        assert timeout == 30
        captured.append(request)
        return io.BytesIO(b'{"token":"short-lived"}')

    monkeypatch.setattr(github_app_token.urllib.request, "urlopen", open_request)
    assert github_app_token.mint("read", "Qredence/fleet-rlm") == "short-lived"
    assert json.loads(captured[0].data) == {
        "repositories": ["fleet-rlm"],
        "permissions": {"contents": "read", "pull_requests": "read"},
    }
    assert captured[0].full_url.endswith("/installations/456/access_tokens")
