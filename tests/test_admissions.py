import tempfile
import unittest
from pathlib import Path

from crawlers.admissions import CONTINUING_URL, AdmissionsCrawler, admission_code_from_label
from database import KnowledgeStore


HTML = """
<html><body>
<h1>Thông báo tuyển sinh hệ Đại học tại trường Đại học Nha Trang năm 2026</h1>
<p>Người đã có bằng tốt nghiệp đại học có thể tham gia dự tuyển.</p>
<p>Xét tuyển vào các tháng 02, 5, 8 và tháng 11 năm 2026.</p>
<table>
<tr><th>TT</th><th>Ngành đào tạo</th><th>Mã ngành</th><th>Loại hình đào tạo, thời gian</th></tr>
<tr><td>1</td><td>Công nghệ thông tin</td><td>7480201</td><td>
Đại học liên thông từ Đại học (VB2) (1,5 năm) – số 1
Đại học liên thông từ Cao đẳng (1,5 năm) – số 2
Đại học liên thông từ Trung cấp (2,5 năm) – số 3
Đại học vừa làm vừa học (4 năm) – số 4</td></tr>
<tr><td>2</td><td>Kế toán</td><td>7340301</td></tr>
</table></body></html>
"""


class FakeFetcher:
    def text(self, *_args, **_kwargs):
        return HTML


class AdmissionsExtractorTests(unittest.TestCase):
    def test_code_parser_does_not_absorb_xem_label(self):
        self.assertEqual(admission_code_from_label("7480201A Xem"), "7480201A")
        self.assertEqual(admission_code_from_label("7480201 Xem"), "7480201")
        self.assertIsNone(admission_code_from_label("Xem"))

    def test_continuing_tracks_are_distinct_and_year_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            with KnowledgeStore(Path(directory) / "test.db") as store:
                crawler = AdmissionsCrawler(store)
                crawler.fetcher = FakeFetcher()
                crawler._crawl_continuing_admissions()
                rows = list(store.connection.execute("SELECT * FROM admissions"))
                self.assertEqual(len(rows), 8)
                self.assertEqual({row["year"] for row in rows}, {2026})
                self.assertEqual(
                    {row["admission_category"] for row in rows},
                    {
                        "second_degree",
                        "articulation_college",
                        "articulation_intermediate",
                        "part_time_undergraduate",
                    },
                )
                second_degree = next(
                    row for row in rows if row["admission_category"] == "second_degree"
                )
                self.assertIn("bằng tốt nghiệp đại học", second_degree["admission_conditions"])
                self.assertEqual(second_degree["source_url"], CONTINUING_URL)
                self.assertNotEqual(CONTINUING_URL.rsplit("/", 1)[-1], "n")


if __name__ == "__main__":
    unittest.main()
