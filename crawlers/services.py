"""Student-service source discovery and explicit unit records."""

import hashlib
import json
import re
from html import unescape
from pathlib import Path

from crawlers.base import KeywordCandidateCrawler
from crawlers.ctdt import now_iso
from database import KnowledgeStore
from extractors.html_extractor import parse_html


class ServicesCrawler(KeywordCandidateCrawler):
    LIBRARY_SERVICES_URL = "https://thuvien.ntu.edu.vn/contentbrowser.aspx?contentid=375"
    LIBRARY_HOURS_URL = "https://thuvien.ntu.edu.vn/News.aspx?contentid=526"
    INSURANCE_GUIDE_URL = "https://phongctsv.ntu.edu.vn/tin-tuc/nhung-dieu-can-biet-ve-viec-su-dung-bao-hiem-y-te"
    CAREER_URL = "https://phongctsv.ntu.edu.vn/tin-tuc/ngay-hoi-tuyen-dung-viec-lam-%E2%80%93-ntu-job-fair-2026"
    CAREER_SUPPORT_URL = "https://htdnhtsv.ntu.edu.vn/a/75548/TRUNG-TAM-QHDNHTSV-HO-TRO-TIM-KIEM-VIEC-LAM-VA-KHOI-NGHIEP-DU-AN-V2WORK?c=8907"
    INTERNSHIP_URL = "https://htdnhtsv.ntu.edu.vn/a/236962/Thuc-tap-sinh-Dien-Xay-dung-Moi-truong-Giao-thong"
    DORMITORY_URL = "https://trungtampvth.ntu.edu.vn/dich-vu/he-thong-ky-tuc-xa"
    UNITS = {
        "thuvien.ntu.edu.vn": ("Thư viện Trường Đại học Nha Trang", "https://thuvien.ntu.edu.vn/"),
        "htdnhtsv.ntu.edu.vn": ("Đơn vị hỗ trợ doanh nghiệp và hỗ trợ sinh viên", "https://htdnhtsv.ntu.edu.vn/"),
        "phongctsv.ntu.edu.vn": ("Phòng Công tác Chính trị và Sinh viên", "https://phongctsv.ntu.edu.vn/"),
        "trungtampvth.ntu.edu.vn": ("Trung tâm Phục vụ Trường học", "https://trungtampvth.ntu.edu.vn/"),
    }

    def crawl(self, limit: int | None = None) -> dict[str, int]:
        super().crawl(limit)
        for domain, (name, source_url) in self.UNITS.items():
            candidate_services = [
                row[0]
                for row in self.store.connection.execute(
                    "SELECT title FROM source_candidates WHERE category='services' AND source_domain=?",
                    (domain,),
                )
            ]
            services = [
                title for title in candidate_services
                if not title.casefold().startswith(("thông báo", "ngày hội", "sơ đồ", "đại học"))
                and (
                    "dịch vụ" in title.casefold()
                    or "tra cứu" in title.casefold()
                    or title.casefold() == "thư viện"
                )
            ]
            source = self.store.connection.execute(
                "SELECT raw_path, retrieved_at FROM sources WHERE url=?", (source_url,)
            ).fetchone()
            blocks = []
            lines = []
            if source and source["raw_path"] and Path(source["raw_path"]).suffix.casefold() == ".html":
                html = Path(source["raw_path"]).read_text(encoding="utf-8-sig", errors="replace")
                blocks, _ = parse_html(html, source_url)
                plain = unescape(re.sub(r"<[^>]+>", "\n", html))
                lines = [re.sub(r"\s+", " ", line).strip() for line in plain.splitlines() if line.strip()]

            def labelled_value(labels: tuple[str, ...]) -> str | None:
                for index, line in enumerate(lines):
                    lowered = line.casefold()
                    if not lowered.startswith(labels):
                        continue
                    value = line.split(":", 1)[1].strip() if ":" in line else ""
                    if value:
                        return value
                    if index + 1 < len(lines):
                        return lines[index + 1]
                return None

            address = labelled_value(("địa chỉ", "address"))
            email = next(
                (
                    match.group(0)
                    for line in lines
                    if (match := re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", line))
                ),
                None,
            )
            phone = next(
                (
                    line.split(":", 1)[-1].strip()
                    for line in lines
                    if line.casefold().startswith(("điện thoại:", "phone:", "tel:"))
                    and "0258" in line
                ),
                None,
            )
            working_hours = next(
                (
                    block for block in blocks
                    if any(term in block.casefold() for term in ("giờ làm việc", "thời gian phục vụ"))
                ),
                None,
            )
            unit_id = hashlib.sha256(domain.encode("utf-8")).hexdigest()
            self.store.connection.execute(
                """INSERT INTO units
                   (unit_id, name, address, phone, email, working_hours,
                    services_json, source_url, retrieved_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(unit_id) DO UPDATE SET
                     address=excluded.address, phone=excluded.phone,
                     email=excluded.email, working_hours=excluded.working_hours,
                     services_json=excluded.services_json,
                     source_url=excluded.source_url, retrieved_at=excluded.retrieved_at""",
                (
                    unit_id, name, address, phone, email, working_hours,
                    json.dumps(services, ensure_ascii=False), source_url,
                    source["retrieved_at"] if source and source["retrieved_at"] else now_iso(),
                ),
            )
        self.store.connection.commit()
        self.stats["service_units_materialized"] = len(self.UNITS)
        self._materialize_services()
        return dict(self.stats)

    def _upsert_service(
        self, unit_domain: str, name: str, description: str, source_url: str,
        citation: str, retrieved_at: str,
    ) -> None:
        service_id = hashlib.sha256(f"{unit_domain}|{name}|{source_url}".encode()).hexdigest()
        unit_id = hashlib.sha256(unit_domain.encode()).hexdigest()
        self.store.connection.execute(
            """INSERT INTO student_services
               (service_id, unit_id, name, description, source_url, citation,
                retrieved_at, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'verified')
               ON CONFLICT(service_id) DO UPDATE SET
                 description=excluded.description, citation=excluded.citation,
                 retrieved_at=excluded.retrieved_at, status='verified'""",
            (service_id, unit_id, name, description, source_url, citation, retrieved_at),
        )

    def _source_blocks(self, url: str) -> tuple[list[str], str] | None:
        source = self.store.connection.execute(
            "SELECT raw_path, retrieved_at FROM sources WHERE url=?", (url,)
        ).fetchone()
        if not source or not str(source["raw_path"]).casefold().endswith(".html"):
            return None
        html = Path(source["raw_path"]).read_text(encoding="utf-8-sig", errors="replace")
        blocks, _ = parse_html(html, url)
        return blocks, source["retrieved_at"] or now_iso()

    def _materialize_services(self) -> None:
        library = self._source_blocks(self.LIBRARY_SERVICES_URL)
        if library:
            blocks, retrieved_at = library
            start = next((i for i, block in enumerate(blocks) if "Dịch vụ miễn phí 1." in block), None)
            stop = next((i for i, block in enumerate(blocks) if block == "DỊCH VỤ CÓ THU PHÍ"), len(blocks))
            free_blocks = blocks[start:stop] if start is not None else []
            for block in free_blocks:
                match = re.search(r"(?:Dịch vụ miễn phí\s+)?\d+\.\s*(.+?)(?:\.$|$)", block)
                if not match:
                    continue
                name = re.sub(r"\s+", " ", match.group(1)).strip(" -.")
                if not name or len(name) > 100:
                    continue
                self._upsert_service(
                    "thuvien.ntu.edu.vn", name, name, self.LIBRARY_SERVICES_URL,
                    f"DỊCH VỤ THƯ VIỆN – Dịch vụ miễn phí: {name}", retrieved_at,
                )
        hours = self._source_blocks(self.LIBRARY_HOURS_URL)
        if hours:
            blocks, _ = hours
            hours_text = next(
                (b for b in blocks if "thời gian phục vụ" in b.casefold() or "giờ phục vụ" in b.casefold()),
                None,
            )
            if hours_text:
                self.store.connection.execute(
                    "UPDATE units SET working_hours=? WHERE unit_id=?",
                    (hours_text, hashlib.sha256(b"thuvien.ntu.edu.vn").hexdigest()),
                )
        for domain, name, url, evidence in (
            ("phongctsv.ntu.edu.vn", "Hướng dẫn sử dụng bảo hiểm y tế", self.INSURANCE_GUIDE_URL, "Tra cứu hạn sử dụng, mã số thẻ và hướng dẫn sử dụng BHYT số"),
            ("phongctsv.ntu.edu.vn", "Kết nối tuyển dụng việc làm", self.CAREER_URL, "Ngày hội tuyển dụng việc làm NTU Job Fair 2026"),
            ("htdnhtsv.ntu.edu.vn", "Hỗ trợ tìm kiếm việc làm và khởi nghiệp", self.CAREER_SUPPORT_URL, "Trung tâm QHDN&HTSV hỗ trợ tìm kiếm việc làm và khởi nghiệp (Dự án V2WORK)"),
            ("htdnhtsv.ntu.edu.vn", "Thông tin cơ hội thực tập", self.INTERNSHIP_URL, "Thông báo tuyển dụng thực tập sinh Điện, Xây dựng, Môi trường, Giao thông"),
            ("trungtampvth.ntu.edu.vn", "Ký túc xá sinh viên", self.DORMITORY_URL, "Hệ thống Ký túc xá K1-K8; trang nguồn nêu sức chứa, tiện ích, an ninh và đối tượng ưu tiên"),
        ):
            source = self._source_blocks(url)
            if source:
                _, retrieved_at = source
                self._upsert_service(domain, name, evidence, url, evidence, retrieved_at)
        for domain in self.UNITS:
            unit_id = hashlib.sha256(domain.encode()).hexdigest()
            names = [row[0] for row in self.store.connection.execute(
                "SELECT name FROM student_services WHERE unit_id=? ORDER BY name", (unit_id,)
            )]
            self.store.connection.execute(
                "UPDATE units SET services_json=? WHERE unit_id=?",
                (json.dumps(names, ensure_ascii=False), unit_id),
            )
        self.store.connection.commit()
        self.stats["student_services_verified"] = self.store.connection.execute(
            "SELECT COUNT(*) FROM student_services WHERE status='verified'"
        ).fetchone()[0]


def build_services_crawler(store: KnowledgeStore, raw_directory: str = "data/raw") -> ServicesCrawler:
    return ServicesCrawler(
        store,
        category="services",
        seeds=(
            "https://thuvien.ntu.edu.vn/",
            "https://htdnhtsv.ntu.edu.vn/",
            "https://phongctsv.ntu.edu.vn/",
            "https://trungtampvth.ntu.edu.vn/",
        ),
        keywords=(
            "thư viện",
            "ký túc xá",
            "thẻ sinh viên",
            "việc làm",
            "thực tập",
            "hỗ trợ sinh viên",
            "bảo hiểm",
        ),
        direct_sources=(
            ("DỊCH VỤ THƯ VIỆN", ServicesCrawler.LIBRARY_SERVICES_URL, ("dịch vụ",)),
            ("Lịch phục vụ Thư viện", ServicesCrawler.LIBRARY_HOURS_URL, ("thời gian phục vụ",)),
            ("Những điều cần biết về việc sử dụng bảo hiểm y tế", ServicesCrawler.INSURANCE_GUIDE_URL, ("bảo hiểm y tế",)),
            ("Ngày hội tuyển dụng việc làm – NTU Job Fair 2026", ServicesCrawler.CAREER_URL, ("tuyển dụng việc làm",)),
            ("Hỗ trợ tìm kiếm việc làm và khởi nghiệp", ServicesCrawler.CAREER_SUPPORT_URL, ("việc làm", "khởi nghiệp")),
            ("Thông tin cơ hội thực tập", ServicesCrawler.INTERNSHIP_URL, ("thực tập",)),
            ("Hệ thống Ký túc xá", ServicesCrawler.DORMITORY_URL, ("ký túc xá",)),
        ),
        raw_directory=raw_directory,
    )
