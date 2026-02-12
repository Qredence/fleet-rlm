from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("reportlab") is None,
    reason="reportlab is required for PDF builder tests",
)


def _load_pdf_builder_module():
    root = Path(__file__).resolve().parents[2]
    target = root / "tmp" / "pdfs" / "build_fleet_summary_pdf.py"
    spec = importlib.util.spec_from_file_location("build_fleet_summary_pdf", target)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module from {target}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeCanvas:
    def __init__(self, out_path: str, pagesize):
        self.out_path = out_path
        self.pagesize = pagesize
        self.calls: list[tuple] = []

    def setFont(self, font: str, size: int) -> None:
        self.calls.append(("setFont", font, size))

    def drawString(self, x: float, y: float, text: str) -> None:
        self.calls.append(("drawString", x, y, text))

    def drawRightString(self, x: float, y: float, text: str) -> None:
        self.calls.append(("drawRightString", x, y, text))

    def showPage(self) -> None:
        self.calls.append(("showPage",))

    def save(self) -> None:
        self.calls.append(("save",))


def test_wrap_text_breaks_lines_to_max_width(monkeypatch):
    mod = _load_pdf_builder_module()
    monkeypatch.setattr(mod, "stringWidth", lambda text, font, size: len(text))

    lines = mod.wrap_text("alpha beta gamma delta", "Helvetica", 10, max_width=11)
    assert lines == ["alpha beta", "gamma delta"]


def test_draw_paragraph_writes_lines_and_returns_final_y(monkeypatch):
    mod = _load_pdf_builder_module()
    fake = _FakeCanvas("x.pdf", mod.letter)
    monkeypatch.setattr(
        mod,
        "wrap_text",
        lambda text, font, size, max_width: ["line one", "line two", "line three"],
    )

    start_y = 500.0
    end_y = mod.draw_paragraph(fake, start_y, "ignored")

    draw_calls = [c for c in fake.calls if c[0] == "drawString"]
    assert len(draw_calls) == 3
    assert draw_calls[0][2] == start_y
    expected_delta = 3 * (mod.BODY_SIZE + mod.LINE_GAP)
    assert end_y == start_y - expected_delta


def test_draw_bullets_draws_marker_once_per_bullet(monkeypatch):
    mod = _load_pdf_builder_module()
    fake = _FakeCanvas("x.pdf", mod.letter)

    lines_by_text = {
        "first bullet": ["first line", "second line"],
        "second bullet": ["only line"],
    }
    monkeypatch.setattr(
        mod,
        "wrap_text",
        lambda text, font, size, max_width: lines_by_text[text],
    )

    mod.draw_bullets(fake, 400.0, ["first bullet", "second bullet"])
    draws = [c for c in fake.calls if c[0] == "drawString"]
    bullet_markers = [c for c in draws if c[3] == "-"]
    assert len(bullet_markers) == 2


def test_main_builds_pdf_with_expected_canvas_calls(monkeypatch):
    mod = _load_pdf_builder_module()
    created: list[_FakeCanvas] = []

    def _canvas_factory(out_path: str, pagesize):
        instance = _FakeCanvas(out_path, pagesize)
        created.append(instance)
        return instance

    monkeypatch.setattr(mod.canvas, "Canvas", _canvas_factory)
    mod.main()

    assert created, "Expected main() to instantiate a canvas"
    fake = created[0]
    assert fake.out_path == mod.OUT_PATH
    assert ("showPage",) in fake.calls
    assert ("save",) in fake.calls
    assert any(
        call[0] == "drawString" and "fleet-rlm: One-Page App Summary" in call[3]
        for call in fake.calls
    )
