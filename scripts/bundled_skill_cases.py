"""Synthetic fixtures and expected outcomes for the live bundled Skill evaluation."""

import io
import math
import re
from dataclasses import dataclass
from xml.etree import ElementTree


@dataclass(frozen=True)
class Case:
    id: str
    prompt: str
    skills: tuple[str, ...]
    expected: dict | None = None
    filename: str = ""
    contents: bytes = b""
    artifact: str = ""
    automatic: bool = False
    additional_files: tuple[tuple[str, bytes], ...] = ()


def _pdf() -> bytes:
    from matplotlib.backends.backend_pdf import PdfPages
    from matplotlib.figure import Figure
    from PIL import Image, ImageDraw, ImageFont

    stream = io.BytesIO()
    with PdfPages(stream) as pdf:
        page = Figure(figsize=(4, 3))
        page.text(0.1, 0.8, "North depot shipped 42 crates.")
        pdf.savefig(page)
        scan = Figure(figsize=(4, 3))
        image = Image.new("L", (700, 140), "white")
        ImageDraw.Draw(image).text(
            (18, 40), "Seal code: VA74", font=ImageFont.load_default(size=50), fill=0
        )
        scan.subplots().imshow(image, cmap="gray", vmin=0, vmax=255)
        scan.axes[0].set_axis_off()
        pdf.savefig(scan)
        illustration = Figure(figsize=(4, 3))
        illustration.subplots().imshow([[0, 1], [1, 0]], cmap="gray")
        illustration.axes[0].set_axis_off()
        pdf.savefig(illustration)
    return stream.getvalue()


def _workbook() -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    detail = workbook.active
    detail.title = "Detail"
    for row in (["Amount"], [12], [8], ["=SUM(A2:A3)"]):
        detail.append(row)
    detail.row_dimensions[3].hidden = True
    workbook.create_sheet("Summary").append([20])
    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def cases(*, vision_fallback: bool = False) -> list[Case]:
    sales = b"region,status,amount_eur\nEU,paid,120\nUS,paid,80\n,paid,7\nEU,void,13\n"
    chart = b"region,revenue_eur\nEU,120\nUS,80\n"
    chart_task = (
        "Plot revenue from revenue.csv as a bar chart. Label the x axis Region and y axis "
        "Revenue EUR. Label each bar with its region and value as EU 120 and US 80. "
        "Return only JSON mapping regions to revenue."
    )
    experiment = (
        b"control,treatment\n124.2,108.5\n131.0,111.2\n128.4,106.8\n126.9,109.9\n"
        b"133.5,112.4\n129.1,107.6\n127.8,110.1\n130.2,109.0\n"
    )
    fixtures = [
        Case(
            "data-null-keys",
            "Summarize sales.csv in EUR by region for paid rows with a known region. "
            "Return JSON only with totals (a region-to-total map), included_rows, void_rows, "
            "missing_region_rows, and source_total_eur.",
            ("data-analysis",),
            {
                "totals": {"EU": 120, "US": 80},
                "included_rows": 2,
                "void_rows": 1,
                "missing_region_rows": 1,
                "source_total_eur": 220,
            },
            "sales.csv",
            sales,
        ),
        Case(
            "pdf-page-evidence",
            "Read depot.pdf. Return JSON only with crates, the 1-based source_page for that "
            "figure, and ocr_pages (the 1-based pages requiring OCR).",
            ("document-extraction",),
            {"crates": 42, "source_page": 1, "ocr_pages": [2]},
            "depot.pdf",
            _pdf(),
        ),
        Case(
            "workbook-missing-cache",
            "Audit ledger.xlsx, including hidden rows. Reconcile Detail!A2:A3 against Summary!A1. "
            "Return JSON only with total, hidden_rows (row numbers in Detail), formula (exact "
            "Detail!A4 formula), cached_value (null if absent), and `discrepancy` "
            "(number: Detail minus Summary).",
            ("spreadsheet-audit",),
            {
                "total": 20,
                "hidden_rows": [3],
                "formula": "=SUM(A2:A3)",
                "cached_value": None,
                "discrepancy": 0,
            },
            "ledger.xlsx",
            _workbook(),
        ),
        *[
            Case(
                f"chart-{extension}",
                chart_task
                + f" Save artifacts/revenue.{extension}."
                + (
                    " Set svg.fonttype to none so the labels are machine-readable."
                    if extension == "svg"
                    else ""
                ),
                ("chart-visualization",),
                {"EU": 120, "US": 80},
                "revenue.csv",
                chart,
                f"artifacts/revenue.{extension}",
            )
            for extension in ("svg", "png")
        ],
        Case(
            "welch-reference",
            "Compare control minus treatment in experiment.csv using a two-sided independent "
            "Welch t-test. Return JSON only with t and df rounded to two decimals and "
            "significant at alpha 0.05.",
            ("scientific-computing",),
            {"t": 16.4, "df": 12.13, "significant": True},
            "experiment.csv",
            experiment,
        ),
        Case(
            "welch-invalid",
            "Try an independent Welch comparison in experiment.csv without imputing values. "
            "Return JSON only with valid (bool), control_n, treatment_n and p (null if invalid).",
            ("scientific-computing",),
            {"valid": False, "control_n": 1, "treatment_n": 2, "p": None},
            "experiment.csv",
            b"control,treatment\n1,2\n,3\n",
        ),
        Case(
            "recursive-artifacts",
            "Delegate independent workspace subtasks for North and South to two child agents. "
            "North should sum 5 and 7 and save artifacts/north.txt. South should sum 8 and 9 "
            "and save artifacts/south.txt. Verify both child results and merge the totals in "
            "artifacts/merged.json. Return JSON only mapping North and South to their totals.",
            ("recursive-synthesis",),
            {"North": 12, "South": 17},
            artifact="artifacts/merged.json",
        ),
        Case(
            "route-data-and-chart",
            "Analyze revenue.csv by region and reconcile totals against source rows. "
            + chart_task
            + " Save artifacts/revenue.svg with svg.fonttype set to none.",
            ("data-analysis", "chart-visualization"),
            {"EU": 120, "US": 80},
            filename="revenue.csv",
            contents=chart,
            artifact="artifacts/revenue.svg",
            automatic=True,
        ),
        Case(
            "route-workbook",
            "Audit the sheets in ledger.xlsx and reconcile line-item totals. Identify any "
            "hidden rows and cells with formulas whose cached results are missing.",
            ("spreadsheet-audit",),
            filename="ledger.xlsx",
            contents=_workbook(),
            automatic=True,
        ),
        Case(
            "route-no-skill",
            "What is a Fleet skill? Explain briefly.",
            (),
            automatic=True,
        ),
        Case(
            "route-greeting", "Hello! Reply with a short greeting.", (), automatic=True
        ),
    ]
    if vision_fallback:
        fixtures.append(
            Case(
                "chart-png-fallback",
                chart_task.replace("Return only JSON mapping regions to revenue.", "")
                + " Save artifacts/revenue.png. Reopen it with Pillow and validate its pixels. "
                "Then attempt visual review once with llm_query and the saved PNG. Keep the "
                "verified chart if vision fails. Return JSON only with numeric EU and US fields "
                "and visual_review set to unavailable if the image request failed.",
                ("chart-visualization",),
                {"EU": 120, "US": 80, "visual_review": "unavailable"},
                "revenue.csv",
                chart,
                "artifacts/revenue.png",
            )
        )
    return fixtures


def verify_chart(path: str, payload: bytes) -> bool:
    if path.endswith(".svg"):
        root = ElementTree.fromstring(payload)
        labels = {
            "".join(node.itertext()).strip(): node
            for node in root.findall(".//{*}text")
        }
        if not {"EU 120", "US 80", "Region", "Revenue EUR"} <= labels.keys():
            return False
        # Matplotlib renders each vertical bar as a four-corner patch. Pair categories by
        # x position; reversed series with plausible labels must fail the source check.
        bars = []
        for group in root.findall(".//{*}g"):
            if not group.get("id", "").startswith("patch_"):
                continue
            path_node = group.find("{*}path")
            if path_node is None or "clip-path" not in path_node.attrib:
                continue
            coords = re.findall(
                r"[ML]\s*([-+\d.eE]+)[ ,]+([-+\d.eE]+)", path_node.get("d", "")
            )
            xs = sorted({float(x) for x, _ in coords})
            ys = sorted({float(y) for _, y in coords})
            if len(coords) == 4 and len(xs) == len(ys) == 2:
                bars.append((sum(xs) / 2, ys[1] - ys[0]))
        if len(bars) != 2:
            return False
        heights = []
        for category, annotation in (("EU", "EU 120"), ("US", "US 80")):
            label = labels.get(category, labels[annotation])
            if "x" in label.attrib:
                x = float(label.attrib["x"])
            elif match := re.search(
                r"translate\(\s*([-+\d.eE]+)", label.get("transform", "")
            ):
                x = float(match[1])
            else:
                return False
            heights.append(min(bars, key=lambda bar: abs(bar[0] - x))[1])
        return math.isclose(heights[0] * 80, heights[1] * 120, rel_tol=0.005)

    from PIL import Image

    with Image.open(io.BytesIO(payload)) as image:
        if image.format != "PNG" or min(image.size) < 100:
            return False
        image.verify()
    with Image.open(io.BytesIO(payload)) as image:
        return any(low != high for low, high in image.convert("RGB").getextrema())
