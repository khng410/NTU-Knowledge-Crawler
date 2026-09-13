"""Year-scoped undergraduate admission program extraction."""

from __future__ import annotations

import hashlib
import re
from collections import Counter

from crawlers.ctdt import now_iso
from database import KnowledgeStore
from extractors.html_extractor import parse_html, parse_tables
from pipeline.crawl import SourceFetcher


ADMISSION_YEAR_URL = "https://tuyensinh.ntu.edu.vn/de-an-tuyen-sinh"
PROGRAMS_URL = "https://tuyensinh.ntu.edu.vn/%C4%91ai-hoc/tra-cuu-nganh-to-hop-xet-tuyen"
CONTINUING_URL = (
    "https://trungtamdtbd.ntu.edu.vn/tin-tuc/"
    "thong-bao-tuyen-sinh-he-dai-hoc-tai-truong-dai-hoc-nha-trang-nam-2026"
)

CONTINUING_MODES = (
    ("second_degree", "Đại học liên thông từ Đại học (VB2)", "1,5 năm"),
    ("articulation_college", "Đại học liên thông từ Cao đẳng", "1,5 năm"),
    ("articulation_intermediate", "Đại học liên thông từ Trung cấp", "2,5 năm"),
    ("part_time_undergraduate", "Đại học vừa làm vừa học", "4 năm"),
)


def admission_code_from_label(label: str) -> str | None:
    """Extract a code without absorbing the adjacent UI action label `Xem`."""
    match = re.match(r"([0-9]{7}[A-Z]*)\b", label.strip())
    return match.group(1) if match else None


class AdmissionsCrawler:
    def __init__(self, store: KnowledgeStore, raw_directory: str = "data/raw") -> None:
        self.store = store
        self.fetcher = SourceFetcher(store, raw_directory)
        self.stats: Counter[str] = Counter()

    def crawl(self) -> dict[str, int]:
        year_html = self.fetcher.text(ADMISSION_YEAR_URL, category="admission_context")
        year_blocks, _ = parse_html(year_html, ADMISSION_YEAR_URL)
        year_heading = next(
            (block for block in year_blocks if re.search(r"tuyển sinh đại học năm\s+20\d{2}", block, re.I)),
            None,
        )
        if year_heading is None:
            raise ValueError("Admission year is not explicitly stated by the source")
        year_match = re.search(r"\b20\d{2}\b", year_heading)
        assert year_match is not None
        year = int(year_match.group())
        general_methods = [
            block for block in year_blocks if re.match(r"Phương thức\s*\d+\s*:", block, re.I)
        ]
        if not general_methods:
            raise ValueError("Admission methods are not explicitly stated by the source")

        programs_html = self.fetcher.text(PROGRAMS_URL, category="admission_programs")
        table = next(
            (
                rows
                for rows in parse_tables(programs_html)
                if rows and "Mã xét tuyển" in rows[0]
            ),
            None,
        )
        if table is None:
            raise ValueError("Admission program table was not found")

        # Replace this source/year snapshot so parser corrections cannot leave
        # stale admissions behind under an old derived identifier.
        self.store.connection.execute(
            """DELETE FROM admissions
               WHERE year=? AND admission_category='regular_undergraduate'
                 AND source_url=?""",
            (year, PROGRAMS_URL),
        )

        program_type: str | None = None
        seen: set[tuple[str, str]] = set()
        for row in table[1:]:
            if len(row) == 1:
                program_type = row[0].removeprefix("Nhóm ngành ").strip()
                continue
            if len(row) < 5 or not row[0].isdigit():
                continue
            # Keep an optional program suffix but stop before the UI label
            # "Xem". Removing spaces first would turn 7480201A Xem into the
            # false code 7480201AX.
            admission_code = admission_code_from_label(row[1])
            if admission_code is None:
                continue
            major_name = row[2]
            logical_key = (admission_code, major_name.casefold())
            if logical_key in seen:
                continue
            seen.add(logical_key)
            conditions = [f"Tổ hợp xét tuyển: {row[4]}"]
            if len(row) > 5 and row[5].strip().upper() == "X":
                conditions.append("Có điều kiện Tiếng Anh theo bảng nguồn")
            admission_id = hashlib.sha256(
                f"{year}|{admission_code}|{major_name}".encode("utf-8")
            ).hexdigest()
            self.store.connection.execute(
                """INSERT INTO admissions
                   (admission_id, year, admission_code, major_name, admission_mode,
                    admission_category, program_type, admission_conditions, source_url, year_source_url,
                    retrieved_at)
                   VALUES (?, ?, ?, ?, ?, 'regular_undergraduate', ?, ?, ?, ?, ?)
                   ON CONFLICT(admission_id) DO UPDATE SET
                     admission_mode=excluded.admission_mode,
                     program_type=excluded.program_type,
                     admission_conditions=excluded.admission_conditions,
                     source_url=excluded.source_url,
                     year_source_url=excluded.year_source_url,
                     retrieved_at=excluded.retrieved_at""",
                (
                    admission_id,
                    year,
                    admission_code,
                    major_name,
                    "\n".join(general_methods),
                    program_type,
                    "\n".join(conditions) or None,
                    PROGRAMS_URL,
                    ADMISSION_YEAR_URL,
                    now_iso(),
                ),
            )
            self.stats["admission_programs"] += 1
        self.store.connection.commit()
        self.stats["admission_year"] = year
        self._crawl_continuing_admissions()
        return dict(self.stats)

    def _crawl_continuing_admissions(self) -> None:
        html = self.fetcher.text(
            CONTINUING_URL,
            category="admission_continuing",
            source_type="official_news",
        )
        blocks, _ = parse_html(html, CONTINUING_URL)
        heading = next(
            (
                block for block in blocks
                if "Thông báo tuyển sinh hệ Đại học" in block and re.search(r"20\d{2}", block)
            ),
            None,
        )
        if heading is None:
            raise ValueError("Continuing-admission year is not explicit")
        year_match = re.search(r"20\d{2}", heading)
        assert year_match is not None
        year = int(year_match.group())
        # Replace every continuing-admission record for this year. This also
        # migrates records created from the server's old, non-canonical `/n`
        # alias to the stable article URL.
        self.store.connection.execute(
            """DELETE FROM admissions
               WHERE year=? AND admission_category!='regular_undergraduate'""",
            (year,),
        )
        table = next(
            (rows for rows in parse_tables(html) if rows and "Ngành đào tạo" in rows[0]),
            None,
        )
        if table is None or len(table) < 2 or len(table[1]) < 4:
            raise ValueError("Continuing-admission program table was not found")
        mode_evidence = table[1][3]
        modes = [mode for mode in CONTINUING_MODES if mode[1] in mode_evidence]
        if len(modes) != len(CONTINUING_MODES):
            raise ValueError("Continuing-admission modes are incomplete in the source table")
        condition_blocks = [
            block for block in blocks
            if block.startswith("Người đã") or block.startswith("Người có bằng")
        ]
        conditions_by_category = {
            "part_time_undergraduate": condition_blocks,
            "articulation_intermediate": condition_blocks[1:] if len(condition_blocks) >= 2 else condition_blocks,
            "articulation_college": condition_blocks[2:] if len(condition_blocks) >= 3 else condition_blocks,
            "second_degree": condition_blocks[-1:] if condition_blocks else [],
        }
        dates = next((block for block in blocks if block.startswith("Xét tuyển vào các tháng")), None)
        for row in table[1:]:
            if len(row) < 3 or not row[0].isdigit() or not re.fullmatch(r"\d{7}", row[2].strip()):
                continue
            major_name = row[1].strip()
            major_code = row[2].strip()
            for category, label, duration in modes:
                admission_id = hashlib.sha256(
                    f"{year}|{category}|{major_code}|{major_name}".encode("utf-8")
                ).hexdigest()
                self.store.connection.execute(
                    """INSERT INTO admissions
                       (admission_id, year, admission_code, major_code, major_name,
                        admission_mode, admission_category, program_type, duration,
                        admission_conditions, important_dates, source_url,
                        year_source_url, retrieved_at)
                       VALUES (?, ?, ?, ?, ?, 'xét tuyển', ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(admission_id) DO UPDATE SET
                         admission_conditions=excluded.admission_conditions,
                         important_dates=excluded.important_dates,
                         retrieved_at=excluded.retrieved_at""",
                    (
                        admission_id, year, major_code, major_code, major_name,
                        category, label, duration,
                        "\n".join(conditions_by_category[category]) or None, dates,
                        CONTINUING_URL, CONTINUING_URL, now_iso(),
                    ),
                )
                self.stats["continuing_admission_programs"] += 1
        self.store.connection.commit()
