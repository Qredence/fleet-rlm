"""The terminal setup checklist. All configuration and provider work goes through FastAPI."""

from __future__ import annotations

import json
import sys
import uuid
from collections.abc import Callable
from contextlib import suppress
from typing import Any, ClassVar

import httpx
from rich.console import Console
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Collapsible, Input, Label, RichLog, Select, Static


class ConfirmAction(ModalScreen[bool]):
    """Confirm an explicit provider mutation or potentially billed test."""

    DEFAULT_CSS = """
    ConfirmAction { align: center middle; background: $background 75%; }
    #confirmation { width: 65; height: auto; padding: 2; border: round $accent; }
    #confirmation Horizontal { height: auto; }
    """

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        """Render the operation description and explicit continue/cancel buttons."""
        with VerticalScroll(id="confirmation"):
            yield Static(self.message, markup=False)
            with Horizontal():
                yield Button("Continue", variant="primary", id="confirm")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Return the user's explicit action selection."""
        self.dismiss(event.button.id == "confirm")


def request(client: httpx.Client, method: str, path: str, **kwargs: Any) -> Any:
    """Call a setup endpoint and retain only its sanitized response."""
    response = client.request(method, path, **kwargs)
    response.raise_for_status()
    return response.json()


def operation_events(client: httpx.Client, operation_id: str, emit: Callable[[dict[str, Any]], None]) -> bool:
    """Read progress without owning or cancelling the server operation."""
    with client.stream("GET", f"/setup/operations/{operation_id}/events") as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if line.startswith("data:"):
                event = json.loads(line[5:].strip())
                emit(event)
                if event["event"] in {"complete", "error"}:
                    return event["event"] == "complete"
    return False


class SetupApp(App[bool]):
    """A responsive checklist backed by the private setup API."""

    CSS = """
    Screen { background: $surface; }
    #checklist { padding: 1 2; }
    #heading { text-style: bold; margin-bottom: 1; }
    .field-label { margin-top: 1; }
    Input { margin-bottom: 1; }
    #actions { height: 6; padding: 0 2; grid-size: 3; grid-gutter: 0 1; }
    #actions Button { width: 1fr; min-width: 10; }
    #progress { height: 5; margin: 0 2; border: round $primary; }
    #notice { margin: 0 2; height: auto; }
    Collapsible { margin-bottom: 1; }
    """
    BINDINGS: ClassVar = [
        ("ctrl+q", "leave", "Exit setup"),
        Binding("alt+b", "press_setup('build')", "Build snapshot", priority=True),
        Binding("alt+a", "press_setup('activate')", "Activate snapshot", priority=True),
        Binding("alt+v", "press_setup('verify')", "Verify", priority=True),
        Binding("alt+s", "press_setup('save')", "Save settings", priority=True),
        Binding("alt+enter", "press_setup('apply')", "Start / restart", priority=True),
    ]

    def __init__(self, client: httpx.Client, status: dict[str, Any], *, automatic: bool = False) -> None:
        super().__init__()
        self.client = client
        self.status = status
        self._working = False
        self._operation_id: str | None = None
        self._operation_path: str | None = None
        self.automatic = automatic

    def compose(self) -> ComposeResult:
        """Render four sections, masked inputs, service actions, and progress."""
        with VerticalScroll(id="checklist"):
            yield Static("Fleet setup", id="heading")
            yield Static(
                "Check access, complete missing settings, then start Fleet. Model inference testing is optional."
            )
            for section, title in (
                ("models", "Models"),
                ("database", "Database"),
                ("mlflow", "MLflow"),
                ("daytona", "Daytona"),
            ):
                check = self.status["checks"].get(section, {})
                with Collapsible(
                    title=title, collapsed=check.get("status") in {"ready", "warning"}, id=f"section-{section}"
                ):
                    yield Static(check.get("message", "Not checked yet"), id=f"check-{section}", markup=False)
                    for field in self.status["fields"]:
                        if field["section"] != section:
                            continue
                        label = field["name"].removeprefix("FLEET_").replace("_", " ").title()
                        yield Label(f"{label} · {field['source']}", classes="field-label", id=f"label-{field['name']}")
                        yield Input(
                            value="" if field["secret"] else field["value"] or "",
                            password=field["secret"],
                            placeholder="Configured (leave blank to keep)"
                            if field["secret"] and field["configured"]
                            else "",
                            disabled=not field["editable"],
                            id=field["name"],
                        )
                    if section == "models":
                        yield Select([], prompt="Choose an available root model", id="root-model-choice")
                        yield Select([], prompt="Choose an available sub model", id="sub-model-choice")
                        yield Button("Test root model (may incur cost)", id="model-test")
                    if section == "daytona":
                        yield Button("Find Volumes", id="list-volumes")
                        yield Select([], prompt="Select an existing workspace Volume", id="volumes")
                        yield Input(placeholder="New Volume name", value="fleet-workspace", id="volume-name")
                        yield Button("Create Volume", id="create-volume")
                        yield Button("Build snapshot · Alt+B", id="build")
                        yield Button("Activate snapshot · Alt+A", id="activate")
                    yield Button("Verify this section", id=f"verify-{section}")
        yield Static("", id="notice", markup=False)
        yield RichLog(id="progress", wrap=True, markup=False, max_lines=1000)
        with Grid(id="actions"):
            yield Button("Save .env", id="save")
            yield Button("Verify", id="verify")
            yield Button("Resume progress", id="reconnect")
            yield Button("Start / Restart", id="apply", variant="primary")
            yield Button("Back / Exit", id="leave")
            yield Button("Discard edits", id="discard")

    def on_mount(self) -> None:
        """Explain the save location and initiate readiness checks."""
        self._refresh(self.status)

        def initialize() -> None:
            if self.status.get("restart_required"):
                self.call_from_thread(self._log, "A restart was already accepted. Choose Start / Restart to continue.")
                return
            self._operation("/setup/verify")
            if (
                self.automatic
                and self.status["ready"]
                and all(check["status"] != "warning" for check in self.status["checks"].values())
            ):
                request(self.client, "POST", "/setup/apply")
                self.call_from_thread(self.exit, True)

        self._run(initialize)

    def _refresh(self, status: dict[str, Any]) -> None:
        self.status = status
        for field in status["fields"]:
            label = field["name"].removeprefix("FLEET_").replace("_", " ").title()
            self.query_one(f"#label-{field['name']}", Label).update(f"{label} · {field['source']}")
        for section, check in status["checks"].items():
            self.query_one(f"#check-{section}", Static).update(check["message"])
            block = self.query_one(f"#section-{section}", Collapsible)
            block.title = f"{section.title()} · {check['status']}"
            block.collapsed = check["status"] == "ready"
        ready = sum(check["status"] == "ready" for check in status["checks"].values())
        self.query_one("#heading", Static).update(f"Fleet setup · {ready}/4 sections ready")
        self._update_controls()
        available = status["checks"].get("models", {}).get("models", [])
        for role in ("root", "sub"):
            choice = self.query_one(f"#{role}-model-choice", Select)
            choice.set_options([(model, model) for model in available])
            field = next((field for field in status["fields"] if field["name"] == f"FLEET_{role.upper()}_MODEL"), None)
            choice.disabled = self._working or not available or field is None or not field["editable"]

    def _update_controls(self) -> None:
        status = self.status
        message = f"Save destination: {status['env_path']}. " + " ".join(status["errors"])
        if status["dirty"]:
            message += " Unsaved changes. Save and verify before starting."
        client_dirty = any(
            field["editable"]
            and (
                bool(self.query_one(f"#{field['name']}", Input).value)
                if field["secret"]
                else self.query_one(f"#{field['name']}", Input).value != field["value"]
            )
            for field in status["fields"]
        )
        if client_dirty:
            message += " Save your edited fields before verifying or applying."
        self.query_one("#notice", Static).update(message)
        self.query_one("#apply", Button).disabled = not status["ready"] or self._working or client_dirty
        state = status["checks"].get("daytona", {}).get("state")
        blocked = self._working or client_dirty or status["dirty"] or status.get("restart_required", False)
        pending = status.get("restart_required", False)
        self.query_one("#save", Button).disabled = self._working or pending
        self.query_one("#discard", Button).disabled = self._working or pending
        if pending:
            for widget in self.query(Input):
                widget.disabled = True
        self.query_one("#build", Button).disabled = blocked or state != "missing"
        self.query_one("#activate", Button).disabled = blocked or state != "inactive"
        for action in (
            "verify",
            "list-volumes",
            "create-volume",
            "model-test",
            *(f"verify-{s}" for s in ("models", "database", "mlflow", "daytona")),
        ):
            self.query_one(f"#{action}", Button).disabled = blocked

    def _log(self, message: str) -> None:
        self.query_one("#progress", RichLog).write(Text(message))

    def _run(self, action: Callable[[], None]) -> None:
        if self._working:
            return
        self._working = True
        for button in self.query(Button):
            button.disabled = True
        for widget in self.query(Input):
            widget.disabled = True

        def worker() -> None:
            try:
                action()
            except httpx.HTTPStatusError as exc:
                try:
                    detail = exc.response.json().get("detail", "Setup request failed")
                except ValueError:
                    detail = "Setup request failed"
                # Validation errors may echo request inputs; only display deliberate string details.
                self.call_from_thread(self._log, detail if isinstance(detail, str) else "Check the submitted fields.")
            except Exception as exc:
                self.call_from_thread(
                    self._log,
                    f"Connection or operation failed ({type(exc).__name__}). Reconnect to progress before retrying.",
                )
            finally:

                def finish() -> None:
                    self._working = False
                    for button in self.query(Button):
                        button.disabled = False
                    for field in self.status["fields"]:
                        self.query_one(f"#{field['name']}", Input).disabled = not field["editable"]
                    self.query_one("#volume-name", Input).disabled = False
                    self._refresh(self.status)

                with suppress(RuntimeError):
                    self.call_from_thread(finish)

        self.run_worker(worker, thread=True, exit_on_error=False)

    def _reload(self) -> None:
        self.call_from_thread(self._refresh, request(self.client, "GET", "/setup/status"))

    def _event(self, event: dict[str, Any]) -> None:
        self.call_from_thread(self._log, event["message"])
        if event["event"] == "complete" and self._operation_path == "/setup/daytona/volumes":
            result = event.get("result")
            if isinstance(result, dict) and "id" in result and "name" in result:
                field = next(field for field in self.status["fields"] if field["name"] == "FLEET_DAYTONA_VOLUME_ID")
                if not field["editable"] or field["value"] == result["id"]:
                    return
                request(
                    self.client, "PATCH", "/setup/config", json={"values": {"FLEET_DAYTONA_VOLUME_ID": result["id"]}}
                )
                self.call_from_thread(setattr, self.query_one("#FLEET_DAYTONA_VOLUME_ID", Input), "value", result["id"])

    def _operation(self, path: str, **values: Any) -> None:
        self._operation_path = path
        data = request(self.client, "POST", path, json={"request_id": uuid.uuid4().hex, **values})
        self._operation_id = data["operation_id"]
        operation_events(self.client, self._operation_id, self._event)
        self._reload()
        if path in {"/setup/daytona/snapshot/build", "/setup/daytona/snapshot/activate"}:
            self._operation("/setup/verify", sections=["daytona"])

    def _save(self, values: dict[str, str]) -> None:
        if values:
            request(self.client, "PATCH", "/setup/config", json={"values": values})
        request(self.client, "POST", "/setup/save")

        def clear_secrets() -> None:
            for field in self.status["fields"]:
                if field["secret"]:
                    self.query_one(f"#{field['name']}", Input).value = ""

        self.call_from_thread(clear_secrets)
        self.call_from_thread(self._log, "Saved settings. Verify to check the effective configuration.")
        self._reload()

    def on_select_changed(self, event: Select.Changed) -> None:
        """Stage the selected existing Volume in the editable field."""
        if event.select.id == "volumes" and event.value is not Select.BLANK:
            self.query_one("#FLEET_DAYTONA_VOLUME_ID", Input).value = str(event.value)
            self.query_one("#apply", Button).disabled = True
        elif event.select.id in {"root-model-choice", "sub-model-choice"} and event.value is not Select.BLANK:
            role = "ROOT" if event.select.id == "root-model-choice" else "SUB"
            self.query_one(f"#FLEET_{role}_MODEL", Input).value = str(event.value)

    def on_input_changed(self, _event: Input.Changed) -> None:
        """Require saving and rechecking after edits."""
        if self.query("#apply"):
            self._update_controls()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Dispatch API calls from user actions without blocking the UI."""
        action = event.button.id
        if action == "leave":
            self.action_leave()
        elif action == "discard":

            def discard() -> None:
                status = request(self.client, "DELETE", "/setup/config")

                def reload_fields() -> None:
                    for field in status["fields"]:
                        self.query_one(f"#{field['name']}", Input).value = (
                            "" if field["secret"] else field["value"] or ""
                        )
                    self._refresh(status)

                self.call_from_thread(reload_fields)

            self._run(discard)
        elif action == "save":
            values = {}
            for field in self.status["fields"]:
                value = self.query_one(f"#{field['name']}", Input).value
                if field["editable"] and (
                    (field["secret"] and value) or (not field["secret"] and value != field["value"])
                ):
                    values[field["name"]] = value
            self._run(lambda: self._save(values))
        elif action == "verify":
            self._run(lambda: self._operation("/setup/verify"))
        elif action and action.startswith("verify-"):
            self._run(lambda: self._operation("/setup/verify", sections=[action.removeprefix("verify-")]))
        elif action == "reconnect" and self._operation_id:

            def reconnect() -> None:
                operation_events(self.client, self._operation_id or "", self._event)
                self._reload()

            self._run(reconnect)
        elif action == "list-volumes":

            def load_volumes() -> None:
                data = request(self.client, "GET", "/setup/daytona/volumes")
                self.call_from_thread(
                    self.query_one("#volumes", Select).set_options,
                    [(f"{v['name']} · {v['state']}", v["id"]) for v in data],
                )

            self._run(load_volumes)
        elif action == "apply":

            def apply() -> None:
                request(self.client, "POST", "/setup/apply")
                self.call_from_thread(self.exit, True)

            self._run(apply)
        elif action in {"build", "activate", "create-volume", "model-test"}:
            path = {
                "build": "/setup/daytona/snapshot/build",
                "activate": "/setup/daytona/snapshot/activate",
                "create-volume": "/setup/daytona/volumes",
                "model-test": "/setup/models/test",
            }[action]
            values = {"name": self.query_one("#volume-name", Input).value} if action == "create-volume" else {}
            message = {
                "build": (
                    f"Build {self.status['snapshot_name']}? This creates provider resources and "
                    "may incur charges. An interrupted wait does not cancel the remote build."
                ),
                "activate": f"Activate {self.status['snapshot_name']}? This changes provider state.",
                "create-volume": (
                    f"Create workspace Volume {values.get('name', '')!r}? Provider storage charges may apply."
                ),
                "model-test": (
                    "Send one small request to the configured root model? Provider inference charges may apply."
                ),
            }[action]

            def confirmed(yes: bool | None) -> None:
                if yes:
                    self._run(lambda: self._operation(path, **values))

            self.push_screen(ConfirmAction(message), confirmed)

    def action_leave(self) -> None:
        """Leave setup without cancelling a provider operation or discarding saved files."""
        if self._working:
            self._log(
                "An operation is running. Wait for its result before leaving; "
                "remote work may continue after disconnect."
            )
            return
        self.exit(False)

    def action_press_setup(self, button_id: str) -> None:
        """Offer keyboard access to setup actions even in a small terminal."""
        if len(self.screen_stack) == 1:
            button = self.query_one(f"#{button_id}", Button)
            if not button.disabled:
                button.press()


def run_setup(client: httpx.Client, *, automatic: bool = True, console: Console | None = None) -> bool:
    """Check via FastAPI, skipping the checklist on healthy launches; never prompt on piped input."""
    output = console or Console()
    status = request(client, "GET", "/setup/status")
    if sys.stdin.isatty() and output.is_terminal:
        return bool(SetupApp(client, status, automatic=automatic).run())
    if automatic:
        action = request(client, "POST", "/setup/verify", json={"request_id": uuid.uuid4().hex})
        operation_events(client, action["operation_id"], lambda _event: None)
        status = request(client, "GET", "/setup/status")
        if status["ready"] and all(c["status"] != "warning" for c in status["checks"].values()):
            request(client, "POST", "/setup/apply")
            return True
    output.print("Fleet setup needs attention. Run `fleet cli` in an interactive terminal.")
    for section, check in status["checks"].items():
        output.print(f"{section}: {check['message']}", markup=False)
    for error in status["errors"]:
        output.print(error, markup=False)
    return False
