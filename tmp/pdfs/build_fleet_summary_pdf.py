from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from reportlab.pdfbase.pdfmetrics import stringWidth

OUT_PATH = "output/pdf/fleet-rlm-app-summary.pdf"

PAGE_W, PAGE_H = letter
MARGIN = 0.65 * inch
CONTENT_W = PAGE_W - (2 * MARGIN)

TITLE_FONT = "Helvetica-Bold"
HEAD_FONT = "Helvetica-Bold"
BODY_FONT = "Helvetica"

TITLE_SIZE = 18
HEAD_SIZE = 11
BODY_SIZE = 9
LINE_GAP = 2
SECTION_GAP = 8
BULLET_INDENT = 10
TEXT_INDENT = 18


def wrap_text(text: str, font: str, size: int, max_width: float):
    words = text.split()
    lines = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if stringWidth(candidate, font, size) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def draw_heading(c: canvas.Canvas, y: float, heading: str) -> float:
    c.setFont(HEAD_FONT, HEAD_SIZE)
    c.drawString(MARGIN, y, heading)
    return y - (HEAD_SIZE + 2)


def draw_paragraph(c: canvas.Canvas, y: float, text: str) -> float:
    c.setFont(BODY_FONT, BODY_SIZE)
    lines = wrap_text(text, BODY_FONT, BODY_SIZE, CONTENT_W)
    for line in lines:
        c.drawString(MARGIN, y, line)
        y -= (BODY_SIZE + LINE_GAP)
    return y


def draw_bullets(c: canvas.Canvas, y: float, bullets: list[str]) -> float:
    c.setFont(BODY_FONT, BODY_SIZE)
    bullet_width = CONTENT_W - TEXT_INDENT
    for bullet in bullets:
        wrapped = wrap_text(bullet, BODY_FONT, BODY_SIZE, bullet_width)
        for i, line in enumerate(wrapped):
            if i == 0:
                c.drawString(MARGIN + BULLET_INDENT, y, "-")
                c.drawString(MARGIN + TEXT_INDENT, y, line)
            else:
                c.drawString(MARGIN + TEXT_INDENT, y, line)
            y -= (BODY_SIZE + LINE_GAP)
        y -= 1
    return y


def main():
    c = canvas.Canvas(OUT_PATH, pagesize=letter)

    y = PAGE_H - MARGIN
    c.setFont(TITLE_FONT, TITLE_SIZE)
    c.drawString(MARGIN, y, "fleet-rlm: One-Page App Summary")
    y -= (TITLE_SIZE + 8)

    c.setFont(BODY_FONT, 8)
    c.drawString(
        MARGIN,
        y,
        "Evidence sources: README.md, AGENTS.md, docs/explanation/architecture.md, pyproject.toml, src/fleet_rlm/*.py",
    )
    y -= (8 + SECTION_GAP)

    y = draw_heading(c, y, "What it is")
    y = draw_paragraph(
        c,
        y,
        "fleet-rlm is a Python package for Recursive Language Models (RLM) that combines DSPy planning with Modal sandbox execution for secure long-context code workflows. It lets an agent generate Python code, run it in an isolated cloud container, and use results iteratively to answer complex tasks.",
    )
    y -= SECTION_GAP

    y = draw_heading(c, y, "Who it is for")
    y = draw_paragraph(
        c,
        y,
        "Primary persona: AI/agent developers (including Claude Code users) who need safe, programmatic analysis of large documents or datasets without local execution risk.",
    )
    y -= SECTION_GAP

    y = draw_heading(c, y, "What it does")
    y = draw_bullets(
        c,
        y,
        [
            "Runs RLM tasks where DSPy plans and generates code while Modal executes that code in an isolated sandbox.",
            "Provides Typer CLI commands for basic Q&A, architecture/API extraction, error pattern analysis, and trajectory/tool demos.",
            "Offers OpenTUI-based interactive chat (`fleet-rlm code-chat --opentui`) with WebSocket-backed interaction.",
            "Exposes an optional FastAPI server with health, chat, task, and WebSocket routes.",
            "Exposes an optional MCP server (`fleet-rlm serve-mcp`) for tool-oriented integrations.",
            "Supports stateful execution, helper tooling, and sub-LLM tool calls (`llm_query`) via interpreter/driver protocol.",
        ],
    )
    y -= SECTION_GAP

    y = draw_heading(c, y, "How it works (architecture overview)")
    y = draw_bullets(
        c,
        y,
        [
            "Entry surfaces: CLI (`src/fleet_rlm/cli.py`) and optional FastAPI app (`src/fleet_rlm/server/main.py`).",
            "Orchestration layer: runner functions and `RLMReActChatAgent` build DSPy tasks and drive iterative reasoning.",
            "Execution layer: `ModalInterpreter` starts/controls a Modal sandbox and communicates with sandbox driver over JSON via stdio.",
            "Sandbox layer: `src/fleet_rlm/core/driver.py` executes generated Python with persistent state and helper functions, then streams structured outputs back.",
            "Data flow: user request -> planner/code generation -> sandbox execution/observations -> iterative refinement -> final response to CLI/API/TUI.",
        ],
    )
    y -= SECTION_GAP

    y = draw_heading(c, y, "How to run (minimal getting started)")
    y = draw_bullets(
        c,
        y,
        [
            "From repo root: `uv sync --extra dev`",
            "Create local env file: `cp .env.example .env`",
            "Configure Modal once: `uv run modal setup`",
            "Create secret (replace values): `uv run modal secret create LITELLM DSPY_LM_MODEL=... DSPY_LM_API_BASE=... DSPY_LLM_API_KEY=...`",
            "Run a first command: `uv run fleet-rlm run-basic --question \"What are the first 12 Fibonacci numbers?\"`",
            "Not found in repo: exact required model/provider values for `LITELLM` secret fields.",
        ],
    )

    c.setFont(BODY_FONT, 7)
    c.drawRightString(PAGE_W - MARGIN, 0.45 * inch, "Generated summary - single page")

    c.showPage()
    c.save()


if __name__ == "__main__":
    main()
