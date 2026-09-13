import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from database import KnowledgeStore
from pipeline.export import export_academic_master_markdown, export_json, export_ontology
from pipeline.validate import quality_checks
from pipeline.version import upsert_fact
from crawlers.finance import classify_policy_types, is_index_only_policy, verify_policy


class OutputAndQualityTests(unittest.TestCase):
    def test_finance_classifier_keeps_policy_kinds_distinct(self):
        self.assertEqual(classify_policy_types("Thông báo đóng học phí"), ["tuition_notice"])
        self.assertEqual(
            classify_policy_types("Gia hạn học phí và BHYT"),
            ["payment_extension", "insurance"],
        )
        self.assertIn("scholarship", classify_policy_types("Thông tin học bổng"))

    def test_finance_verification_preserves_unresolved_and_historical(self):
        status, note = verify_policy("scholarship", "Học bổng", [], [], None)
        self.assertEqual(status, "unresolved")
        self.assertIn("thiếu", note)
        status, _ = verify_policy(
            "tuition_notice", "Học phí 2024-2025",
            ["Thời gian đóng học phí: đến hết ngày 02/03/2025"], [], "2024-2025",
        )
        self.assertEqual(status, "historical")

    def test_generic_finance_landing_pages_are_not_policies(self):
        self.assertTrue(is_index_only_policy("Học bổng", "unresolved"))
        self.assertTrue(
            is_index_only_policy("Chi tiết Chế độ và Chính sách", "unresolved")
        )
        self.assertFalse(is_index_only_policy("Học bổng 2026", "current"))
    def test_exports_are_parseable_and_preserve_discipline_occurrences(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with KnowledgeStore(root / "test.db") as store:
                store.connection.execute(
                    """INSERT INTO discipline_rules
                       (rule_id, behavior, first_violation, fourth_violation,
                        fifth_violation, citation, source_url, retrieved_at, status)
                       VALUES ('r1', 'Hành vi A', 'nhắc nhở',
                               'đình chỉ học tập có thời hạn', 'buộc thôi học',
                               'Phụ lục, dòng 1, trang PDF 9',
                               'https://phongctsv.ntu.edu.vn/a.pdf',
                               '2026-09-13T00:00:00+07:00', 'verified_ocr')"""
                )
                store.connection.commit()
                counts = export_json(store, root / "output")
                self.assertEqual(counts["discipline_rules"], 1)
                records = json.loads((root / "output" / "discipline_rules.json").read_text())
                self.assertEqual(records[0]["fifth_violation"], "buộc thôi học")

                upsert_fact(store, {
                    "entity": "A & B",
                    "type": "test",
                    "content": "x < y",
                    "source_url": "https://ctdt.ntu.edu.vn/a",
                    "source_domain": "ctdt.ntu.edu.vn",
                    "source_type": "official_api",
                    "citation": "field=x",
                    "retrieved_at": "2026-09-13T00:00:00+07:00",
                })
                export_ontology(store, root / "output" / "ontology.owl")
                tree = ET.parse(root / "output" / "ontology.owl")
                self.assertTrue(tree.findall(".//{http://www.w3.org/1999/02/22-rdf-syntax-ns#}type"))

                export_academic_master_markdown(store, root / "output" / "academic.md")
                report = (root / "output" / "academic.md").read_text()
                self.assertEqual(
                    [line for line in report.splitlines() if line.startswith("## ") and line[3:4].isdigit()],
                    [f"## {number}. {title}" for number, title in (
                        (1, "Đơn vị và đầu mối dịch vụ"),
                        (2, "Cổng thông tin, thủ tục và biểu mẫu"),
                        (3, "Học phí, học bổng và chính sách tài chính"),
                        (4, "Rèn luyện, kỷ luật và xử lý vi phạm"),
                        (5, "Dịch vụ sinh viên: ký túc xá, thư viện, việc làm và hỗ trợ"),
                        (6, "Chương trình đào tạo đại học"),
                        (7, "Tuyển sinh đại học theo năm"),
                        (8, "Sau đại học và nguồn đào tạo mở rộng"),
                        (9, "Nguồn pháp lý và provenance"),
                        (10, "Danh mục định danh và miền chính thức"),
                        (11, "Chưa tìm thấy, chưa đủ dữ kiện và lỗi nguồn"),
                        (12, "Chất lượng dữ liệu"),
                        (13, "Quy tắc metadata, hiệu lực và độ tin cậy"),
                        (14, "Loại trừ và audit phạm vi"),
                        (15, "Nhật ký lần chạy hoàn tất gần nhất"),
                        (16, "Thống kê và artifact chi tiết"),
                    )],
                )

    def test_quality_detects_missing_discipline_evidence_and_multiple_current_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            with KnowledgeStore(Path(directory) / "test.db") as store:
                store.connection.execute(
                    """INSERT INTO discipline_rules
                       (rule_id, behavior, source_url, retrieved_at, status)
                       VALUES ('r1', 'Hành vi A', 'https://phongctsv.ntu.edu.vn/a.pdf',
                               '2026-09-13T00:00:00+07:00', 'partial_ocr')"""
                )
                for digest in ("a", "b"):
                    store.connection.execute(
                        """INSERT INTO source_versions
                           (url, content_hash, raw_path, first_retrieved_at,
                            last_retrieved_at, status)
                           VALUES ('https://ctdt.ntu.edu.vn/a', ?, 'a.html',
                                   '2026-09-13', '2026-09-13', 'current')""",
                        (digest,),
                    )
                store.connection.commit()
                checks = quality_checks(store)
                self.assertEqual(checks["discipline_rules_missing_evidence"], 1)
                self.assertEqual(checks["partial_discipline_rules"], 1)
                self.assertEqual(checks["multiple_current_source_versions"], 1)


if __name__ == "__main__":
    unittest.main()
