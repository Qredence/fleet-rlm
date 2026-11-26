"""Fixed inputs and independent artifact checks for the catalog comparison."""

import io
import re
import subprocess
import tempfile
from pathlib import Path


def stream_bytes(document):
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()


def catalog_cases():
    from bundled_skill_cases import Case, _pdf, _workbook
    from docx import Document
    from openpyxl import load_workbook
    from pptx import Presentation

    workbook = load_workbook(io.BytesIO(_workbook()))
    workbook["Detail"]["A4"] = "=SUM(A2:A2)"
    ledger = stream_bytes(workbook)
    word = Document()
    word.add_heading("Depot review", 0)
    word.add_paragraph("Draft count: 9 crates")
    word.add_table(rows=1, cols=1).cell(0, 0).text = "Retain code F17"
    slides = Presentation()
    for title in ("Depot review", "Appendix F17"):
        slides.slides.add_slide(slides.slide_layouts[5]).shapes.title.text = title

    styled = Document()
    styled.add_heading("Depot operations", level=1)
    paragraph = styled.add_paragraph()
    paragraph.add_run("Confirmed intake: ")
    paragraph.add_run("40").bold = True
    paragraph.add_run(" crates").italic = True
    styled.add_table(rows=1, cols=1).cell(0, 0).text = "Ticket OPS-14"

    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Inches

    chart_deck = Presentation()
    chart_slide = chart_deck.slides.add_slide(chart_deck.slide_layouts[5])
    chart_slide.shapes.title.text = "Depot throughput"
    chart_data = CategoryChartData()
    chart_data.categories = ["Shipped", "Received"]
    chart_data.add_series("Crates", [42, 38])
    chart_slide.shapes.add_chart(
        XL_CHART_TYPE.COLUMN_CLUSTERED,
        Inches(1),
        Inches(1.5),
        Inches(7),
        Inches(4),
        chart_data,
    )
    chart_slide.shapes.add_textbox(
        Inches(1), Inches(6), Inches(7), Inches(0.5)
    ).text = "Source: depot log, section 2"
    chart_deck.slides.add_slide(
        chart_deck.slide_layouts[5]
    ).shapes.title.text = "Appendix F17"

    findings = (
        b"Dispatch log, section 2: 42 crates shipped.\n"
        b"Intake log, section 4: 40 received. Two crates remain unconfirmed.\n"
    )
    return [
        Case(
            "word-styled-edit",
            "Update confirmed intake from 40 to 41 in status.docx. Keep the number bold, the "
            "unit italic, and the surrounding heading and ticket table unchanged. "
            "Save status-final.docx.",
            ("docx",),
            filename="status.docx",
            contents=stream_bytes(styled),
            artifact="artifacts/status-final.docx",
        ),
        Case(
            "slides-chart-edit",
            "Update the editable chart in throughput.pptx to 40 received crates, retaining 42 "
            "shipped. Keep the category order, source caption and appendix slide. "
            "Save throughput-final.pptx.",
            ("pptx",),
            filename="throughput.pptx",
            contents=stream_bytes(chart_deck),
            artifact="artifacts/throughput-final.pptx",
        ),
        Case(
            "exhibit-evidence",
            "Locate the seal note VA74 and the passage stating 42 crates in depot.pdf. "
            "Create exhibits.pdf containing only those two exhibits in that order, preserving "
            "orientation. Cite their one-based original pages as JSON with source_pages.",
            ("pdf",),
            {"source_pages": [2, 1]},
            "depot.pdf",
            _pdf(),
            "artifacts/exhibits.pdf",
        ),
        Case(
            "plain-text-evidence",
            "Extract the dispatch count from findings.txt. "
            "Return JSON with count and source_section as an integer.",
            ("document-extraction",),
            {"count": 42, "source_section": 2},
            "findings.txt",
            findings,
        ),
        Case(
            "cross-file-reconciliation",
            "Compare shipping.txt with returns.txt. Reconcile units, and tell me whether the "
            "damage count is known. Return JSON with gross_units, net_units, confirmed_damage "
            "(null if unresolved), gross_source, and returns_source (filenames).",
            ("recursive-synthesis",),
            {
                "gross_units": 42,
                "net_units": 40,
                "confirmed_damage": None,
                "gross_source": "shipping.txt",
                "returns_source": "returns.txt",
            },
            "shipping.txt",
            b"42 units shipped before returns. Two items marked damaged.",
            additional_files=(
                (
                    "returns.txt",
                    b"2 units returned. Four units marked damaged. No inspection available.",
                ),
            ),
        ),
        Case(
            "pdf-appendix",
            "Make appendix.pdf from depot.pdf: put the original last page first, "
            "rotated clockwise 90 degrees, followed by original page 1. Return JSON "
            "with source_pages in output order.",
            ("pdf",),
            {"source_pages": [3, 1]},
            "depot.pdf",
            _pdf(),
            "artifacts/appendix.pdf",
        ),
        Case(
            "pdf-scan",
            "Read the scanned note in depot.pdf. Return JSON with its seal_code and physical page.",
            ("document-extraction",),
            {"seal_code": "VA74", "page": 2},
            "depot.pdf",
            _pdf(),
        ),
        Case(
            "xlsx-edit",
            "Fix the understated Detail total in ledger.xlsx. Keep its layout and "
            "hidden rows. Save corrected.xlsx with a live formula covering all "
            "detail amounts and refreshed cached values.",
            ("xlsx",),
            filename="ledger.xlsx",
            contents=ledger,
            artifact="artifacts/corrected.xlsx",
        ),
        Case(
            "xlsx-create",
            "Build budget.xlsx with an Inputs sheet. Put 12 and 8 in A2 and A3, and "
            "a working total in A4. Save it ready for a colleague to change either "
            "amount.",
            ("xlsx",),
            artifact="artifacts/budget.xlsx",
        ),
        Case(
            "xlsx-analysis",
            "What is the sum of the raw amounts in Detail rows 2 and 3 of "
            "ledger.xlsx? Return JSON with amount.",
            ("data-analysis",),
            {"amount": 20},
            "ledger.xlsx",
            ledger,
        ),
        Case(
            "xlsx-audit",
            "Audit ledger.xlsx. Return JSON with the faulty_cell (sheet!cell), "
            "expected_total from the detail rows, and cached_value (null if "
            "absent).",
            ("spreadsheet-audit",),
            {"faulty_cell": "Detail!A4", "expected_total": 20, "cached_value": None},
            "ledger.xlsx",
            ledger,
        ),
        Case(
            "sourced-brief",
            "Save a one-page Markdown brief named handoff.md for the depot "
            "supervisor using findings.txt. Give counts, cite each source section, "
            "and say what remains uncertain.",
            ("report-writing",),
            filename="findings.txt",
            contents=findings,
            artifact="artifacts/handoff.md",
        ),
        Case(
            "word-create",
            "Turn findings.txt into depot.docx with a heading, the two source "
            "counts and citations, and the outstanding uncertainty.",
            ("docx",),
            filename="findings.txt",
            contents=findings,
            artifact="artifacts/depot.docx",
        ),
        Case(
            "word-edit",
            "Update template.docx: replace the draft count with 42 crates, "
            "preserving the existing heading, table, and layout. Save revised.docx.",
            ("docx",),
            filename="template.docx",
            contents=stream_bytes(word),
            artifact="artifacts/revised.docx",
        ),
        Case(
            "slides-create",
            "Make briefing.pptx with two slides for operations: dispatch first, "
            "intake second. Put the count and source section on the corresponding "
            "slide and flag the unresolved difference.",
            ("pptx",),
            filename="findings.txt",
            contents=findings,
            artifact="artifacts/briefing.pptx",
        ),
        Case(
            "slides-edit",
            "In template.pptx change the first slide title to Dispatch update. Keep "
            "the appendix unchanged and save updated.pptx.",
            ("pptx",),
            filename="template.pptx",
            contents=stream_bytes(slides),
            artifact="artifacts/updated.pptx",
        ),
        Case(
            "reconcile-reports",
            "Reconcile the two records in reports.txt. Return JSON with sold_total, "
            "returns, net_units, and verified_damage_count (null when unresolved).",
            ("recursive-synthesis",),
            {
                "sold_total": 42,
                "returns": 2,
                "net_units": 40,
                "verified_damage_count": None,
            },
            "reports.txt",
            b"Shipping report: 42 sold, including two subsequently returned; damage "
            b"count 1.\nReceiving report: 40 net of the two returns; damage count 3. "
            b"Neither damage number has corroboration.",
        ),
        Case(
            "web-direct",
            "Read https://www.rfc-editor.org/info/rfc9114 and give the protocol "
            "name with a link to the source.",
            ("web-research",),
            automatic=True,
        ),
        Case(
            "web-lookup",
            "Look up the RFC that defines HTTP/3. Give the RFC number and cite the "
            "published specification.",
            ("web-research",),
            automatic=True,
        ),
        Case(
            "report-neighbor",
            "What does the term decision memo mean? Answer in one sentence.",
            (),
            automatic=True,
        ),
    ]


def office_convert(source, destination, target):
    with tempfile.TemporaryDirectory() as profile:
        subprocess.run(
            [
                "soffice",
                "-env:UserInstallation=" + Path(profile).as_uri(),
                "--headless",
                "--convert-to",
                target,
                "--outdir",
                str(destination),
                str(source),
            ],
            capture_output=True,
            check=True,
            timeout=45,
        )
    return destination / (source.stem + "." + target)


def verify_artifact(name, payload, evidence=None):
    from pypdf.errors import PdfReadError

    try:
        return _verify_artifact(name, payload, evidence)
    except PdfReadError:
        return False


def _verify_artifact(name, payload, evidence):
    from docx import Document
    from openpyxl import load_workbook
    from pptx import Presentation
    from pypdf import PdfReader

    if name in {"pdf-appendix", "exhibit-evidence"}:
        from bundled_skill_cases import _pdf
        from PIL import Image, ImageChops, ImageStat

        pages = PdfReader(io.BytesIO(payload)).pages
        if len(pages) != 2 or "42 crates" not in pages[1].extract_text():
            return False
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for stem, data in (("original", _pdf()), ("output", payload)):
                path = root / (stem + ".pdf")
                path.write_bytes(data)
                subprocess.run(
                    ["pdftoppm", "-png", "-r", "60", str(path), str(root / stem)],
                    check=True,
                    capture_output=True,
                    timeout=30,
                )
            with (
                Image.open(
                    root
                    / ("original-3.png" if name == "pdf-appendix" else "original-2.png")
                ) as exhibit,
                Image.open(root / "original-1.png") as first,
            ):
                for index, expected in enumerate(
                    (
                        exhibit.transpose(Image.Transpose.ROTATE_270)
                        if name == "pdf-appendix"
                        else exhibit,
                        first,
                    ),
                    1,
                ):
                    with Image.open(root / f"output-{index}.png") as actual:
                        if (
                            actual.size != expected.size
                            or max(
                                ImageStat.Stat(
                                    ImageChops.difference(actual, expected)
                                ).mean
                            )
                            > 2
                        ):
                            return False
            return True
    if name == "sourced-brief":
        text = payload.decode().casefold()
        return all(
            re.search(pattern, text)
            for pattern in (
                r"\b42\b",
                r"\b40\b",
                r"dispatch.{0,35}\b2\b",
                r"intake.{0,35}\b4\b",
                r"unconfirm|uncertain|unaccount|pending|unverified",
            )
        )
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        out = root / "out"
        out.mkdir()
        if name.startswith("xlsx"):
            book = load_workbook(io.BytesIO(payload))
            sheet = book["Detail" if name == "xlsx-edit" else "Inputs"]
            if name == "xlsx-edit" and (
                not sheet.row_dimensions[3].hidden or book["Summary"]["A1"].value != 20
            ):
                return False
            if [sheet[c].value for c in ("A2", "A3")] != [12, 8] or sheet[
                "A4"
            ].data_type != "f":
                return False
            if (
                name == "xlsx-edit"
                and load_workbook(io.BytesIO(payload), data_only=True)[sheet.title][
                    "A4"
                ].value
                != 20
            ):
                return False
            sheet["A3"] = 18
            source = root / "changed.xlsx"
            book.save(source)
            recalculated = office_convert(source, out, "xlsx")
            return (
                load_workbook(recalculated, data_only=True)[sheet.title]["A4"].value
                == 30
            )
        if name.startswith("word"):
            doc = Document(io.BytesIO(payload))
            paragraphs = [p.text for p in doc.paragraphs]
            cells = [
                cell.text
                for table in doc.tables
                for row in table.rows
                for cell in row.cells
            ]
            text = " ".join([*paragraphs, *cells])
            if name == "word-styled-edit":
                runs = doc.paragraphs[1].runs
                valid = (
                    doc.paragraphs[0].text == "Depot operations"
                    and doc.paragraphs[0].style.name == "Heading 1"
                    and doc.paragraphs[1].text == "Confirmed intake: 41 crates"
                    and any("41" in run.text and run.bold for run in runs)
                    and any("crates" in run.text and run.italic for run in runs)
                    and doc.tables[0].cell(0, 0).text == "Ticket OPS-14"
                )
                expected = ["41 crates", "Ticket OPS-14"]
            elif name == "word-edit":
                valid = (
                    "42 crates" in text
                    and "9 crates" not in text
                    and doc.tables[0].cell(0, 0).text == "Retain code F17"
                    and doc.paragraphs[0].style.name == "Title"
                    and doc.paragraphs[0].text == "Depot review"
                )
                expected = ["42 crates", "Retain code F17"]
            else:
                valid = any(
                    p.style.name.startswith(("Heading", "Title"))
                    for p in doc.paragraphs
                ) and verify_artifact("sourced-brief", text.encode())
                expected = ["42", "40"]
            extension = "docx"
        else:
            deck = Presentation(io.BytesIO(payload))
            slides = [
                " ".join(shape.text for shape in slide.shapes if shape.has_text_frame)
                for slide in deck.slides
            ]
            if len(slides) != 2:
                return False
            if name == "slides-chart-edit":
                charts = [
                    shape.chart for shape in deck.slides[0].shapes if shape.has_chart
                ]
                valid = (
                    len(charts) == 1
                    and len(charts[0].series) == 1
                    and list(charts[0].series[0].values) == [42, 40]
                    and [c.label for c in charts[0].plots[0].categories]
                    == ["Shipped", "Received"]
                    and "depot log, section 2" in slides[0]
                    and slides[1] == "Appendix F17"
                )
                expected = ["Received", "Appendix F17"]
            else:
                valid = (
                    slides == ["Dispatch update", "Appendix F17"]
                    if name == "slides-edit"
                    else (
                        "42" in slides[0]
                        and "40" in slides[1]
                        and verify_artifact("sourced-brief", " ".join(slides).encode())
                    )
                )
                expected = (
                    ["Dispatch update", "Appendix F17"]
                    if name == "slides-edit"
                    else ["42", "40"]
                )
            extension = "pptx"
        if not valid:
            return False
        source = root / ("artifact." + extension)
        source.write_bytes(payload)
        pdf_path = office_convert(source, out, "pdf")
        if evidence is not None:
            subprocess.run(
                [
                    "pdftoppm",
                    "-png",
                    "-r",
                    "120",
                    str(pdf_path),
                    str(evidence / "page"),
                ],
                check=True,
                capture_output=True,
                timeout=45,
            )
        rendered = PdfReader(pdf_path)
        pages = [page.extract_text() for page in rendered.pages]
        if extension == "pptx":
            return len(pages) == len(expected) and all(
                token in page for token, page in zip(expected, pages, strict=False)
            )
        return len(pages) == 1 and all(token in pages[0] for token in expected)


def cited_open(result, term):
    return term in result["answer"].lower() and any(
        event["tool"] == "web_open"
        and event["outcome"] in {"opened", "truncated"}
        and event["target"] in result["answer"]
        for event in result["web_calls"]
    )


def matches_catalog_answer(case, answer):
    """Score the requested fields; catalog prompts permit additional handoff metadata."""
    import json

    decoder = json.JSONDecoder()
    records = []
    for match in re.finditer(r"\{", answer):
        try:
            actual, _ = decoder.raw_decode(answer[match.start() :])
        except ValueError:
            continue
        if case.id == "pdf-scan" and "page" not in actual and "physical_page" in actual:
            actual["page"] = actual["physical_page"]
        if case.expected.keys() <= actual.keys():
            records.append(actual)
    return bool(records) and all(
        all(actual[key] == value for key, value in case.expected.items())
        for actual in records
    )


def review_catalog(output, *, merged_formats=False, combined_workbook=False):
    """Re-score saved live outputs without calling a model or modifying the raw run."""
    import json

    definitions = {case.id: case for case in catalog_cases()}
    run = json.loads((output / "workflow.json").read_text())
    reviewed = []
    for row in run["rows"]:
        item = dict(row)
        case = definitions.get(row["id"])
        if case is not None and "active_skills" in row:
            expected = row.get("expected_skills", list(case.skills))
            if combined_workbook and case.id == "xlsx-audit":
                expected = ["xlsx"]
            if merged_formats and case.id in {"pdf-scan", "xlsx-audit"}:
                expected = ["pdf" if case.id == "pdf-scan" else "xlsx"]
            item["routing_passed"] = set(row["active_skills"]) == set(expected)
            if case.expected is not None or case.artifact:
                valid = case.expected is None or matches_catalog_answer(
                    case, row["answer"]
                )
                if case.artifact:
                    evidence = output / "artifacts" / case.id / str(row["repeat"])
                    path = evidence / Path(case.artifact).name
                    valid = (
                        path.exists()
                        and verify_artifact(case.id, path.read_bytes(), evidence)
                        and valid
                    )
                item["output_passed"] = valid
            item["passed"] = (
                item["routing_passed"] and item.get("output_passed") is not False
            )
        reviewed.append(item)
    (output / "catalog-review.json").write_text(
        json.dumps({**run, "rows": reviewed}, indent=2) + "\n"
    )


def evaluate_routing(output, *, merged_pdf=False):
    """Run five independent selector passes, including multi-turn holdout prompts."""
    import json
    from concurrent.futures import ThreadPoolExecutor
    from time import monotonic

    import dspy
    from fleet_rlm.config import Settings
    from fleet_rlm.skills import SkillRegistry, select_skills

    settings = Settings()
    registry = SkillRegistry.load(settings.skills_dir, fallback_to_bundled=True)
    names = {skill.name for skill in registry.skills}
    merged_workbook = "xlsx" in names and "spreadsheet-audit" not in names
    tasks = []
    path = settings.eval_datasets_dir / "skills.jsonl"
    for line in path.read_text().splitlines():
        row = json.loads(line)
        if row.get("split") != "holdout":
            continue
        expected = [
            name
            for name in (row["expected_skill"], *row.get("secondary_skills", []))
            if name
        ]
        if "spreadsheet-audit" in names and not merged_workbook:
            expected = [
                "spreadsheet-audit" if name == "xlsx" else name for name in expected
            ]
        if merged_pdf and ".pdf" in row["task"].lower():
            expected = [
                "pdf" if name == "document-extraction" else name for name in expected
            ]
        tasks.append(
            (row["lineage"], row["task"], expected, row.get("prior_turns", []))
        )
    for case in catalog_cases():
        expected = list(case.skills)
        if case.id == "xlsx-audit" and merged_workbook:
            expected = ["xlsx"]
        if case.id == "pdf-scan" and merged_pdf:
            expected = ["pdf"]
        tasks.append((case.id, case.prompt, expected, []))

    dspy.configure_cache(enable_disk_cache=False, enable_memory_cache=False)
    lm = dspy.LM(settings.decision_model, cache=False)

    def route(job):
        repeat, (identifier, task, expected, history) = job
        start = monotonic()
        result = select_skills(
            settings, registry, task, prior_turns=history, decision_lm=lm
        )
        actual = [skill.name for skill in result.active_skills]
        return {
            "id": identifier,
            "repeat": repeat,
            "expected": expected,
            "actual": actual,
            "passed": actual == expected,
            "seconds": monotonic() - start,
        }

    output.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(4) as pool, (output / "routing.jsonl").open("w") as stream:
        for row in pool.map(route, ((n, task) for n in range(1, 6) for task in tasks)):
            print(json.dumps(row), file=stream, flush=True)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Re-check downloaded catalog artifacts and live answers."
    )
    parser.add_argument(
        "output", type=Path, help="Output directory from --catalog-skills"
    )
    parser.add_argument("--merged-formats", action="store_true")
    parser.add_argument("--combined-workbook", action="store_true")
    parser.add_argument(
        "--routing",
        action="store_true",
        help="Evaluate first-turn and holdout selection using the configured catalog.",
    )
    args = parser.parse_args()
    if args.routing:
        evaluate_routing(args.output, merged_pdf=args.merged_formats)
        raise SystemExit(0)
    review_catalog(
        args.output,
        merged_formats=args.merged_formats,
        combined_workbook=args.combined_workbook,
    )
