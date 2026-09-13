"""Export strict, source-per-row NTU student-information Markdown."""

from __future__ import annotations

import json
import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit

from database import KnowledgeStore
from pipeline.crawl import SourceFetcher, load_approved_domains
from pipeline.validate import quality_checks


VIETNAM_TIMEZONE = timezone(timedelta(hours=7))
ALLOWED_TYPES = {"thủ tục", "quy tắc", "đơn vị", "biểu mẫu", "ngành", "khái niệm", "mức thu"}
EXCLUDED_DOCUMENTS = re.compile(
    r"(?:QĐ|QD|quyết định|quyet[- ]dinh)[^\n|]{0,35}(?:1052|1965|626|753|729|317)\b",
    re.IGNORECASE,
)
LANGUAGE_OR_IT = re.compile(
    r"ngoại ngữ|tiếng anh|tiếng nhật|tin học|công nghệ thông tin", re.IGNORECASE
)
TUITION_RELEVANT = re.compile(
    r"đại học|tín chỉ|toàn khóa|thạc sĩ|tiến sĩ|vừa làm vừa học|đào tạo từ xa|học lại|quá thời hạn",
    re.IGNORECASE,
)
PERSONAL_CONTACT = re.compile(
    r"\b(?:liên hệ|gặp)\s+(?:cô|thầy|ông|bà)\s+[^:;,]{1,60}", re.IGNORECASE
)
SECTION_TITLES = {
    1: "Đơn vị và đầu mối phục vụ sinh viên",
    2: "Cổng thông tin, tài khoản, thủ tục và biểu mẫu",
    3: "Học phí, miễn giảm và chính sách hỗ trợ",
    4: "Điểm rèn luyện, khen thưởng và kỷ luật",
    5: "Ký túc xá, thư viện, việc làm và dịch vụ sinh viên",
    6: "Chương trình đào tạo đại học theo ngành và khóa",
    7: "Tuyển sinh đại học chính quy năm 2026",
    8: "Sau đại học, vừa làm vừa học, liên thông và văn bằng 2",
    9: "Văn bản pháp quy và nguyên tắc áp dụng",
}


@dataclass(frozen=True)
class StudentFact:
    entity: str
    fact_type: str
    content: str
    relation: str
    citation: str
    document: str
    url: str
    section: int


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = re.sub(r"\s+", " ", unicodedata.normalize("NFC", str(value))).strip()
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    # Repair a few deterministic spacing artefacts emitted by the official
    # pages' PDF-to-HTML conversion without otherwise rewriting source text.
    for broken, repaired in (
        (r"\bt\s+uyển\b", "tuyển"),
        (r"\bn\s+ghiệp\b", "nghiệp"),
        (r"\bv\s+ào\b", "vào"),
        (r"\bs\s+ố\b", "số"),
    ):
        text = re.sub(broken, repaired, text, flags=re.IGNORECASE)
    return text


def _markdown(value: str) -> str:
    return _clean(value).replace("\\", "\\\\").replace("|", "\\|")


def _json_list(value: str | None) -> list[Any]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _document_name(url: str, fallback: str) -> str:
    host = urlsplit(url).hostname or ""
    return fallback or f"Nguồn chính thức {host}"


def _locator(text: str, prefix: str = "đoạn bắt đầu") -> str:
    compact = _clean(text).rstrip(".;:")
    if len(compact) > 72:
        compact = compact[:69].rstrip() + "…"
    return f"{prefix} “{compact}”"


def _atomic_clauses(value: Any, *, max_chars: int = 300) -> list[str]:
    """Split source text at explicit sentence/list boundaries without paraphrasing."""
    if value is None:
        return []
    text = unicodedata.normalize("NFC", str(value)).replace("\r", "\n")
    first_pass = re.split(r"\n+|(?=\s*[•▪]\s*)", text)
    clauses: list[str] = []
    for part in first_pass:
        part = re.sub(r"^[\s•▪+-]+", "", part).strip()
        if not part:
            continue
        # Semicolons inside parentheses often separate certificate names, for
        # example `DELF B1; TCF 350–400`; those must remain in one fact.
        depth = 0
        start = 0
        top_level: list[str] = []
        for index, character in enumerate(part):
            if character in "([":
                depth += 1
            elif character in ")]" and depth:
                depth -= 1
            elif character == ";" and depth == 0:
                top_level.append(part[start:index + 1])
                start = index + 1
        top_level.append(part[start:])
        for clause in top_level:
            clause = _clean(clause).lstrip(": ")
            if not clause:
                continue
            if len(clause) <= max_chars:
                clauses.append(clause)
                continue
            sentences = re.split(r"(?<=[.!?])\s+(?=[A-ZÀ-ỸĐ0-9])", clause)
            clauses.extend(_clean(sentence) for sentence in sentences if _clean(sentence))
    return clauses


def _admission_condition_clauses(value: Any) -> list[str]:
    text = _clean(value)
    if not text:
        return []
    if "Tổ hợp xét tuyển:" in text:
        text = text.replace("Tổ hợp xét tuyển:", "", 1).strip()
    parts = re.split(r"(?=Tổ hợp\s+\d+\s*:|Có điều kiện Tiếng Anh)", text)
    return [part.strip().rstrip(".") + "." for part in parts if part.strip()]


def _append(
    facts: list[StudentFact],
    *,
    entity: Any,
    fact_type: str,
    content: Any,
    citation: Any,
    document: Any,
    url: Any,
    section: int,
    relation: Any = "",
) -> None:
    fact = StudentFact(
        _clean(entity), fact_type, _clean(content), _clean(relation),
        _clean(citation), _clean(document), _clean(url), section,
    )
    if not fact.entity or not fact.content or not fact.citation or not fact.url:
        return
    if fact.fact_type not in ALLOWED_TYPES:
        raise ValueError(f"Loại fact không được phép: {fact.fact_type}")
    facts.append(fact)


def _program_facts(store: KnowledgeStore, facts: list[StudentFact]) -> None:
    rows = store.connection.execute(
        """SELECT * FROM programs WHERE crawl_status='success'
           ORDER BY major_code, cohort, external_id"""
    )
    for row in rows:
        external_id = row["external_id"]
        entity = f"{row['program_name'] or row['major_name']} — {row['cohort']}"
        source = row["source_url"]
        document = f"API CTĐT NTU, chuongTrinhDTID={external_id}"
        values = (
            ("Mã ngành", row["major_code"], "maNganh", ""),
            ("Thời gian đào tạo", row["duration"], "thoiGianDaoTao", ""),
            ("Đơn vị quản lý", row["faculty"], "tenKhoa", f"thuộc khoa → {row['faculty']}" if row["faculty"] else ""),
        )
        for label, value, field, relation in values:
            if value:
                _append(
                    facts, entity=entity, fact_type="ngành",
                    content=f"{label}: {_clean(value)}.", relation=relation,
                    citation=f"trường {field}; chuongTrinhDTID={external_id}",
                    document=document, url=source, section=6,
                )
        if row["total_credits"] is not None and row["credit_citation"]:
            credit_url = row["pdf_url"] if (row["credit_method"] or "").startswith("official_pdf") else source
            _append(
                facts, entity=entity, fact_type="ngành",
                content=f"Tổng số tín chỉ: {row['total_credits']:g}.",
                citation=row["credit_citation"],
                document=f"Chương trình đào tạo {entity}", url=credit_url,
                section=6,
            )
        for outcome in _json_list(row["plos_json"]):
            if not isinstance(outcome, dict):
                continue
            content = _clean(outcome.get("content"))
            if content and LANGUAGE_OR_IT.search(content):
                code = _clean(outcome.get("code")) or "không mã"
                for clause in _atomic_clauses(content):
                    if LANGUAGE_OR_IT.search(clause):
                        _append(
                            facts, entity=entity, fact_type="quy tắc", content=clause,
                            relation=f"áp dụng cho → {entity}",
                            citation=f"chuẩn đầu ra {code}; chuongTrinhDTID={external_id}",
                            document=document, url=source, section=6,
                        )


def _graduate_program_facts(store: KnowledgeStore, facts: list[StudentFact]) -> None:
    for row in store.connection.execute(
        "SELECT * FROM graduate_programs ORDER BY degree_level, major_code, cohort"
    ):
        entity = f"{row['program_name']} — {row['cohort']}"
        base = row["citation"] or f"chuongTrinhDTID={row['external_id']}"
        document = f"API CTĐT NTU, {base}"
        for label, field, value in (
            ("Mã ngành", "maNganh", row["major_code"]),
            ("Định hướng", "dinhHuong", row["orientation"]),
            ("Tổng số tín chỉ", "tongTinChi", row["credits"]),
            ("Thời gian đào tạo", "thoiGianDaoTao", row["duration"]),
        ):
            if value is not None and _clean(value):
                rendered = f"{value:g}" if isinstance(value, float) else _clean(value)
                _append(
                    facts, entity=entity, fact_type="ngành",
                    content=f"{label}: {rendered}.", citation=f"trường {field}; {base}",
                    document=document, url=row["source_url"], section=8,
                )
        for label, field, value in (
            ("Điều kiện đầu vào", "dieuKienDauVao", row["entry_conditions"]),
            ("Chuẩn ngoại ngữ đầu vào", "ngoaiNguDauVao", row["language_entry_requirement"]),
            ("Chuẩn ngoại ngữ đầu ra", "ngoaiNguDauRa", row["language_exit_requirement"]),
            ("Điều kiện tốt nghiệp", "dieuKienTotNghiep", row["graduation_conditions"]),
        ):
            if value:
                for clause in _atomic_clauses(value):
                    _append(
                        facts, entity=entity, fact_type="quy tắc", content=clause,
                        relation=f"áp dụng cho → {entity}", citation=f"trường {field}; {base}",
                        document=document, url=row["source_url"], section=8,
                    )


def _procedure_facts(store: KnowledgeStore, facts: list[StudentFact]) -> None:
    rows = store.connection.execute(
        """SELECT * FROM procedures
           WHERE status='verified' AND procedure_kind='official_form'
           ORDER BY name"""
    )
    for row in rows:
        entity = row["name"]
        document = "Biểu mẫu/thủ tục chính thức Phòng Công tác Chính trị và Sinh viên"
        if row["applicable_to"]:
            _append(
                facts, entity=entity, fact_type="thủ tục", content=row["applicable_to"],
                relation=f"áp dụng cho → {row['applicable_to']}", citation="mục đối tượng/đương sự",
                document=document, url=row["source_url"], section=2,
            )
        unit_text = row["submission_unit"] or row["processing_unit"]
        if unit_text:
            relation_name = "nộp tại" if row["submission_unit"] else "xử lý bởi"
        for unit in (item for item in re.split(r"[\r\n]+", unit_text or "") if _clean(item)):
            _append(
                facts, entity=entity, fact_type="thủ tục", content=unit,
                relation=f"{relation_name} → {_clean(unit)}", citation="mục nơi nộp/tuyến ký duyệt",
                document=document, url=row["source_url"], section=2,
            )
        for item in _json_list(row["conditions_json"]):
            _append(
                facts, entity=entity, fact_type="quy tắc", content=item,
                relation=f"áp dụng cho → {entity}", citation=_locator(item, "mục điều kiện bắt đầu"),
                document=document, url=row["source_url"], section=2,
            )
        for item in _json_list(row["required_documents_json"]):
            _append(
                facts, entity=entity, fact_type="thủ tục", content=item,
                relation=f"yêu cầu giấy tờ → {_clean(item)}", citation=_locator(item, "mục hồ sơ bắt đầu"),
                document=document, url=row["source_url"], section=2,
            )
        for label, field, relation in (
            ("Thời hạn", "deadline", "thời hạn"),
            ("Thời gian xử lý", "processing_time", "thời hạn"),
            ("Kết quả", "result", ""),
            ("Lệ phí", "fee", ""),
            ("Căn cứ", "legal_basis", "quy định bởi"),
        ):
            value = row[field]
            if value:
                _append(
                    facts, entity=entity, fact_type="thủ tục",
                    content=f"{label}: {_clean(value)}.",
                    relation=f"{relation} → {_clean(value)}" if relation else "",
                    citation=f"mục {label.lower()}", document=document,
                    url=row["source_url"], section=2,
                )
        if row["form"]:
            form_name = unquote(Path(urlsplit(row["form"]).path).name)
            _append(
                facts, entity=entity, fact_type="biểu mẫu",
                content=f"Tệp biểu mẫu: {form_name}",
                relation=f"dùng mẫu → {_clean(row['form'])}", citation="tệp biểu mẫu chính thức",
                document=document, url=row["source_url"], section=2,
            )


def _unit_and_service_facts(store: KnowledgeStore, facts: list[StudentFact]) -> None:
    for row in store.connection.execute("SELECT * FROM units ORDER BY name"):
        for label, field in (
            ("Địa chỉ", "address"), ("Điện thoại", "phone"),
            ("Email", "email"), ("Giờ làm việc", "working_hours"),
        ):
            if row[field]:
                _append(
                    facts, entity=row["name"], fact_type="đơn vị",
                    content=f"{label}: {_clean(row[field])}.", citation=f"mục liên hệ — {label.lower()}",
                    document=f"Trang chính thức {row['name']}", url=row["source_url"], section=1,
                )
    rows = store.connection.execute(
        """SELECT s.*, u.name AS unit_name FROM student_services s
           JOIN units u ON u.unit_id=s.unit_id WHERE s.status='verified'
           ORDER BY u.name, s.name"""
    )
    for row in rows:
        content = row["description"] or row["name"]
        _append(
            facts, entity=row["name"], fact_type="khái niệm", content=content,
            relation=f"cung cấp bởi → {row['unit_name']}", citation=row["citation"],
            document=f"Trang dịch vụ {row['unit_name']}", url=row["source_url"], section=5,
        )


def _finance_facts(store: KnowledgeStore, facts: list[StudentFact]) -> None:
    rows = store.connection.execute(
        "SELECT * FROM financial_policies WHERE status='current' ORDER BY policy_type, title"
    )
    for row in rows:
        title = row["title"] or row["policy_type"]
        for raw_content in _json_list(row["conditions_json"]):
            blocks = _atomic_clauses(raw_content)
            for content in blocks:
                if not content or content.casefold() == _clean(title).casefold():
                    continue
                if content.casefold().endswith("xem tại đây"):
                    continue
                if row["policy_type"] == "tuition_rule" and not TUITION_RELEVANT.search(content):
                    continue
                if row["policy_type"] == "tuition_rule" and re.search(
                    r"mầm non|phổ thông", content, re.IGNORECASE
                ):
                    continue
                _append(
                    facts, entity=title, fact_type="quy tắc", content=content,
                    relation=f"áp dụng cho → {row['beneficiary']}" if row["beneficiary"] else "",
                    citation=_locator(content), document=title, url=row["source_url"], section=3,
                )
        if row["amount"] is not None or row["percentage"] is not None:
            value = row["amount"] if row["amount"] is not None else row["percentage"]
            suffix = " đồng" if row["amount"] is not None else "%"
            _append(
                facts, entity=title, fact_type="mức thu", content=f"Mức: {value:g}{suffix}.",
                relation=f"áp dụng cho → {row['beneficiary']}" if row["beneficiary"] else "",
                citation="mục mức tiền/mức hưởng", document=title,
                url=row["source_url"], section=3,
            )


def _discipline_facts(store: KnowledgeStore, facts: list[StudentFact]) -> None:
    for row in store.connection.execute("SELECT * FROM discipline_rules ORDER BY citation, rule_id"):
        for occurrence, field in enumerate(
            ("first_violation", "second_violation", "third_violation", "fourth_violation", "fifth_violation"), 1
        ):
            if row[field]:
                _append(
                    facts, entity=row["behavior"], fact_type="quy tắc", content=row[field],
                    relation=f"vi phạm lần {occurrence} → {_clean(row[field])}", citation=row["citation"],
                    document="QĐ 1351/QĐ-ĐHNT về văn hóa học đường", url=row["source_url"], section=4,
                )
        if row["sanction"]:
            _append(
                facts, entity=row["behavior"], fact_type="quy tắc", content=row["sanction"],
                citation=row["citation"], document="QĐ 1351/QĐ-ĐHNT về văn hóa học đường",
                url=row["source_url"], section=4,
            )


def _curated_facts(store: KnowledgeStore, facts: list[StudentFact]) -> None:
    """Load hand-reviewed atomic facts only when their source snapshot exists."""
    path = Path("config/student_facts.json")
    if not path.exists():
        return
    records = json.loads(path.read_text(encoding="utf-8"))
    snapshotted_urls = {
        row[0] for row in store.connection.execute(
            "SELECT url FROM sources WHERE status='success'"
        )
    }
    for record in records:
        if record.get("url") not in snapshotted_urls:
            continue
        _append(
            facts,
            entity=record.get("entity"),
            fact_type=record.get("fact_type", ""),
            content=record.get("content"),
            relation=record.get("relation", ""),
            citation=record.get("citation"),
            document=record.get("document"),
            url=record.get("url"),
            section=int(record.get("section", 9)),
        )


def crawl_curated_student_sources(
    store: KnowledgeStore, raw_directory: str | Path = "data/raw"
) -> dict[str, int]:
    """Snapshot every official URL referenced by the curated fact manifest."""
    path = Path("config/student_facts.json")
    records = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    urls = sorted({_clean(record.get("url")) for record in records if record.get("url")})
    fetcher = SourceFetcher(store, raw_directory)
    stats = {"student_fact_sources": len(urls), "student_fact_sources_fetched": 0,
             "student_fact_source_errors": 0}
    for url in urls:
        try:
            source_type = "official_document" if urlsplit(url).path.lower().endswith(
                (".pdf", ".doc", ".docx")
            ) else "official_unit_page"
            fetcher.fetch(url, category="student_facts", source_type=source_type)
            stats["student_fact_sources_fetched"] += 1
        except Exception:
            # The export requires a successful snapshot, so failed sources are
            # omitted without preventing unrelated crawler stages.
            stats["student_fact_source_errors"] += 1
    return stats


def _admission_facts(store: KnowledgeStore, facts: list[StudentFact]) -> None:
    rows = list(store.connection.execute(
        """SELECT * FROM admissions WHERE year=2026
           ORDER BY admission_category, admission_code, major_name"""
    ))
    regular = [row for row in rows if row["admission_category"] == "regular_undergraduate"]
    continuing = [row for row in rows if row["admission_category"] != "regular_undergraduate"]

    # Admission methods are source-level rules, not 50 copies attached to each
    # major. Materialize them once and retain the year-context source URL.
    if regular:
        method_source = regular[0]["year_source_url"]
        for method in _atomic_clauses(regular[0]["admission_mode"]):
            match = re.match(r"Phương thức\s*(\d+)", method, re.IGNORECASE)
            locator = f"mục Phương thức {match.group(1)}" if match else _locator(method)
            _append(
                facts, entity="Tuyển sinh đại học chính quy năm 2026",
                fact_type="quy tắc", content=method,
                relation="áp dụng cho → tuyển sinh đại học chính quy năm 2026",
                citation=locator, document="Đề án tuyển sinh NTU năm 2026",
                url=method_source, section=7,
            )

    for row in regular:
        entity = row["major_name"]
        citation = f"bảng tuyển sinh 2026, dòng mã {row['admission_code']}"
        document = "Bảng tra cứu ngành và tổ hợp xét tuyển NTU năm 2026"
        _append(
            facts, entity=entity, fact_type="ngành",
            content=f"Mã xét tuyển: {row['admission_code']}.",
            relation="áp dụng cho → tuyển sinh năm 2026", citation=citation,
            document=document, url=row["source_url"], section=7,
        )
        if row["program_type"]:
            _append(
                facts, entity=entity, fact_type="ngành",
                content=f"Nhóm chương trình: {_clean(row['program_type'])}.",
                relation=f"áp dụng cho → {_clean(row['program_type'])}", citation=citation,
                document=document, url=row["source_url"], section=7,
            )
        for condition in _admission_condition_clauses(row["admission_conditions"]):
            _append(
                facts, entity=entity, fact_type="quy tắc", content=condition,
                relation="áp dụng cho → tuyển sinh năm 2026", citation=citation,
                document=document, url=row["source_url"], section=7,
            )

    if not continuing:
        return
    source = continuing[0]["source_url"]
    document = "Thông báo tuyển sinh hệ đại học tại NTU năm 2026"
    by_category: dict[str, Any] = {}
    for row in continuing:
        by_category.setdefault(row["admission_category"], row)
    duration_modes: dict[str, list[str]] = {}
    condition_modes: dict[str, list[str]] = {}
    for row in by_category.values():
        mode = row["program_type"] or row["admission_category"]
        if row["duration"]:
            duration_modes.setdefault(_clean(row["duration"]), []).append(mode)
        for condition in _atomic_clauses(row["admission_conditions"]):
            condition_modes.setdefault(condition, []).append(mode)
    shared_entity = "Tuyển sinh VLVH, liên thông và văn bằng 2 năm 2026"
    for duration, modes in duration_modes.items():
        _append(
            facts, entity=shared_entity, fact_type="quy tắc",
            content=f"Thời gian đào tạo: {duration}.",
            relation=f"áp dụng cho → {', '.join(modes)}",
            citation="bảng mục 1, cột loại hình đào tạo và thời gian",
            document=document, url=source, section=8,
        )
    for condition, modes in condition_modes.items():
        _append(
            facts, entity=shared_entity, fact_type="quy tắc", content=condition,
            relation=f"áp dụng cho → {', '.join(modes)}",
            citation="mục 2 — đối tượng dự tuyển",
            document=document, url=source, section=8,
        )
    dates = next((row["important_dates"] for row in continuing if row["important_dates"]), None)
    if dates:
        for clause in _atomic_clauses(dates):
            _append(
                facts, entity=shared_entity,
                fact_type="quy tắc", content=clause,
                relation="áp dụng cho → tuyển sinh năm 2026", citation="mục 4 — thời gian xét tuyển",
                document=document, url=source, section=8,
            )
    modes_by_major: dict[tuple[str, str], list[Any]] = {}
    for row in continuing:
        modes_by_major.setdefault((row["major_code"], row["major_name"]), []).append(row)
    for (major_code, major_name), major_rows in sorted(modes_by_major.items()):
        citation = f"bảng mục 1, dòng mã ngành {major_code}"
        _append(
            facts, entity=major_name, fact_type="ngành",
            content=f"Mã ngành: {major_code}.", relation="áp dụng cho → tuyển sinh năm 2026",
            citation=citation, document=document, url=source, section=8,
        )
        for row in major_rows:
            mode = row["program_type"] or row["admission_category"]
            _append(
                facts, entity=major_name, fact_type="ngành",
                content=f"Có trong danh mục tuyển sinh {mode} năm 2026.",
                relation=f"áp dụng cho → {mode}", citation=citation,
                document=document, url=source, section=8,
            )


def collect_student_facts(store: KnowledgeStore) -> list[StudentFact]:
    facts: list[StudentFact] = []
    _curated_facts(store, facts)
    _unit_and_service_facts(store, facts)
    _procedure_facts(store, facts)
    _finance_facts(store, facts)
    _discipline_facts(store, facts)
    _program_facts(store, facts)
    _admission_facts(store, facts)
    _graduate_program_facts(store, facts)

    approved = load_approved_domains()
    unique: dict[tuple[str, ...], StudentFact] = {}
    for fact in facts:
        host = (urlsplit(fact.url).hostname or "").lower().rstrip(".")
        joined = " | ".join((fact.entity, fact.content, fact.citation, fact.document, fact.url))
        if host not in approved or urlsplit(fact.url).scheme != "https":
            continue
        if EXCLUDED_DOCUMENTS.search(joined):
            continue
        if PERSONAL_CONTACT.search(joined):
            continue
        key = tuple(_clean(value).casefold() for value in (
            fact.entity, fact.fact_type, fact.content, fact.relation,
            fact.citation, fact.document, fact.url,
        ))
        unique.setdefault(key, fact)
    return sorted(unique.values(), key=lambda item: (item.section, item.entity.casefold(), item.content.casefold()))


def _missing_items(store: KnowledgeStore) -> list[str]:
    checks = quality_checks(store)
    count = lambda sql: int(store.connection.execute(sql).fetchone()[0])
    missing_programs = count("SELECT COUNT(*) FROM programs WHERE crawl_status='missing'")
    unverified_procedures = count("SELECT COUNT(*) FROM procedures WHERE status!='verified'")
    return [
        f"{missing_programs} tổ hợp ngành/khóa chưa được API CTĐT công bố; giữ trạng thái `missing`, không suy đoán.",
        f"{checks['draft_programs_without_courses']} CTĐT ở trạng thái đang biên soạn chưa có chi tiết học phần.",
        f"{checks['complete_programs_missing_total_credits']} CTĐT hoàn chỉnh chưa trích xuất được tổng số tín chỉ từ bằng chứng chính thức.",
        "Chưa có ma trận học phí hiện hành đầy đủ theo từng ngành và từng khóa trong nguồn đã xác minh.",
        "Chưa tìm được giờ làm việc được công bố chính thức cho toàn bộ đơn vị phục vụ sinh viên.",
        "Chưa tìm được quy trình chính thức đầy đủ về cấp lại thẻ sinh viên khi mất hoặc hỏng.",
        "Chưa tìm được quy trình chính thức đầy đủ về khôi phục tài khoản sinh viên khi không thể tự đặt lại mật khẩu.",
        "Chưa tìm được bảng giá ký túc xá hiện hành theo loại phòng.",
        "Chưa xác minh được vị trí trang chính xác của các mức học bổng trong PDF Nghị định 179/2026/NĐ-CP; chưa đưa các mức tiền đó vào bảng fact.",
        "Hai trang chính thức của NTU đang công bố quy mô ký túc xá khác nhau (8 tòa/405 phòng/hơn 2.682 chỗ và 10 tòa/1.000 phòng/hơn 5.000 chỗ); chưa chọn một con số hiện hành khi chưa có văn bản giải quyết xung đột.",
        f"{unverified_procedures} thủ tục đã cấu trúc nhưng chưa đạt trạng thái xác minh.",
    ]


def _fact_table(lines: list[str], facts: Iterable[StudentFact]) -> None:
    lines.extend([
        "| Thực thể | Loại | Nội dung | Quan hệ | Trích dẫn | Văn bản | Link |",
        "|---|---|---|---|---|---|---|",
    ])
    section_facts = list(facts)
    if not section_facts:
        lines.append("| — | — | Chưa có phát biểu đạt điều kiện xuất bản. |  | — | — | — |")
    for fact in section_facts:
        lines.append("| " + " | ".join(_markdown(value) for value in (
            fact.entity, fact.fact_type, fact.content, fact.relation,
            fact.citation, fact.document, fact.url,
        )) + " |")
    lines.append("")


def _evidence_grade(fact: StudentFact, source_type: str) -> str:
    if "chuongTrinhDTID=" in fact.citation or fact.citation.startswith("bảng "):
        return "direct_structured"
    if re.search(r"trang PDF|khoản|Điều|Phụ lục", fact.citation, re.IGNORECASE):
        return "direct_document_locator"
    if source_type in {"official_document", "official_program_pdf"}:
        return "official_document"
    return "official_page_locator"


def export_student_fact_audit(
    store: KnowledgeStore, facts: Iterable[StudentFact], destination: str | Path
) -> int:
    """Write per-fact source metadata without widening the seven-column master schema."""
    source_rows = {
        row["url"]: dict(row) for row in store.connection.execute("SELECT * FROM sources")
    }
    version_rows = {
        row["url"]: dict(row) for row in store.connection.execute(
            """SELECT url, content_hash, raw_path, first_retrieved_at,
                      last_retrieved_at, status
               FROM source_versions WHERE status='current'"""
        )
    }
    count = 0
    output = Path(destination)
    with output.open("w", encoding="utf-8") as handle:
        for fact in facts:
            source = source_rows.get(fact.url, {})
            version = version_rows.get(fact.url, {})
            logical = "\0".join((
                fact.entity, fact.fact_type, fact.content, fact.relation,
                fact.citation, fact.document, fact.url,
            ))
            record = {
                "fact_id": hashlib.sha256(logical.encode("utf-8")).hexdigest(),
                "entity": fact.entity,
                "type": fact.fact_type,
                "content": fact.content,
                "relation": fact.relation or None,
                "citation": fact.citation,
                "document": fact.document,
                "source_url": fact.url,
                "source_domain": source.get("domain") or urlsplit(fact.url).hostname,
                "source_type": source.get("source_type"),
                "source_snapshot_status": source.get("status"),
                "source_version_status": version.get("status"),
                "content_hash": version.get("content_hash") or source.get("content_hash"),
                "raw_path": version.get("raw_path") or source.get("raw_path"),
                "retrieved_at": source.get("retrieved_at"),
                "first_retrieved_at": version.get("first_retrieved_at"),
                "last_retrieved_at": version.get("last_retrieved_at"),
                "evidence_grade": _evidence_grade(fact, source.get("source_type", "")),
                "effective_status": "not_inferred",
            }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1
    return count


def export_student_fact_markdown(store: KnowledgeStore, destination: str | Path) -> int:
    """Write a 16-section master while preserving the strict seven-column facts."""
    facts = collect_student_facts(store)
    today = datetime.now(VIETNAM_TIMEZONE).date().isoformat()
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    audit_path = destination.with_name(f"{destination.stem}_AUDIT.jsonl")
    audit_count = export_student_fact_audit(store, facts, audit_path)
    lines = [
        "# Thông tin học vụ chính thức — Trường Đại học Nha Trang",
        "",
        f"> Bản master lai sinh tự động từ SQLite ngày {today}. Mỗi dòng trong các bảng fact là một phát biểu có nguồn riêng; không chứa danh sách sinh viên hoặc đầu mối cá nhân.",
        "",
        "Các phần 1–9 dùng cùng schema 7 cột. Metadata kỹ thuật được tách sang Phần 13 để bảng nội dung không bị phình thêm cột.",
        "",
    ]
    for section, title in SECTION_TITLES.items():
        lines.extend([f"## {section}. {title}", ""])
        _fact_table(lines, (fact for fact in facts if fact.section == section))

    lines.extend([
        "## 10. Tên gọi khác",
        "",
        "- Phòng Công tác Chính trị và Sinh viên: Phòng CTCT&SV, Phòng CTSV.",
        "- Phòng Kế hoạch - Tài chính: Phòng KH-TC, Phòng tài vụ.",
        "- Chương trình đào tạo: CTĐT, khung chương trình.",
        "- Ký túc xá: KTX.",
        "- Bảo hiểm y tế: BHYT.",
        "- Cổng thông tin sinh viên: trang sinh viên, portal sinh viên.",
        "",
        "## 11. Chưa tìm được hoặc chưa thể khẳng định",
        "",
    ])
    lines.extend(f"- {item}" for item in _missing_items(store))

    strict_errors = validate_student_facts(facts)
    contents = [fact.content for fact in facts]
    fact_urls = {fact.url for fact in facts}
    snapshotted = {
        row[0] for row in store.connection.execute(
            "SELECT url FROM sources WHERE status='success'"
        )
    }
    lines.extend([
        "",
        "## 12. Kiểm soát chất lượng",
        "",
        "| Kiểm tra | Kết quả |",
        "|---|---:|",
        f"| Tổng fact | {len(facts)} |",
        f"| Vi phạm schema/whitelist/trích dẫn/riêng tư | {len(strict_errors)} |",
        f"| URL fact chưa có raw snapshot thành công | {len(fact_urls - snapshotted)} |",
        f"| Duplicate chính xác | 0 |",
        f"| Nội dung lặp giữa các thực thể | {len(contents) - len(set(contents))} |",
        f"| Dòng nội dung dài trên 300 ký tự | {sum(len(value) > 300 for value in contents)} |",
        "",
        "`Nội dung lặp giữa các thực thể` không mặc nhiên là duplicate: mã ngành, thời gian đào tạo hoặc quy tắc có thể áp dụng cho nhiều CTĐT. Chỉ số được công khai để theo dõi độ cô đọng.",
        "",
        "## 13. Metadata, độ tin cậy và hiệu lực",
        "",
        f"Metadata chi tiết của {audit_count} fact nằm trong [{audit_path.name}]({audit_path.name}). Mỗi record có SHA-256, raw path, loại nguồn, thời điểm truy xuất, trạng thái snapshot và cấp bằng chứng.",
        "",
        "Báo cáo không suy đoán hiệu lực pháp lý. Trường `effective_status` trong audit giữ `not_inferred` cho đến khi nguồn nêu rõ thời hạn hoặc có quy trình versioning xác nhận văn bản thay thế.",
        "",
        "## 14. Loại trừ và bảo vệ dữ liệu",
        "",
        "Không dùng làm căn cứ: QĐ 1052 ngày 17/07/2025 và QĐ 1965 sửa đổi; QĐ 626 ngày 29/04/2026; QĐ 753/2021; QĐ 729 về học phí; QĐ 317 về học bổng; danh mục đầy đủ biểu mẫu Phòng Đào tạo.",
        "",
        "Không xuất tên, email, số điện thoại cá nhân hoặc danh sách sinh viên. Địa chỉ, điện thoại và email chung của đơn vị vẫn được phép.",
        "",
        "## 15. Nhật ký lần chạy",
        "",
    ])
    latest = store.connection.execute(
        "SELECT id, started_at, finished_at, status FROM crawl_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    lines.extend([
        "| Run ID | Bắt đầu | Kết thúc | Trạng thái |",
        "|---:|---|---|---|",
        f"| {latest['id']} | {_markdown(latest['started_at'])} | {_markdown(latest['finished_at'] or '')} | {_markdown(latest['status'])} |"
        if latest else "| — | — | — | Chưa có run |",
        "",
        "## 16. Thống kê và artifact",
        "",
        "| Phần fact | Số dòng |",
        "|---|---:|",
    ])
    lines.extend(
        f"| {section}. {title} | {sum(fact.section == section for fact in facts)} |"
        for section, title in SECTION_TITLES.items()
    )
    lines.extend([
        "",
        f"Tổng số phát biểu: **{len(facts)}**.",
        "",
        "Artifact liên quan: [facts.jsonl](facts.jsonl), [programs.json](programs.json), [program_gaps.json](program_gaps.json), [CRAWL_AUDIT.md](CRAWL_AUDIT.md), [DATA_QUALITY_REPORT.md](DATA_QUALITY_REPORT.md).",
    ])
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(facts)


def validate_student_facts(facts: Iterable[StudentFact]) -> list[str]:
    """Return strict-schema violations for tests and release checks."""
    approved = load_approved_domains()
    errors: list[str] = []
    seen: set[tuple[str, ...]] = set()
    for index, fact in enumerate(facts, 1):
        if fact.fact_type not in ALLOWED_TYPES:
            errors.append(f"dòng {index}: loại không hợp lệ")
        if not fact.citation:
            errors.append(f"dòng {index}: thiếu trích dẫn")
        parsed = urlsplit(fact.url)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() not in approved:
            errors.append(f"dòng {index}: nguồn ngoài whitelist")
        joined = " | ".join((fact.content, fact.citation, fact.document, fact.url))
        if EXCLUDED_DOCUMENTS.search(joined):
            errors.append(f"dòng {index}: dùng văn bản bị loại trừ")
        if PERSONAL_CONTACT.search(joined):
            errors.append(f"dòng {index}: chứa đầu mối cá nhân")
        key = tuple(_clean(value).casefold() for value in (
            fact.entity, fact.fact_type, fact.content, fact.relation,
            fact.citation, fact.document, fact.url,
        ))
        if key in seen:
            errors.append(f"dòng {index}: trùng phát biểu")
        seen.add(key)
    return errors
