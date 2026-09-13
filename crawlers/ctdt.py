"""Crawler for the public undergraduate curriculum API at ctdt.ntu.edu.vn."""

from __future__ import annotations

import hashlib
import json
import re
import ssl
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from database import KnowledgeStore
from extractors.table_extractor import extract_courses, extract_program, program_identifier
from extractors.pdf_extractor import extract_explicit_total_credits
from extractors.html_extractor import parse_tables


BASE_URL = "https://ctdt.ntu.edu.vn"
MAJORS_URL = f"{BASE_URL}/api/curriculum/publicCTDT/nganhDaoTaos/daihoc"
COHORTS_URL = f"{BASE_URL}/api/curriculum/publicCTDT/last5Years"
EXPECTED_COHORTS = tuple(f"K{number}" for number in range(63, 69))
VIETNAM_TIMEZONE = timezone(timedelta(hours=7))


def now_iso() -> str:
    return datetime.now(VIETNAM_TIMEZONE).isoformat(timespec="seconds")


class CTDTCrawler:
    def __init__(
        self,
        store: KnowledgeStore,
        raw_directory: str | Path = "data/raw/ctdt",
        *,
        timeout: float = 30,
        retries: int = 2,
    ) -> None:
        self.store = store
        self.raw_directory = Path(raw_directory)
        self.timeout = timeout
        self.retries = retries
        self.stats: Counter[str] = Counter()
        default_paths = ssl.get_default_verify_paths()
        system_ca = Path("/etc/ssl/cert.pem")
        self.ssl_context = (
            ssl.create_default_context(cafile=str(system_ca))
            if default_paths.cafile is None and system_ca.exists()
            else ssl.create_default_context()
        )

    def _validate_url(self, url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != "ctdt.ntu.edu.vn":
            raise ValueError(f"URL is outside the approved CTĐT source: {url!r}")

    def _download(self, url: str) -> tuple[bytes, str]:
        self._validate_url(url)
        request = Request(url, headers={"User-Agent": "NTUKnowledgeCrawler/0.1"})
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                with urlopen(request, timeout=self.timeout, context=self.ssl_context) as response:
                    return response.read(), response.headers.get_content_type()
            except (HTTPError, URLError, TimeoutError) as error:
                last_error = error
                if attempt < self.retries:
                    time.sleep(0.5 * (2**attempt))
        assert last_error is not None
        raise last_error

    def _fetch_and_store(self, url: str, category: str) -> tuple[bytes, bool]:
        content, content_type = self._download(url)
        digest = hashlib.sha256(content).hexdigest()
        extension = {
            "application/pdf": ".pdf",
            "application/json": ".json",
            "text/html": ".html",
        }.get(content_type, ".bin")
        raw_path = self.raw_directory / f"{digest}{extension}"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        if not raw_path.exists():
            raw_path.write_bytes(content)
        changed = self.store.save_source(
            {
                "url": url,
                "domain": "ctdt.ntu.edu.vn",
                "source_type": "official_program_pdf" if extension == ".pdf" else "official_unit_page",
                "category": category,
                "content_hash": digest,
                "raw_path": str(raw_path),
                "status": "success",
                "retrieved_at": now_iso(),
            }
        )
        self.stats["documents_changed" if changed else "documents_unchanged"] += 1
        return content, changed

    def _json(self, url: str, category: str) -> Any:
        content, _ = self._fetch_and_store(url, category)
        return json.loads(content.decode("utf-8-sig"))

    def discover(self) -> dict[str, int]:
        """Discover majors, cohort gaps, program detail pages, and PDFs."""

        retrieved_at = now_iso()
        cohorts_payload = self._json(COHORTS_URL, "ctdt_index")
        listed_cohorts = {
            f"K{item['khoa']}" for item in cohorts_payload if item.get("khoa") is not None
        }
        self.stats["cohorts_listed"] = len(listed_cohorts & set(EXPECTED_COHORTS))
        majors = self._json(MAJORS_URL, "ctdt_index")
        self.stats["majors_discovered"] = len(majors)

        all_programs = [
            program
            for major in majors
            for program in (major.get("chuongTrinhDTs") or [])
        ]
        key_counts = Counter(
            (
                str(item.get("maNganh") or ""),
                f"K{item.get('apDungTuKhoa')}",
                bool(item.get("coTTCLC")),
            )
            for item in all_programs
        )

        for major in majors:
            major_code = str(major.get("maNganh") or "")
            self.store.save_major(
                {
                    "major_id": major["nganhDaoTaoID"],
                    "major_code": major_code,
                    "major_name": major.get("tenNganh") or "",
                    "faculty": major.get("tenDonVi"),
                    "source_url": MAJORS_URL,
                    "retrieved_at": retrieved_at,
                }
            )
            programs = major.get("chuongTrinhDTs") or []
            found_cohorts = {
                f"K{item['apDungTuKhoa']}"
                for item in programs
                if item.get("apDungTuKhoa") is not None
            }

            for cohort in found_cohorts:
                missing_id = program_identifier(major_code, cohort, suffix="MISSING")
                self.store.connection.execute(
                    "DELETE FROM programs WHERE program_id=? AND crawl_status='missing'",
                    (missing_id,),
                )
                self.store.connection.execute(
                    "DELETE FROM program_gaps WHERE major_code=? AND cohort=?",
                    (major_code, cohort),
                )
            self.store.connection.commit()

            for cohort in EXPECTED_COHORTS:
                if cohort not in found_cohorts:
                    missing_id = program_identifier(major_code, cohort, suffix="MISSING")
                    self.store.save_program(
                        {
                            "program_id": missing_id,
                            "external_id": None,
                            "major_code": major_code,
                            "major_name": major.get("tenNganh"),
                            "program_name": None,
                            "cohort": cohort,
                            "faculty": major.get("tenDonVi"),
                            "degree_level": major.get("tenTrinhDo"),
                            "training_mode": None,
                            "duration": None,
                            "total_credits": None,
                            "language": None,
                            "degree": None,
                            "decision_number": None,
                            "decision_date": None,
                            "updated_date": None,
                            "plos_json": None,
                            "source_url": MAJORS_URL,
                            "pdf_url": None,
                            "crawl_status": "missing",
                            "content_status": "api_absent",
                            "retrieved_at": retrieved_at,
                        }
                    )
                    gap_id = hashlib.sha256(
                        f"{major_code}|{cohort}".encode("utf-8")
                    ).hexdigest()
                    self.store.connection.execute(
                        """INSERT INTO program_gaps
                           (gap_id, major_code, major_name, cohort, gap_status,
                            expected_reason, evidence_url, citation, review_note,
                            retrieved_at)
                           VALUES (?, ?, ?, ?, 'api_absent', ?, ?, ?, ?, ?)
                           ON CONFLICT(major_code, cohort) DO UPDATE SET
                             major_name=excluded.major_name,
                             evidence_url=excluded.evidence_url,
                             citation=excluded.citation,
                             retrieved_at=excluded.retrieved_at""",
                        (
                            gap_id, major_code, major.get("tenNganh"), cohort,
                            "Cross-product audit for configured K63-K68 scope; applicability not yet proven",
                            MAJORS_URL,
                            f"maNganh={major_code}; no chuongTrinhDT with apDungTuKhoa={cohort[1:]}",
                            "Absence from the current API is not evidence that the program never existed.",
                            retrieved_at,
                        ),
                    )
                    self.store.connection.commit()
                    self.stats["program_cohorts_missing"] += 1

            for summary in programs:
                external_id = summary["chuongTrinhDTID"]
                cohort = f"K{summary['apDungTuKhoa']}" if summary.get("apDungTuKhoa") is not None else "K-UNKNOWN"
                special = bool(summary.get("coTTCLC"))
                key = (major_code, cohort, special)
                suffix = external_id if key_counts[key] > 1 else None
                program_id = program_identifier(major_code, cohort, special=special, suffix=suffix)
                detail_url = f"{BASE_URL}/api/curriculum/publicCTDT/hocPhanKhungCTDT/{external_id}"
                pdf_url = f"{BASE_URL}/ctdt/{summary['filePDF']}" if summary.get("filePDF") else None
                existing_program = self.store.connection.execute(
                    "SELECT crawl_status FROM programs WHERE external_id=?",
                    (external_id,),
                ).fetchone()
                if existing_program is None or existing_program["crawl_status"] != "success":
                    self.store.save_program({
                        "program_id": program_id,
                        "external_id": external_id,
                        "major_code": major_code,
                        "major_name": major.get("tenNganh"),
                        "program_name": summary.get("tenChuongTrinh"),
                        "cohort": cohort if cohort != "K-UNKNOWN" else None,
                        "faculty": summary.get("tenDonVi") or major.get("tenDonVi"),
                        "degree_level": summary.get("tenTrinhDo"),
                        "training_mode": summary.get("tenHinhThucDT"),
                        "duration": None,
                        "total_credits": None,
                        "language": summary.get("tenNgonNgu"),
                        "degree": summary.get("tenVanBang"),
                        "decision_number": summary.get("soQuyetDinh"),
                        "decision_date": None,
                        "updated_date": summary.get("thoiGianXayDung"),
                        "plos_json": None,
                        "source_url": detail_url,
                        "pdf_url": pdf_url,
                        "crawl_status": "pending",
                        "retrieved_at": retrieved_at,
                    })
                # Program JSON is mutable while NTU is drafting it. Refresh it
                # on every discovery run while preserving content versions.
                self.store.enqueue(detail_url, "ctdt_program", refresh=True)
                if pdf_url:
                    self.store.enqueue(pdf_url, "ctdt_pdf")
                self.stats["programs_discovered"] += 1
        return dict(self.stats)

    def crawl_programs(self, *, limit: int | None = None) -> dict[str, int]:
        for queue_item in self.store.queued(["ctdt_program"], limit):
            url = queue_item["url"]
            attempted_at = now_iso()
            self.store.mark_queue(url, "running", attempted_at)
            try:
                payload = self._json(url, "ctdt_program")
                external_id = payload.get("chuongTrinhDTID")
                existing = self.store.connection.execute(
                    """SELECT program_id, pdf_url, pdf_status, fallback_url,
                              total_credits, credit_status, credit_method, credit_citation
                       FROM programs WHERE external_id=?""",
                    (external_id,),
                ).fetchone()
                if existing is None:
                    raise ValueError(f"Program {external_id!r} was not discovered")
                program = extract_program(
                    payload,
                    program_id=existing["program_id"],
                    source_url=url,
                    pdf_url=existing["pdf_url"],
                    retrieved_at=attempted_at,
                )
                if str(existing["pdf_status"] or "").startswith("missing_asset"):
                    program["pdf_status"] = existing["pdf_status"]
                    program["fallback_url"] = existing["fallback_url"]
                if program["total_credits"] is None and existing["credit_status"] == "verified":
                    for field in (
                        "total_credits", "credit_status", "credit_method", "credit_citation"
                    ):
                        program[field] = existing[field]
                self.store.save_program(program)
                course_count = self.store.replace_courses(
                    program["program_id"],
                    extract_courses(
                        payload,
                        program_id=program["program_id"],
                        source_url=url,
                        retrieved_at=attempted_at,
                    ),
                )
                self.store.mark_queue(url, "done", now_iso())
                self.stats["programs_crawled"] += 1
                self.stats["courses_extracted"] += course_count
            except Exception as error:
                self._record_error(url, error)
        return dict(self.stats)

    def crawl_pdfs(self, *, limit: int | None = None) -> dict[str, int]:
        for queue_item in self.store.queued(["ctdt_pdf"], limit):
            url = queue_item["url"]
            attempted_at = now_iso()
            self.store.mark_queue(url, "running", attempted_at)
            try:
                self._fetch_and_store(url, "ctdt_pdf")
                self.store.connection.execute(
                    "UPDATE programs SET pdf_status='available' WHERE pdf_url=?",
                    (url,),
                )
                self.store.connection.commit()
                self.store.mark_queue(url, "done", now_iso())
                self.stats["pdfs_downloaded"] += 1
            except Exception as error:
                if isinstance(error, HTTPError) and error.code == 404:
                    self.store.connection.execute(
                        "UPDATE programs SET pdf_status='missing_asset' WHERE pdf_url=?",
                        (url,),
                    )
                    self.store.connection.commit()
                    self.store.mark_queue(url, "failed", attempted_at, str(error))
                    self.stats["pdf_assets_missing"] += 1
                else:
                    self._record_error(url, error)
        self.backfill_pdf_credits()
        self.backfill_missing_pdf_fallbacks()
        return dict(self.stats)

    def backfill_pdf_credits(self) -> int:
        rows = list(
            self.store.connection.execute(
                """SELECT p.program_id, p.pdf_url, s.raw_path
                   FROM programs p JOIN sources s ON s.url=p.pdf_url
                   WHERE p.crawl_status='success' AND p.total_credits IS NULL
                     AND p.content_status='complete'
                     AND s.source_type='official_program_pdf'"""
            )
        )
        updated = 0
        for row in rows:
            evidence = extract_explicit_total_credits(row["raw_path"])
            if evidence is None:
                continue
            value, citation = evidence
            self.store.connection.execute(
                """UPDATE programs SET total_credits=?, credit_status='verified',
                          credit_method='official_pdf_explicit', credit_citation=?
                   WHERE program_id=?""",
                (value, citation, row["program_id"]),
            )
            updated += 1
        self.store.connection.commit()
        self.stats["program_credits_from_pdf"] += updated
        return updated

    def backfill_missing_pdf_fallbacks(self) -> int:
        """Use the official HTML curriculum view when its linked PDF is gone."""
        rows = list(
            self.store.connection.execute(
                """SELECT program_id, external_id FROM programs
                   WHERE pdf_status='missing_asset' AND external_id IS NOT NULL"""
            )
        )
        resolved = 0
        for row in rows:
            url = f"{BASE_URL}/chuongtrinhdt/{row['external_id']}"
            try:
                content, _ = self._fetch_and_store(url, "ctdt_program_html")
            except Exception:
                continue
            total_credits = None
            for table in parse_tables(content.decode("utf-8-sig", errors="replace")):
                for cells in table:
                    if not cells or "tổng số tín chỉ" not in cells[0].casefold():
                        continue
                    total_credits = next(
                        (
                            float(value.replace(",", "."))
                            for cell in cells[1:]
                            for value in [cell.strip()]
                            if re.fullmatch(r"\d{2,3}(?:[.,]\d+)?", value)
                        ),
                        None,
                    )
                    if total_credits is not None:
                        break
                if total_credits is not None:
                    break
            values = {
                "pdf_status": "missing_asset_with_html_fallback",
                "fallback_url": url,
            }
            if total_credits is not None:
                values.update(
                    {
                        "total_credits": int(total_credits) if total_credits.is_integer() else total_credits,
                        "credit_status": "verified",
                        "credit_method": "official_html_explicit",
                        "credit_citation": "Tổng số tín chỉ, bảng CTĐT HTML chính thức",
                    }
                )
            assignments = ", ".join(f"{key}=?" for key in values)
            self.store.connection.execute(
                f"UPDATE programs SET {assignments} WHERE program_id=?",
                (*values.values(), row["program_id"]),
            )
            resolved += 1
        self.store.connection.commit()
        self.stats["pdf_assets_with_html_fallback"] += resolved
        return resolved

    def _record_error(self, url: str, error: Exception) -> None:
        attempted_at = now_iso()
        self.store.mark_queue(url, "failed", attempted_at, str(error))
        status = error.code if isinstance(error, HTTPError) else None
        self.store.connection.execute(
            """INSERT INTO crawl_errors(url, http_status, error, created_at)
               SELECT ?, ?, ?, ? WHERE NOT EXISTS (
                   SELECT 1 FROM crawl_errors WHERE url=? AND error=?
               )""",
            (url, status, str(error), attempted_at, url, str(error)),
        )
        self.store.connection.commit()
        self.stats["errors"] += 1
