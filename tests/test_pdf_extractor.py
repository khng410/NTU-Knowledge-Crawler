import unittest
from unittest.mock import patch

from extractors.pdf_extractor import extract_explicit_total_credits


class PdfCreditExtractorTests(unittest.TestCase):
    @patch("extractors.pdf_extractor.extract_pdf_text")
    def test_explicit_total_keeps_page_citation(self, extract_text):
        extract_text.return_value = "Trang đầu\fTỔNG SỐ TÍN CHỈ: 130\n"
        self.assertEqual(
            extract_explicit_total_credits("ignored.pdf"),
            (130, "Tổng tín chỉ được nêu trực tiếp trong bảng, trang PDF 2"),
        )

    @patch("extractors.pdf_extractor.extract_pdf_text")
    def test_explicit_summary_row_handles_split_total_label(self, extract_text):
        extract_text.return_value = "TỔNG SỐ TÍN CHỈ\nTổng cộng 150 100,0 132 88,0 18 12"
        self.assertEqual(extract_explicit_total_credits("ignored.pdf")[0], 150)

    @patch("extractors.pdf_extractor.extract_pdf_text")
    def test_conflicting_explicit_totals_are_not_guessed(self, extract_text):
        extract_text.return_value = "TỔNG SỐ TÍN CHỈ 120\fTỔNG SỐ TÍN CHỈ 130"
        self.assertIsNone(extract_explicit_total_credits("ignored.pdf"))


if __name__ == "__main__":
    unittest.main()
