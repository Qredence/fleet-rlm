"""User-facing keyboard and mouse contracts for the Textual command menu."""

from __future__ import annotations

import httpx
import pytest

from fleet_rlm.tui import local_paths, mentions
from fleet_rlm.tui.app import FleetTextualApp, _PromptInput
from fleet_rlm.tui.commands import COMMANDS, SessionState


async def _wait_until_idle(tui, pilot):
    for _ in range(100):
        if not tui._turn_active:
            return
        await pilot.pause(0.01)
    pytest.fail("Turn worker did not finish")


@pytest.fixture
def menu_app(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    def unexpected_request(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"Unexpected HTTP request: {request.method} {request.url.path}")

    with httpx.Client(base_url="http://fleet.test", transport=httpx.MockTransport(unexpected_request)) as client:
        yield FleetTextualApp(client, "menu-test", SessionState())


@pytest.mark.asyncio
async def test_history_preserves_drafts_and_does_not_reopen_command_suggestions(menu_app):
    async with menu_app.run_test(size=(100, 30)) as pilot:
        composer = menu_app.query_one("#prompt-input", _PromptInput)
        composer.value = "/timeout off"
        await pilot.press("enter")
        composer.value = "/timeout 120"
        await pilot.press("enter")
        composer.value = "keep this unsent draft"

        await pilot.press("down")
        assert composer.value == "keep this unsent draft"

        await pilot.press("up")
        assert composer.value == "/timeout 120"
        assert not menu_app.query_one("#slash-popup").display
        await pilot.press("up")
        assert composer.value == "/timeout off"
        assert not menu_app.query_one("#slash-popup").display

        await pilot.press("down", "down")
        assert composer.value == "keep this unsent draft"
        assert not menu_app.query_one("#slash-popup").display


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(132, 40), (80, 24)])
async def test_every_command_is_reachable_and_the_highlight_stays_visible(menu_app, size):
    from textual.widgets import OptionList

    async with menu_app.run_test(size=size) as pilot:
        composer = menu_app.query_one("#prompt-input", _PromptInput)
        composer.value = "/"
        await pilot.pause()
        popup = menu_app.query_one("#slash-popup", OptionList)
        assert popup.option_count == len(COMMANDS) + 3  # /help and both exit aliases

        await pilot.press("up")
        await pilot.pause()
        assert popup.highlighted == popup.option_count - 1
        assert popup.scroll_y > 0
        assert "/quit" in menu_app.export_screenshot()
        await pilot.press("tab")
        assert composer.value == "/quit"
        assert menu_app.is_running  # accepting a completion never runs it


@pytest.mark.asyncio
async def test_enter_accepts_incomplete_suggestions_before_executing_the_command(menu_app):
    async with menu_app.run_test(size=(100, 30)) as pilot:
        composer = menu_app.query_one("#prompt-input", _PromptInput)
        composer.value = "/ti"
        await pilot.press("enter")
        assert composer.value == "/timeout "
        assert menu_app.state.timeout_seconds is None
        await pilot.press("enter")
        assert composer.value == "/timeout off"
        assert menu_app.state.timeout_seconds is None
        await pilot.press("enter")
        assert composer.value == ""
        assert menu_app.state.timeout_seconds == 0

        composer.value = "/r"
        await pilot.press("enter")
        assert composer.value == "/rm "  # no DELETE request while selecting a command


@pytest.mark.asyncio
async def test_mouse_selection_fills_the_composer_without_running_the_command(menu_app):
    async with menu_app.run_test(size=(100, 30)) as pilot:
        composer = menu_app.query_one("#prompt-input", _PromptInput)
        composer.value = "/ti"
        await pilot.pause()
        await pilot.click("#slash-popup", offset=(2, 1))
        assert composer.value == "/timeout "
        assert composer.has_focus
        assert menu_app.state.timeout_seconds is None


@pytest.mark.asyncio
async def test_escape_keeps_the_input_and_explicit_tab_reopens_completion(menu_app):
    async with menu_app.run_test(size=(100, 30)) as pilot:
        composer = menu_app.query_one("#prompt-input", _PromptInput)
        composer.value = "/ti"
        await pilot.press("escape")
        await pilot.pause()
        assert composer.value == "/ti"
        assert not menu_app.query_one("#slash-popup").display
        await pilot.press("tab")
        assert composer.value == "/timeout "
        assert menu_app.query_one("#slash-popup").display


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prefix", "choices"),
    [
        ("/mode ", ["rlm", "flex", "hybrid"]),
        ("/timeout ", ["off", "default"]),
        ("/skills ", ["push", "pull"]),
        ("/replay ", ["on", "off"]),
        ("/model ", ["root", "sub"]),
        ("/config ", ["mode ", "json"]),
        ("/config mode ", ["rlm", "flex", "hybrid"]),
        ("/settings ", ["mode ", "json"]),
        ("/settings mode ", ["rlm", "flex", "hybrid"]),
        ("/context ", ["clear"]),
        ("/optimize ", ["--max-metric-calls", "--approve"]),
    ],
)
async def test_every_advertised_submenu_can_be_navigated_and_completed(menu_app, prefix, choices):
    from textual.widgets import OptionList

    async with menu_app.run_test(size=(80, 24)) as pilot:
        composer = menu_app.query_one("#prompt-input", _PromptInput)
        for index, choice in enumerate(choices):
            composer.value = prefix
            await pilot.pause()
            popup = menu_app.query_one("#slash-popup", OptionList)
            assert popup.display
            assert popup.option_count == len(choices)
            await pilot.press(*(["down"] * index), "tab")
            assert composer.value == prefix + choice
            assert composer.has_focus


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["/attach", "/put"])
async def test_local_path_menu_can_descend_into_a_directory(menu_app, tmp_path, command):
    (tmp_path / "folder").mkdir()
    (tmp_path / "folder" / "revenue.csv").write_text("amount\n42\n")
    async with menu_app.run_test(size=(80, 24)) as pilot:
        composer = menu_app.query_one("#prompt-input", _PromptInput)
        composer.value = command + " fol"
        await pilot.press("tab")
        assert composer.value == command + " folder/"
        assert menu_app.query_one("#slash-popup").display
        await pilot.press("tab")
        assert composer.value == command + " folder/revenue.csv"


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["/cat", "/stat", "/get", "/rm", "/context"])
async def test_workspace_file_menus_and_mentions_include_all_cached_paths(menu_app, command):
    menu_app.state.workspace_files = [f"files/input-{index:02}.csv" for index in range(12)]
    async with menu_app.run_test(size=(80, 24)) as pilot:
        composer = menu_app.query_one("#prompt-input", _PromptInput)
        composer.value = command + " files/"
        await pilot.press("up", "tab")
        assert composer.value == command + " files/input-11.csv"
        composer.value = "Summarize @files/"
        await pilot.press("up", "tab")
        assert composer.value == "Summarize @files/input-11.csv "


@pytest.mark.asyncio
async def test_transfer_menu_quotes_paths_with_spaces_and_the_commands_execute(monkeypatch, tmp_path):
    import base64
    import json

    monkeypatch.chdir(tmp_path)
    source = tmp_path / "quarter report.csv"
    source.write_bytes(b"amount\n42\n")
    remote: dict[str, bytes] = {}

    def transfer(request: httpx.Request) -> httpx.Response:
        key = request.url.path.rsplit("/workspace/files/", 1)[1]
        if request.method == "PUT":
            remote[key] = base64.b64decode(json.loads(request.content)["content_base64"])
            return httpx.Response(200, json={"path": key})
        assert request.method == "GET"
        return httpx.Response(200, content=remote[key])

    with httpx.Client(base_url="http://fleet.test", transport=httpx.MockTransport(transfer)) as client:
        tui = FleetTextualApp(client, "menu-transfer", SessionState())
        async with tui.run_test(size=(80, 24)) as pilot:
            composer = tui.query_one("#prompt-input", _PromptInput)
            composer.value = "/put qua"
            await pilot.press("tab")
            assert composer.value == "/put 'quarter report.csv'"
            await pilot.press("enter")
            assert remote == {"files/quarter report.csv": b"amount\n42\n"}

            source.unlink()
            composer.value = "/get files/qua"
            await pilot.press("tab")
            assert composer.value == "/get 'files/quarter report.csv'"
            await pilot.press("enter")
            assert source.read_bytes() == b"amount\n42\n"


@pytest.mark.asyncio
async def test_all_commands_wrap_in_both_directions_and_page_keys_scroll(menu_app):
    from textual.widgets import OptionList

    async with menu_app.run_test(size=(80, 24)) as pilot:
        composer = menu_app.query_one("#prompt-input", _PromptInput)
        composer.value = "/"
        await pilot.pause()
        popup = menu_app.query_one("#slash-popup", OptionList)
        visited = []
        for _ in range(popup.option_count):
            option = popup.get_option_at_index(popup.highlighted)
            visited.append(option.prompt.plain.split()[0])
            await pilot.press("down")
        assert visited == [*COMMANDS, "/help", "/exit", "/quit"]
        assert popup.highlighted == 0
        await pilot.press("up", "down", "down", "up")
        assert popup.highlighted == 0
        await pilot.press("pagedown")
        assert popup.highlighted > 0
        assert popup.scroll_y > 0
        await pilot.press("pageup")
        assert popup.highlighted == 0
        assert composer.has_focus


@pytest.mark.asyncio
async def test_multiline_editing_and_editing_recalled_history_preserve_the_new_draft(menu_app):
    async with menu_app.run_test(size=(100, 30)) as pilot:
        composer = menu_app.query_one("#prompt-input", _PromptInput)
        composer.value = "/timeout off"
        await pilot.press("enter")
        composer.value = "first line\nsecond line\nthird line"
        await pilot.press("up")
        assert composer.cursor_location[0] == 1
        assert composer.value == "first line\nsecond line\nthird line"
        await pilot.press("up", "up")
        assert composer.value == "/timeout off"
        await pilot.press("down")
        assert composer.value == "first line\nsecond line\nthird line"

        await pilot.press("up", "up", "up")
        assert composer.value == "/timeout off"
        await pilot.press("backspace")
        assert composer.value == "/timeout of"
        await pilot.press("escape", "down")
        assert composer.value == "/timeout of"
        await pilot.press("up", "down")
        assert composer.value == "/timeout of"


@pytest.mark.asyncio
async def test_nested_mode_selection_dispatches_only_after_the_completed_command_is_submitted(monkeypatch, tmp_path):
    import json

    monkeypatch.chdir(tmp_path)
    submitted: list[dict] = []

    def mode(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT" and request.url.path == "/sessions/menu-mode/mode"
        submitted.append(json.loads(request.content))
        return httpx.Response(200, json={"mode": submitted[-1]["mode"]})

    with httpx.Client(base_url="http://fleet.test", transport=httpx.MockTransport(mode)) as client:
        tui = FleetTextualApp(client, "menu-mode", SessionState())
        async with tui.run_test(size=(80, 24)) as pilot:
            composer = tui.query_one("#prompt-input", _PromptInput)
            composer.value = "/config m"
            await pilot.press("enter")
            assert composer.value == "/config mode "
            await pilot.press("down", "enter")
            assert composer.value == "/config mode flex"
            assert submitted == []
            await pilot.press("enter")
            assert submitted == [{"mode": "flex"}]
            assert tui.state.mode == "flex"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prefix", "burst", "expected"),
    [
        ("/", [("down", None), ("down", None), ("tab", None)], "/artifacts"),
        ("", [("slash", "/"), ("m", "m"), ("o", "o"), ("down", None), ("tab", None)], "/models "),
        ("/mo", [("down", None), ("escape", None)], "/mo"),
    ],
)
async def test_terminal_key_bursts_preserve_input_and_completion_order(menu_app, prefix, burst, expected):
    from textual import events

    async with menu_app.run_test(size=(80, 24)) as pilot:
        composer = menu_app.query_one("#prompt-input", _PromptInput)
        composer.value = "/timeout 120"
        await pilot.press("enter")
        composer.value = prefix
        await pilot.pause()
        # Unlike Pilot.press(), a terminal can deliver multiple keys without waiting for
        # the screen to settle after each one. Submit the same public key events as a burst.
        for key, character in burst:
            menu_app.post_message(events.Key(key, character))
        await pilot.pause()
        assert composer.value == expected
        if burst[-1][0] == "escape":
            assert not menu_app.query_one("#slash-popup").display


@pytest.mark.asyncio
async def test_local_mentions_browse_folders_and_quote_files_without_uploading(menu_app, tmp_path):
    folder = tmp_path / "reports"
    folder.mkdir()
    (folder / "quarter report.csv").write_text("amount\n42\n")
    async with menu_app.run_test(size=(80, 24)) as pilot:
        composer = menu_app.query_one("#prompt-input", _PromptInput)
        composer.value = "Summarize @"
        await pilot.pause()
        assert menu_app.query_one("#slash-popup").display
        assert any(usage == "@./reports/" for _fill, usage, _description in menu_app._popup_items)
        composer.value = "Summarize @rep"
        await pilot.press("down", "tab")
        assert composer.value == "Summarize @./reports/"
        assert menu_app.query_one("#slash-popup").display
        await pilot.press("tab")
        assert composer.value == 'Summarize @"./reports/quarter report.csv" '
        assert not menu_app.query_one("#slash-popup").display


@pytest.mark.asyncio
async def test_local_mentions_keep_typing_inside_quoted_folder_paths(menu_app, tmp_path):
    folder = tmp_path / "my reports"
    folder.mkdir()
    (folder / 'quarter "one".csv').write_text("amount\n42\n")
    async with menu_app.run_test(size=(80, 24)) as pilot:
        composer = menu_app.query_one("#prompt-input", _PromptInput)
        composer.value = "Read @my"
        await pilot.press("down", "tab")
        assert composer.value == 'Read @"./my reports/"'
        await pilot.press("q")
        assert composer.value == 'Read @"./my reports/q"'
        assert menu_app.query_one("#slash-popup").display
        await pilot.press("tab")
        assert composer.value == 'Read @"./my reports/quarter \\"one\\".csv" '


@pytest.mark.asyncio
async def test_local_mentions_complete_at_the_cursor_and_ignore_email_addresses(menu_app, tmp_path):
    (tmp_path / "report.csv").write_text("amount\n42\n")
    async with menu_app.run_test(size=(80, 24)) as pilot:
        composer = menu_app.query_one("#prompt-input", _PromptInput)
        composer.value = "Read @rep and summarize it"
        composer.move_cursor((0, len("Read @rep")))
        await pilot.pause()
        assert menu_app.query_one("#slash-popup").display
        await pilot.press("tab")
        assert composer.value == "Read @./report.csv and summarize it"
        assert composer.cursor_location == (0, len("Read @./report.csv"))
        composer.value = "Contact user@rep"
        await pilot.pause()
        assert not menu_app.query_one("#slash-popup").display
        composer.value = 'Read @"./rep'
        await pilot.press("tab")
        assert composer.value == "Read @./report.csv "


@pytest.mark.asyncio
async def test_mention_menu_tracks_cursor_movement_without_editing(menu_app, tmp_path):
    (tmp_path / "report.csv").write_text("amount\n42\n")
    async with menu_app.run_test(size=(80, 24)) as pilot:
        composer = menu_app.query_one("#prompt-input", _PromptInput)
        composer.value = "Read @rep and summarize"
        await pilot.pause()
        popup = menu_app.query_one("#slash-popup")
        assert not popup.display
        composer.move_cursor((0, len("Read @rep")))
        await pilot.pause()
        assert popup.display
        assert menu_app._popup_items[0][1] == "@./report.csv"
        composer.move_cursor(composer.document.end)
        await pilot.pause()
        assert not popup.display
        assert composer.value == "Read @rep and summarize"
        assert composer.has_focus


@pytest.mark.asyncio
@pytest.mark.parametrize("style", ["relative", "absolute", "home"])
async def test_local_mentions_support_typed_paths_and_scroll_all_files(menu_app, monkeypatch, tmp_path, style):
    folder = tmp_path / "reports"
    folder.mkdir()
    for index in range(12):
        (folder / f"input-{index:02}.csv").write_text("amount\n42\n")
    monkeypatch.setenv("HOME", str(tmp_path))
    prefix = {"relative": "reports/", "absolute": f"{folder}/", "home": "~/reports/"}[style]
    completed_prefix = "./reports/" if style == "relative" else prefix
    async with menu_app.run_test(size=(80, 24)) as pilot:
        composer = menu_app.query_one("#prompt-input", _PromptInput)
        composer.value = f"Read @{prefix}"
        await pilot.pause()
        assert menu_app.query_one("#slash-popup").option_count == 12
        await pilot.press("up", "tab")
        assert composer.value == f"Read @{completed_prefix}input-11.csv "


@pytest.mark.asyncio
async def test_workspace_and_local_mentions_with_the_same_path_stay_distinct(menu_app, tmp_path):
    (tmp_path / "files").mkdir()
    (tmp_path / "files" / "report.csv").write_text("amount\n42\n")
    menu_app.state.workspace_files = ["files/report.csv"]
    async with menu_app.run_test(size=(80, 24)) as pilot:
        composer = menu_app.query_one("#prompt-input", _PromptInput)
        composer.value = "Read @files/rep"
        await pilot.pause()
        assert [usage for _fill, usage, _description in menu_app._popup_items] == [
            "@files/report.csv",
            "@./files/report.csv",
        ]
        await pilot.press("down", "tab")
        assert composer.value == "Read @./files/report.csv "


@pytest.mark.asyncio
async def test_mentions_upload_on_submit_deduplicate_and_rewrite_before_the_turn(monkeypatch, tmp_path):
    import json
    import re

    monkeypatch.chdir(tmp_path)
    (tmp_path / "quarter report.csv").write_bytes(b"first file")
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "quarter report.csv").write_bytes(b"second file")
    uploads = []
    tasks = []

    def respond(request):
        if request.url.path.endswith("/attach"):
            uploads.append(request.content)
            return httpx.Response(200, json={"rel_path": f"files/upload-{len(uploads)}.csv"})
        assert request.url.path.endswith("/turn")
        tasks.append(json.loads(request.content)["task"])
        return httpx.Response(200, text='data: {"event":"done"}\n\n')

    with httpx.Client(base_url="http://fleet.test", transport=httpx.MockTransport(respond)) as client:
        tui = FleetTextualApp(client, "mentions", SessionState(workspace_files=["files/existing.csv"]))
        async with tui.run_test(size=(80, 24)) as pilot:
            composer = tui.query_one("#prompt-input", _PromptInput)
            composer.value = (
                'Compare @"./quarter report.csv" @"./other/quarter report.csv" '
                '@"./quarter report.csv" @files/existing.csv'
            )
            await pilot.press("enter")
            await _wait_until_idle(tui, pilot)
            assert len(uploads) == 2
            assert b"first file" in uploads[0] and b"second file" in uploads[1]
            names = [re.search(rb'filename="([^"]+)"', upload).group(1) for upload in uploads]
            assert names[0] != names[1]
            assert tasks == ["Compare @files/upload-1.csv @files/upload-2.csv @files/upload-1.csv @files/existing.csv"]
            assert tui.state.workspace_files == ["files/existing.csv", "files/upload-1.csv", "files/upload-2.csv"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "directory", "http", "unclosed_quote"])
async def test_attachment_failures_keep_the_draft_and_never_start_a_turn(monkeypatch, tmp_path, failure):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "report.csv").write_text("amount\n42\n")
    (tmp_path / "folder").mkdir()
    requested = []

    def respond(request):
        requested.append(request.url.path)
        assert request.url.path.endswith("/attach")
        return httpx.Response(413, json={"detail": "Attachment exceeds the configured size limit"})

    text = {
        "missing": "Read @./missing.csv",
        "directory": "Read @./folder/",
        "http": "Read @./report.csv",
        "unclosed_quote": 'Read @"./report.csv',
    }[failure]
    with httpx.Client(base_url="http://fleet.test", transport=httpx.MockTransport(respond)) as client:
        tui = FleetTextualApp(client, "mention-failure", SessionState())
        async with tui.run_test(size=(80, 24)) as pilot:
            composer = tui.query_one("#prompt-input", _PromptInput)
            composer.value = text
            await pilot.press("escape", "enter")
            await _wait_until_idle(tui, pilot)
            assert composer.value == text
            assert len(requested) == (1 if failure == "http" else 0)


@pytest.mark.asyncio
async def test_cancellation_during_an_attachment_does_not_send_a_turn(monkeypatch, tmp_path):
    import threading

    monkeypatch.chdir(tmp_path)
    (tmp_path / "report.csv").write_bytes(b"amount\n42\n")
    uploading = threading.Event()
    release = threading.Event()
    requested = []

    def respond(request):
        requested.append(request.url.path)
        assert request.url.path.endswith("/attach")
        uploading.set()
        assert release.wait(5)
        return httpx.Response(200, json={"rel_path": "files/report.csv"})

    with httpx.Client(base_url="http://fleet.test", transport=httpx.MockTransport(respond)) as client:
        tui = FleetTextualApp(client, "mention-cancel", SessionState())
        async with tui.run_test(size=(80, 24)) as pilot:
            composer = tui.query_one("#prompt-input", _PromptInput)
            composer.value = "Read @./report.csv"
            try:
                await pilot.press("enter")
                for _ in range(100):
                    if uploading.is_set():
                        break
                    await pilot.pause(0.01)
                assert uploading.is_set()
                await pilot.press("ctrl+c")
                assert tui._cancel_requested
            finally:
                release.set()
            await _wait_until_idle(tui, pilot)
            assert requested == ["/sessions/mention-cancel/attach"]
            assert composer.value == "Read @./report.csv"


@pytest.mark.asyncio
async def test_enter_keeps_exact_workspace_paths_even_when_not_first(menu_app, monkeypatch):
    submitted = []
    monkeypatch.setattr(menu_app, "submit_text", submitted.append)
    menu_app.state.workspace_files = ["files/report-old.csv", "files/report"]
    async with menu_app.run_test(size=(80, 24)) as pilot:
        composer = menu_app.query_one("#prompt-input", _PromptInput)
        composer.value = "/cat files/report"
        await pilot.press("enter")
        assert submitted == ["/cat files/report"]


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["/put", "/get"])
async def test_enter_keeps_free_form_transfer_destinations(menu_app, monkeypatch, tmp_path, command):
    submitted = []
    monkeypatch.setattr(menu_app, "submit_text", submitted.append)
    menu_app.state.workspace_files = ["files/report-old.csv"]
    (tmp_path / "report-old.csv").write_bytes(b"existing file")
    text = f"{command} source.csv " + ("files/report" if command == "/put" else "report")
    async with menu_app.run_test(size=(80, 24)) as pilot:
        composer = menu_app.query_one("#prompt-input", _PromptInput)
        composer.value = text
        await pilot.press("enter")
        assert submitted == [text]


@pytest.fixture
def git_mention_project(monkeypatch, tmp_path):
    import shutil
    import subprocess

    if shutil.which("git") is None:
        pytest.skip("Git is required for Git ignore contract tests")
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True, capture_output=True)
    folder = tmp_path / "src" / "fleet_rlm"
    (folder / "nested").mkdir(parents=True)
    (folder / "__init__.py").write_text("root source")
    (folder / "nested" / "settings.py").write_text("nested source")
    (folder / "ignored").mkdir()
    (folder / "ignored" / "secret.py").write_text("ignored source")
    (folder / "tracked.tmp").write_text("tracked but ignored")
    (folder / ".hidden.py").write_text("hidden source")
    (folder / ".private").mkdir()
    (folder / ".private" / "secret.py").write_text("hidden subtree")
    (folder / "nested" / ".gitignore").write_text("private.py\n")
    (folder / "nested" / "private.py").write_text("nested ignored")
    (folder / "alias.py").symlink_to(folder / "__init__.py")
    (folder / "alias-dir").symlink_to(folder / "nested", target_is_directory=True)
    (tmp_path / ".gitignore").write_text("ignored/\n*.tmp\n")
    subprocess.run(["git", "add", "-f", "src/fleet_rlm/tracked.tmp"], check=True, capture_output=True)
    return folder


@pytest.mark.asyncio
@pytest.mark.usefixtures("git_mention_project")
async def test_bare_name_suggests_nested_directories_and_files(menu_app):
    async with menu_app.run_test(size=(80, 24)) as pilot:
        composer = menu_app.query_one("#prompt-input", _PromptInput)
        composer.value = "Review @fleet_rlm"
        await pilot.pause()
        assert [usage for _fill, usage, _description in menu_app._popup_items] == [
            "@./src/fleet_rlm",
            "@./src/fleet_rlm/",
        ]
        await pilot.press("tab")
        assert composer.value == "Review @./src/fleet_rlm "
        assert not menu_app.query_one("#slash-popup").display
        composer.value = "Read @settings"
        await pilot.press("tab")
        assert composer.value == "Read @./src/fleet_rlm/nested/settings.py "
        composer.value = "Read @private"
        await pilot.pause()
        assert not menu_app.query_one("#slash-popup").display


@pytest.mark.asyncio
@pytest.mark.usefixtures("git_mention_project")
async def test_local_directory_mentions_complete_during_a_terminal_key_burst(menu_app):
    from textual import events

    async with menu_app.run_test(size=(80, 24)) as pilot:
        for character in "review @fleet_rlm":
            key = {" ": "space", "@": "at", "_": "underscore"}.get(character, character)
            menu_app.post_message(events.Key(key, character))
        menu_app.post_message(events.Key("tab", None))
        await pilot.pause()
        composer = menu_app.query_one("#prompt-input", _PromptInput)
        assert composer.value == "review @./src/fleet_rlm "
        assert composer.has_focus
        assert menu_app.state.workspace_files == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("git_mention_project")
@pytest.mark.parametrize("selection", ["tab", "enter", "mouse"])
async def test_selected_directory_uploads_recursively_before_starting_the_turn(selection):
    import base64
    import json

    uploaded = {}
    tasks = []

    def respond(request):
        if request.method == "PUT":
            path = request.url.path.split("/workspace/files/", 1)[1]
            uploaded[path] = base64.b64decode(json.loads(request.content)["content_base64"])
            return httpx.Response(200, json={"path": path})
        assert request.url.path.endswith("/turn")
        tasks.append(json.loads(request.content)["task"])
        return httpx.Response(200, text='data: {"event":"done"}\n\n')

    with httpx.Client(base_url="http://fleet.test", transport=httpx.MockTransport(respond)) as client:
        tui = FleetTextualApp(client, "directory-mention", SessionState())
        async with tui.run_test(size=(80, 24)) as pilot:
            composer = tui.query_one("#prompt-input", _PromptInput)
            composer.value = "Review @fleet_rlm"
            await pilot.pause()
            if selection == "mouse":
                await pilot.click("#slash-popup", offset=(2, 1))
            else:
                await pilot.press(selection)
            assert composer.value == "Review @./src/fleet_rlm "
            assert uploaded == {} and tasks == []
            await pilot.press("enter")
            await _wait_until_idle(tui, pilot)
            root = next(iter(uploaded)).split("/fleet_rlm/", 1)[0] + "/fleet_rlm"
            assert uploaded == {f"{root}/__init__.py": b"root source", f"{root}/nested/settings.py": b"nested source"}
            assert tasks == [f"Review @{root}"]
            assert set(tui.state.workspace_files) == set(uploaded)


@pytest.mark.asyncio
@pytest.mark.usefixtures("git_mention_project")
async def test_partial_directory_upload_failure_keeps_draft_and_never_starts_a_turn():
    requested = []

    def respond(request):
        assert request.method == "PUT" and "/workspace/files/" in request.url.path
        requested.append(request.url.path)
        if len(requested) == 2:
            return httpx.Response(413, json={"detail": "Directory file too large"})
        return httpx.Response(200, json={"path": request.url.path.split("/workspace/files/", 1)[1]})

    with httpx.Client(base_url="http://fleet.test", transport=httpx.MockTransport(respond)) as client:
        tui = FleetTextualApp(client, "directory-failure", SessionState())
        async with tui.run_test(size=(80, 24)) as pilot:
            composer = tui.query_one("#prompt-input", _PromptInput)
            composer.value = "Review @./src/fleet_rlm"
            await pilot.press("enter")
            await _wait_until_idle(tui, pilot)
            assert composer.value == "Review @./src/fleet_rlm"
            assert len(requested) == 2
            assert len(tui.state.workspace_files) == 1


@pytest.mark.parametrize("mention", ["src/fleet_rlm", "./src/fleet_rlm", "./src/fleet_rlm/"])
@pytest.mark.usefixtures("git_mention_project")
def test_directory_mentions_preserve_structure_and_deduplicate_overlaps(mention):
    from fleet_rlm.tui.commands import Context

    uploaded = []

    def respond(request):
        assert request.method == "PUT"
        uploaded.append(request.url.path.split("/workspace/files/", 1)[1])
        return httpx.Response(200, json={"path": uploaded[-1]})

    text = f"Review @{mention} @./src/fleet_rlm/nested @./src/fleet_rlm/__init__.py @{mention}"
    with httpx.Client(base_url="http://fleet.test", transport=httpx.MockTransport(respond)) as client:
        ctx = Context(client, "directory-overlap", SessionState())
        rewritten = mentions.attach_local_mentions(ctx, text, cancelled=lambda: False)
        root = uploaded[0].split("/fleet_rlm/", 1)[0] + "/fleet_rlm"
        assert rewritten == f"Review @{root} @{root}/nested @{root}/__init__.py @{root}"
        assert len(uploaded) == len(set(uploaded)) == 2


@pytest.mark.parametrize("limit", ["files", "bytes"])
@pytest.mark.usefixtures("git_mention_project")
def test_directory_limits_refuse_before_upload_instead_of_truncating(monkeypatch, limit):
    from fleet_rlm.tui.commands import Context

    monkeypatch.setattr(mentions, "_MAX_DIRECTORY_FILES" if limit == "files" else "_MAX_DIRECTORY_BYTES", 1)

    def unexpected(request):
        pytest.fail(f"Unexpected upload: {request.url.path}")

    with (
        httpx.Client(base_url="http://fleet.test", transport=httpx.MockTransport(unexpected)) as client,
        pytest.raises(ValueError, match="limited to"),
    ):
        mentions.attach_local_mentions(Context(client, "limit"), "Read @src/fleet_rlm", cancelled=lambda: False)


@pytest.mark.usefixtures("git_mention_project")
def test_cancelling_directory_upload_skips_remaining_files():
    from fleet_rlm.tui.commands import Context

    uploaded = []

    def respond(request):
        uploaded.append(request.url.path)
        return httpx.Response(200, json={"path": request.url.path.split("/workspace/files/", 1)[1]})

    with httpx.Client(base_url="http://fleet.test", transport=httpx.MockTransport(respond)) as client:
        ctx = Context(client, "directory-cancel")
        text = "Read @src/fleet_rlm"
        assert mentions.attach_local_mentions(ctx, text, cancelled=lambda: bool(uploaded)) == text
        assert len(uploaded) == len(ctx.state.workspace_files) == 1


@pytest.mark.usefixtures("git_mention_project")
def test_directory_discovery_respects_gitignore_negations(tmp_path):
    folder = tmp_path / "src" / "fleet_rlm"
    (folder / "keep.tmp").write_text("explicitly unignored")
    (tmp_path / ".gitignore").write_text("ignored/\n*.tmp\n!src/fleet_rlm/keep.tmp\n")
    assert {path.as_posix() for path in local_paths.iter_local_files(folder)} == {
        "__init__.py",
        "nested/settings.py",
        "keep.tmp",
    }


@pytest.mark.usefixtures("git_mention_project")
def test_directory_upload_fails_closed_when_git_cannot_inspect_ignores(monkeypatch):
    from fleet_rlm.tui.commands import Context

    def no_git(*_args, **_kwargs):
        raise FileNotFoundError("git is unavailable")

    def unexpected(request):
        pytest.fail(f"Unexpected upload: {request.url.path}")

    monkeypatch.setattr(local_paths.subprocess, "run", no_git)
    with (
        httpx.Client(base_url="http://fleet.test", transport=httpx.MockTransport(unexpected)) as client,
        pytest.raises(ValueError, match="Git is required"),
    ):
        mentions.attach_local_mentions(Context(client, "no-git"), "Read @src/fleet_rlm", cancelled=lambda: False)


def test_directory_upload_outside_git_skips_hidden_entries_and_symlinks(monkeypatch, tmp_path):
    import base64
    import json

    from fleet_rlm.tui.commands import Context

    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "source code"
    (folder / "nested").mkdir(parents=True)
    (folder / "main.py").write_text("root")
    (folder / "nested" / "main.py").write_text("nested")
    (folder / ".hidden").write_text("private")
    (folder / "link").symlink_to(folder / "nested", target_is_directory=True)
    uploaded = {}

    def respond(request):
        path = request.url.path.split("/workspace/files/", 1)[1]
        uploaded[path] = base64.b64decode(json.loads(request.content)["content_base64"])
        return httpx.Response(200, json={"path": path})

    with httpx.Client(base_url="http://fleet.test", transport=httpx.MockTransport(respond)) as client:
        rewritten = mentions.attach_local_mentions(
            Context(client, "no-repo"), 'Read @"./source code"', cancelled=lambda: False
        )
        root = next(iter(uploaded)).rsplit("/main.py", 1)[0]
        assert uploaded == {f"{root}/main.py": b"root", f"{root}/nested/main.py": b"nested"}
        assert rewritten == f'Read @"{root}"'
