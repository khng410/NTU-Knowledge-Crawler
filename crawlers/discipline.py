"""Discover and extract the official student-conduct sanction appendix."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from pathlib import Path
from typing import Any

from crawlers.base import KeywordCandidateCrawler
from crawlers.ctdt import now_iso
from database import KnowledgeStore
from extractors.pdf_extractor import ocr_pdf


DISCIPLINE_URL = "https://phongctsv.ntu.edu.vn/uploads/46/files/2025/269-Q%C4%90%201351_Q%C4%90%20V%C4%83n%20h%C3%B3a%20h%E1%BB%8Dc%20%C4%91%C6%B0%E1%BB%9Dng%202025.pdf"

SANCTION_COLUMNS = (
    (0.43, "nhắc nhở"),
    (0.49, "khiển trách"),
    (0.56, "cảnh cáo"),
    (0.65, "đình chỉ học tập có thời hạn"),
    (0.73, "buộc thôi học"),
)

# Manually checked against the scanned appendix pages 8-10 (PDF pages 9-11).
# Keeping these corrections keyed by row makes the review reproducible while
# preserving the raw OCR observations in data/extracted/ocr.
MANUAL_CORRECTIONS = {
    "1": {
        "sanction": "Người học vi phạm bị xử lý kỷ luật phải viết bản kiểm điểm và trừ điểm rèn luyện theo mức tương ứng: nhắc nhở (5đ); khiển trách (10đ); cảnh cáo (20đ).",
    },
    "2.1": {
        "sanction": "Tùy theo mức độ, Hội đồng khen thưởng, kỷ luật sẽ họp và quyết định xử lý từ khiển trách đến buộc thôi học.",
    },
    "2.2": {"behavior": "Đăng tin, chia sẻ và bình luận thiếu tính xây dựng về Trường; phán xét, nhận định không đúng sự thật theo chiều hướng tiêu cực đối với viên chức và người lao động."},
    "2.3": {"behavior": "Đăng tải, chia sẻ bài viết, hình ảnh có nội dung xâm hại an ninh Quốc gia, chống phá Đảng và Nhà nước, xuyên tạc vu khống, xúc phạm uy tín của tổ chức, danh dự và nhân phẩm cá nhân trên mạng Internet."},
    "4.1": {"sanction": "Tùy theo mức độ, Hội đồng khen thưởng, kỷ luật sẽ họp và quyết định xử lý từ khiển trách đến buộc thôi học và phải bồi thường thiệt hại."},
    "5.1": {"sanction": "Trường hợp nghiêm trọng, chuyển cho cơ quan chức năng xử lý theo quy định của pháp luật."},
    "5.4": {"behavior": "Mua bán, tàng trữ, sử dụng, tổ chức sử dụng, vận chuyển chất ma túy và các chất gây nghiện khác."},
    "5.7": {
        "behavior": "Kích động, lôi kéo người khác biểu tình, chống đối, viết - rải truyền đơn, áp phích trái pháp luật.",
        "sanction": "Trường hợp nghiêm trọng, chuyển cho cơ quan chức năng xử lý theo quy định của pháp luật.",
    },
    "6.2": {"behavior": "Để xe máy, xe đạp, ô tô không đúng nơi quy định."},
    "8": {"sanction": "Tùy theo mức độ, Hội đồng khen thưởng, kỷ luật sẽ xem xét nhắc nhở, xử lý kỷ luật từ khiển trách đến buộc thôi học. Nếu nghiêm trọng, giao cho cơ quan chức năng xử lý theo quy định pháp luật."},
}
MANUALLY_REVIEWED_CODES = {
    "1", "2.1", "2.2", "2.3", "3.1", "3.2", "3.3", "4.1", "5.1", "5.2",
    "5.3", "5.4", "5.5", "5.6", "5.7", "5.8", "6.1", "6.2", "7", "8",
}


def _sanction_for_x(x: float) -> str:
    for upper, sanction in SANCTION_COLUMNS:
        if x < upper:
            return sanction
    return "buộc thôi học"


def _row_boundary(
    upper: dict[str, Any], lower: dict[str, Any], detail_prefixes: set[str]
) -> float:
    upper_y = float(upper["y"])
    lower_y = float(lower["y"])
    lower_code = str(lower["text"]).strip()
    if lower_code in detail_prefixes:
        return lower_y + 0.005
    return (upper_y + lower_y) / 2


def extract_discipline_rows(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recover appendix rows from OCR geometry without guessing missing cells."""
    observations = [item for item in observations if int(item.get("page", 0)) >= 9]
    code_items = [
        item for item in observations
        if float(item.get("x", 1)) < 0.11
        and re.fullmatch(r"\d+(?:\.\d+)?", str(item.get("text", "")).strip())
        and float(item.get("y", 1)) < 0.72
    ]
    all_codes = {str(item["text"]).strip() for item in code_items}
    detail_prefixes = {code.split(".", 1)[0] for code in all_codes if "." in code}
    rows: list[dict[str, Any]] = []
    for item in code_items:
        code = str(item["text"]).strip()
        if "." not in code and code in detail_prefixes:
            continue
        page = int(item["page"])
        row_y = float(item["y"])
        page_codes = sorted(
            (other for other in code_items if int(other["page"]) == page),
            key=lambda other: float(other["y"]),
            reverse=True,
        )
        position = page_codes.index(item)
        upper_bound = (
            _row_boundary(page_codes[position - 1], item, detail_prefixes)
            if position > 0 else row_y + (0.03 if row_y > 0.65 else 0.05)
        )
        if position + 1 < len(page_codes):
            lower_bound = _row_boundary(item, page_codes[position + 1], detail_prefixes)
        else:
            lower_bound = 0.045
        cells = [
            other for other in observations
            if int(other["page"]) == page
            and lower_bound <= float(other["y"]) <= upper_bound
            and other is not item
        ]
        behavior = " ".join(
            str(other["text"]).strip() for other in cells
            if 0.115 <= float(other["x"]) < 0.35
        ).strip()
        if not behavior:
            continue
        violations: dict[int, str] = {}
        sanction_notes: list[str] = []
        for other in cells:
            x = float(other["x"])
            if x < 0.35:
                continue
            text = str(other["text"]).strip()
            match = re.fullmatch(r"L(?:ầ|â|a)n\s*(\d+)", text, flags=re.I)
            if match:
                violations[int(match.group(1))] = _sanction_for_x(x)
            elif len(text) > 2 and "Số lần vi phạm" not in text:
                sanction_notes.append(text)
        rows.append({
            "code": code,
            "behavior": behavior,
            "first_violation": violations.get(1),
            "second_violation": violations.get(2),
            "third_violation": violations.get(3),
            "fourth_violation": violations.get(4),
            "fifth_violation": violations.get(5),
            "sanction": " ".join(sanction_notes).strip() or None,
            "citation": f"Phụ lục QĐ 1351/QĐ-ĐHNT, dòng {code}, trang PDF {page}",
            "status": "verified_ocr" if violations or sanction_notes else "partial_ocr",
        })
    rows_by_code = {row["code"]: row for row in rows}
    ordered_rows = sorted(rows, key=lambda row: [int(part) for part in row["code"].split(".")])
    for previous, current in zip(ordered_rows, ordered_rows[1:]):
        behavior = current["behavior"]
        if not behavior or not behavior[0].islower():
            continue
        split_at = None
        if previous["behavior"].count("(") > previous["behavior"].count(")") and ")" in behavior:
            split_at = behavior.index(")") + 1
        elif "." in behavior:
            split_at = behavior.index(".") + 1
        if split_at:
            continuation = behavior[:split_at].strip()
            previous["behavior"] = f"{previous['behavior']} {continuation}".strip()
            current["behavior"] = behavior[split_at:].strip()
    for previous, current in zip(ordered_rows, ordered_rows[1:]):
        sanction = current["sanction"] or ""
        is_continuation = re.match(
            r"^(?:xử lý|theo quy định|và quyết định|học và|pháp luật)\b",
            sanction,
            flags=re.I,
        )
        if is_continuation and "." in sanction:
            split_at = sanction.index(".") + 1
            continuation = sanction[:split_at].strip()
            previous["sanction"] = " ".join(
                value for value in (previous["sanction"], continuation) if value
            )
            current["sanction"] = sanction[split_at:].strip() or None
    # QĐ 1351 uses one merged sanction cell for rows 5.2 and 5.3.
    if "5.2" in rows_by_code and "5.3" in rows_by_code:
        merged = " ".join(
            value for value in (rows_by_code["5.2"]["sanction"], rows_by_code["5.3"]["sanction"])
            if value
        ) or None
        rows_by_code["5.2"]["sanction"] = merged
        rows_by_code["5.3"]["sanction"] = merged
        if merged:
            rows_by_code["5.2"]["status"] = rows_by_code["5.3"]["status"] = "verified_ocr"
    for code in MANUALLY_REVIEWED_CODES:
        if code in rows_by_code:
            rows_by_code[code].update(MANUAL_CORRECTIONS.get(code, {}))
            rows_by_code[code]["status"] = "verified_manual"
            rows_by_code[code]["citation"] += "; đối soát thủ công với ảnh quét"
    return ordered_rows


class DisciplineCrawler:
    def __init__(self, store: KnowledgeStore, raw_directory: str = "data/raw") -> None:
        self.store = store
        self.base = KeywordCandidateCrawler(
            store,
            category="discipline",
            seeds=(
                "https://phongctsv.ntu.edu.vn/van-ban-bieu-mau",
                "https://phongctsv.ntu.edu.vn/van-ban-bieu-mau/van-ban",
                "https://phongctsv.ntu.edu.vn/en-us/van-ban-bieu-mau/quy-trinh-xu-ly-cong-viec",
            ),
            keywords=("kỷ luật", "rèn luyện", "vi phạm", "khiển trách", "cảnh cáo", "đình chỉ"),
            direct_sources=((
                "Quy định Văn hóa học đường - QĐ 1351/QĐ-ĐHNT ngày 03/09/2025",
                DISCIPLINE_URL,
                ("kỷ luật", "văn hóa học đường"),
            ),),
            raw_directory=raw_directory,
        )
        self.stats: Counter[str] = Counter()

    def discover(self) -> dict[str, int]:
        result = self.base.discover()
        self.stats.update(result)
        return dict(self.stats)

    def crawl(self, limit: int | None = None) -> dict[str, int]:
        result = self.base.crawl(limit)
        self.stats.update(result)
        source = self.store.connection.execute(
            "SELECT raw_path, retrieved_at FROM sources WHERE url=?", (DISCIPLINE_URL,)
        ).fetchone()
        if not source or not str(source["raw_path"]).casefold().endswith(".pdf"):
            return dict(self.stats)
        rows = extract_discipline_rows(ocr_pdf(Path(source["raw_path"])))
        self.store.connection.execute("DELETE FROM discipline_rules WHERE source_url=?", (DISCIPLINE_URL,))
        for row in rows:
            rule_id = hashlib.sha256(f"QĐ1351|{row['code']}".encode()).hexdigest()
            self.store.connection.execute(
                """INSERT INTO discipline_rules
                   (rule_id, behavior, first_violation, second_violation, third_violation,
                    fourth_violation, fifth_violation,
                    sanction, citation, source_url, retrieved_at, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    rule_id, row["behavior"], row["first_violation"], row["second_violation"],
                    row["third_violation"], row["fourth_violation"], row["fifth_violation"],
                    row["sanction"], row["citation"],
                    DISCIPLINE_URL, source["retrieved_at"] or now_iso(), row["status"],
                ),
            )
        self.store.connection.commit()
        self.stats["discipline_rules_materialized"] = len(rows)
        return dict(self.stats)


def build_discipline_crawler(store: KnowledgeStore, raw_directory: str = "data/raw") -> DisciplineCrawler:
    return DisciplineCrawler(store, raw_directory)
