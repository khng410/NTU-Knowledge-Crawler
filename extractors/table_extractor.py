"""Extract programs and course rows from the public CTĐT API payload."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any


def _number(value: Any) -> int | float | None:
    if value is None or value == "":
        return None
    number = float(value)
    return int(number) if number.is_integer() else number


def _total_credits(payload: Mapping[str, Any]) -> int | float | None:
    groups = payload.get("nhomKhungCTs") or []
    values = [
        (_number(group.get("soTinChiBB")) or 0) + (_number(group.get("soTinChiTC")) or 0)
        for group in groups
    ]
    total = sum(values)
    return total if total else None


def _content_status(payload: Mapping[str, Any]) -> str:
    course_count = len(payload.get("hocPhanKhungs") or []) + len(
        payload.get("hocPhanKhungTTCLCs") or []
    )
    if course_count:
        return "complete"
    if "biên soạn" in str(payload.get("tenTrangThaiCTDT") or "").casefold():
        return "draft_empty"
    return "inconsistent"


def program_identifier(
    major_code: str,
    cohort: str,
    *,
    special: bool = False,
    suffix: str | int | None = None,
) -> str:
    safe_code = re.sub(r"[^A-Z0-9]+", "-", major_code.upper()).strip("-") or "UNKNOWN"
    variant = "SPECIAL" if special else "STANDARD"
    identifier = f"NTU-PROGRAM-{safe_code}-{cohort.upper()}-{variant}"
    return f"{identifier}-{suffix}" if suffix is not None else identifier


def extract_program(
    payload: Mapping[str, Any],
    *,
    program_id: str,
    source_url: str,
    pdf_url: str | None,
    retrieved_at: str,
) -> dict[str, Any]:
    cohort_value = payload.get("apDungTuKhoa")
    plos = [
        {
            "code": item.get("maChuanDauRa"),
            "content": item.get("noiDungCDR"),
        }
        for item in (payload.get("chuanDauRas") or [])
        if item.get("noiDungCDR")
    ]
    duration = payload.get("thoiGianDaoTao")
    duration_text = (
        f"{duration:g} năm" if isinstance(duration, (int, float)) else None
    )
    total_credits = _total_credits(payload)
    return {
        "program_id": program_id,
        "external_id": payload.get("chuongTrinhDTID"),
        "major_code": str(payload.get("maNganh") or ""),
        "major_name": payload.get("tenNganh"),
        "program_name": payload.get("tenChuongTrinh"),
        "cohort": f"K{cohort_value}" if cohort_value is not None else None,
        "faculty": payload.get("tenDonVi"),
        "degree_level": payload.get("tenTrinhDo"),
        "training_mode": payload.get("tenHinhThucDT"),
        "duration": duration_text,
        "total_credits": total_credits,
        "language": payload.get("tenNgonNgu"),
        "degree": payload.get("tenVanBang"),
        "decision_number": payload.get("soQuyetDinh"),
        "decision_date": payload.get("ngayQuyetDinh"),
        "updated_date": payload.get("thoiGianXayDung"),
        "plos_json": json.dumps(plos, ensure_ascii=False),
        "source_url": source_url,
        "pdf_url": pdf_url,
        "crawl_status": "success",
        "content_status": _content_status(payload),
        "publication_status": payload.get("tenTrangThaiCTDT"),
        "published": 1 if payload.get("published") else 0,
        "declared_course_count": payload.get("soHocPhan"),
        "credit_status": "verified" if total_credits is not None else "not_stated",
        "credit_method": "api_group_totals" if total_credits is not None else None,
        "credit_citation": "nhomKhungCTs.soTinChiBB + soTinChiTC" if total_credits is not None else None,
        "pdf_status": "available" if pdf_url else "not_listed",
        "retrieved_at": retrieved_at,
    }


def extract_courses(
    payload: Mapping[str, Any],
    *,
    program_id: str,
    source_url: str,
    retrieved_at: str,
) -> list[dict[str, Any]]:
    course_rows = list(payload.get("hocPhanKhungs") or [])
    course_rows.extend(payload.get("hocPhanKhungTTCLCs") or [])
    records_by_course: dict[str, dict[str, Any]] = {}

    for row in course_rows:
        source_id = row.get("hocPhanKhungID")
        course_code = str(row.get("maHocPhan") or "").strip().casefold()
        course_name = str(row.get("tenHocPhan") or "").strip().casefold()
        logical_id = f"{course_code}|{course_name}" if course_code or course_name else row.get("hocPhanID") or source_id
        identity = f"{program_id}|{logical_id}"
        course_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        if course_id in records_by_course:
            existing = records_by_course[course_id]
            if existing["semester"] != row.get("phanBoHocKy"):
                existing["semester"] = None
            new_type = "tự chọn" if row.get("tuChon") else "bắt buộc"
            if existing["course_type"] != new_type:
                existing["course_type"] = "bắt buộc hoặc tự chọn tùy nhóm"
            citation = f"hocPhanKhungID={source_id}" if source_id is not None else None
            if citation and citation not in (existing["citation"] or "").split(", "):
                existing["citation"] = ", ".join(filter(None, [existing["citation"], citation]))
            continue

        prerequisites = row.get("dieuKienTQs")
        prerequisite_json = (
            json.dumps(prerequisites, ensure_ascii=False)
            if prerequisites
            else None
        )
        records_by_course[course_id] = {
                "course_id": course_id,
                "course_code": row.get("maHocPhan"),
                "course_name": row.get("tenHocPhan") or "",
                "credits": _number(row.get("soTC")),
                "theory_hours_or_credits": _number(row.get("tietLyThuyet")),
                "practice_hours_or_credits": _number(row.get("tietThucHanh")),
                "workload_unit": "tiết",
                "semester": row.get("phanBoHocKy"),
                "course_type": "tự chọn" if row.get("tuChon") else "bắt buộc",
                "prerequisite_json": prerequisite_json,
                "program_id": program_id,
                "source_url": source_url,
                "citation": f"hocPhanKhungID={source_id}" if source_id is not None else None,
                "retrieved_at": retrieved_at,
        }
    return list(records_by_course.values())
