import tempfile
import unittest
import re
from pathlib import Path

from database import KnowledgeStore
from pipeline.student_fact_export import (
    ALLOWED_TYPES,
    _admission_condition_clauses,
    _atomic_clauses,
    collect_student_facts,
    export_student_fact_markdown,
    validate_student_facts,
)


class StudentFactExportTests(unittest.TestCase):
    def test_admission_conditions_are_split_into_atomic_facts(self):
        clauses = _admission_condition_clauses(
            "Tổ hợp xét tuyển: Tổ hợp 1: Toán, Lý. Có điều kiện Tiếng Anh theo bảng nguồn."
        )
        self.assertEqual(
            clauses,
            ["Tổ hợp 1: Toán, Lý.", "Có điều kiện Tiếng Anh theo bảng nguồn."],
        )

    def test_atomic_split_preserves_semicolons_inside_parentheses(self):
        clauses = _atomic_clauses(
            "Chuẩn tương đương (DELF B1; TCF 350–400); ứng dụng CNTT cơ bản."
        )
        self.assertEqual(
            clauses,
            ["Chuẩn tương đương (DELF B1; TCF 350–400);", "ứng dụng CNTT cơ bản."],
        )

    def test_export_has_strict_schema_and_rejects_excluded_decisions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with KnowledgeStore(root / "test.db") as store:
                store.connection.execute(
                    """INSERT INTO units
                       (unit_id, name, email, source_url, retrieved_at)
                       VALUES ('u1', 'Thư viện', 'tttl@ntu.edu.vn',
                               'https://thuvien.ntu.edu.vn/', '2026-09-13')"""
                )
                store.connection.execute(
                    """INSERT INTO financial_policies
                       (policy_id, policy_type, conditions_json, source_url,
                        retrieved_at, status, title)
                       VALUES ('p1', 'tuition', '[\"Nội dung QĐ 729 học phí\"]',
                               'https://phongkhtc.ntu.edu.vn/a', '2026-09-13',
                               'current', 'QĐ 729 học phí')"""
                )
                store.connection.commit()
                facts = collect_student_facts(store)
                self.assertFalse(validate_student_facts(facts))
                self.assertEqual({fact.fact_type for fact in facts}, {"đơn vị"})
                self.assertTrue(all(f.fact_type in ALLOWED_TYPES for f in facts))
                count = export_student_fact_markdown(store, root / "facts.md")
                self.assertEqual(count, 1)
                report = (root / "facts.md").read_text(encoding="utf-8")
                self.assertIn("| Thực thể | Loại | Nội dung | Quan hệ | Trích dẫn | Văn bản | Link |", report)
                self.assertIn("## 10. Tên gọi khác", report)
                self.assertIn("## 11. Chưa tìm được", report)
                self.assertIn("## 16. Thống kê và artifact", report)
                self.assertEqual(
                    len([line for line in report.splitlines() if re.match(r"## \d+\.", line)]),
                    16,
                )
                self.assertTrue((root / "facts_AUDIT.jsonl").exists())
                self.assertNotIn("QĐ 729 học phí |", report)


if __name__ == "__main__":
    unittest.main()
