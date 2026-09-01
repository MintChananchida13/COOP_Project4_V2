import re
import unicodedata
from typing import Any, Dict, List, Optional


_WHITESPACE_RE = re.compile(r"[ \t\r\f\v]+")
_DOTTED_LINE_RE = re.compile(r"^[\s.·•…_\-=~*|/\\:;,]+$")
_REPEATED_LINE_NOISE_RE = re.compile(r"(?<![\wก-๙])[\s.·•…_\-=~*|/\\:;,]{3,}(?![\wก-๙])")
_PUNCTUATION_NOISE_RE = re.compile(r"^[.·•…_\-=~*|/\\:;,]{2,}$")
_THAI_RE = re.compile(r"[ก-๙]")
_SINGLE_ASCII_LETTER_RE = re.compile(r"^[A-Za-z]$")
_SINGLE_LETTER_CONTEXT_WORDS = {
    "ประเภท",
    "type",
    "class",
    "grade",
    "group",
    "category",
    "หมวด",
    "กลุ่ม",
    "ระดับ",
}

# Override mojibake-prone regex literals with Unicode escapes so Thai/noise
# detection stays stable across Windows terminals and source encodings.
_DOTTED_LINE_RE = re.compile(r"^[\s.\u00b7\u2022\u2026_\-=~*|/\\:;,]+$")
_REPEATED_LINE_NOISE_RE = re.compile(r"(?<![\w\u0e01-\u0e5b])[\s.\u00b7\u2022\u2026_\-=~*|/\\:;,]{3,}(?![\w\u0e01-\u0e5b])")
_PUNCTUATION_NOISE_RE = re.compile(r"^[.\u00b7\u2022\u2026_\-=~*|/\\:;,]{2,}$")
_THAI_RE = re.compile(r"[\u0e01-\u0e5b]")
_SINGLE_LETTER_CONTEXT_WORDS = {
    "\u0e1b\u0e23\u0e30\u0e40\u0e20\u0e17",
    "type",
    "class",
    "grade",
    "group",
    "category",
    "\u0e2b\u0e21\u0e27\u0e14",
    "\u0e01\u0e25\u0e38\u0e48\u0e21",
    "\u0e23\u0e30\u0e14\u0e31\u0e1a",
}


def cleanup_ocr_noise(text: str) -> str:
    lines: List[str] = []
    for raw_line in str(text or "").splitlines():
        line = _REPEATED_LINE_NOISE_RE.sub(" ", raw_line)
        line = _WHITESPACE_RE.sub(" ", line).strip()
        if not line:
            continue
        if len(line) >= 2 and _DOTTED_LINE_RE.match(line):
            continue

        has_thai = bool(_THAI_RE.search(line))
        tokens = line.split(" ")
        cleaned_tokens: List[str] = []
        for token in tokens:
            if not token:
                continue
            if _PUNCTUATION_NOISE_RE.match(token):
                continue
            if has_thai and _SINGLE_ASCII_LETTER_RE.match(token):
                previous = cleaned_tokens[-1].strip(" :：-").lower() if cleaned_tokens else ""
                if previous not in _SINGLE_LETTER_CONTEXT_WORDS:
                    continue
            cleaned_tokens.append(token)

        cleaned_line = _WHITESPACE_RE.sub(" ", " ".join(cleaned_tokens)).strip()
        if cleaned_line:
            lines.append(cleaned_line)
    return "\n".join(lines).strip()


def normalize_ocr_text(text: Any, cleanup_noise: bool = True) -> str:
    value = unicodedata.normalize("NFKC", str(text or ""))
    try:
        from pythainlp.util import normalize as thai_normalize  # type: ignore

        value = thai_normalize(value)
    except Exception:
        pass
    lines = [_WHITESPACE_RE.sub(" ", line).strip() for line in value.splitlines()]
    normalized = "\n".join(line for line in lines if line).strip()
    return cleanup_ocr_noise(normalized) if cleanup_noise else normalized


def _read_span(value: Any) -> int:
    try:
        return max(1, int(value or 1))
    except (TypeError, ValueError):
        return 1


def parse_table_html_with_bs4(html: str) -> Optional[Dict[str, Any]]:
    if not html:
        return None
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except Exception:
        return None

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception:
            return None

    table = soup.find("table") or soup
    rows: List[List[str]] = []
    cells: List[Dict[str, Any]] = []
    occupied: set[tuple[int, int]] = set()

    for row_index, tr in enumerate(table.find_all("tr")):
        row: List[str] = []
        col_index = 0
        for cell in tr.find_all(["th", "td"], recursive=False):
            while (row_index, col_index) in occupied:
                row.append("")
                col_index += 1
            text = normalize_ocr_text(cell.get_text(" ", strip=True))
            row_span = _read_span(cell.get("rowspan"))
            col_span = _read_span(cell.get("colspan"))
            row.append(text)
            for _ in range(col_span - 1):
                row.append("")
            cells.append(
                {
                    "row": row_index,
                    "col": col_index,
                    "text": text,
                    "rowSpan": row_span,
                    "colSpan": col_span,
                    "ocrText": text,
                    "groundTruth": text,
                }
            )
            for row_offset in range(row_span):
                for col_offset in range(col_span):
                    occupied.add((row_index + row_offset, col_index + col_offset))
                    if row_offset != 0 or col_offset != 0:
                        cells.append(
                            {
                                "row": row_index + row_offset,
                                "col": col_index + col_offset,
                                "text": "",
                                "rowSpan": 1,
                                "colSpan": 1,
                                "ocrText": "",
                                "groundTruth": "",
                                "hidden": True,
                            }
                        )
            col_index += col_span
        rows.append(row)

    if not rows:
        return None
    max_columns = max((len(row) for row in rows), default=0)
    normalized_rows = [row + [""] * (max_columns - len(row)) for row in rows]
    return {
        "rows": normalized_rows,
        "cells": cells,
        "headerRowCount": 1,
        "parser": "beautifulsoup4+lxml",
    }


def normalize_table_rows(rows: List[List[Any]], preserve_empty_rows: bool = True) -> List[List[str]]:
    if not rows:
        return []
    normalized = [[normalize_ocr_text(cell) for cell in row] for row in rows]
    max_columns = max((len(row) for row in normalized), default=0)
    normalized = [row + [""] * (max_columns - len(row)) for row in normalized]
    if preserve_empty_rows:
        return normalized
    return [row for row in normalized if any(cell.strip() for cell in row)]
