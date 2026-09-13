import unittest

from crawlers.procedures import ProcedureCrawler


class ProcedureFormTests(unittest.TestCase):
    def test_official_form_metadata_does_not_invent_deadlines(self):
        metadata = ProcedureCrawler._form_metadata(
            "Đơn xin chuyển Trường 2026",
            "Ý kiến của Hiệu trưởng. Kèm theo giấy chứng nhận kết quả học tập.",
        )
        self.assertEqual(len(metadata["documents"]), 2)
        self.assertNotIn("deadline", metadata)

    def test_part_time_form_has_specific_audience(self):
        metadata = ProcedureCrawler._form_metadata(
            "Đơn xin tiếp tục học (hệ vừa làm vừa học)", "Kính gửi Hiệu trưởng"
        )
        self.assertIn("vừa làm vừa học", metadata["applicable_to"])


if __name__ == "__main__":
    unittest.main()
