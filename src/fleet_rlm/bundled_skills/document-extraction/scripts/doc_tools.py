"""Dual-mode document inspection, PDF/Office extraction, and bounded image metadata tools."""

from __future__ import annotations

import argparse
import errno
import html
import importlib
import json
import os
import posixpath
import re
import shutil
import struct
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

_FORMAT_BY_EXT: dict[str, str] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".pptx": "pptx",
    ".xlsx": "xlsx",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".gif": "image",
    ".webp": "image",
    ".html": "html",
    ".htm": "html",
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "text",
    ".csv": "text",
    ".tsv": "text",
    ".json": "text",
    ".log": "text",
}


class _HtmlStripParser(HTMLParser):
    """Lightweight stdlib HTML text and title extractor."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignore_depth = 0
        self._in_title = False
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Track script/style/nav/footer exclusion and title/block boundaries."""
        _ = attrs
        lower = tag.lower()
        if self._ignore_depth > 0:
            if lower in {"script", "style", "noscript", "svg"}:
                self._ignore_depth += 1
            return
        if lower in {"script", "style", "noscript", "svg"}:
            self._ignore_depth = 1
        elif lower == "title":
            self._in_title = True
        elif lower in {"p", "div", "li", "tr", "br", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        """Close ignored subtrees and title nodes."""
        lower = tag.lower()
        if self._ignore_depth > 0:
            if lower in {"script", "style", "noscript", "svg"}:
                self._ignore_depth -= 1
            return
        if lower == "title":
            self._in_title = False
        elif lower in {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        """Capture visible document text and title."""
        if self._ignore_depth > 0:
            return
        if self._in_title:
            self.title_parts.append(data)
        else:
            self.text_parts.append(data)


def _try_import(module_name: str) -> Any | None:
    """Dynamically import an optional package without triggering static unresolved imports."""
    try:
        return importlib.import_module(module_name)
    except ImportError:
        return None


# Largest ZIP member (OOXML part) read into memory; sheets and documents beyond this are refused.
_MAX_ZIP_MEMBER_BYTES = 32 * 1024 * 1024


def _read_member(archive: zipfile.ZipFile, name: str, limit: int) -> bytes:
    """Read one ZIP member, refusing any payload larger than ``limit`` bytes."""
    info = archive.getinfo(name)
    if info.file_size > limit:
        raise ValueError(f"ZIP member {name!r} declares {info.file_size} bytes, over the {limit}-byte limit")
    with archive.open(info) as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"ZIP member {name!r} expands past the {limit}-byte limit")
    return data


def _resolve_file(path: str | Path) -> Path:
    """Resolve an existing non-symlink file path, checking FLEET_WORKSPACE for relative paths."""
    candidate = Path(path)
    if not candidate.is_absolute() and not candidate.exists():
        ws_root = Path(os.environ.get("FLEET_WORKSPACE", "/workspace"))
        ws_candidate = ws_root / candidate
        if ws_candidate.exists():
            candidate = ws_candidate
    if not candidate.exists():
        raise FileNotFoundError(f"Document not found: {path}")
    if candidate.is_symlink():
        raise ValueError(f"Document path may not be a symbolic link: {path}")
    if not candidate.is_file():
        raise ValueError(f"Document path is not a regular file: {path}")
    return candidate


def _detect_format(target: Path) -> str:
    """Map file extension to canonical document format label."""
    ext = target.suffix.lower()
    fmt = _FORMAT_BY_EXT.get(ext)
    if fmt is None:
        raise ValueError(f"Unsupported document extension: {ext or '(none)'}")
    return fmt


def _parse_pages(pages: list[int] | tuple[int, ...] | range | str | None, total_pages: int) -> list[int]:
    """Parse 1-based page selectors into a sorted list of validated page numbers."""
    if pages is None:
        return list(range(1, total_pages + 1))
    selected: list[int] = []
    if isinstance(pages, str):
        for token in (part.strip() for part in pages.split(",") if part.strip()):
            if "-" in token:
                start_s, end_s = token.split("-", 1)
                start, end = int(start_s), int(end_s)
                if start > end:
                    raise ValueError(f"Invalid page range {token!r}")
                if start < 1 or end > total_pages:
                    raise ValueError(f"Page range {token!r} out of bounds for {total_pages}-page PDF")
                selected.extend(range(start, end + 1))
            else:
                selected.append(int(token))
    else:
        selected = [int(p) for p in pages]
    if not selected:
        raise ValueError("pages selector produced no pages")
    for page_num in selected:
        if page_num < 1 or page_num > total_pages:
            raise ValueError(f"Page {page_num} out of bounds for {total_pages}-page PDF")
    return sorted(set(selected))


def _inspect_image_header(raw: bytes) -> tuple[str, int, int]:
    """Inspect PNG, JPEG, GIF, or WebP dimensions directly from binary headers."""
    if raw.startswith(b"\x89PNG\r\n\x1a\n") and len(raw) >= 24:
        width, height = struct.unpack(">II", raw[16:24])
        return "PNG", width, height
    if raw.startswith((b"GIF87a", b"GIF89a")) and len(raw) >= 10:
        width, height = struct.unpack("<HH", raw[6:10])
        return "GIF", width, height
    if raw.startswith(b"RIFF") and len(raw) >= 30 and raw[8:12] == b"WEBP":
        chunk_type = raw[12:16]
        if chunk_type == b"VP8 " and len(raw) >= 30:
            width = struct.unpack("<H", raw[26:28])[0] & 0x3FFF
            height = struct.unpack("<H", raw[28:30])[0] & 0x3FFF
            return "WEBP", width, height
        if chunk_type == b"VP8L" and len(raw) >= 25:
            bits = struct.unpack("<I", raw[21:25])[0]
            return "WEBP", (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        if chunk_type == b"VP8X" and len(raw) >= 30:
            width = int.from_bytes(raw[24:27], "little") + 1
            height = int.from_bytes(raw[27:30], "little") + 1
            return "WEBP", width, height
    if raw.startswith(b"\xff\xd8"):
        idx = 2
        length = len(raw)
        while idx + 4 <= length:
            if raw[idx] != 0xFF:
                idx += 1
                continue
            while idx < length and raw[idx] == 0xFF:
                idx += 1
            if idx >= length:
                break
            marker = raw[idx]
            idx += 1
            if marker in {0xD8, 0xD9, 0x01} or 0xD0 <= marker <= 0xD7:
                continue
            if idx + 2 > length:
                break
            seg_len = struct.unpack(">H", raw[idx : idx + 2])[0]
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                if idx + 7 <= length:
                    height, width = struct.unpack(">HH", raw[idx + 3 : idx + 7])
                    return "JPEG", width, height
                break
            idx += seg_len
    raise ValueError("Unsupported or corrupted image header")


def inspect_image(
    path: str | Path,
    *,
    resize_max_dim: int | None = None,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Read image format and dimensions, optionally resizing via Pillow when installed.

    Args:
        path: Image file path.
        resize_max_dim: Optional maximum width/height for thumbnail resizing (requires Pillow).
        output_path: Destination for resized image; relative paths use FLEET_WORKSPACE
            (default /workspace). If omitted, saves beside the input as ``<stem>_resized<ext>``.

    Returns:
        Dictionary with image format, dimensions, byte size, and resize output metadata.
    """
    target = _resolve_file(path)
    size_bytes = target.stat().st_size
    pil_image = _try_import("PIL.Image")

    if pil_image is None:
        if resize_max_dim is not None:
            raise RuntimeError("Resizing images requires Pillow (PIL), which is not installed")
        img_format, width, height = _inspect_image_header(target.read_bytes())
        return {
            "path": str(target),
            "format": "image",
            "image_format": img_format,
            "width": width,
            "height": height,
            "mode": None,
            "size_bytes": size_bytes,
            "inspector": "stdlib-header",
        }

    with pil_image.open(target) as img:
        img_format = str(img.format or target.suffix.lstrip(".").upper())
        width, height = img.size
        mode = str(img.mode)
        result: dict[str, Any] = {
            "path": str(target),
            "format": "image",
            "image_format": img_format,
            "width": int(width),
            "height": int(height),
            "mode": mode,
            "size_bytes": size_bytes,
            "inspector": "pillow",
        }
        if resize_max_dim is not None:
            if isinstance(resize_max_dim, bool) or not isinstance(resize_max_dim, int) or resize_max_dim < 1:
                raise ValueError("resize_max_dim must be a positive integer")
            resized = img.copy()
            resized.thumbnail((resize_max_dim, resize_max_dim))
            dest = (
                Path(output_path)
                if output_path is not None
                else target.with_name(f"{target.stem}_resized{target.suffix}")
            )
            if output_path is not None and not dest.is_absolute():
                dest = Path(os.environ.get("FLEET_WORKSPACE", "/workspace")) / dest
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Replace rather than truncate an existing artifact (which may be write-protected).
            descriptor, temporary = tempfile.mkstemp(dir=dest.parent, suffix=dest.suffix)
            os.close(descriptor)
            try:
                resized.save(Path(temporary))
                try:
                    os.replace(temporary, dest)
                except OSError as exc:
                    if exc.errno not in {errno.ENOSYS, errno.EXDEV, errno.EOPNOTSUPP}:
                        raise
                    # Volume FUSE may not implement rename. Encoding is complete before removing
                    # the old destination; this fallback cannot provide atomic replacement.
                    if dest.exists():
                        dest.unlink()
                    shutil.copyfile(temporary, dest)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            result["output_path"] = str(dest)
            result["resized_width"] = int(resized.width)
            result["resized_height"] = int(resized.height)
    return result


def _inspect_pdf_metadata(target: Path, size_bytes: int) -> dict[str, Any]:
    """Return structural PDF metadata without extracting full text."""
    base: dict[str, Any] = {"path": str(target), "format": "pdf", "size_bytes": size_bytes}
    pypdf_mod = _try_import("pypdf")
    if pypdf_mod is not None:
        reader = pypdf_mod.PdfReader(str(target))
        meta = {str(k).lstrip("/"): str(v) for k, v in (reader.metadata or {}).items() if v is not None}
        base.update(
            {
                "page_count": len(reader.pages),
                "encrypted": bool(reader.is_encrypted),
                "metadata": meta,
                "inspector": "pypdf",
            }
        )
        return base

    pdfplumber_mod = _try_import("pdfplumber")
    if pdfplumber_mod is not None:
        with pdfplumber_mod.open(target) as pdf:
            base.update(
                {
                    "page_count": len(pdf.pages),
                    "encrypted": False,
                    "metadata": {str(k): str(v) for k, v in (pdf.metadata or {}).items() if v is not None},
                    "inspector": "pdfplumber",
                }
            )
            return base

    raw = target.read_bytes()
    pages_est = len(re.findall(rb"/Type\s*/Page\b", raw))
    base.update({"page_count": pages_est or None, "encrypted": None, "metadata": {}, "inspector": "header"})
    return base


def _inspect_office_metadata(target: Path, doc_fmt: str, size_bytes: int) -> dict[str, Any]:
    """Return structural metadata for DOCX, PPTX, and XLSX without dumping body text."""
    base: dict[str, Any] = {"path": str(target), "format": doc_fmt, "size_bytes": size_bytes}
    if doc_fmt == "docx":
        docx_mod = _try_import("docx")
        if docx_mod is not None:
            doc = docx_mod.Document(str(target))
            base.update(
                {
                    "paragraph_count": len(doc.paragraphs),
                    "table_count": len(doc.tables),
                    "section_count": len(doc.sections),
                    "inspector": "python-docx",
                }
            )
            return base
        with zipfile.ZipFile(target) as archive:
            xml_data = _read_member(archive, "word/document.xml", _MAX_ZIP_MEMBER_BYTES)
            root = ET.fromstring(xml_data)
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            base.update(
                {
                    "paragraph_count": len(root.findall(".//w:p", ns)),
                    "table_count": len(root.findall(".//w:tbl", ns)),
                    "inspector": "zip-xml",
                }
            )
            return base

    if doc_fmt == "pptx":
        pptx_mod = _try_import("pptx")
        if pptx_mod is not None:
            prs = pptx_mod.Presentation(str(target))
            base.update({"slide_count": len(prs.slides), "inspector": "python-pptx"})
            return base
        with zipfile.ZipFile(target) as archive:
            slides = [name for name in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)]
            base.update({"slide_count": len(slides), "inspector": "zip-xml"})
            return base

    openpyxl_mod = _try_import("openpyxl")
    if openpyxl_mod is not None:
        wb = openpyxl_mod.load_workbook(str(target), read_only=True, data_only=True)
        try:
            sheet_names = list(wb.sheetnames)
        finally:
            wb.close()
        base.update({"sheet_count": len(sheet_names), "sheet_names": sheet_names, "inspector": "openpyxl"})
        return base
    with zipfile.ZipFile(target) as archive:
        wb_root = ET.fromstring(_read_member(archive, "xl/workbook.xml", _MAX_ZIP_MEMBER_BYTES))
        ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        sheet_names = [elem.attrib["name"] for elem in wb_root.findall(".//s:sheet", ns) if "name" in elem.attrib]
        base.update({"sheet_count": len(sheet_names), "sheet_names": sheet_names, "inspector": "zip-xml"})
        return base


def inspect_document(path: str | Path) -> dict[str, Any]:
    """Inspect a document's format and structural metadata without dumping full contents.

    Args:
        path: Path to the document in the workspace or filesystem.

    Returns:
        Dictionary of detected format and structural metadata.
    """
    target = _resolve_file(path)
    doc_fmt = _detect_format(target)
    size_bytes = target.stat().st_size

    if doc_fmt == "pdf":
        return _inspect_pdf_metadata(target, size_bytes)
    if doc_fmt in {"docx", "pptx", "xlsx"}:
        return _inspect_office_metadata(target, doc_fmt, size_bytes)
    if doc_fmt == "image":
        return inspect_image(target)

    text = target.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    if doc_fmt == "html":
        parser = _HtmlStripParser()
        parser.feed(text)
        parser.close()
        title = re.sub(r"\s+", " ", "".join(parser.title_parts)).strip()
        return {
            "path": str(target),
            "format": "html",
            "size_bytes": size_bytes,
            "title": title,
            "line_count": len(lines),
            "char_count": len(text),
        }

    meta: dict[str, Any] = {
        "path": str(target),
        "format": doc_fmt,
        "size_bytes": size_bytes,
        "line_count": len(lines),
        "char_count": len(text),
    }
    if doc_fmt == "markdown":
        meta["headings"] = [line.strip() for line in lines if line.lstrip().startswith("#")][:20]
    return meta


def extract_pdf(
    path: str | Path,
    *,
    pages: list[int] | tuple[int, ...] | range | str | None = None,
    extract_tables: bool = True,
    max_chars_per_page: int = 10_000,
) -> dict[str, Any]:
    """Extract per-page text and optional tables from a PDF using pdfplumber or pypdf.

    Args:
        path: PDF file path.
        pages: Optional 1-based page numbers, range, or comma/dash spec (e.g. ``"1-3,5"``).
        extract_tables: Whether to extract tables when ``pdfplumber`` is available.
        max_chars_per_page: Character cap per extracted page.

    Returns:
        Dictionary containing extractor name, total page count, and per-page payloads.
    """
    target = _resolve_file(path)
    if _detect_format(target) != "pdf":
        raise ValueError(f"Expected a .pdf file, got {target.suffix!r}")
    if max_chars_per_page <= 0:
        raise ValueError("max_chars_per_page must be positive")

    pdfplumber_mod = _try_import("pdfplumber")
    if pdfplumber_mod is not None:
        with pdfplumber_mod.open(target) as pdf:
            total_pages = len(pdf.pages)
            selected = _parse_pages(pages, total_pages)
            extracted: list[dict[str, Any]] = []
            for page_num in selected:
                page = pdf.pages[page_num - 1]
                raw_text = page.extract_text() or ""
                tables = page.extract_tables() if extract_tables else []
                extracted.append(
                    {
                        "page": page_num,
                        "text": raw_text[:max_chars_per_page],
                        "truncated": len(raw_text) > max_chars_per_page,
                        "total_chars": len(raw_text),
                        "tables": tables or [],
                    }
                )
        return {
            "path": str(target),
            "format": "pdf",
            "extractor": "pdfplumber",
            "total_pages": total_pages,
            "extracted_pages": selected,
            "pages": extracted,
        }

    pypdf_mod = _try_import("pypdf")
    if pypdf_mod is not None:
        reader = pypdf_mod.PdfReader(str(target))
        total_pages = len(reader.pages)
        selected = _parse_pages(pages, total_pages)
        extracted = []
        for page_num in selected:
            raw_text = reader.pages[page_num - 1].extract_text() or ""
            extracted.append(
                {
                    "page": page_num,
                    "text": raw_text[:max_chars_per_page],
                    "truncated": len(raw_text) > max_chars_per_page,
                    "total_chars": len(raw_text),
                    "tables": [],
                }
            )
        return {
            "path": str(target),
            "format": "pdf",
            "extractor": "pypdf",
            "total_pages": total_pages,
            "extracted_pages": selected,
            "pages": extracted,
        }

    raise RuntimeError("PDF extraction requires pdfplumber or pypdf")


def _extract_docx_zip(target: Path) -> dict[str, Any]:
    """Extract DOCX paragraphs and tables via stdlib ZIP/XML fallback."""
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    with zipfile.ZipFile(target) as archive:
        root = ET.fromstring(_read_member(archive, "word/document.xml", _MAX_ZIP_MEMBER_BYTES))
    paragraphs = [
        "".join(t.text or "" for t in p.findall(".//w:t", ns)).strip() for p in root.findall(".//w:body/w:p", ns)
    ]
    paragraphs = [p for p in paragraphs if p]
    tables: list[list[list[str]]] = []
    for tbl in root.findall(".//w:body/w:tbl", ns):
        rows = [
            ["".join(t.text or "" for t in cell.findall(".//w:t", ns)).strip() for cell in row.findall("w:tc", ns)]
            for row in tbl.findall("w:tr", ns)
        ]
        tables.append(rows)
    return {
        "path": str(target),
        "format": "docx",
        "extractor": "zip-xml",
        "paragraphs": paragraphs,
        "tables": tables,
        "text": "\n\n".join(paragraphs),
    }


def _slide_sort_key(name: str) -> int:
    """Sort PPTX slide XML paths numerically by slide index."""
    match = re.search(r"slide(\d+)\.xml", name)
    return int(match.group(1)) if match else 0


def _extract_pptx_zip(target: Path) -> dict[str, Any]:
    """Extract PPTX slide text via stdlib ZIP/XML fallback."""
    ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
    slides: list[dict[str, Any]] = []
    with zipfile.ZipFile(target) as archive:
        slide_names = sorted(
            (name for name in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
            key=_slide_sort_key,
        )
        for idx, name in enumerate(slide_names, start=1):
            root = ET.fromstring(_read_member(archive, name, _MAX_ZIP_MEMBER_BYTES))
            texts = ["".join(node.itertext()).strip() for node in root.findall(".//a:p", ns)]
            non_empty = [t for t in texts if t]
            slides.append({"slide": idx, "texts": non_empty, "notes": "", "tables": []})
    return {"path": str(target), "format": "pptx", "extractor": "zip-xml", "slides": slides}


def _extract_xlsx_zip(target: Path, max_rows_per_sheet: int) -> dict[str, Any]:
    """Extract XLSX sheet rows via stdlib ZIP/XML fallback."""
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(target) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            sst = ET.fromstring(_read_member(archive, "xl/sharedStrings.xml", _MAX_ZIP_MEMBER_BYTES))
            shared = ["".join(si.itertext()) for si in sst.findall("s:si", ns)]
        wb_root = ET.fromstring(_read_member(archive, "xl/workbook.xml", _MAX_ZIP_MEMBER_BYTES))
        sheet_elems = wb_root.findall(".//s:sheet", ns)
        rel_root = ET.fromstring(_read_member(archive, "xl/_rels/workbook.xml.rels", _MAX_ZIP_MEMBER_BYTES))
        rel_ns = "{http://schemas.openxmlformats.org/package/2006/relationships}"
        relationships = {rel.attrib["Id"]: rel for rel in rel_root.findall(f"{rel_ns}Relationship")}
        sheets: list[dict[str, Any]] = []
        for idx, sheet in enumerate(sheet_elems):
            relationship_id = sheet.attrib.get(
                "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
            )
            relationship = relationships.get(relationship_id)
            if relationship is None:
                raise ValueError(f"Missing XLSX worksheet relationship: {relationship_id!r}")
            if relationship.attrib.get("TargetMode") == "External":
                raise ValueError("External XLSX worksheet relationships are unsupported")
            if relationship.attrib.get("Type") != (
                "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
            ):
                raise ValueError("Unsupported XLSX sheet relationship type")
            target_path = relationship.attrib["Target"]
            sheet_path = posixpath.normpath(
                target_path.lstrip("/") if target_path.startswith("/") else posixpath.join("xl", target_path)
            )
            if not sheet_path.startswith("xl/") or "\\" in sheet_path:
                raise ValueError("XLSX worksheet relationship escapes the workbook")
            sheet_name = sheet.attrib.get("name", f"Sheet{idx + 1}")
            ws_root = ET.fromstring(_read_member(archive, sheet_path, _MAX_ZIP_MEMBER_BYTES))
            all_rows = ws_root.findall(".//s:sheetData/s:row", ns)
            rows: list[list[Any]] = []
            for row_elem in all_rows[:max_rows_per_sheet]:
                row_vals: list[Any] = []
                for cell in row_elem.findall("s:c", ns):
                    coordinate = cell.attrib.get("r")
                    column = len(row_vals)
                    if coordinate is not None:
                        match = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]{0,6})", coordinate)
                        if match is None:
                            raise ValueError(f"Invalid XLSX cell coordinate: {coordinate!r}")
                        column = 0
                        for letter in match[1]:
                            column = column * 26 + ord(letter) - ord("A") + 1
                        column -= 1
                        if column >= 16384 or int(match[2]) > 1048576:
                            raise ValueError(f"XLSX cell coordinate out of range: {coordinate!r}")
                    if column >= 16384:
                        raise ValueError("XLSX row exceeds the maximum column count")
                    if column >= len(row_vals):
                        row_vals.extend([None] * (column + 1 - len(row_vals)))
                    cell_type = cell.attrib.get("t", "")
                    val_elem = cell.find("s:v", ns)
                    inline = cell.find("s:is", ns)
                    if cell_type == "s" and val_elem is not None and val_elem.text is not None:
                        s_idx = int(val_elem.text)
                        row_vals[column] = shared[s_idx] if s_idx < len(shared) else ""
                    elif cell_type == "inlineStr" and inline is not None:
                        row_vals[column] = "".join(inline.itertext())
                    elif val_elem is not None and val_elem.text is not None:
                        row_vals[column] = val_elem.text
                    else:
                        row_vals[column] = None
                rows.append(row_vals)
            sheets.append(
                {
                    "name": sheet_name,
                    "rows": rows,
                    "total_rows": len(all_rows),
                    "truncated": len(all_rows) > max_rows_per_sheet,
                }
            )
    return {"path": str(target), "format": "xlsx", "extractor": "zip-xml", "sheets": sheets}


def _extract_with_markitdown(target: Path, doc_fmt: str) -> dict[str, Any] | None:
    """Fallback extractor using markitdown when specialized Office libraries are absent."""
    markitdown_mod = _try_import("markitdown")
    if markitdown_mod is None:
        return None
    converter = markitdown_mod.MarkItDown()
    res = converter.convert(str(target))
    text = str(getattr(res, "text_content", "") or "")
    return {"path": str(target), "format": doc_fmt, "extractor": "markitdown", "text": text}


def extract_office(path: str | Path, *, max_rows_per_sheet: int = 500) -> dict[str, Any]:
    """Extract structured text, tables, slides, or sheets from Office files.

    Args:
        path: Path to a ``.docx``, ``.pptx``, or ``.xlsx`` file.
        max_rows_per_sheet: Maximum rows per spreadsheet sheet for ``.xlsx`` extraction.

    Returns:
        Structured dictionary containing extracted document sections.
    """
    target = _resolve_file(path)
    doc_fmt = _detect_format(target)
    if doc_fmt not in {"docx", "pptx", "xlsx"}:
        raise ValueError(f"Expected an Office file (.docx/.pptx/.xlsx), got {target.suffix!r}")
    if max_rows_per_sheet <= 0:
        raise ValueError("max_rows_per_sheet must be positive")

    if doc_fmt == "docx":
        docx_mod = _try_import("docx")
        if docx_mod is not None:
            doc = docx_mod.Document(str(target))
            paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
            tables = [[[cell.text.strip() for cell in row.cells] for row in table.rows] for table in doc.tables]
            return {
                "path": str(target),
                "format": "docx",
                "extractor": "python-docx",
                "paragraphs": paragraphs,
                "tables": tables,
                "text": "\n\n".join(paragraphs),
            }
        return _extract_with_markitdown(target, doc_fmt) or _extract_docx_zip(target)

    if doc_fmt == "pptx":
        pptx_mod = _try_import("pptx")
        if pptx_mod is not None:
            prs = pptx_mod.Presentation(str(target))
            slides: list[dict[str, Any]] = []
            for idx, slide in enumerate(prs.slides, start=1):
                texts = [
                    shape.text.strip()
                    for shape in slide.shapes
                    if getattr(shape, "has_text_frame", False) and shape.text.strip()
                ]
                tables = [
                    [[cell.text.strip() for cell in row.cells] for row in shape.table.rows]
                    for shape in slide.shapes
                    if getattr(shape, "has_table", False)
                ]
                notes = ""
                if getattr(slide, "has_notes_slide", False) and slide.notes_slide.notes_text_frame:
                    notes = slide.notes_slide.notes_text_frame.text.strip()
                slides.append({"slide": idx, "texts": texts, "notes": notes, "tables": tables})
            return {"path": str(target), "format": "pptx", "extractor": "python-pptx", "slides": slides}
        return _extract_with_markitdown(target, doc_fmt) or _extract_pptx_zip(target)

    openpyxl_mod = _try_import("openpyxl")
    if openpyxl_mod is not None:
        wb = openpyxl_mod.load_workbook(str(target), read_only=True, data_only=True)
        sheets: list[dict[str, Any]] = []
        try:
            for name in wb.sheetnames:
                ws = wb[name]
                rows: list[list[Any]] = []
                total_rows = 0
                for row in ws.iter_rows(values_only=True):
                    total_rows += 1
                    if len(rows) < max_rows_per_sheet:
                        rows.append(
                            [
                                cell if isinstance(cell, (str, int, float, bool)) or cell is None else str(cell)
                                for cell in row
                            ]
                        )
                sheets.append(
                    {
                        "name": name,
                        "rows": rows,
                        "total_rows": total_rows,
                        "truncated": total_rows > max_rows_per_sheet,
                    }
                )
        finally:
            wb.close()
        return {"path": str(target), "format": "xlsx", "extractor": "openpyxl", "sheets": sheets}
    return _extract_xlsx_zip(target, max_rows_per_sheet)


def run(
    path: str | Path,
    *,
    pages: list[int] | tuple[int, ...] | range | str | None = None,
    extract_tables: bool = True,
) -> dict[str, Any]:
    """Dispatch document extraction by file extension.

    Args:
        path: Path to the document.
        pages: Optional PDF page selector.
        extract_tables: Whether to extract PDF tables when ``pdfplumber`` is present.

    Returns:
        Structured extraction or image-inspection dictionary.
    """
    target = _resolve_file(path)
    doc_fmt = _detect_format(target)
    if doc_fmt == "pdf":
        return extract_pdf(target, pages=pages, extract_tables=extract_tables)
    if doc_fmt in {"docx", "pptx", "xlsx"}:
        return extract_office(target)
    if doc_fmt == "image":
        return inspect_image(target)

    raw_text = target.read_text(encoding="utf-8", errors="replace")
    if doc_fmt == "html":
        parser = _HtmlStripParser()
        parser.feed(raw_text)
        parser.close()
        cleaned = re.sub(r"\n{3,}", "\n\n", "".join(parser.text_parts)).strip()
        return {
            "path": str(target),
            "format": "html",
            "title": html.unescape(re.sub(r"\s+", " ", "".join(parser.title_parts)).strip()),
            "content": cleaned,
            "size_bytes": target.stat().st_size,
        }
    return {
        "path": str(target),
        "format": doc_fmt,
        "content": raw_text,
        "size_bytes": target.stat().st_size,
    }


def main(argv: list[str] | None = None) -> int:
    """Run the CLI entrypoint for inspecting or extracting documents."""
    parser = argparse.ArgumentParser(description="Inspect or extract PDF, Office, image, and text files.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="Inspect structural metadata without dumping text.")
    inspect_parser.add_argument("path", help="Document path.")

    extract_parser = subparsers.add_parser("extract", help="Extract content from a document.")
    extract_parser.add_argument("path", help="Document path.")
    extract_parser.add_argument("--pages", default=None, help="1-based PDF pages, e.g. '1-3,5'.")
    extract_parser.add_argument("--no-tables", action="store_true", help="Skip PDF table extraction.")
    extract_parser.add_argument("--max-chars-per-page", type=int, default=10_000)
    extract_parser.add_argument("--max-rows-per-sheet", type=int, default=500)
    extract_parser.add_argument("--resize-max-dim", type=int, default=None)
    extract_parser.add_argument("--output-path", default=None)

    args = parser.parse_args(argv)
    if args.command == "inspect":
        result = inspect_document(args.path)
    else:
        target = _resolve_file(args.path)
        doc_fmt = _detect_format(target)
        if doc_fmt == "pdf":
            result = extract_pdf(
                target,
                pages=args.pages,
                extract_tables=not args.no_tables,
                max_chars_per_page=args.max_chars_per_page,
            )
        elif doc_fmt in {"docx", "pptx", "xlsx"}:
            result = extract_office(target, max_rows_per_sheet=args.max_rows_per_sheet)
        elif doc_fmt == "image":
            result = inspect_image(target, resize_max_dim=args.resize_max_dim, output_path=args.output_path)
        else:
            result = run(target)
    sys.stdout.write(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
