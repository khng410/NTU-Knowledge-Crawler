"""PDF text extraction with a macOS Vision fallback for scanned documents."""

from __future__ import annotations

import json
import hashlib
import platform
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


def extract_pdf_text(path: str | Path, *, allow_ocr: bool = True) -> str:
    """Return embedded PDF text when available; never fabricate OCR output."""
    source = Path(path)
    if shutil.which("pdftotext"):
        try:
            result = subprocess.run(
                ["pdftotext", "-layout", str(source), "-"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except subprocess.TimeoutExpired:
            result = None
        if result is not None and result.stdout.strip():
            return result.stdout
    if not allow_ocr:
        return ""
    return "\n".join(item["text"] for item in ocr_pdf(source))


def extract_explicit_total_credits(path: str | Path) -> tuple[int | float, str] | None:
    """Return an explicitly labelled total, never a sum of course rows."""
    text = extract_pdf_text(path, allow_ocr=False)
    matches: list[tuple[float, int]] = []
    for page_number, page in enumerate(text.split("\f"), start=1):
        for line in page.splitlines():
            normalized = " ".join(line.split())
            match = re.search(
                r"(?:tổng\s+(?:số\s+)?tín\s+chỉ|tổng\s+số\s+tc)\s*[:：]?\s*(\d{2,3}(?:[.,]\d+)?)\b",
                normalized,
                re.I,
            )
            if match:
                matches.append((float(match.group(1).replace(",", ".")), page_number))
                continue
            # Some exported tables split a three-digit total vertically beside
            # the label. The summary row remains explicit as
            # "Tổng cộng <credits> 100[,00] ...".
            summary = re.match(
                r"tổng\s+cộng\s+(\d{2,3}(?:[.,]\d+)?)\s+100(?:[.,]0+)?\b",
                normalized,
                re.I,
            )
            if summary:
                matches.append((float(summary.group(1).replace(",", ".")), page_number))
    values = {value for value, _ in matches}
    if len(values) != 1:
        return None
    value = values.pop()
    page = next(page for found, page in matches if found == value)
    rendered: int | float = int(value) if value.is_integer() else value
    return rendered, f"Tổng tín chỉ được nêu trực tiếp trong bảng, trang PDF {page}"


def ocr_pdf(path: str | Path) -> list[dict[str, Any]]:
    """OCR a scanned PDF and retain page coordinates and confidence."""
    source = Path(path)
    cache_directory = Path(__file__).resolve().parent.parent / "data" / "extracted" / "ocr"
    digest = hashlib.sha256(b"vision-v2\0" + source.read_bytes()).hexdigest()
    cache_path = cache_directory / f"{digest}.jsonl"
    if cache_path.exists():
        return [json.loads(line) for line in cache_path.read_text(encoding="utf-8").splitlines() if line]
    if platform.system() != "Darwin" or not shutil.which("swift"):
        return []
    script = Path(__file__).resolve().parent.parent / "scripts" / "pdf_ocr.swift"
    result = subprocess.run(
        ["swift", str(script), str(source.resolve())],
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Vision OCR failed")
    records: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("text"):
            records.append(record)
    cache_directory.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    return records
