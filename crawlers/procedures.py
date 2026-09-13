"""Keyword-based discovery of student procedure source candidates."""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from pathlib import Path
from collections import Counter
from urllib.error import HTTPError
from urllib.parse import urlsplit, urlunsplit

from crawlers.ctdt import now_iso
from database import KnowledgeStore
from extractors.html_extractor import parse_html, parse_tables
from pipeline.crawl import SourceFetcher


PROCEDURE_KEYWORDS = (
    "bảo lưu",
    "nghỉ học",
    "học trở lại",
    "tiếp tục học",
    "chuyển ngành",
    "chuyển trường",
    "học hai chương trình",
    "rút học phần",
    "học lại",
    "cải thiện điểm",
    "phúc khảo",
    "xét tốt nghiệp",
    "gia hạn học phí",
    "giấy xác nhận",
    "thẻ sinh viên",
)

SEED_URLS = (
    "https://phongctsv.ntu.edu.vn/van-ban-bieu-mau/bieu-mau",
    "https://phongctsv.ntu.edu.vn/van-ban-bieu-mau",
    "https://pdtdaihoc.ntu.edu.vn/",
)
WORKFLOW_URL = "https://phongctsv.ntu.edu.vn/en-us/van-ban-bieu-mau/quy-trinh-xu-ly-cong-viec"
WORKFLOW_NAMES = (
    "Quy trình nhận và quản lý hồ sơ sinh viên",
    "Quy trình đánh giá điểm rèn luyện của sinh viên",
    "Quy trình xét chuyển ngành, chuyển trường, chuyển bậc đào tạo, nghỉ học có thời hạn và thôi học",
)

EXCLUDED_LABELS = (
    "qđ 1052",
    "qđ 1965",
    "qđ 626",
    "qđ 753",
    "qđ 729",
    "qđ 317",
)


class ProcedureCrawler:
    def __init__(self, store: KnowledgeStore, raw_directory: str = "data/raw") -> None:
        self.store = store
        self.fetcher = SourceFetcher(store, raw_directory)
        self.stats: Counter[str] = Counter()

    @staticmethod
    def _https(url: str) -> str:
        parsed = urlsplit(url)
        return urlunsplit(("https", parsed.netloc, parsed.path, parsed.query, ""))

    @staticmethod
    def matched_keywords(label: str) -> list[str]:
        lowered = label.casefold()
        if any(exclusion in lowered for exclusion in EXCLUDED_LABELS):
            return []
        if "kế hoạch" in lowered and any(
            phrase in lowered for phrase in ("luận văn", "thạc sĩ", "tiến sĩ")
        ):
            return []
        return [keyword for keyword in PROCEDURE_KEYWORDS if keyword in lowered]

    def discover(self) -> dict[str, int]:
        for seed_url in SEED_URLS:
            try:
                html = self.fetcher.text(seed_url, category="procedure_index")
                _, links = parse_html(html, seed_url)
                for link in links:
                    matched = self.matched_keywords(link.text)
                    if not matched:
                        continue
                    url = self._https(link.url)
                    try:
                        domain = self.fetcher.validate(url)
                    except ValueError:
                        continue
                    candidate_id = hashlib.sha256(url.encode("utf-8")).hexdigest()
                    cursor = self.store.connection.execute(
                        """INSERT OR IGNORE INTO procedure_candidates
                           (candidate_id, name, matched_keywords_json, source_url,
                            source_domain, retrieved_at)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            candidate_id,
                            link.text,
                            json.dumps(matched, ensure_ascii=False),
                            url,
                            domain,
                            now_iso(),
                        ),
                    )
                    self.store.enqueue(url, "procedure_candidate")
                    self.stats["procedure_candidates_discovered"] += cursor.rowcount
                self.store.connection.commit()
            except Exception as error:
                self._log_error(seed_url, error)
        self.materialize_candidates()
        self.extract_workflow_procedures()
        return dict(self.stats)

    def materialize_candidates(self) -> int:
        count = 0
        for row in self.store.connection.execute("SELECT * FROM procedure_candidates"):
            self.store.connection.execute(
                """INSERT INTO procedures
                   (procedure_id, name, form, source_url, retrieved_at, status)
                   VALUES (?, ?, ?, ?, ?, 'candidate')
                   ON CONFLICT(procedure_id) DO UPDATE SET
                     name=excluded.name, form=excluded.form,
                     source_url=excluded.source_url, retrieved_at=excluded.retrieved_at""",
                (
                    row["candidate_id"],
                    row["name"],
                    row["source_url"],
                    row["source_url"],
                    row["retrieved_at"],
                ),
            )
            count += 1
        self.store.connection.commit()
        self.stats["procedures_candidate"] = count
        return count

    def extract_workflow_procedures(self) -> int:
        source = self.store.connection.execute(
            "SELECT raw_path, retrieved_at FROM sources WHERE url=?", (WORKFLOW_URL,)
        ).fetchone()
        if source is None:
            return 0
        html = Path(source["raw_path"]).read_text(encoding="utf-8-sig", errors="replace")
        tables = parse_tables(html)
        count = 0
        for name, table in zip(WORKFLOW_NAMES, tables):
            rows = [row for row in table[1:] if len(row) >= 4]
            processing_units = list(dict.fromkeys(row[2] for row in rows if row[2]))
            deadlines = [row[3] for row in rows if row[3]]
            documents = list(dict.fromkeys(row[4] for row in rows if len(row) > 4 and row[4]))
            legal_bases = list(dict.fromkeys(row[5] for row in rows if len(row) > 5 and row[5]))
            notes = [row[6] for row in rows if len(row) > 6 and row[6]]
            results = [row[1] for row in rows if "quyết định" in row[1].casefold()]
            procedure_id = hashlib.sha256(f"{name}|{WORKFLOW_URL}".encode("utf-8")).hexdigest()
            self.store.connection.execute(
                """INSERT INTO procedures
                   (procedure_id, name, applicable_to, conditions_json,
                    required_documents_json, processing_unit, deadline,
                    processing_time, result, form, legal_basis, source_url,
                    retrieved_at, status, procedure_kind, verification_note)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'verified', 'workflow', ?)
                   ON CONFLICT(procedure_id) DO UPDATE SET
                     conditions_json=excluded.conditions_json,
                     required_documents_json=excluded.required_documents_json,
                     processing_unit=excluded.processing_unit,
                     deadline=excluded.deadline,
                     processing_time=excluded.processing_time,
                     result=excluded.result, form=excluded.form,
                     legal_basis=excluded.legal_basis,
                     retrieved_at=excluded.retrieved_at, status='verified',
                     procedure_kind='workflow',
                     verification_note=excluded.verification_note""",
                (
                    procedure_id,
                    name,
                    "Sinh viên Trường Đại học Nha Trang",
                    json.dumps(notes, ensure_ascii=False),
                    json.dumps(documents, ensure_ascii=False),
                    "\n".join(processing_units) or None,
                    "\n".join(deadlines) or None,
                    "\n".join(deadlines) or None,
                    "\n".join(results) or None,
                    "\n".join(documents) or None,
                    "\n".join(legal_bases) or None,
                    WORKFLOW_URL,
                    source["retrieved_at"],
                    "Bảng quy trình chính thức nêu bước xử lý, đơn vị, thời hạn và hồ sơ.",
                ),
            )
            count += 1
        self.store.connection.commit()
        self.stats["procedures_verified"] = count
        return count

    def crawl_candidates(self, limit: int | None = None) -> dict[str, int]:
        for item in self.store.queued(["procedure_candidate"], limit):
            url = item["url"]
            attempted_at = now_iso()
            self.store.mark_queue(url, "running", attempted_at)
            try:
                path = urlsplit(url).path.casefold()
                if path.endswith(".pdf"):
                    source_type = "official_document"
                elif path.endswith((".doc", ".docx", ".xls", ".xlsx")):
                    source_type = "official_form"
                else:
                    source_type = "official_unit_page"
                self.fetcher.fetch(
                    url,
                    category="procedure_candidate",
                    source_type=source_type,
                )
                self.store.mark_queue(url, "done", now_iso())
                self.stats["procedure_candidates_crawled"] += 1
            except Exception as error:
                self.store.mark_queue(url, "failed", now_iso(), str(error))
                self._log_error(url, error)
        self.materialize_verified_forms()
        return dict(self.stats)

    @staticmethod
    def _docx_text(path: Path) -> str:
        """Read visible DOCX text with stdlib only; no Office dependency."""
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml").decode("utf-8", errors="replace")
        xml = re.sub(r"</w:(?:p|tr)>", "\n", xml)
        xml = re.sub(r"<w:tab\s*/>", "\t", xml)
        text = re.sub(r"<[^>]+>", "", xml)
        return re.sub(r"[ \t]+", " ", text).strip()

    @staticmethod
    def _form_metadata(name: str, text: str) -> dict[str, object]:
        lowered = name.casefold()
        applicable_to = (
            "Sinh viên hệ vừa làm vừa học còn học phần chưa hoàn thành"
            if "vừa làm vừa học" in lowered
            else "Sinh viên Trường Đại học Nha Trang"
        )
        processing_units = ["Hiệu trưởng Trường Đại học Nha Trang"]
        for label in (
            "Cố vấn học tập",
            "Trường chuyên ngành/Khoa",
            "Trường chuyên ngành/Khoa nơi sinh viên đang học",
            "Trường chuyên ngành/Khoa nơi sinh viên muốn chuyển đến",
            "Trung tâm Đào tạo Xuất sắc",
            "Phòng Công tác Chính trị và Sinh viên",
        ):
            compact = label.casefold().replace("trung tâm", "tt.").replace(
                "phòng công tác chính trị và sinh viên", "phòng ctsv"
            )
            if label.casefold() in text.casefold() or compact in text.casefold():
                processing_units.append(label)
        documents: list[str] = []
        if "chuyển trường" in lowered:
            documents = [
                "Giấy chứng nhận kết quả học tập và rèn luyện của trường đang học",
                "Giấy xác nhận di chuyển hộ khẩu hoặc chuyển công tác/địa điểm sản xuất kinh doanh nếu dùng làm lý do chuyển trường",
            ]
        conditions: list[str] = []
        if "bị tạm dừng" in lowered or "xin học lại" in lowered:
            conditions.append("Khai diện và quyết định tạm dừng học tương ứng trên đơn")
        if "nghỉ học tạm thời" in lowered:
            conditions.append("Khai thời hạn nghỉ và lý do trên đơn")
        if "chuyển ngành" in lowered:
            conditions.append("Khai điểm tuyển sinh, ngành/lớp hiện tại và ngành/lớp đề nghị chuyển đến")
        return {
            "applicable_to": applicable_to,
            "conditions": conditions,
            "documents": documents,
            "processing_units": list(dict.fromkeys(processing_units)),
        }

    def materialize_verified_forms(self) -> int:
        """Promote downloaded official forms without inventing workflow fields."""
        count = 0
        rows = self.store.connection.execute(
            """SELECT p.*, s.raw_path, s.retrieved_at AS source_retrieved_at
               FROM procedure_candidates p JOIN sources s ON s.url=p.source_url
               WHERE s.source_type='official_form' AND lower(s.raw_path) LIKE '%.docx'"""
        ).fetchall()
        for row in rows:
            text = self._docx_text(Path(row["raw_path"]))
            metadata = self._form_metadata(row["name"], text)
            self.store.connection.execute(
                """UPDATE procedures SET applicable_to=?, conditions_json=?,
                       required_documents_json=?, processing_unit=?, result=NULL,
                       form=?, legal_basis=NULL, retrieved_at=?, status='verified',
                       procedure_kind='official_form', verification_note=?
                   WHERE procedure_id=?""",
                (
                    metadata["applicable_to"],
                    json.dumps(metadata["conditions"], ensure_ascii=False),
                    json.dumps(metadata["documents"], ensure_ascii=False),
                    "\n".join(metadata["processing_units"]),
                    row["source_url"],
                    row["source_retrieved_at"] or row["retrieved_at"],
                    "Biểu mẫu chính thức xác minh tên, trường thông tin và tuyến ký duyệt; nguồn không nêu thời hạn, phí hay kết quả xử lý.",
                    row["candidate_id"],
                ),
            )
            count += 1
        self.store.connection.commit()
        self.stats["procedure_forms_verified"] = count
        return count

    def _log_error(self, url: str, error: Exception) -> None:
        status = error.code if isinstance(error, HTTPError) else None
        self.store.connection.execute(
            "INSERT INTO crawl_errors(url, http_status, error, created_at) VALUES (?, ?, ?, ?)",
            (url, status, str(error), now_iso()),
        )
        self.store.connection.commit()
        self.stats["procedure_errors"] += 1
