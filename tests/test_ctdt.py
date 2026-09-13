import json
import tempfile
import unittest
from pathlib import Path

from database import KnowledgeStore
from crawlers.ctdt import CTDTCrawler
from extractors.table_extractor import extract_courses, extract_program, program_identifier


SAMPLE = {
    "chuongTrinhDTID": 210,
    "maNganh": "7480201",
    "tenNganh": "Công nghệ thông tin",
    "tenChuongTrinh": "Công nghệ thông tin",
    "apDungTuKhoa": 68,
    "tenDonVi": "Khoa Công nghệ thông tin",
    "tenTrinhDo": "Đại học",
    "tenHinhThucDT": "Chính quy",
    "thoiGianDaoTao": 4,
    "tenNgonNgu": "Tiếng Việt",
    "tenVanBang": "Cử nhân",
    "nhomKhungCTs": [{"soTinChiBB": 120, "soTinChiTC": 10}],
    "chuanDauRas": [{"maChuanDauRa": "PLO1", "noiDungCDR": "Nội dung"}],
    "hocPhanKhungs": [
        {
            "hocPhanKhungID": 1,
            "maHocPhan": "NEC334",
            "tenHocPhan": "Kiến trúc máy tính",
            "soTC": 3,
            "tietLyThuyet": 30,
            "tietThucHanh": 15,
            "phanBoHocKy": 3,
            "tuChon": False,
            "dieuKienTQs": [],
        }
    ],
}


class ExtractorTests(unittest.TestCase):
    def test_program_and_course_are_explicit(self):
        program_id = program_identifier("7480201", "K68")
        self.assertEqual(program_id, "NTU-PROGRAM-7480201-K68-STANDARD")
        program = extract_program(
            SAMPLE,
            program_id=program_id,
            source_url="https://ctdt.ntu.edu.vn/program/210",
            pdf_url=None,
            retrieved_at="2026-09-12T23:00:00+07:00",
        )
        self.assertEqual(program["total_credits"], 130)
        self.assertEqual(program["content_status"], "complete")
        self.assertEqual(program["credit_method"], "api_group_totals")
        self.assertEqual(json.loads(program["plos_json"])[0]["code"], "PLO1")
        courses = extract_courses(
            SAMPLE,
            program_id=program_id,
            source_url=program["source_url"],
            retrieved_at=program["retrieved_at"],
        )
        self.assertEqual(courses[0]["semester"], 3)
        self.assertIsNone(courses[0]["prerequisite_json"])

    def test_missing_optional_tables_do_not_crash(self):
        payload = dict(SAMPLE, hocPhanKhungs=None, hocPhanKhungTTCLCs=None)
        self.assertEqual(
            extract_courses(payload, program_id="p", source_url="https://ctdt.ntu.edu.vn", retrieved_at="now"),
            [],
        )

    def test_duplicate_course_relationship_is_collapsed_without_guessing(self):
        first, second = SAMPLE["hocPhanKhungs"][0], dict(SAMPLE["hocPhanKhungs"][0])
        second.update(hocPhanKhungID=2, hocPhanID=999, phanBoHocKy=4, tuChon=True)
        payload = dict(SAMPLE, hocPhanKhungs=[first, second])
        courses = extract_courses(
            payload,
            program_id="program",
            source_url="https://ctdt.ntu.edu.vn/program/210",
            retrieved_at="2026-09-12T23:00:00+07:00",
        )
        self.assertEqual(len(courses), 1)
        self.assertIsNone(courses[0]["semester"])
        self.assertEqual(courses[0]["course_type"], "bắt buộc hoặc tự chọn tùy nhóm")
        self.assertEqual(courses[0]["citation"], "hocPhanKhungID=1, hocPhanKhungID=2")

    def test_sqlite_replaces_program_courses_without_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            with KnowledgeStore(Path(directory) / "test.db") as store:
                program_id = program_identifier("7480201", "K68")
                program = extract_program(
                    SAMPLE,
                    program_id=program_id,
                    source_url="https://ctdt.ntu.edu.vn/program/210",
                    pdf_url=None,
                    retrieved_at="2026-09-12T23:00:00+07:00",
                )
                store.save_program(program)
                courses = extract_courses(
                    SAMPLE,
                    program_id=program_id,
                    source_url=program["source_url"],
                    retrieved_at=program["retrieved_at"],
                )
                store.replace_courses(program_id, courses)
                store.replace_courses(program_id, courses)
                count = store.connection.execute("SELECT COUNT(*) FROM courses").fetchone()[0]
                self.assertEqual(count, 1)

    def test_failed_queue_item_stops_after_three_attempts(self):
        with tempfile.TemporaryDirectory() as directory:
            with KnowledgeStore(Path(directory) / "test.db") as store:
                url = "https://ctdt.ntu.edu.vn/missing.pdf"
                store.enqueue(url, "ctdt_pdf")
                for attempt in range(3):
                    self.assertEqual(len(store.queued(["ctdt_pdf"])), 1)
                    store.mark_queue(url, "failed", f"attempt-{attempt}", "404")
                self.assertEqual(store.queued(["ctdt_pdf"]), [])

    def test_refresh_requeues_a_completed_mutable_source(self):
        with tempfile.TemporaryDirectory() as directory:
            with KnowledgeStore(Path(directory) / "test.db") as store:
                url = "https://ctdt.ntu.edu.vn/api/program/1"
                store.enqueue(url, "ctdt_program")
                store.mark_queue(url, "done", "2026-09-13T00:00:00+07:00")
                self.assertEqual(store.queued(["ctdt_program"]), [])
                store.enqueue(url, "ctdt_program", refresh=True)
                self.assertEqual(len(store.queued(["ctdt_program"])), 1)

    def test_missing_pdf_uses_official_html_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            with KnowledgeStore(Path(directory) / "test.db") as store:
                program = extract_program(
                    SAMPLE,
                    program_id="program-210",
                    source_url="https://ctdt.ntu.edu.vn/api/program/210",
                    pdf_url="https://ctdt.ntu.edu.vn/ctdt/missing.pdf",
                    retrieved_at="2026-09-13T00:00:00+07:00",
                )
                program.update(total_credits=None, credit_status="not_stated", pdf_status="missing_asset")
                store.save_program(program)
                crawler = CTDTCrawler(store, Path(directory) / "raw")
                crawler._fetch_and_store = lambda *_args: (
                    b"<table><tr><td>Tong so tin chi</td><td>130</td></tr>"
                    b"<tr><td>T\xe1\xbb\x95NG S\xe1\xbb\x90 T\xc3\x8dN CH\xe1\xbb\x88</td><td>130</td></tr></table>",
                    True,
                )
                self.assertEqual(crawler.backfill_missing_pdf_fallbacks(), 1)
                row = store.connection.execute(
                    "SELECT * FROM programs WHERE program_id='program-210'"
                ).fetchone()
                self.assertEqual(row["total_credits"], 130)
                self.assertEqual(row["credit_method"], "official_html_explicit")

    def test_task_status_is_persisted_per_run(self):
        with tempfile.TemporaryDirectory() as directory:
            with KnowledgeStore(Path(directory) / "test.db") as store:
                run_id = store.start_run("2026-09-13T00:00:00+07:00")
                task_id = store.start_task(run_id, "discover", "2026-09-13T00:00:00+07:00")
                store.finish_task(task_id, "success", "2026-09-13T00:01:00+07:00", {"records": 3})
                row = store.connection.execute("SELECT * FROM crawl_tasks").fetchone()
                self.assertEqual(row["status"], "success")
                self.assertEqual(json.loads(row["stats_json"])["records"], 3)


if __name__ == "__main__":
    unittest.main()
