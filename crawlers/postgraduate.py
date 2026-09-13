"""Graduate curriculum and admission crawler."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from typing import Any
from urllib.error import HTTPError

from crawlers.ctdt import BASE_URL, now_iso
from database import KnowledgeStore
from extractors.html_extractor import parse_html, section, year_from_text
from extractors.table_extractor import extract_program
from pipeline.crawl import SourceFetcher


PROGRAM_INDEXES = {
    "master": f"{BASE_URL}/api/curriculum/publicCTDT/nganhDaoTaos/caohoc",
    "doctoral": f"{BASE_URL}/api/curriculum/publicCTDT/nganhDaoTaos/nghiencuusinh",
}
ADMISSION_INDEXES = {
    "master": "https://pdtsaudaihoc.ntu.edu.vn/danh-muc/tuyen-sinh",
    "doctoral": "https://pdtsaudaihoc.ntu.edu.vn/thong-bao-tuyen-sinh-tien-si?view=all",
}


class PostgraduateCrawler:
    def __init__(self, store: KnowledgeStore, raw_directory: str = "data/raw") -> None:
        self.store = store
        self.fetcher = SourceFetcher(store, raw_directory)
        self.stats: Counter[str] = Counter()

    def discover(self) -> dict[str, int]:
        for level, url in PROGRAM_INDEXES.items():
            majors = self.fetcher.json(url, category="postgraduate_index")
            for major in majors:
                for summary in major.get("chuongTrinhDTs") or []:
                    external_id = summary["chuongTrinhDTID"]
                    detail_url = f"{BASE_URL}/api/curriculum/publicCTDT/hocPhanKhungCTDT/{external_id}"
                    self.store.enqueue(detail_url, f"postgraduate_program_{level}")
                    self._save_program_summary(level, major, summary, detail_url)
                    self.stats[f"{level}_programs_discovered"] += 1

        for level, index_url in ADMISSION_INDEXES.items():
            html = self.fetcher.text(index_url, category="postgraduate_admission_index")
            _, links = parse_html(html, index_url)
            seen: set[str] = set()
            for link in links:
                lowered = link.text.casefold()
                matches_level = "thạc sĩ" in lowered if level == "master" else (
                    "tiến sĩ" in lowered or "nghiên cứu sinh" in lowered
                )
                if (
                    "tuyển sinh" not in lowered
                    or "trúng tuyển" in lowered
                    or not matches_level
                    or year_from_text(link.text) is None
                    or link.url in seen
                ):
                    continue
                seen.add(link.url)
                self.store.enqueue(link.url, f"postgraduate_admission_{level}")
                self._save_admission_placeholder(level, link.text, link.url)
                self.stats[f"{level}_admissions_discovered"] += 1
        return dict(self.stats)

    def _save_program_summary(
        self, level: str, major: dict[str, Any], summary: dict[str, Any], source_url: str
    ) -> None:
        external_id = summary["chuongTrinhDTID"]
        cohort = f"K{summary['apDungTuKhoa']}" if summary.get("apDungTuKhoa") is not None else None
        program_id = f"NTU-GRAD-{level.upper()}-{summary.get('maNganh') or 'UNKNOWN'}-{external_id}"
        self.store.connection.execute(
            """INSERT INTO graduate_programs
               (program_id, external_id, degree_level, major_code, major_name,
                program_name, orientation, cohort, source_url, retrieved_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(program_id) DO UPDATE SET
                 major_name=excluded.major_name, program_name=excluded.program_name,
                 orientation=excluded.orientation, cohort=excluded.cohort,
                 source_url=excluded.source_url, retrieved_at=excluded.retrieved_at""",
            (
                program_id,
                external_id,
                level,
                str(summary.get("maNganh") or major.get("maNganh") or ""),
                summary.get("tenNganh") or major.get("tenNganh") or "",
                summary.get("tenChuongTrinh") or "",
                summary.get("tenDinhHuongDT"),
                cohort,
                source_url,
                now_iso(),
            ),
        )
        self.store.connection.commit()

    def _save_admission_placeholder(self, level: str, title: str, url: str) -> None:
        admission_id = hashlib.sha256(url.encode("utf-8")).hexdigest()
        self.store.connection.execute(
            """INSERT INTO graduate_admissions
               (admission_id, degree_level, admission_year, title, source_url, retrieved_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(admission_id) DO UPDATE SET
                 title=excluded.title, admission_year=excluded.admission_year,
                 retrieved_at=excluded.retrieved_at""",
            (admission_id, level, year_from_text(title), title, url, now_iso()),
        )
        self.store.connection.commit()

    def crawl_programs(self, limit: int | None = None) -> dict[str, int]:
        categories = ["postgraduate_program_master", "postgraduate_program_doctoral"]
        for item in self.store.queued(categories, limit):
            url = item["url"]
            attempted_at = now_iso()
            self.store.mark_queue(url, "running", attempted_at)
            try:
                payload = self.fetcher.json(url, category="postgraduate_program")
                existing = self.store.connection.execute(
                    "SELECT * FROM graduate_programs WHERE external_id=?",
                    (payload.get("chuongTrinhDTID"),),
                ).fetchone()
                if existing is None:
                    raise ValueError("Graduate program was not discovered")
                extracted = extract_program(
                    payload,
                    program_id=existing["program_id"],
                    source_url=url,
                    pdf_url=None,
                    retrieved_at=attempted_at,
                )
                self.store.connection.execute(
                    """UPDATE graduate_programs SET
                       major_code=?, major_name=?, program_name=?, orientation=?,
                       credits=?, duration=?, cohort=?, source_url=?, citation=?, retrieved_at=?
                       WHERE program_id=?""",
                    (
                        extracted["major_code"],
                        extracted["major_name"] or "",
                        extracted["program_name"] or "",
                        payload.get("tenDinhHuongDT"),
                        extracted["total_credits"],
                        extracted["duration"],
                        extracted["cohort"],
                        url,
                        f"chuongTrinhDTID={payload.get('chuongTrinhDTID')}",
                        attempted_at,
                        existing["program_id"],
                    ),
                )
                self.store.connection.commit()
                self.store.mark_queue(url, "done", now_iso())
                self.stats[f"{existing['degree_level']}_programs_crawled"] += 1
            except Exception as error:
                self._error(url, error)
        return dict(self.stats)

    def crawl_admissions(self, limit: int | None = None) -> dict[str, int]:
        categories = ["postgraduate_admission_master", "postgraduate_admission_doctoral"]
        for item in self.store.queued(categories, limit):
            url = item["url"]
            attempted_at = now_iso()
            self.store.mark_queue(url, "running", attempted_at)
            try:
                html = self.fetcher.text(url, category="postgraduate_admission")
                blocks, links = parse_html(html, url)
                existing = self.store.connection.execute(
                    "SELECT * FROM graduate_admissions WHERE source_url=?", (url,)
                ).fetchone()
                if existing is None:
                    raise ValueError("Admission notice was not discovered")
                entry = section(blocks, r"điều\s*kiện dự tuyển", r"^\s*3\.")
                language_entry = section(
                    blocks,
                    r"điều\s*kiện ngoại ngữ|ngoại ngữ đầu vào",
                    r"^\s*\d+(?:\.\d+)+\.",
                )
                language_exit = section(
                    blocks,
                    r"ngoại ngữ đầu ra",
                    r"^\s*\d+(?:\.\d+)*\.",
                )
                dates = section(blocks, r"thời gian tuyển sinh|kế hoạch tuyển sinh", r"^\s*\d+\.")
                forms = [
                    {"name": link.text, "url": link.url}
                    for link in links
                    if re.search(r"hồ sơ|biểu mẫu|phụ lục|đăng ký", link.text, re.I)
                ]
                decisions = [
                    {"name": link.text, "url": link.url}
                    for link in links
                    if re.search(r"quyết định|quy định", link.text, re.I)
                ]
                documents = [
                    {"name": link.text, "url": link.url}
                    for link in links
                    if re.search(r"\.(?:pdf|docx?|xlsx?)(?:$|[?#])", link.url, re.I)
                ]
                for document in documents:
                    try:
                        self.fetcher.fetch(
                            document["url"],
                            category="postgraduate_document",
                            source_type="official_document",
                        )
                        self.stats["postgraduate_documents_downloaded"] += 1
                    except Exception as document_error:
                        self.store.connection.execute(
                            "INSERT INTO crawl_errors(url, http_status, error, created_at) VALUES (?, ?, ?, ?)",
                            (
                                document["url"],
                                document_error.code if isinstance(document_error, HTTPError) else None,
                                str(document_error),
                                attempted_at,
                            ),
                        )
                        self.store.connection.commit()
                        self.stats["postgraduate_document_errors"] += 1
                self.store.connection.execute(
                    """UPDATE graduate_admissions SET entry_conditions=?,
                       language_entry_requirement=?, language_exit_requirement=?,
                       important_dates=?, forms_json=?, decisions_json=?, documents_json=?,
                       citation=?, retrieved_at=?
                       WHERE admission_id=?""",
                    (
                        entry,
                        language_entry,
                        language_exit,
                        dates,
                        json.dumps(forms, ensure_ascii=False),
                        json.dumps(decisions, ensure_ascii=False),
                        json.dumps(documents, ensure_ascii=False),
                        existing["title"],
                        attempted_at,
                        existing["admission_id"],
                    ),
                )
                self.store.connection.commit()
                self.store.mark_queue(url, "done", now_iso())
                self.stats[f"{existing['degree_level']}_admissions_crawled"] += 1
            except Exception as error:
                self._error(url, error)
        return dict(self.stats)

    def _error(self, url: str, error: Exception) -> None:
        attempted_at = now_iso()
        self.store.mark_queue(url, "failed", attempted_at, str(error))
        status = error.code if isinstance(error, HTTPError) else None
        self.store.connection.execute(
            "INSERT INTO crawl_errors(url, http_status, error, created_at) VALUES (?, ?, ?, ?)",
            (url, status, str(error), attempted_at),
        )
        self.store.connection.commit()
        self.stats["postgraduate_errors"] += 1
