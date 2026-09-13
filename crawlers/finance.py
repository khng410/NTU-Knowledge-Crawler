"""Finance and student-policy source discovery and conservative extraction."""

import hashlib
import json
import re
from datetime import date

from crawlers.base import KeywordCandidateCrawler
from crawlers.ctdt import now_iso
from database import KnowledgeStore
from extractors.html_extractor import parse_html


def classify_policy_types(title: str) -> list[str]:
    lowered = title.casefold()
    policy_types: list[str] = []
    is_exemption = "miễn giảm học phí" in lowered or "miễn, giảm học phí" in lowered
    is_extension = "gia hạn" in lowered and "học phí" in lowered
    is_debt = "nợ học phí" in lowered
    if "đóng học phí" in lowered:
        policy_types.append("tuition_notice")
    elif "học phí" in lowered and not (is_exemption or is_extension or is_debt):
        policy_types.append("tuition_rule")
    if is_exemption:
        policy_types.append("exemption")
    if "trợ cấp xã hội" in lowered or "hỗ trợ chi phí học tập" in lowered:
        policy_types.append("social_support")
    if "học bổng" in lowered:
        policy_types.append("scholarship")
    if is_extension:
        policy_types.append("payment_extension")
    if is_debt:
        policy_types.append("debt_rule")
    if any(term in lowered for term in ("bảo hiểm", "bhyt", "bhtt")):
        policy_types.append("insurance")
    return list(dict.fromkeys(policy_types))


def _date_values(blocks: list[str]) -> list[date]:
    values = []
    for block in blocks:
        for day, month, year in re.findall(r"\b(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(20\d{2})\b", block):
            try:
                values.append(date(int(year), int(month), int(day)))
            except ValueError:
                pass
    return values


def verify_policy(
    policy_type: str, title: str, relevant: list[str], legal: list[str], academic_year: str | None
) -> tuple[str, str]:
    """Return an evidence-based lifecycle state and an auditable explanation."""
    lowered = "\n".join(relevant).casefold()
    dates = _date_values(relevant)
    if "nghị định số 238/2025/nđ-cp" in lowered and policy_type == "tuition_rule":
        return "current", "Khung pháp lý hiện hành được nguồn dẫn rõ Nghị định 238/2025/NĐ-CP; không coi đây là mức thu cụ thể của NTU."
    if policy_type == "insurance" and any(term in lowered for term in ("vssid", "bhyt số", "tra cứu hạn sử dụng")):
        return "current", "Trang hướng dẫn thường trực của NTU nêu các thao tác tra cứu và sử dụng BHYT số; nguồn không công bố mức thu hiện hành."
    if dates and max(dates) < date.today():
        return "historical", f"Thông báo có hạn cuối {max(dates).isoformat()} đã qua."
    if academic_year and re.sub(r"\s", "", academic_year) in {"2024-2025", "2025-2026"}:
        return "historical", f"Thông báo áp dụng cho năm học {re.sub(r'\s', '', academic_year)} đã kết thúc."
    has_action = any(term in lowered for term in ("nộp hồ sơ", "thời gian đóng", "thời gian gửi đơn"))
    if academic_year and has_action and dates:
        return "current", "Thông báo NTU nêu năm học, đối tượng/hành động và hạn thực hiện cụ thể."
    missing = []
    if not academic_year:
        missing.append("năm học")
    if not has_action:
        missing.append("hành động/điều kiện áp dụng")
    if not dates and not legal:
        missing.append("thời hạn hoặc căn cứ pháp lý")
    return "unresolved", "Chưa đủ bằng chứng cấu trúc: thiếu " + ", ".join(missing or ["phạm vi áp dụng cụ thể"] ) + "."


def is_index_only_policy(title: str, status: str) -> bool:
    """Keep generic landing pages as candidates instead of fake policies."""
    normalized = " ".join(title.casefold().split())
    return status == "unresolved" and (
        normalized in {"học phí", "học bổng"}
        or normalized.startswith("chi tiết chế độ và chính sách")
    )


class FinanceCrawler(KeywordCandidateCrawler):
    def crawl(self, limit: int | None = None) -> dict[str, int]:
        super().crawl(limit)
        count = 0
        candidates = list(
            self.store.connection.execute(
                """SELECT c.*, s.raw_path FROM source_candidates c
                   JOIN sources s ON s.url=c.source_url
                   WHERE c.category='finance' AND s.raw_path LIKE '%.html'"""
            )
        )
        for candidate in candidates:
            title = candidate["title"]
            html = open(candidate["raw_path"], encoding="utf-8-sig", errors="replace").read()
            blocks, _ = parse_html(html, candidate["source_url"])
            relevant = [
                block
                for block in blocks
                if len(block) > 30
                and any(
                    term in block.casefold()
                    for term in ("học phí", "trợ cấp", "hỗ trợ", "học bổng", "bảo hiểm", "bhyt", "bhtt")
                )
            ][:30]
            deadlines = [
                block
                for block in relevant
                if "hạn" in block.casefold() or re.search(r"\b\d{1,2}/\d{1,2}/20\d{2}\b", block)
            ]
            legal = [
                block
                for block in relevant
                if re.search(r"nghị định|quyết định|thông tư|qđ[- /]", block, re.I)
            ]
            policy_types = classify_policy_types(title)
            evidence_text = "\n".join(relevant).casefold()
            if "gia hạn đóng học phí" in evidence_text:
                policy_types.append("payment_extension")
            if "nợ học phí" in evidence_text:
                policy_types.append("debt_rule")
            policy_types = list(dict.fromkeys(policy_types))
            if not policy_types:
                continue
            academic_year = next(iter(re.findall(r"20\d{2}\s*[-–]\s*20\d{2}", title)), None)
            verification = [
                verify_policy(policy_type, title, relevant, legal, academic_year)
                for policy_type in policy_types
            ]
            if verification and all(
                is_index_only_policy(title, status) for status, _ in verification
            ):
                self.store.connection.execute(
                    "DELETE FROM financial_policies WHERE source_url=?",
                    (candidate["source_url"],),
                )
                self.store.connection.execute(
                    "UPDATE source_candidates SET status='index_only' WHERE candidate_id=?",
                    (candidate["candidate_id"],),
                )
                self.stats["finance_index_pages"] += 1
                continue
            for policy_type, (status, verification_note) in zip(policy_types, verification):
                is_national = "nghị định số 238/2025/nđ-cp" in evidence_text
                scope = "national_legal" if is_national else (
                    "semester_notice" if policy_type in {"tuition_notice", "payment_extension", "debt_rule"}
                    else "ntu_implementation"
                )
                beneficiary = None
                if "đối tượng đóng học phí:" in evidence_text:
                    beneficiary = next(
                        (block.split(":", 1)[1].strip() for block in relevant if block.casefold().startswith("đối tượng đóng học phí:")),
                        None,
                    )
                elif "nộp hồ sơ miễn giảm học phí" in title.casefold():
                    beneficiary = "Sinh viên thuộc diện miễn/giảm học phí, hỗ trợ chi phí học tập, trợ cấp xã hội hoặc học bổng cho sinh viên khuyết tật theo chính sách tương ứng"
                type_deadlines = deadlines
                if policy_type == "social_support":
                    type_deadlines = [block for block in deadlines if "trợ cấp xã hội" in block.casefold()]
                elif policy_type == "exemption":
                    type_deadlines = [block for block in deadlines if "miễn giảm học phí" in block.casefold()]
                elif policy_type == "tuition_notice":
                    type_deadlines = [block for block in relevant if "thời gian đóng học phí:" in block.casefold()]
                elif policy_type == "payment_extension":
                    type_deadlines = [block for block in relevant if "thời gian gửi đơn" in block.casefold() or "để được gia hạn" in block.casefold()]
                elif policy_type == "debt_rule":
                    type_deadlines = []
                policy_id = hashlib.sha256(
                    f"{policy_type}|{candidate['source_url']}".encode("utf-8")
                ).hexdigest()
                self.store.connection.execute(
                    """INSERT INTO financial_policies
                       (policy_id, title, policy_type, policy_scope, beneficiary, academic_year, conditions_json,
                        deadline, legal_basis, source_url, retrieved_at, status, verification_note)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(policy_id) DO UPDATE SET
                         title=excluded.title, policy_scope=excluded.policy_scope,
                         academic_year=excluded.academic_year,
                         conditions_json=excluded.conditions_json,
                         deadline=excluded.deadline, legal_basis=excluded.legal_basis,
                         beneficiary=excluded.beneficiary, status=excluded.status,
                         verification_note=excluded.verification_note,
                         retrieved_at=excluded.retrieved_at""",
                    (
                        policy_id,
                        title,
                        policy_type,
                        scope,
                        beneficiary,
                        academic_year,
                        json.dumps(relevant, ensure_ascii=False),
                        "\n".join(type_deadlines) or None,
                        "\n".join(legal) or None,
                        candidate["source_url"],
                        now_iso(),
                        status,
                        verification_note,
                    ),
                )
                count += 1
        self.store.connection.commit()
        self.stats["financial_policies_materialized"] = count
        return dict(self.stats)


def build_finance_crawler(store: KnowledgeStore, raw_directory: str = "data/raw") -> FinanceCrawler:
    return FinanceCrawler(
        store,
        category="finance",
        seeds=(
            "https://phongkhtc.ntu.edu.vn/",
            "https://phongctsv.ntu.edu.vn/",
        ),
        keywords=(
            "học phí",
            "miễn giảm",
            "miễn, giảm",
            "gia hạn",
            "học bổng",
            "bảo hiểm",
            "bhyt",
            "hỗ trợ",
        ),
        exclusions=(
            "qđ 729",
            "qđ 317",
            "quyết định 729",
            "quyết định 317",
            "nhiều trường đại học",
            "các trường đại học",
            "nhận học bổng",
        ),
        direct_sources=((
            "Những điều cần biết về việc sử dụng bảo hiểm y tế",
            "https://phongctsv.ntu.edu.vn/tin-tuc/nhung-dieu-can-biet-ve-viec-su-dung-bao-hiem-y-te",
            ("bảo hiểm y tế",),
        ),),
        raw_directory=raw_directory,
    )
