"""Create provenance-rich atomic facts from normalized SQLite records."""

from __future__ import annotations

import json
from collections import Counter
from urllib.parse import urlsplit

from database import KnowledgeStore
from pipeline.version import upsert_fact


def _source_type(store: KnowledgeStore, url: str) -> str:
    row = store.connection.execute(
        "SELECT source_type FROM sources WHERE url=?", (url,)
    ).fetchone()
    return row["source_type"] if row else "official_unit_page"


def generate_facts(store: KnowledgeStore) -> dict[str, int]:
    stats: Counter[str] = Counter()

    for row in store.connection.execute(
        "SELECT * FROM programs WHERE crawl_status='success'"
    ):
        common = {
            "entity": row["program_id"],
            "code": row["major_code"],
            "cohort": row["cohort"],
            "degree_level": row["degree_level"],
            "source_url": row["source_url"],
            "source_domain": urlsplit(row["source_url"]).hostname,
            "source_type": _source_type(store, row["source_url"]),
            "citation": f"chuongTrinhDTID={row['external_id']}",
            "retrieved_at": row["retrieved_at"],
        }
        facts = [
            {
                **common,
                "type": "program_name",
                "content": row["program_name"] or row["major_name"] or "",
                "relation": "belongsToMajor",
                "target": row["major_code"],
            }
        ]
        if row["total_credits"] is not None:
            credit_from_pdf = row["credit_method"] == "official_pdf_explicit"
            credit_from_html = row["credit_method"] == "official_html_explicit"
            credit_url = (
                row["pdf_url"] if credit_from_pdf
                else row["fallback_url"] if credit_from_html
                else row["source_url"]
            )
            facts.append(
                {
                    **common,
                    "type": "total_credits",
                    "content": str(row["total_credits"]),
                    "relation": "hasCreditRequirement",
                    "target": str(row["total_credits"]),
                    "source_url": credit_url,
                    "source_domain": urlsplit(credit_url).hostname,
                    "source_type": "official_program_pdf" if credit_from_pdf else common["source_type"],
                    "citation": row["credit_citation"] or common["citation"],
                }
            )
        for fact in facts:
            stats[f"facts_{upsert_fact(store, fact)}"] += 1

    for row in store.connection.execute("SELECT * FROM courses"):
        fact = {
            "entity": row["program_id"],
            "type": "has_course",
            "code": row["course_code"],
            "content": row["course_name"],
            "relation": "hasCourse",
            "target": row["course_id"],
            "citation": row["citation"],
            "source_url": row["source_url"],
            "source_domain": urlsplit(row["source_url"]).hostname,
            "source_type": _source_type(store, row["source_url"]),
            "retrieved_at": row["retrieved_at"],
        }
        stats[f"facts_{upsert_fact(store, fact)}"] += 1

    for row in store.connection.execute("SELECT * FROM procedures WHERE status='verified'"):
        fact = {
            "entity": row["name"],
            "type": "procedure",
            "content": row["result"] or row["name"],
            "relation": "processedBy",
            "target": row["processing_unit"],
            "source_url": row["source_url"],
            "source_domain": urlsplit(row["source_url"]).hostname,
            "source_type": _source_type(store, row["source_url"]),
            "citation": row["legal_basis"] or row["verification_note"] or row["form"],
            "retrieved_at": row["retrieved_at"],
        }
        stats[f"facts_{upsert_fact(store, fact)}"] += 1

    for row in store.connection.execute("SELECT * FROM units"):
        fact = {
            "entity": row["name"],
            "type": "unit",
            "content": row["name"],
            "source_url": row["source_url"],
            "source_domain": urlsplit(row["source_url"]).hostname,
            "source_type": _source_type(store, row["source_url"]),
            "citation": "Trang chính thức của đơn vị",
            "retrieved_at": row["retrieved_at"],
        }
        stats[f"facts_{upsert_fact(store, fact)}"] += 1

    for row in store.connection.execute("SELECT * FROM student_services WHERE status='verified'"):
        unit = store.connection.execute(
            "SELECT name FROM units WHERE unit_id=?", (row["unit_id"],)
        ).fetchone()
        service_fact = {
            "entity": unit["name"],
            "type": "student_service",
            "content": row["description"] or row["name"],
            "relation": "providesService",
            "target": row["name"],
            "source_url": row["source_url"],
            "source_domain": urlsplit(row["source_url"]).hostname,
            "source_type": _source_type(store, row["source_url"]),
            "citation": row["citation"],
            "retrieved_at": row["retrieved_at"],
        }
        stats[f"facts_{upsert_fact(store, service_fact)}"] += 1

    for row in store.connection.execute("SELECT * FROM graduate_programs"):
        fact = {
            "entity": row["program_id"],
            "type": "graduate_program",
            "code": row["major_code"],
            "content": row["program_name"],
            "relation": "belongsToMajor",
            "target": row["major_code"],
            "cohort": row["cohort"],
            "degree_level": row["degree_level"],
            "citation": row["citation"],
            "source_url": row["source_url"],
            "source_domain": urlsplit(row["source_url"]).hostname,
            "source_type": _source_type(store, row["source_url"]),
            "retrieved_at": row["retrieved_at"],
        }
        stats[f"facts_{upsert_fact(store, fact)}"] += 1

    for row in store.connection.execute("SELECT * FROM admissions"):
        fact = {
            "entity": f"{row['major_name']} [{row['admission_category']}]",
            "type": "admission_program",
            "code": row["admission_code"],
            "content": f"Mã xét tuyển {row['admission_code']}",
            "relation": "offeredInYear",
            "target": str(row["year"]),
            "academic_year": str(row["year"]),
            "citation": f"Mã xét tuyển: {row['admission_code']}",
            "source_url": row["source_url"],
            "source_domain": urlsplit(row["source_url"]).hostname,
            "source_type": _source_type(store, row["source_url"]),
            "retrieved_at": row["retrieved_at"],
            "note": f"Admission year evidence: {row['year_source_url']}",
        }
        stats[f"facts_{upsert_fact(store, fact)}"] += 1

    for row in store.connection.execute(
        "SELECT * FROM discipline_rules WHERE status IN ('verified_ocr', 'verified_manual')"
    ):
        sanctions = "; ".join(
            f"{label}: {row[field]}"
            for label, field in (
                ("lần 1", "first_violation"),
                ("lần 2", "second_violation"),
                ("lần 3", "third_violation"),
                ("lần 4", "fourth_violation"),
                ("lần 5", "fifth_violation"),
                ("quy định", "sanction"),
            )
            if row[field]
        )
        code = row["citation"].split(", dòng ")[-1].split(",")[0]
        fact = {
            "entity": f"QĐ 1351 - {code}",
            "type": "discipline_rule",
            "content": f"{row['behavior']}; {sanctions}" if sanctions else row["behavior"],
            "relation": "hasSanction",
            "target": "disciplinaryResponse",
            "citation": row["citation"],
            "document_number": "1351/QĐ-ĐHNT",
            "document_date": "2025-09-03",
            "source_url": row["source_url"],
            "source_domain": urlsplit(row["source_url"]).hostname,
            "source_type": _source_type(store, row["source_url"]),
            "retrieved_at": row["retrieved_at"],
            "note": "Trích xuất OCR từ bảng; các dòng verified_manual đã được đối soát với ảnh quét; ô không đọc được vẫn là null.",
        }
        stats[f"facts_{upsert_fact(store, fact)}"] += 1

    return dict(stats)
