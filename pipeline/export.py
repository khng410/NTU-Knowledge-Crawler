"""Generate JSON and crawl-audit outputs from SQLite."""

from __future__ import annotations

import json
import hashlib
from html import escape
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from database import KnowledgeStore
from pipeline.validate import quality_checks


VIETNAM_TIMEZONE = timezone(timedelta(hours=7))


def _md(value: Any) -> str:
    """Render a database value safely inside a Markdown table cell."""
    if value is None:
        return "—"
    rendered = str(value).strip()
    if not rendered:
        return "—"
    return (
        rendered.replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r", " ")
        .replace("\n", "<br>")
    )


def _json_text(value: str | None) -> str:
    if not value:
        return "—"
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return str(value)
    if isinstance(parsed, list):
        return "; ".join(str(item) for item in parsed if item)
    return str(parsed)


def _source_link(url: str | None, label: str = "Nguồn") -> str:
    return f"[{label}]({url})" if url else "—"


def _append_table(lines: list[str], headers: tuple[str, ...], rows: list[tuple[Any, ...]]) -> None:
    lines.extend(
        [
            "| " + " | ".join(headers) + " |",
            "|" + "|".join("---" for _ in headers) + "|",
        ]
    )
    if rows:
        lines.extend("| " + " | ".join(_md(cell) for cell in row) + " |" for row in rows)
    else:
        lines.append("| " + " | ".join("—" for _ in headers) + " |")
    lines.append("")


def _rows(store: KnowledgeStore, table: str) -> list[dict[str, Any]]:
    allowed = {
        "programs",
        "program_gaps",
        "courses",
        "graduate_programs",
        "graduate_admissions",
        "procedure_candidates",
        "procedures",
        "admissions",
        "source_candidates",
        "financial_policies",
        "units",
        "student_services",
        "discipline_rules",
    }
    if table not in allowed:
        raise ValueError(f"Unsupported export table: {table}")
    return [dict(row) for row in store.connection.execute(f"SELECT * FROM {table}")]

def export_json(store: KnowledgeStore, output_directory: str | Path) -> dict[str, int]:
    output_path = Path(output_directory)
    output_path.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for table in (
        "programs",
        "program_gaps",
        "courses",
        "graduate_programs",
        "graduate_admissions",
        "procedure_candidates",
        "procedures",
        "admissions",
        "source_candidates",
        "financial_policies",
        "units",
        "student_services",
        "discipline_rules",
    ):
        records = _rows(store, table)
        for record in records:
            for field in (
                "plos_json",
                "prerequisite_json",
                "forms_json",
                "decisions_json",
                "documents_json",
                "matched_keywords_json",
                "conditions_json",
                "required_documents_json",
                "services_json",
            ):
                if field in record:
                    record[field.removesuffix("_json")] = (
                        json.loads(record.pop(field)) if record[field] else None
                    )
        (output_path / f"{table}.json").write_text(
            json.dumps(records, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        counts[table] = len(records)
    return counts


def generate_audit(store: KnowledgeStore, destination: str | Path) -> None:
    def count(sql: str) -> int:
        return int(store.connection.execute(sql).fetchone()[0])

    errors = list(
        store.connection.execute(
            "SELECT url, http_status, error FROM crawl_errors ORDER BY id DESC LIMIT 100"
        )
    )
    missing = list(
        store.connection.execute(
            """SELECT major_code, major_name, cohort, gap_status, citation,
                      evidence_url, review_note
               FROM program_gaps ORDER BY major_code, cohort"""
        )
    )
    discovered_count = count("SELECT COUNT(*) FROM crawl_queue")
    done_count = count("SELECT COUNT(*) FROM crawl_queue WHERE status='done'")
    failed_count = count("SELECT COUNT(*) FROM crawl_queue WHERE status='failed'")
    pdf_count = count("SELECT COUNT(*) FROM sources WHERE source_type='official_program_pdf'")
    program_count = count("SELECT COUNT(*) FROM programs WHERE crawl_status='success'")
    course_count = count("SELECT COUNT(*) FROM courses")
    graduate_program_count = count("SELECT COUNT(*) FROM graduate_programs")
    graduate_admission_count = count("SELECT COUNT(*) FROM graduate_admissions")
    procedure_candidate_count = count("SELECT COUNT(*) FROM procedure_candidates")
    procedure_verified_count = count("SELECT COUNT(*) FROM procedures WHERE status='verified'")
    procedure_form_count = count("SELECT COUNT(*) FROM procedures WHERE status='verified' AND procedure_kind='official_form'")
    admission_count = count("SELECT COUNT(*) FROM admissions")
    regular_admission_count = count("SELECT COUNT(*) FROM admissions WHERE admission_category='regular_undergraduate'")
    continuing_admission_count = admission_count - regular_admission_count
    finance_candidate_count = count("SELECT COUNT(*) FROM source_candidates WHERE category='finance'")
    service_candidate_count = count("SELECT COUNT(*) FROM source_candidates WHERE category='services'")
    discipline_candidate_count = count("SELECT COUNT(*) FROM source_candidates WHERE category='discipline'")
    financial_policy_count = count("SELECT COUNT(*) FROM financial_policies")
    current_policy_count = count("SELECT COUNT(*) FROM financial_policies WHERE status='current'")
    historical_policy_count = count("SELECT COUNT(*) FROM financial_policies WHERE status='historical'")
    unit_count = count("SELECT COUNT(*) FROM units")
    student_service_count = count("SELECT COUNT(*) FROM student_services WHERE status='verified'")
    discipline_rule_count = count("SELECT COUNT(*) FROM discipline_rules")
    discipline_partial_count = count("SELECT COUNT(*) FROM discipline_rules WHERE status='partial_ocr'")
    unresolved_policy_count = count("SELECT COUNT(*) FROM financial_policies WHERE status='unresolved'")
    current_fact_count = count("SELECT COUNT(*) FROM facts WHERE status='current'")
    historical_fact_count = count("SELECT COUNT(*) FROM facts WHERE status='historical'")
    unresolved_conflict_count = count("SELECT COUNT(*) FROM conflicts WHERE status='unresolved'")
    latest_run = store.connection.execute("SELECT MAX(id) FROM crawl_runs").fetchone()[0]
    task_rows = list(
        store.connection.execute(
            "SELECT task, status FROM crawl_tasks WHERE run_id=? ORDER BY id", (latest_run,)
        )
    ) if latest_run else []
    lines = [
        "# Crawl Audit",
        "",
        f"Ngày chạy: {datetime.now(VIETNAM_TIMEZONE).date().isoformat()}",
        "",
        "## ctdt.ntu.edu.vn",
        "",
        f"- URL phát hiện: {discovered_count}",
        f"- URL crawl thành công: {done_count}",
        f"- URL lỗi: {failed_count}",
        f"- PDF: {pdf_count}",
        f"- CTĐT trích xuất: {program_count}",
        f"- Học phần: {course_count}",
        "",
        "## Sau đại học",
        "",
        f"- Chương trình thạc sĩ/tiến sĩ: {graduate_program_count}",
        f"- Thông báo tuyển sinh: {graduate_admission_count}",
        "",
        "## Thủ tục học vụ",
        "",
        f"- Candidate procedure: {procedure_candidate_count}",
        f"- Thủ tục/biểu mẫu đã xác minh: {procedure_verified_count}",
        f"- Trong đó biểu mẫu chính thức: {procedure_form_count}",
        "",
        "## Milestone 5",
        "",
        f"- Tuyển sinh chính quy theo năm: {regular_admission_count}",
        f"- Tuyển sinh VLVH/liên thông/VB2 theo năm: {continuing_admission_count}",
        f"- Nguồn tài chính/chính sách phát hiện: {finance_candidate_count}",
        f"- Chính sách tài chính đã cấu trúc hóa: {financial_policy_count}",
        f"- Chính sách current: {current_policy_count}",
        f"- Chính sách historical: {historical_policy_count}",
        f"- Chính sách còn unresolved: {unresolved_policy_count}",
        f"- Nguồn dịch vụ phát hiện: {service_candidate_count}",
        f"- Đơn vị dịch vụ: {unit_count}",
        f"- Dịch vụ có provenance riêng: {student_service_count}",
        f"- Nguồn kỷ luật/rèn luyện phát hiện: {discipline_candidate_count}",
        f"- Quy tắc kỷ luật từ QĐ 1351: {discipline_rule_count}",
        f"- Quy tắc OCR còn thiếu ô: {discipline_partial_count}",
        "",
        "## Facts và versioning",
        "",
        f"- Fact current: {current_fact_count}",
        f"- Fact historical: {historical_fact_count}",
        f"- Conflict unresolved: {unresolved_conflict_count}",
        "",
        "## Trạng thái task",
        "",
    ]
    lines.extend(f"- {row['task']}: {row['status']}" for row in task_rows)
    if not task_rows:
        lines.append("Chưa có task được ghi nhận.")
    lines.extend([
        "",
        "## Lỗi",
        "",
    ])
    lines.extend(
        f"- `{row['url']}` — HTTP {row['http_status'] or 'N/A'}: {row['error']}"
        for row in errors
    )
    if not errors:
        lines.append("Không có lỗi được ghi nhận.")
    lines.extend(["", "## Chưa có CTĐT theo khóa", ""])
    lines.extend(
        f"- {row['cohort']} — {row['major_code']} — {row['major_name']} — `{row['gap_status']}`"
        for row in missing
    )
    if not missing:
        lines.append("Không có khoảng trống được ghi nhận.")
    Path(destination).write_text("\n".join(lines) + "\n", encoding="utf-8")
    cohort_counts = list(store.connection.execute(
        """SELECT cohort, COUNT(*) AS count FROM program_gaps
           GROUP BY cohort ORDER BY cohort"""
    ))
    failed_ctdt = list(store.connection.execute(
        """SELECT url, retry_count, error FROM crawl_queue
           WHERE category='ctdt_pdf' AND status='failed' ORDER BY url"""
    ))
    gap_lines = [
        "# CTĐT Gap Audit", "",
        "> `missing` means the current official CTĐT API did not publish that major/cohort combination. It does not prove that a curriculum never existed.",
        "", "## Evidence", "",
        "- Discovery API: https://ctdt.ntu.edu.vn/api/curriculum/publicCTDT/nganhDaoTaos/daihoc",
        "- Expected scope: K63–K68", f"- Missing combinations: {len(missing)}", "",
        "## Missing by cohort", "",
    ]
    gap_lines.extend(f"- {row['cohort']}: {row['count']}" for row in cohort_counts)
    gap_lines.extend(["", "## Failed official PDF links", ""])
    gap_lines.extend(
        f"- `{row['url']}` — retries: {row['retry_count']}; {row['error']}"
        for row in failed_ctdt
    )
    if not failed_ctdt:
        gap_lines.append("None.")
    gap_lines.extend(["", "## Missing combinations", ""])
    gap_lines.extend(
        f"- {row['cohort']} — {row['major_code']} — {row['major_name']} — "
        f"`{row['gap_status']}` — {row['citation']} — {row['evidence_url']}"
        for row in missing
    )
    Path(destination).with_name("CTDT_GAP_AUDIT.md").write_text(
        "\n".join(gap_lines) + "\n", encoding="utf-8"
    )


def export_facts_jsonl(store: KnowledgeStore, destination: str | Path) -> int:
    rows = [dict(row) for row in store.connection.execute("SELECT * FROM facts ORDER BY type, entity")]
    with Path(destination).open("w", encoding="utf-8") as output:
        for row in rows:
            output.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows)


def export_master_markdown(store: KnowledgeStore, destination: str | Path) -> int:
    rows = list(store.connection.execute("SELECT * FROM facts ORDER BY type, entity"))
    lines = [
        "# NTU Knowledge Base",
        "",
        "> Generated from SQLite. Do not edit this file manually.",
        "",
    ]
    current_type: str | None = None
    for row in rows:
        if row["type"] != current_type:
            current_type = row["type"]
            lines.extend(
                [
                    f"## {current_type}",
                    "",
                    "| Entity | Content | Relation | Target | Cohort/Year | Citation | Document | Status | Confidence | Retrieved | Source | Note |",
                    "|---|---|---|---|---|---|---|---|---|---|---|---|",
                ]
            )
        clean = lambda value: str(value or "").replace("|", "\\|").replace("\n", " ")
        cohort_year = row["cohort"] or row["academic_year"] or ""
        lines.append(
            "| " + " | ".join(
                clean(value)
                for value in (
                    row["entity"],
                    row["content"],
                    row["relation"],
                    row["target"],
                    cohort_year,
                    row["citation"],
                    row["document_number"],
                    row["status"],
                    row["confidence"],
                    row["retrieved_at"],
                    row["source_url"],
                    row["note"],
                )
            ) + " |"
        )
    Path(destination).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(rows)


def export_academic_master_markdown(store: KnowledgeStore, destination: str | Path) -> int:
    """Build the curated, human-readable 16-section academic master report.

    Unlike MASTER.md, this is a presentation view over normalized tables. Large
    course-level data remains in the linked machine-readable artifacts.
    """
    today = datetime.now(VIETNAM_TIMEZONE).date().isoformat()
    checks = quality_checks(store)
    current_facts = int(
        store.connection.execute("SELECT COUNT(*) FROM facts WHERE status='current'").fetchone()[0]
    )
    lines = [
        "# Thông tin học vụ chính thức — Trường Đại học Nha Trang (NTU)",
        "",
        f"## Bản tổng hợp tự động — Cập nhật {today}",
        "",
        "> Báo cáo này được sinh từ SQLite và không nên chỉnh sửa thủ công. Mỗi bảng giữ liên kết nguồn; dữ liệu chi tiết cấp học phần nằm trong các artifact JSON được dẫn ở Phần 16.",
        "",
        f"Phạm vi hiện tại: **{current_facts} fact đang hiệu lực**. Trạng thái `missing` hoặc `unresolved` là khoảng trống cần xác minh, không phải bằng chứng rằng thông tin không tồn tại.",
        "",
        "## 1. Đơn vị và đầu mối dịch vụ",
        "",
    ]

    unit_rows = list(
        store.connection.execute("SELECT * FROM units ORDER BY name")
    )
    _append_table(
        lines,
        ("Đơn vị", "Địa chỉ", "Điện thoại", "Email", "Giờ làm việc", "Dịch vụ", "Nguồn"),
        [
            (
                row["name"], row["address"], row["phone"], row["email"],
                row["working_hours"], _json_text(row["services_json"]),
                _source_link(row["source_url"]),
            )
            for row in unit_rows
        ],
    )

    lines.extend(["## 2. Cổng thông tin, thủ tục và biểu mẫu", ""])
    procedures = list(
        store.connection.execute(
            "SELECT * FROM procedures WHERE status='verified' ORDER BY procedure_kind, name"
        )
    )
    _append_table(
        lines,
        ("Tên", "Loại", "Đối tượng", "Nơi xử lý", "Hồ sơ/biểu mẫu", "Thời hạn", "Kết quả", "Căn cứ/xác minh", "Nguồn"),
        [
            (
                row["name"], row["procedure_kind"], row["applicable_to"],
                row["processing_unit"] or row["submission_unit"],
                _json_text(row["required_documents_json"]) if row["required_documents_json"] else row["form"],
                row["deadline"] or row["processing_time"], row["result"],
                row["legal_basis"] or row["verification_note"], _source_link(row["source_url"]),
            )
            for row in procedures
        ],
    )

    lines.extend(["## 3. Học phí, học bổng và chính sách tài chính", ""])
    for status, heading in (
        ("current", "### Đang áp dụng"),
        ("historical", "### Lịch sử/hết hiệu lực theo thời điểm nguồn"),
        ("unresolved", "### Chưa đủ dữ kiện xác minh"),
    ):
        lines.extend([heading, ""])
        policies = list(
            store.connection.execute(
                "SELECT * FROM financial_policies WHERE status=? ORDER BY policy_type, title",
                (status,),
            )
        )
        _append_table(
            lines,
            ("Chính sách", "Loại", "Phạm vi", "Đối tượng", "Mức", "Năm/khóa", "Điều kiện", "Hạn", "Căn cứ/ghi chú", "Nguồn"),
            [
                (
                    row["title"], row["policy_type"], row["policy_scope"], row["beneficiary"],
                    row["amount"] if row["amount"] is not None else row["percentage"],
                    row["academic_year"] or row["cohort"], _json_text(row["conditions_json"]),
                    row["deadline"], row["legal_basis"] or row["verification_note"],
                    _source_link(row["source_url"]),
                )
                for row in policies
            ],
        )

    lines.extend(["## 4. Rèn luyện, kỷ luật và xử lý vi phạm", ""])
    discipline = list(store.connection.execute("SELECT * FROM discipline_rules ORDER BY citation"))
    _append_table(
        lines,
        ("Hành vi", "Lần 1", "Lần 2", "Lần 3", "Lần 4", "Lần 5", "Quy định khác", "Đối soát", "Nguồn"),
        [
            (
                row["behavior"], row["first_violation"], row["second_violation"],
                row["third_violation"], row["fourth_violation"], row["fifth_violation"],
                row["sanction"], f"{row['status']}; {row['citation']}", _source_link(row["source_url"]),
            )
            for row in discipline
        ],
    )

    lines.extend(["## 5. Dịch vụ sinh viên: ký túc xá, thư viện, việc làm và hỗ trợ", ""])
    services = list(
        store.connection.execute(
            """SELECT s.*, u.name AS unit_name FROM student_services s
               JOIN units u ON u.unit_id=s.unit_id
               WHERE s.status='verified' ORDER BY u.name, s.name"""
        )
    )
    _append_table(
        lines,
        ("Đơn vị", "Dịch vụ", "Mô tả", "Đối tượng", "Phí", "Giờ", "Trích dẫn", "Nguồn"),
        [
            (
                row["unit_name"], row["name"], row["description"], row["eligibility"],
                row["fee"], row["working_hours"], row["citation"], _source_link(row["source_url"]),
            )
            for row in services
        ],
    )

    lines.extend(["## 6. Chương trình đào tạo đại học", ""])
    programs = list(
        store.connection.execute(
            """SELECT p.*, COUNT(c.course_id) AS course_count
               FROM programs p LEFT JOIN courses c ON c.program_id=p.program_id
               WHERE p.crawl_status='success'
               GROUP BY p.program_id
               ORDER BY p.major_code, p.cohort"""
        )
    )
    _append_table(
        lines,
        ("Mã ngành", "Ngành/chương trình", "Khóa", "Trạng thái nội dung", "Trạng thái nguồn", "Thời gian", "Tín chỉ", "Học phần", "Quyết định", "Ngôn ngữ", "API", "Tài liệu"),
        [
            (
                row["major_code"], row["program_name"] or row["major_name"], row["cohort"],
                row["content_status"], row["publication_status"], row["duration"],
                row["total_credits"], row["course_count"], row["decision_number"],
                row["language"], _source_link(row["source_url"], "API"),
                _source_link(row["fallback_url"], "HTML fallback")
                if row["pdf_status"] == "missing_asset_with_html_fallback"
                else _source_link(row["pdf_url"], "PDF") if row["pdf_url"] else "—",
            )
            for row in programs
        ],
    )
    missing_by_cohort = list(
        store.connection.execute(
            """SELECT cohort, COUNT(*) AS total FROM programs WHERE crawl_status='missing'
               GROUP BY cohort ORDER BY cohort"""
        )
    )
    lines.extend(
        [
            "### Khoảng trống CTĐT",
            "",
            "Các tổ hợp không được API hiện tại công bố được giữ riêng, không suy đoán. Danh sách đầy đủ: [CTDT_GAP_AUDIT.md](CTDT_GAP_AUDIT.md).",
            "",
        ]
    )
    _append_table(lines, ("Khóa", "Số tổ hợp missing"), [(r["cohort"], r["total"]) for r in missing_by_cohort])

    lines.extend(["## 7. Tuyển sinh đại học theo năm", "", "### Chính quy", ""])
    regular = list(
        store.connection.execute(
            """SELECT * FROM admissions WHERE admission_category='regular_undergraduate'
               ORDER BY year DESC, admission_code, major_name"""
        )
    )
    _append_table(
        lines,
        ("Năm", "Mã xét tuyển", "Ngành/chuyên ngành", "Nhóm chương trình", "Điều kiện/tổ hợp", "Nguồn", "Nguồn năm"),
        [
            (
                row["year"], row["admission_code"], row["major_name"], row["program_type"],
                row["admission_conditions"], _source_link(row["source_url"]),
                _source_link(row["year_source_url"], "Năm"),
            )
            for row in regular
        ],
    )
    lines.extend(["### Vừa làm vừa học, liên thông và văn bằng 2", ""])
    continuing = list(
        store.connection.execute(
            """SELECT * FROM admissions WHERE admission_category!='regular_undergraduate'
               ORDER BY year DESC, major_code, admission_category"""
        )
    )
    _append_table(
        lines,
        ("Năm", "Mã ngành", "Ngành", "Loại hình", "Thời gian", "Điều kiện", "Mốc thời gian", "Nguồn"),
        [
            (
                row["year"], row["major_code"] or row["admission_code"], row["major_name"],
                row["program_type"], row["duration"], row["admission_conditions"],
                row["important_dates"], _source_link(row["source_url"]),
            )
            for row in continuing
        ],
    )

    lines.extend(["## 8. Sau đại học và nguồn đào tạo mở rộng", "", "### Chương trình sau đại học", ""])
    graduate_programs = list(
        store.connection.execute("SELECT * FROM graduate_programs ORDER BY degree_level, major_code, cohort")
    )
    _append_table(
        lines,
        ("Bậc", "Mã ngành", "Chương trình", "Định hướng", "Tín chỉ", "Thời gian", "Khóa", "Điều kiện đầu vào", "Ngoại ngữ", "Nguồn"),
        [
            (
                row["degree_level"], row["major_code"], row["program_name"], row["orientation"],
                row["credits"], row["duration"], row["cohort"], row["entry_conditions"],
                row["language_entry_requirement"], _source_link(row["source_url"]),
            )
            for row in graduate_programs
        ],
    )
    lines.extend(["### Thông báo tuyển sinh sau đại học", ""])
    graduate_admissions = list(
        store.connection.execute(
            "SELECT * FROM graduate_admissions ORDER BY admission_year DESC, degree_level, title"
        )
    )
    _append_table(
        lines,
        ("Năm", "Bậc", "Thông báo", "Điều kiện", "Ngoại ngữ đầu vào", "Mốc thời gian", "Biểu mẫu", "Quyết định", "Nguồn"),
        [
            (
                row["admission_year"], row["degree_level"], row["title"], row["entry_conditions"],
                row["language_entry_requirement"], row["important_dates"], _json_text(row["forms_json"]),
                _json_text(row["decisions_json"]), _source_link(row["source_url"]),
            )
            for row in graduate_admissions
        ],
    )

    lines.extend(["## 9. Nguồn pháp lý và provenance", ""])
    source_summary = list(
        store.connection.execute(
            """SELECT s.source_type, COUNT(*) AS total, COUNT(DISTINCT sv.url) AS urls,
                      MIN(sv.first_retrieved_at) AS first_seen, MAX(sv.last_retrieved_at) AS last_seen
               FROM source_versions sv JOIN sources s ON s.url=sv.url
               GROUP BY s.source_type ORDER BY s.source_type"""
        )
    )
    _append_table(
        lines,
        ("Loại nguồn", "Phiên bản", "URL", "Ghi nhận đầu", "Ghi nhận cuối"),
        [(r["source_type"], r["total"], r["urls"], r["first_seen"], r["last_seen"]) for r in source_summary],
    )
    lines.extend(
        [
            "Mỗi fact nguyên tử giữ `source_url`, `source_domain`, `source_type`, `citation`, `retrieved_at`, trạng thái phiên bản và confidence trong [facts.jsonl](facts.jsonl).",
            "",
            "## 10. Danh mục định danh và miền chính thức",
            "",
        ]
    )
    domains = list(
        store.connection.execute(
            """SELECT source_domain, category, COUNT(*) AS total
               FROM source_candidates GROUP BY source_domain, category
               ORDER BY source_domain, category"""
        )
    )
    _append_table(lines, ("Miền", "Nhóm", "Candidate"), [(r["source_domain"], r["category"], r["total"]) for r in domains])

    lines.extend(["## 11. Chưa tìm thấy, chưa đủ dữ kiện và lỗi nguồn", ""])
    gap_counts = [
        ("Tổ hợp ngành/khóa CTĐT missing", store.connection.execute("SELECT COUNT(*) FROM programs WHERE crawl_status='missing'").fetchone()[0]),
        ("CTĐT đang biên soạn và chưa có học phần", checks["draft_programs_without_courses"]),
        ("CTĐT complete nhưng chưa có học phần", checks["complete_programs_without_courses"]),
        ("CTĐT complete chưa có tổng tín chỉ", checks["complete_programs_missing_total_credits"]),
        ("Chính sách tài chính unresolved", store.connection.execute("SELECT COUNT(*) FROM financial_policies WHERE status='unresolved'").fetchone()[0]),
        ("Candidate thủ tục chưa xác minh", store.connection.execute("SELECT COUNT(*) FROM procedure_candidates").fetchone()[0]),
        ("URL crawl failed", store.connection.execute("SELECT COUNT(*) FROM crawl_queue WHERE status='failed'").fetchone()[0]),
    ]
    _append_table(lines, ("Nhóm khoảng trống", "Số lượng"), gap_counts)
    failed_urls = list(store.connection.execute("SELECT url, error FROM crawl_queue WHERE status='failed' ORDER BY url"))
    _append_table(lines, ("URL lỗi", "Chi tiết"), [(r["url"], r["error"]) for r in failed_urls])

    lines.extend(["## 12. Chất lượng dữ liệu", ""])
    _append_table(
        lines,
        ("Kiểm tra", "Kết quả", "Đánh giá"),
        [(name, value, "Đạt" if value in {0, "ok"} else "Cần xem xét") for name, value in checks.items()],
    )

    lines.extend(
        [
            "## 13. Quy tắc metadata, hiệu lực và độ tin cậy",
            "",
            "- `current`: bản fact hiện hành trong kho; `historical`: bản cũ được giữ để truy vết.",
            "- `unresolved`: nguồn chưa đủ phạm vi hoặc dữ kiện để nâng thành thông tin có thể hành động.",
            "- `missing`: API chính thức hiện tại không công bố tổ hợp được kỳ vọng; không suy diễn sự không tồn tại.",
            "- `verified_manual`: dữ liệu OCR đã được đối soát trực tiếp với ảnh quét; ô không đọc được vẫn để trống.",
            "- SQLite là nguồn sự thật vận hành; Markdown này là lớp trình bày, còn JSON/JSONL là lớp trao đổi dữ liệu.",
            "",
            "## 14. Loại trừ và audit phạm vi",
            "",
            "Các tài liệu sau không được dùng làm factual basis theo phạm vi dự án: QĐ 1052, QĐ 1965, QĐ 626, QĐ 753/2021, QĐ 729, QĐ 317 và bộ biểu mẫu đầy đủ của Phòng Đào tạo. Quality check `excluded_decisions_used` ở Phần 12 kiểm soát một phần quy tắc này.",
            "",
            "## 15. Nhật ký lần chạy hoàn tất gần nhất",
            "",
        ]
    )
    latest_run = store.connection.execute(
        """SELECT * FROM crawl_runs WHERE status NOT IN ('pending', 'running')
           ORDER BY id DESC LIMIT 1"""
    ).fetchone()
    if latest_run:
        lines.extend(
            [
                f"- Run: `{latest_run['id']}`",
                f"- Bắt đầu: `{latest_run['started_at']}`",
                f"- Kết thúc: `{latest_run['finished_at'] or '—'}`",
                f"- Trạng thái: `{latest_run['status']}`",
                "",
            ]
        )
        tasks = list(
            store.connection.execute(
                "SELECT task, status, error FROM crawl_tasks WHERE run_id=? ORDER BY id",
                (latest_run["id"],),
            )
        )
        _append_table(lines, ("Task", "Trạng thái", "Lỗi"), [(r["task"], r["status"], r["error"]) for r in tasks])
    else:
        lines.extend(["Chưa có crawl run được ghi nhận.", ""])

    lines.extend(["## 16. Thống kê và artifact chi tiết", ""])
    artifact_counts = [
        ("CTĐT thành công", len(programs), "[programs.json](programs.json)"),
        ("Học phần", store.connection.execute("SELECT COUNT(*) FROM courses").fetchone()[0], "[courses.json](courses.json)"),
        ("CTĐT sau đại học", len(graduate_programs), "[graduate_programs.json](graduate_programs.json)"),
        ("Tuyển sinh sau đại học", len(graduate_admissions), "[graduate_admissions.json](graduate_admissions.json)"),
        ("Tuyển sinh đại học", len(regular) + len(continuing), "[admissions.json](admissions.json)"),
        ("Chính sách tài chính", store.connection.execute("SELECT COUNT(*) FROM financial_policies").fetchone()[0], "[financial_policies.json](financial_policies.json)"),
        ("Dịch vụ sinh viên", len(services), "[student_services.json](student_services.json)"),
        ("Quy tắc kỷ luật", len(discipline), "[discipline_rules.json](discipline_rules.json)"),
        ("Fact hiện hành", current_facts, "[facts.jsonl](facts.jsonl)"),
    ]
    _append_table(lines, ("Tập dữ liệu", "Số bản ghi", "Artifact"), artifact_counts)
    lines.extend(
        [
            "Bản fact đầy đủ dạng bảng vẫn có tại [MASTER.md](MASTER.md); audit crawl tại [CRAWL_AUDIT.md](CRAWL_AUDIT.md); báo cáo chất lượng tại [DATA_QUALITY_REPORT.md](DATA_QUALITY_REPORT.md).",
            "",
        ]
    )
    Path(destination).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return current_facts


def export_ontology(store: KnowledgeStore, destination: str | Path) -> int:
    facts = list(store.connection.execute("SELECT * FROM facts WHERE status='current'"))
    class_for_type = {
        "program_name": "AcademicProgram",
        "total_credits": "AcademicProgram",
        "has_course": "AcademicProgram",
        "procedure": "AcademicProcedure",
        "unit": "Unit",
        "student_service": "Unit",
        "graduate_program": "GraduateProgram",
        "admission_program": "Admission",
        "discipline_rule": "DisciplineRule",
    }
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"',
        ' xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"',
        ' xmlns:owl="http://www.w3.org/2002/07/owl#"',
        ' xmlns:ntu="https://ontology.ntu.edu.vn/academic#">',
    ]
    for class_name in sorted(set(class_for_type.values()) | {"Fact"}):
        lines.append(f'  <owl:Class rdf:about="https://ontology.ntu.edu.vn/academic#{class_name}"/>')
    for fact in facts:
        subject_id = hashlib.sha256(fact["entity"].encode("utf-8")).hexdigest()
        class_name = class_for_type.get(fact["type"], "Fact")
        lines.extend(
            [
                f'  <rdf:Description rdf:about="https://ontology.ntu.edu.vn/resource/{subject_id}">',
                f'    <rdf:type rdf:resource="https://ontology.ntu.edu.vn/academic#{class_name}"/>',
                f"    <rdfs:label>{escape(fact['entity'])}</rdfs:label>",
                f"    <ntu:factType>{escape(fact['type'])}</ntu:factType>",
                f"    <ntu:content>{escape(fact['content'])}</ntu:content>",
                f"    <ntu:sourceUrl>{escape(fact['source_url'])}</ntu:sourceUrl>",
                f"    <ntu:retrievedAt>{escape(fact['retrieved_at'])}</ntu:retrievedAt>",
                f"    <ntu:confidence>{escape(fact['confidence'])}</ntu:confidence>",
            ]
        )
        if fact["code"]:
            lines.append(f"    <ntu:code>{escape(fact['code'])}</ntu:code>")
        if fact["cohort"]:
            lines.append(f"    <ntu:cohort>{escape(fact['cohort'])}</ntu:cohort>")
        if fact["academic_year"]:
            lines.append(f"    <ntu:academicYear>{escape(fact['academic_year'])}</ntu:academicYear>")
        if fact["document_number"]:
            lines.append(f"    <ntu:documentNumber>{escape(fact['document_number'])}</ntu:documentNumber>")
        if fact["relation"] and fact["target"]:
            relation = fact["relation"]
            if relation.replace("_", "").isalnum() and not relation[0].isdigit():
                target_id = hashlib.sha256(fact["target"].encode("utf-8")).hexdigest()
                lines.append(
                    f'    <ntu:{relation} rdf:resource="https://ontology.ntu.edu.vn/resource/{target_id}"/>'
                )
        lines.append("  </rdf:Description>")
    lines.append("</rdf:RDF>")
    Path(destination).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(facts)


def generate_quality_report(store: KnowledgeStore, destination: str | Path) -> dict[str, int | str]:
    checks = quality_checks(store)
    lines = ["# Data Quality Report", "", f"Ngày chạy: {datetime.now(VIETNAM_TIMEZONE).date()}", ""]
    for name, value in checks.items():
        marker = "✅" if value in {0, "ok"} else "⚠️"
        lines.append(f"- {marker} {name}: {value}")
    Path(destination).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return checks
