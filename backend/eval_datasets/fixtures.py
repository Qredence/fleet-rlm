"""Programmatic workspace file and deterministic web fixtures for offline RLM evaluation.

All fixtures (CSV, TXT, XLSX, PDF, and recorded web_search/web_open payloads) are generated
deterministically using only the Python standard library so zero binary blobs enter Git and
imports succeed cleanly in ``backend``'s default ``uv`` environment without extra dependencies.
"""

import io
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any
from xml.sax.saxutils import escape as xml_escape


@dataclass(frozen=True)
class FormulaCell:
    """An Excel formula cell with an optional cached value (None simulates missing cache)."""

    formula: str
    cached: int | float | str | None = None


@dataclass(frozen=True)
class SheetSpec:
    """Specification for a single worksheet in a synthetic OOXML workbook."""

    name: str
    rows: tuple[tuple[Any, ...], ...]
    hidden_rows: tuple[int, ...] = ()
    merged_ranges: tuple[str, ...] = ()


@dataclass(frozen=True)
class PdfTextPage:
    """A text-bearing PDF page rendered with standard Type 1 Helvetica."""

    lines: tuple[str, ...]


@dataclass(frozen=True)
class PdfScanPage:
    """An image-only PDF page containing no text layer (requires OCR)."""


PdfPage = PdfTextPage | PdfScanPage


def _col_letter(index: int) -> str:
    """Convert 1-based column index to Excel column label (1 -> 'A', 27 -> 'AA')."""
    chars: list[str] = []
    n = index
    while n > 0:
        n, rem = divmod(n - 1, 26)
        chars.append(chr(ord("A") + rem))
    return "".join(reversed(chars))


def _sheet_xml(spec: SheetSpec) -> str:
    hidden = set(spec.hidden_rows)
    row_chunks: list[str] = []
    for row_idx, row in enumerate(spec.rows, start=1):
        row_attrs = f' r="{row_idx}" hidden="1"' if row_idx in hidden else f' r="{row_idx}"'
        cell_chunks: list[str] = []
        for col_idx, value in enumerate(row, start=1):
            if value is None:
                continue
            coord = f"{_col_letter(col_idx)}{row_idx}"
            if isinstance(value, FormulaCell):
                formula_body = xml_escape(value.formula.lstrip("="))
                if value.cached is None:
                    cell_chunks.append(f'<c r="{coord}"><f>{formula_body}</f></c>')
                elif isinstance(value.cached, str):
                    cached_text = xml_escape(value.cached)
                    cell_chunks.append(
                        f'<c r="{coord}" t="str"><f>{formula_body}</f><v>{cached_text}</v></c>'
                    )
                else:
                    cell_chunks.append(
                        f'<c r="{coord}"><f>{formula_body}</f><v>{value.cached}</v></c>'
                    )
            elif isinstance(value, bool):
                cell_chunks.append(f'<c r="{coord}" t="b"><v>{1 if value else 0}</v></c>')
            elif isinstance(value, (int, float)):
                cell_chunks.append(f'<c r="{coord}"><v>{value}</v></c>')
            else:
                text = xml_escape(str(value))
                cell_chunks.append(f'<c r="{coord}" t="inlineStr"><is><t>{text}</t></is></c>')
        row_chunks.append(f"<row{row_attrs}>{''.join(cell_chunks)}</row>")

    merge_xml = ""
    if spec.merged_ranges:
        merges = "".join(f'<mergeCell ref="{xml_escape(ref)}"/>' for ref in spec.merged_ranges)
        merge_xml = f'<mergeCells count="{len(spec.merged_ranges)}">{merges}</mergeCells>'

    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(row_chunks)}</sheetData>"
        f"{merge_xml}"
        "</worksheet>"
    )


def _build_xlsx(sheets: Sequence[SheetSpec]) -> bytes:
    """Build a valid multi-sheet OOXML .xlsx workbook using only the Python standard library."""
    content_overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(1, len(sheets) + 1)
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        f"{content_overrides}"
        "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    sheet_entries = "".join(
        f'<sheet name="{xml_escape(sheet.name)}" sheetId="{i}" r:id="rId{i}"/>'
        for i, sheet in enumerate(sheets, start=1)
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{sheet_entries}</sheets>"
        "</workbook>"
    )
    wb_rels_entries = "".join(
        f'<Relationship Id="rId{i}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{i}.xml"/>'
        for i in range(1, len(sheets) + 1)
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{wb_rels_entries}"
        "</Relationships>"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        for i, sheet in enumerate(sheets, start=1):
            zf.writestr(f"xl/worksheets/sheet{i}.xml", _sheet_xml(sheet))
    return buf.getvalue()


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _build_pdf(pages: Sequence[PdfPage]) -> bytes:
    """Build a valid multi-page PDF 1.4 supporting text pages and image-only OCR scan pages."""
    # Object 1: Catalog, Object 2: Pages tree, Object 3: Helvetica Font, Object 4: Scan Image
    objects: dict[int, bytes] = {}
    objects[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"

    # 4x4 checkerboard grayscale image (16 raw bytes) for scanned pages without a text layer.
    scan_pixels = bytes(
        [
            40,
            220,
            40,
            220,
            220,
            40,
            220,
            40,
            40,
            220,
            40,
            220,
            220,
            40,
            220,
            40,
        ]
    )
    objects[4] = (
        b"<< /Type /XObject /Subtype /Image /Width 4 /Height 4 "
        b"/ColorSpace /DeviceGray /BitsPerComponent 8 /Length "
        + str(len(scan_pixels)).encode("ascii")
        + b" >>\nstream\n"
        + scan_pixels
        + b"\nendstream"
    )

    page_ids: list[int] = []
    next_id = 5
    for page in pages:
        content_id = next_id
        page_id = next_id + 1
        next_id += 2
        page_ids.append(page_id)

        if isinstance(page, PdfTextPage):
            ops = ["BT", "/F1 11 Tf", "54 740 Td", "16 TL"]
            for idx, line in enumerate(page.lines):
                escaped = _pdf_escape(line)
                ops.append(f"({escaped}) Tj" if idx == 0 else f"T* ({escaped}) Tj")
            ops.append("ET")
            stream_data = "\n".join(ops).encode("latin-1")
            objects[content_id] = (
                f"<< /Length {len(stream_data)} >>\nstream\n".encode("ascii")
                + stream_data
                + b"\nendstream"
            )
            objects[page_id] = (
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                b"/Resources << /Font << /F1 3 0 R >> >> "
                + f"/Contents {content_id} 0 R >>".encode("ascii")
            )
        else:
            stream_data = b"q 180 0 0 180 72 540 cm /Im1 Do Q"
            objects[content_id] = (
                f"<< /Length {len(stream_data)} >>\nstream\n".encode("ascii")
                + stream_data
                + b"\nendstream"
            )
            objects[page_id] = (
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                b"/Resources << /XObject << /Im1 4 0 R >> >> "
                + f"/Contents {content_id} 0 R >>".encode("ascii")
            )

    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects[2] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("ascii")
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"

    max_id = max(objects)
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: dict[int, int] = {}
    for obj_id in range(1, max_id + 1):
        offsets[obj_id] = len(out)
        out.extend(f"{obj_id} 0 obj\n".encode("ascii"))
        out.extend(objects[obj_id])
        out.extend(b"\nendobj\n")

    xref_pos = len(out)
    out.extend(f"xref\n0 {max_id + 1}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for obj_id in range(1, max_id + 1):
        out.extend(f"{offsets[obj_id]:010d} 00000 n \n".encode("ascii"))
    out.extend(
        f"trailer\n<< /Size {max_id + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode(
            "ascii"
        )
    )
    return bytes(out)


# ---------------------------------------------------------------------------
# Category 1: Workspace data & spreadsheet traps
# ---------------------------------------------------------------------------


def _fixture_data_dirty_invoices() -> dict[str, Any]:
    csv_data = (
        "invoice_id,region,status,amount_eur\n"
        "INV-101,EU ,paid, 120.50 \n"
        "INV-102, US,paid,€85.00\n"
        "INV-103,EU,paid,64.50\n"
        "INV-104,APAC,paid,210.00\n"
        "INV-105,,paid,45.00\n"
        "INV-106,US,void,30.00\n"
        "INV-107,EU,refunded,19.00\n"
        "INV-108,APAC,paid,N/A\n"
        "INV-109,US,paid,115.00\n"
    ).encode()
    return {"files": {"invoices.csv": csv_data}, "web": None}


def _fixture_xls_hidden_missing_cache() -> dict[str, Any]:
    xlsx = _build_xlsx(
        [
            SheetSpec(
                name="Detail",
                rows=(
                    ("Item", "Amount"),
                    ("Docking", 45),
                    ("Customs", 55),
                    ("Hidden Adjustment", 35),
                    ("Freight", 40),
                    ("Insurance", 25),
                    ("All Rows Sum", FormulaCell("=SUM(B2:B6)", cached=None)),
                    ("Visible Subtotal", FormulaCell("=SUBTOTAL(109,B2:B6)", cached=999)),
                ),
                hidden_rows=(4,),
            ),
            SheetSpec(
                name="Summary",
                rows=(
                    ("Metric", "Reported"),
                    ("Total", 165),
                ),
            ),
        ]
    )
    return {"files": {"ledger_north.xlsx": xlsx}, "web": None}


def _fixture_data_null_join_reconcile() -> dict[str, Any]:
    shipments = (
        b"shipment_id,hub_code,status,weight_kg,updated_at\n"
        b"SH-1,RTM,delivered,120,2026-04-01T08:00:00Z\n"
        b"SH-1,RTM,delivered,150,2026-04-01T12:00:00Z\n"
        b"SH-2,HAM,delivered,90,2026-04-02T09:00:00Z\n"
        b"SH-3,ANT,delivered,60,2026-04-02T10:00:00Z\n"
        b"SH-4,,delivered,40,2026-04-03T11:00:00Z\n"
        b"SH-5,ZZ9,delivered,25,2026-04-03T12:00:00Z\n"
        b"SH-6,HAM,cancelled,75,2026-04-04T08:00:00Z\n"
        b"SH-7,RTM,delivered,110,2026-04-04T15:00:00Z\n"
    )
    hubs = b"hub_code,hub_name\nRTM,Rotterdam\nHAM,Hamburg\nANT,Antwerp\n"
    return {"files": {"shipments.csv": shipments, "hubs.csv": hubs}, "web": None}


def _fixture_xls_merged_crosssheet() -> dict[str, Any]:
    xlsx = _build_xlsx(
        [
            SheetSpec(
                name="Ops",
                rows=(
                    ("Q2 Operations", None, None),
                    ("Cost Center", "Amount", "Notes"),
                    ("Platform", 210, "Core"),
                    ("Support", 145, "Tier 1"),
                    ("Hidden Reserve", 115, "Audit"),
                    ("Security", 145, "SOC"),
                    ("Total", FormulaCell("=SUM(B3:B6)", cached=None), None),
                ),
                hidden_rows=(5,),
                merged_ranges=("A1:C1",),
            ),
            SheetSpec(
                name="Executive",
                rows=(
                    ("Line", "Amount"),
                    ("Q2 Ops Total", FormulaCell("=Ops!B7", cached=500)),
                ),
            ),
        ]
    )
    return {"files": {"budget_west.xlsx": xlsx}, "web": None}


def _fixture_data_dirty_payroll() -> dict[str, Any]:
    csv_data = (
        b"emp_id,cost_center,contractor_flag,salary_usd\n"
        b'E-01,ENG,N,"1,250.00"\n'
        b"E-02,ENG,N, 950.00 \n"
        b"E-02,ENG,N, 950.00 \n"
        b"E-03,OPS,N,1100.00\n"
        b"E-04,OPS,Y,700.00\n"
        b"E-05,,N,600.00\n"
        b"E-06,FIN,N,TBD\n"
        b"E-07,FIN,N,1400.00\n"
    )
    return {"files": {"payroll_export.csv": csv_data}, "web": None}


def _fixture_xls_treasury_discrepancy() -> dict[str, Any]:
    xlsx = _build_xlsx(
        [
            SheetSpec(
                name="EMEA",
                rows=(
                    ("Entity", "Cash_kEUR"),
                    ("DE-01", 320),
                    ("FR-01", 280),
                    ("Hidden Accrual", 90),
                    ("NL-01", 160),
                    ("EMEA Total", FormulaCell("=SUM(B2:B5)", cached=None)),
                ),
                hidden_rows=(4,),
            ),
            SheetSpec(
                name="APAC",
                rows=(
                    ("APAC Treasury", None),
                    ("Entity", "Cash_kEUR"),
                    ("SG-01", 210),
                    ("JP-01", 190),
                    ("AU-01", 125),
                    ("APAC Total", FormulaCell("=SUM(B3:B5)", cached=None)),
                ),
                merged_ranges=("A1:B1",),
            ),
            SheetSpec(
                name="Consolidated",
                rows=(
                    ("Region", "Total_kEUR"),
                    ("Global Cash", FormulaCell("=EMEA!B6+APAC!B6", cached=1285)),
                ),
            ),
        ]
    )
    return {"files": {"treasury_q3.xlsx": xlsx}, "web": None}


# ---------------------------------------------------------------------------
# Category 2: Document extraction & numerical/statistical rigor
# ---------------------------------------------------------------------------


def _fixture_doc_pdf_ocr_covenants() -> dict[str, Any]:
    pdf = _build_pdf(
        [
            PdfTextPage(
                (
                    "Facility Alpha Compliance Packet",
                    "Audit ID: FAC-904",
                    "Maximum reserve capacity: 1450 pallets.",
                )
            ),
            PdfScanPage(),
            PdfTextPage(
                (
                    "Reserve Allocation Table",
                    "Zone A: 620 pallets",
                    "Zone B: 510 pallets",
                    "Zone C: 320 pallets",
                    "Printed Total: 1450 pallets",
                )
            ),
            PdfScanPage(),
        ]
    )
    return {"files": {"facility_packet.pdf": pdf}, "web": None}


def _fixture_sci_welch_and_nan() -> dict[str, Any]:
    # Asymmetric variances so Welch-Satterthwaite df != pooled df (16).
    csv_data = (
        b"control,treatment\n"
        b"142.5,129.8\n"
        b"138.0,130.4\n"
        b"145.2,129.2\n"
        b"140.8,130.9\n"
        b"144.1,129.5\n"
        b"139.4,130.1\n"
        b"141.7,130.6\n"
        b"143.6,129.9\n"
        b"137.9,\n"
        b"146.0,\n"
    )
    return {"files": {"trial_metrics.csv": csv_data}, "web": None}


def _fixture_doc_pdf_tariff_mismatch() -> dict[str, Any]:
    pdf = _build_pdf(
        [
            PdfTextPage(
                (
                    "Port Tariff Schedule 2026",
                    "Handling: 340 EUR",
                    "Storage: 215 EUR",
                    "Customs: 125 EUR",
                    "Printed Subtotal: 650 EUR",
                )
            ),
            PdfScanPage(),
            PdfTextPage(
                (
                    "Administrative Metadata",
                    "Port Code: RTM-4",
                    "Effective Date: 2026-07-01",
                )
            ),
        ]
    )
    return {"files": {"port_tariff.pdf": pdf}, "web": None}


def _fixture_sci_invalid_zero_variance() -> dict[str, Any]:
    csv_data = b"control,treatment\n12.0,12.0\n12.0,12.0\n12.0,12.0\n12.0,\n"
    return {"files": {"sensor_ab.csv": csv_data}, "web": None}


def _fixture_doc_pdf_bond_dossier() -> dict[str, Any]:
    pdf = _build_pdf(
        [
            PdfTextPage(
                (
                    "Municipal Bond Summary",
                    "Issuer: Cascade Water District",
                    "Par Value USD: 2500000",
                )
            ),
            PdfTextPage(
                (
                    "Coupon and Maturity Terms",
                    "Coupon Rate Pct: 4.35",
                    "Maturity Date: 2034-11-15",
                )
            ),
            PdfScanPage(),
            PdfTextPage(
                (
                    "Redemption Provisions",
                    "Call Date: 2029-11-15",
                )
            ),
        ]
    )
    return {"files": {"municipal_bond.pdf": pdf}, "web": None}


def _fixture_sci_ols_and_residuals() -> dict[str, Any]:
    csv_data = (
        b"temp_c,catalyst_g,yield_pct\n"
        b"180,1.0,62.4\n"
        b"185,1.5,66.1\n"
        b"190,1.0,65.8\n"
        b"195,2.0,72.3\n"
        b"200,1.5,71.2\n"
        b"205,2.5,77.9\n"
        b"210,2.0,76.5\n"
        b"215,2.5,81.4\n"
        b"220,1.5,78.6\n"
        b"225,3.0,86.2\n"
        b"230,3.0,88.1\n"
        b"235,2.5,\n"
    )
    return {"files": {"reactor_runs.csv": csv_data}, "web": None}


# ---------------------------------------------------------------------------
# Category 3: Chart & skill-creator artifacts
# ---------------------------------------------------------------------------


def _fixture_chart_svg_regional() -> dict[str, Any]:
    csv_data = (
        b"region,status,revenue_eur\n"
        b"North,paid,180\n"
        b"North,paid,130\n"
        b"North,void,45\n"
        b"South,paid,240\n"
        b"West,paid,190\n"
        b"West,void,20\n"
    )
    return {"files": {"q1_sales.csv": csv_data}, "web": None}


def _fixture_chart_png_latency() -> dict[str, Any]:
    csv_data = b"service,p50_ms,p95_ms\nauth,18,62\nbilling,34,115\nsearch,52,188\n"
    return {"files": {"service_latency.csv": csv_data}, "web": None}


def _fixture_chart_svg_retention() -> dict[str, Any]:
    csv_data = (
        b"quarter,status,users\n"
        b"Q1,active,25\n"
        b"Q1,active,20\n"
        b"Q1,test,12\n"
        b"Q2,active,62\n"
        b"Q2,test,9\n"
        b"Q3,active,78\n"
    )
    return {"files": {"cohort_events.csv": csv_data}, "web": None}


# ---------------------------------------------------------------------------
# Category 4: Recursive synthesis with spawn_agent
# ---------------------------------------------------------------------------


def _fixture_recursive_regional_conflict() -> dict[str, Any]:
    east = (
        b"East Region Outage Log (Q1)\nINC-E1: 15 minutes\nINC-E2: 20 minutes\nINC-E3: 12 minutes\n"
    )
    west = b"West Region Outage Log (Q1)\nINC-W1: 18 minutes\nINC-W2: 20 minutes\n"
    summary = (
        b"Operations Quarterly Rollup\n"
        b"Reported Combined Downtime: 75 minutes\n"
        b"Note: Verify against raw regional logs.\n"
    )
    return {
        "files": {
            "east_log.txt": east,
            "west_log.txt": west,
            "ops_summary.txt": summary,
        },
        "web": None,
    }


def _fixture_recursive_supplier_merge() -> dict[str, Any]:
    alpha = (
        b"Supplier Audit: alpha\nUnit Price EUR: 14.50\nDefect Rate Pct: 1.2\nLead Time Days: 5\n"
    )
    beta = b"Supplier Audit: beta\nUnit Price EUR: 13.80\nDefect Rate Pct: 3.9\nLead Time Days: 9\n"
    policy = (
        b"Procurement Policy\n"
        b"Maximum Allowed Defect Rate Pct: 2.0\n"
        b"Selection Rule: Lowest unit price among eligible suppliers.\n"
    )
    return {
        "files": {
            "supplier_alpha.txt": alpha,
            "supplier_beta.txt": beta,
            "policy.txt": policy,
        },
        "web": None,
    }


def _fixture_recursive_warehouse_variance() -> dict[str, Any]:
    north = b"Depot North Physical Count\nBay 1: 74 units\nBay 2: 54 units\n"
    south = b"Depot South Physical Count\nBay A: 51 units\nBay B: 43 units\n"
    hq = b"HQ Inventory Manifest\nReported Combined Stock: 208 units\n"
    return {
        "files": {
            "depot_north.txt": north,
            "depot_south.txt": south,
            "hq_manifest.txt": hq,
        },
        "web": None,
    }


def _fixture_recursive_release_readiness() -> dict[str, Any]:
    sec = b"Security Review Packet\nCritical Findings: 0\nMedium Findings: 1\nSignoff: PASS\n"
    rel = (
        b"Reliability Review Packet\n"
        b"Measured p99 Latency ms: 480\n"
        b"Gate Threshold ms: 350\n"
        b"Signoff: FAIL\n"
    )
    gate = (
        b"Release Gate Rule\n"
        b"Decision is GO only if BOTH security and reliability signoffs are PASS; "
        b"otherwise NO-GO.\n"
    )
    return {
        "files": {
            "security_review.txt": sec,
            "reliability_review.txt": rel,
            "release_gate.txt": gate,
        },
        "web": None,
    }


# ---------------------------------------------------------------------------
# Category 5: Deterministic recorded web-research fixtures
# ---------------------------------------------------------------------------


def _fixture_web_truncated_triage() -> dict[str, Any]:
    main_url = "https://docs.openmesh.example/reference/extract"
    modes_url = "https://docs.openmesh.example/reference/extract-modes"
    rumor_url = "https://blog.random-dev.example/openmesh-extract-tips"

    search_results = [
        {
            "title": "OpenMesh Extract Modes Reference",
            "url": modes_url,
            "snippet": "Official reference listing the exact extraction_mode enum values.",
        },
        {
            "title": "Unofficial OpenMesh Tips",
            "url": rumor_url,
            "snippet": "Blog post speculating about an experimental ultra-fast mode.",
        },
    ]
    return {
        "files": {},
        "web": {
            "limit": 8,
            "searches": {
                "openmesh extraction_mode": search_results,
                "extract modes": search_results,
                "*": search_results,
            },
            "pages": {
                main_url: {
                    "markdown": (
                        "# OpenMesh Extract Endpoint\n\n"
                        "Use `POST /v2/extract` to convert public web pages to Markdown.\n"
                        "See the dedicated modes reference for exact parameter values: "
                        f"[Extract Modes Reference]({modes_url}).\n\n"
                        "## Request Parameters\n"
                        "- `urls`: list of public HTTP/HTTPS URLs.\n"
                        "- `extraction_mode`: controls parsing depth (details truncated below)...\n"
                    ),
                    "truncated": True,
                },
                modes_url: {
                    "markdown": (
                        "# OpenMesh Extract Modes Reference\n\n"
                        "The `extraction_mode` parameter accepts exactly two supported values:\n"
                        "1. `standard` — fast HTML-to-Markdown extraction (1 credit per 5 URLs).\n"
                        "2. `deep-scan` — full DOM and table reconstruction "
                        "(2 credits per 5 URLs).\n\n"
                        "No other values are accepted; unrecognized values return HTTP 400.\n"
                    ),
                    "truncated": False,
                },
                rumor_url: {
                    "markdown": (
                        "# Unofficial OpenMesh Tips\n"
                        "I heard there might be an `ultra-fast` option someday, not confirmed.\n"
                    ),
                    "truncated": False,
                },
            },
        },
    }


def _fixture_web_mt_short_followup() -> dict[str, Any]:
    official_url = "https://alpinecup.example/2026/results/slovenia"
    rumor_url = "https://cycling-rumors.example/2026/alpine-cup-predictions"
    search_results = [
        {
            "title": "2026 Alpine Cup — Official Slovenia Stage Results",
            "url": official_url,
            "snippet": "Official timing bulletin for the 2026 Alpine Cup Slovenia stage.",
        },
        {
            "title": "Pre-Race Predictions: Alpine Cup Slovenia",
            "url": rumor_url,
            "snippet": "Many fans expected Lukas Novak to take the Slovenia stage.",
        },
    ]
    return {
        "files": {},
        "web": {
            "limit": 8,
            "searches": {
                "2026 alpine cup slovenia": search_results,
                "slovenia stage winner": search_results,
                "*": search_results,
            },
            "pages": {
                official_url: {
                    "markdown": (
                        "# 2026 Alpine Cup — Official Stage 3 (Slovenia) Results\n\n"
                        "1. **Tomasz Kowalski** — 3h 41m 18s (Official Stage Winner)\n"
                        "2. Henrik Solberg — 3h 41m 42s (+24s)\n"
                        "3. Mateo Rossi — 3h 42m 05s (+47s)\n\n"
                        "Published by the Alpine Cup Commissaires Panel.\n"
                    ),
                    "truncated": False,
                },
                rumor_url: {
                    "markdown": (
                        "# Pre-Race Predictions\n"
                        "Our pre-race favorite was Lukas Novak before the start list closed.\n"
                    ),
                    "truncated": False,
                },
            },
        },
    }


def _fixture_web_synthesis_rubric_grid() -> dict[str, Any]:
    overview_url = "https://nordicgrid.example/2026/interconnector-overview"
    norway_url = "https://statnett-bulletin.example/2026/north-link"
    denmark_url = "https://energinet-bulletin.example/2026/west-corridor"
    finland_url = "https://fingrid-bulletin.example/2026/aurora-review"
    broken_url = "https://archive.nordicgrid.example/2026/finland-annex.pdf"

    search_results = [
        {
            "title": "2026 Nordic Grid Interconnector Expansion Overview",
            "url": overview_url,
            "snippet": "Regional overview linking Norway, Denmark, and Finland bulletins.",
        },
        {
            "title": "Norway North Link 2026 Capacity Bulletin",
            "url": norway_url,
            "snippet": "Official Norwegian grid operator update on 2026 interconnector MW.",
        },
        {
            "title": "Denmark West Corridor 2026 Capacity Bulletin",
            "url": denmark_url,
            "snippet": "Official Danish transmission update on 2026 West Corridor capacity.",
        },
        {
            "title": "Finland Aurora Link 2026 Review Notice",
            "url": finland_url,
            "snippet": "Official Finnish grid notice regarding Q4 capacity confirmation.",
        },
        {
            "title": "Finland Technical Annex PDF",
            "url": broken_url,
            "snippet": "Raw technical annex PDF.",
        },
    ]
    return {
        "files": {},
        "web": {
            "limit": 8,
            "searches": {
                "nordic grid interconnector 2026": search_results,
                "norway denmark finland interconnector": search_results,
                "*": search_results,
            },
            "pages": {
                overview_url: {
                    "markdown": (
                        "# 2026 Nordic Grid Interconnector Expansion\n\n"
                        "See national operator bulletins for confirmed ratings:\n"
                        f"- Norway: {norway_url}\n"
                        f"- Denmark: {denmark_url}\n"
                        f"- Finland: {finland_url}\n"
                        "(Overview table truncated...)\n"
                    ),
                    "truncated": True,
                },
                norway_url: {
                    "markdown": (
                        "# Norway North Link 2026 Bulletin\n\n"
                        "Confirmed bidirectional interconnector capacity: **1,400 MW**.\n"
                        "Commercial energization completed in March 2026.\n"
                    ),
                    "truncated": False,
                },
                denmark_url: {
                    "markdown": (
                        "# Denmark West Corridor 2026 Bulletin\n\n"
                        "Confirmed interconnector transfer capacity: **1,200 MW**.\n"
                        "Commissioning target: September 2026.\n"
                    ),
                    "truncated": False,
                },
                finland_url: {
                    "markdown": (
                        "# Finland Aurora Link 2026 Review Notice\n\n"
                        "Final rated transfer capacity for the Finland section remains "
                        "**unconfirmed** pending the Q4 2026 substation stress-test review. "
                        "Operators have not published a binding MW figure yet.\n"
                    ),
                    "truncated": False,
                },
                broken_url: {
                    "error": "Tavily could not extract this page.",
                },
            },
        },
    }


def _fixture_web_allowance_discipline_rubric() -> dict[str, Any]:
    gothenburg_url = "https://portofgothenburg.example/2025/methanol-bunkering"
    antwerp_url = "https://portofantwerp.example/2025/green-methanol"
    gdansk_url = "https://portofgdansk.example/2025/methanol-plan"
    summary_url = "https://baltic-shipping.example/2025/methanol-digest"

    search_results = [
        {
            "title": "Port of Gothenburg 2025 Green Methanol Bunkering Capacity",
            "url": gothenburg_url,
            "snippet": "Official Port of Gothenburg terminal release for 2025.",
        },
        {
            "title": "Port of Antwerp-Bruges 2025 Green Methanol Hub",
            "url": antwerp_url,
            "snippet": "Official Antwerp-Bruges release detailing annual bunkering tonnes.",
        },
        {
            "title": "Port of Gdansk 2025 Methanol Terminal Spec",
            "url": gdansk_url,
            "snippet": "Port of Gdansk terminal technical specification.",
        },
        {
            "title": "Baltic Shipping Digest — Methanol Overview",
            "url": summary_url,
            "snippet": "Truncated industry roundup covering Northern European ports.",
        },
    ]
    return {
        "files": {},
        "web": {
            "limit": 5,
            "searches": {
                "green methanol bunkering gothenburg antwerp gdansk": search_results,
                "methanol bunkering 2025": search_results,
                "*": search_results,
            },
            "pages": {
                gothenburg_url: {
                    "markdown": (
                        "# Port of Gothenburg 2025 Green Methanol Release\n\n"
                        "Announced 2025 bunkering capacity: **45,000 metric tonnes per year** "
                        "at Energy Port Quay 519.\n"
                    ),
                    "truncated": False,
                },
                antwerp_url: {
                    "markdown": (
                        "# Port of Antwerp-Bruges 2025 Green Methanol Hub\n\n"
                        "Announced 2025 green-methanol bunkering throughput: "
                        "**120,000 metric tonnes per year** across two barge berths.\n"
                    ),
                    "truncated": False,
                },
                gdansk_url: {
                    "error": "Tavily could not extract this page.",
                },
                summary_url: {
                    "markdown": (
                        "# Baltic Shipping Digest\n"
                        "Gothenburg and Antwerp published 2025 capacities; "
                        "Gdansk details omitted...\n"
                    ),
                    "truncated": True,
                },
            },
        },
    }


def _fixture_web_mt_port_followup() -> dict[str, Any]:
    official_url = "https://femern-corridor.example/press/2026-schedule"
    outdated_url = "https://rail-blog.example/2024/fehmarnbelt-2029"
    search_results = [
        {
            "title": "Fehmarnbelt Tunnel 2026 Revised Schedule Bulletin",
            "url": official_url,
            "snippet": (
                "Official 2026 corridor authority announcement revising target opening year."
            ),
        },
        {
            "title": "2024 Preview: Fehmarnbelt on Track for 2029",
            "url": outdated_url,
            "snippet": "Older 2024 article citing the original 2029 opening estimate.",
        },
    ]
    return {
        "files": {},
        "web": {
            "limit": 8,
            "searches": {
                "fehmarnbelt opening year 2026": search_results,
                "fehmarnbelt schedule": search_results,
                "*": search_results,
            },
            "pages": {
                official_url: {
                    "markdown": (
                        "# Fehmarnbelt Tunnel — Official 2026 Schedule Update\n\n"
                        "The corridor authority revised the target opening year from **2029** "
                        "to **2031** following extended sea trials for the specialized immersion "
                        "pontoon vessel.\n"
                    ),
                    "truncated": False,
                },
                outdated_url: {
                    "markdown": (
                        "# 2024 Fehmarnbelt Preview\n"
                        "Published May 2024: planners previously aimed for a 2029 opening.\n"
                    ),
                    "truncated": False,
                },
            },
        },
    }


def _fixture_web_synthesis_rubric_battery() -> dict[str, Any]:
    overview_url = "https://gridstorage.example/2026/sodium-ion-overview"
    lab_url = "https://eurobattery-lab.example/2026/na-ion-benchmarks"
    regulator_url = "https://energy-regulator.example/2026/na-ion-deployments"
    cost_url = "https://energy-regulator.example/2026/cost-appendix-raw"

    search_results = [
        {
            "title": "2026 Sodium-Ion Grid Storage Overview",
            "url": overview_url,
            "snippet": "Truncated overview linking lab benchmark and regulator deployment reports.",
        },
        {
            "title": "EuroBattery Lab 2026 Sodium-Ion Cell Benchmarks",
            "url": lab_url,
            "snippet": "Measured gravimetric energy density and cycle-life retention.",
        },
        {
            "title": "Energy Regulator 2026 Stationary Na-Ion Deployments",
            "url": regulator_url,
            "snippet": "Commissioned MWh pilot projects and safety certification status.",
        },
        {
            "title": "Regulator 2026 Cost Appendix",
            "url": cost_url,
            "snippet": "Capital cost breakdown appendix (unextractable binary table).",
        },
    ]
    return {
        "files": {},
        "web": {
            "limit": 8,
            "searches": {
                "sodium-ion grid storage 2026": search_results,
                "sodium ion battery benchmark": search_results,
                "*": search_results,
            },
            "pages": {
                overview_url: {
                    "markdown": (
                        "# 2026 Sodium-Ion Grid Storage Overview\n"
                        f"Primary benchmarks: {lab_url}\n"
                        f"Deployment registry: {regulator_url}\n"
                        "(Summary truncated...)\n"
                    ),
                    "truncated": True,
                },
                lab_url: {
                    "markdown": (
                        "# EuroBattery Lab 2026 Sodium-Ion Cell Benchmarks\n\n"
                        "- Verified cell gravimetric energy density: **180 Wh/kg**\n"
                        "- Verified cycle life at 80% capacity retention: **4,500 cycles**\n"
                        "- Operating temperature window: -20°C to +55°C\n"
                    ),
                    "truncated": False,
                },
                regulator_url: {
                    "markdown": (
                        "# Energy Regulator 2026 Stationary Na-Ion Deployments\n\n"
                        "Commissioned utility pilots total **240 MWh** across three "
                        "European sites.\n"
                        "All three sites passed IEC 62619 thermal runaway propagation tests.\n"
                        f"Detailed installed $/kWh cost tables are in {cost_url}.\n"
                    ),
                    "truncated": False,
                },
                cost_url: {
                    "error": "Tavily could not extract this page.",
                },
            },
        },
    }


# ---------------------------------------------------------------------------
# Category 6: Blocking compliance & invalid-input fixtures
# ---------------------------------------------------------------------------


def _fixture_block_invalid_stat_refusal() -> dict[str, Any]:
    csv_data = b"control,treatment\n10.2,8.4\n,9.1\n"
    return {"files": {"tiny_trial.csv": csv_data}, "web": None}


_REGISTRY: dict[str, Callable[[], dict[str, Any]]] = {
    "data_dirty_invoices": _fixture_data_dirty_invoices,
    "xls_hidden_missing_cache": _fixture_xls_hidden_missing_cache,
    "data_null_join_reconcile": _fixture_data_null_join_reconcile,
    "xls_merged_crosssheet": _fixture_xls_merged_crosssheet,
    "data_dirty_payroll": _fixture_data_dirty_payroll,
    "xls_treasury_discrepancy": _fixture_xls_treasury_discrepancy,
    "doc_pdf_ocr_covenants": _fixture_doc_pdf_ocr_covenants,
    "sci_welch_and_nan": _fixture_sci_welch_and_nan,
    "doc_pdf_tariff_mismatch": _fixture_doc_pdf_tariff_mismatch,
    "sci_invalid_zero_variance": _fixture_sci_invalid_zero_variance,
    "doc_pdf_bond_dossier": _fixture_doc_pdf_bond_dossier,
    "sci_ols_and_residuals": _fixture_sci_ols_and_residuals,
    "chart_svg_regional": _fixture_chart_svg_regional,
    "chart_png_latency": _fixture_chart_png_latency,
    "chart_svg_retention": _fixture_chart_svg_retention,
    "recursive_regional_conflict": _fixture_recursive_regional_conflict,
    "recursive_supplier_merge": _fixture_recursive_supplier_merge,
    "recursive_warehouse_variance": _fixture_recursive_warehouse_variance,
    "recursive_release_readiness": _fixture_recursive_release_readiness,
    "web_truncated_triage": _fixture_web_truncated_triage,
    "web_mt_short_followup": _fixture_web_mt_short_followup,
    "web_synthesis_rubric_grid": _fixture_web_synthesis_rubric_grid,
    "web_allowance_discipline_rubric": _fixture_web_allowance_discipline_rubric,
    "web_mt_port_followup": _fixture_web_mt_port_followup,
    "web_synthesis_rubric_battery": _fixture_web_synthesis_rubric_battery,
    "block_invalid_stat_refusal": _fixture_block_invalid_stat_refusal,
}


def list_fixture_ids() -> tuple[str, ...]:
    """Return all registered deterministic fixture identifiers in sorted order."""
    return tuple(sorted(_REGISTRY))


def get_case_fixtures(fixture_id: str) -> dict[str, Any]:
    """Return ``{'files': dict[str, bytes], 'web': dict | None}`` for ``fixture_id``."""
    builder = _REGISTRY.get(fixture_id)
    if builder is None:
        raise KeyError(
            f"Unknown fixture_id {fixture_id!r}. Available: {', '.join(list_fixture_ids())}"
        )
    return builder()
