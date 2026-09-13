import tempfile
import unittest
from pathlib import Path

from database import KnowledgeStore
from pipeline.version import confidence_for_source, upsert_fact


def fact(content: str, source_url: str) -> dict[str, str]:
    return {
        "entity": "program-1",
        "type": "total_credits",
        "content": content,
        "relation": "hasCreditRequirement",
        "target": "credits",
        "source_url": source_url,
        "source_domain": "ctdt.ntu.edu.vn",
        "source_type": "official_api",
        "citation": "field=total_credits",
        "retrieved_at": "2026-09-12T23:00:00+07:00",
    }


class VersioningTests(unittest.TestCase):
    def test_citation_enrichment_keeps_one_current_logical_fact(self):
        with tempfile.TemporaryDirectory() as directory:
            with KnowledgeStore(Path(directory) / "test.db") as store:
                base = {
                    "entity": "Đơn vị A",
                    "type": "unit",
                    "content": "Đơn vị A",
                    "source_url": "https://ntu.edu.vn/a",
                    "source_domain": "ntu.edu.vn",
                    "source_type": "official_unit_page",
                    "retrieved_at": "2026-09-13T00:00:00+07:00",
                }
                upsert_fact(store, {**base, "citation": None})
                upsert_fact(store, {**base, "citation": "Trang chính thức của đơn vị"})
                rows = list(
                    store.connection.execute(
                        "SELECT status, citation FROM facts WHERE status='current'"
                    )
                )
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["citation"], "Trang chính thức của đơn vị")

    def test_same_source_change_preserves_history(self):
        with tempfile.TemporaryDirectory() as directory:
            with KnowledgeStore(Path(directory) / "test.db") as store:
                self.assertEqual(upsert_fact(store, fact("120", "https://ctdt.ntu.edu.vn/a")), "new")
                self.assertEqual(upsert_fact(store, fact("130", "https://ctdt.ntu.edu.vn/a")), "updated")
                statuses = list(store.connection.execute("SELECT status FROM facts ORDER BY content"))
                self.assertEqual([row[0] for row in statuses], ["historical", "current"])

    def test_different_sources_create_unresolved_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            with KnowledgeStore(Path(directory) / "test.db") as store:
                upsert_fact(store, fact("120", "https://ctdt.ntu.edu.vn/a"))
                upsert_fact(store, fact("130", "https://ctdt.ntu.edu.vn/b"))
                statuses = [row[0] for row in store.connection.execute("SELECT status FROM facts")]
                self.assertEqual(statuses, ["current", "current"])
                conflict = store.connection.execute("SELECT status FROM conflicts").fetchone()
                self.assertEqual(conflict[0], "unresolved")

    def test_confidence_describes_source_quality(self):
        self.assertEqual(confidence_for_source("official_document"), "Cao")
        self.assertEqual(confidence_for_source("official_news"), "Trung bình")
        self.assertEqual(confidence_for_source("unknown"), "Thấp")


if __name__ == "__main__":
    unittest.main()
