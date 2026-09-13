import unittest

from extractors.html_extractor import parse_html, section, year_from_text
from pipeline.crawl import content_hash, load_approved_domains
from crawlers.procedures import ProcedureCrawler


class PostgraduateExtractorTests(unittest.TestCase):
    def test_admission_sections_keep_language_requirements_separate(self):
        html = """
        <h1>Tuyển sinh trình độ thạc sĩ năm 2026</h1>
        <p><strong>2. Điều kiện dự tuyển</strong></p>
        <p><strong>2.1. Điều kiện văn bằng</strong></p>
        <p>Có bằng tốt nghiệp đại học ngành phù hợp.</p>
        <p><strong>2.2. Điều kiện ngoại ngữ</strong></p>
        <p>Năng lực ngoại ngữ từ Bậc 3 (B1) trở lên.</p>
        <p><strong>2.3. Bổ sung kiến thức</strong></p>
        <p>Học bổ sung nếu nguồn yêu cầu.</p>
        <p><strong>3. Thời gian tuyển sinh</strong></p>
        """
        blocks, _ = parse_html(html, "https://pdtsaudaihoc.ntu.edu.vn/example")
        language_entry = section(
            blocks, r"điều\s*kiện ngoại ngữ", r"^\s*\d+(?:\.\d+)+\."
        )
        language_exit = section(
            blocks, r"ngoại ngữ đầu ra", r"^\s*\d+(?:\.\d+)*\."
        )
        self.assertEqual(language_entry, "Năng lực ngoại ngữ từ Bậc 3 (B1) trở lên.")
        self.assertIsNone(language_exit)

    def test_links_are_resolved_and_year_is_explicit(self):
        _, links = parse_html(
            '<a href="/tin-tuc/tuyen-sinh-2026">Tuyển sinh thạc sĩ 2026</a>',
            "https://pdtsaudaihoc.ntu.edu.vn/danh-muc",
        )
        self.assertEqual(
            links[0].url,
            "https://pdtsaudaihoc.ntu.edu.vn/tin-tuc/tuyen-sinh-2026",
        )
        self.assertEqual(year_from_text(links[0].text), 2026)
        self.assertIsNone(year_from_text("Thông báo không ghi năm"))

    def test_source_allowlist_contains_graduate_domain(self):
        domains = load_approved_domains()
        self.assertIn("pdtsaudaihoc.ntu.edu.vn", domains)
        self.assertNotIn("example.com", domains)

    def test_graduate_plan_is_not_a_student_procedure_candidate(self):
        self.assertEqual(
            ProcedureCrawler.matched_keywords(
                "Kế hoạch luận văn và xét tốt nghiệp thạc sĩ năm 2026"
            ),
            [],
        )
        self.assertEqual(
            ProcedureCrawler.matched_keywords("Đơn xin nghỉ học tạm thời 2026"),
            ["nghỉ học"],
        )

    def test_dynamic_viewstate_does_not_change_semantic_html_hash(self):
        first = b'<html><input type="hidden" name="__VIEWSTATE" value="one"><input name="__RequestVerificationToken" value="alpha"><p>Fact</p></html>'
        second = b'<html><input type="hidden" name="__VIEWSTATE" value="two"><input name="__RequestVerificationToken" value="beta"><p>Fact</p></html>'
        self.assertEqual(content_hash(first, "text/html"), content_hash(second, "text/html"))

    def test_dynamic_visitor_counters_do_not_create_source_versions(self):
        first = '<li class="online-day">Lượt truy cập hôm nay: 100</li><span>Hôm nay:</span><span>8</span>'
        second = '<li class="online-day">Lượt truy cập hôm nay: 101</li><span>Hôm nay:</span><span>9</span>'
        self.assertEqual(
            content_hash(first.encode(), "text/html"),
            content_hash(second.encode(), "text/html"),
        )

    def test_unrelated_dynamic_pagination_does_not_create_source_versions(self):
        first = '<p>Fact</p><div class="mbp_pagination"><a href="/tin-tuc?page=2">2</a></div>'
        second = '<p>Fact</p><div class="mbp_pagination"><a href="/hoc-bong?page=2">2</a></div>'
        self.assertEqual(
            content_hash(first.encode(), "text/html"),
            content_hash(second.encode(), "text/html"),
        )


if __name__ == "__main__":
    unittest.main()
