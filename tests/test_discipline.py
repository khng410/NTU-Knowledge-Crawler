import unittest

from crawlers.discipline import extract_discipline_rows


def observation(page, text, x, y):
    return {"page": page, "text": text, "x": x, "y": y, "confidence": 1.0}


class DisciplineExtractorTests(unittest.TestCase):
    def test_sanction_columns_include_fourth_and_fifth_occurrences(self):
        items = [
            observation(10, "3", 0.09, 0.74),
            observation(10, "3.2", 0.08, 0.55),
            observation(10, "Hút thuốc tại nơi cấm", 0.12, 0.57),
            observation(10, "Lần 1", 0.39, 0.55),
            observation(10, "Lần 2", 0.45, 0.55),
            observation(10, "Lân 3", 0.52, 0.55),
            observation(10, "Lần 4", 0.60, 0.55),
            observation(10, "Lần 5", 0.68, 0.55),
            observation(10, "3.3", 0.08, 0.46),
            observation(10, "Tổ chức uống rượu trong trường", 0.12, 0.47),
        ]
        rows = {row["code"]: row for row in extract_discipline_rows(items)}
        self.assertEqual(rows["3.2"]["third_violation"], "cảnh cáo")
        self.assertEqual(rows["3.2"]["fourth_violation"], "đình chỉ học tập có thời hạn")
        self.assertEqual(rows["3.2"]["fifth_violation"], "buộc thôi học")

    def test_merged_sanction_cell_is_attached_to_both_rows(self):
        items = [
            observation(10, "5", 0.09, 0.31),
            observation(10, "5.2", 0.08, 0.20),
            observation(10, "Đánh bạc", 0.12, 0.19),
            observation(10, "Tùy theo mức độ, xử lý kỷ luật", 0.39, 0.18),
            observation(10, "5.3", 0.08, 0.14),
            observation(10, "Lừa đảo, trộm cắp", 0.12, 0.13),
            observation(10, "theo quy định pháp luật.", 0.39, 0.11),
        ]
        rows = {row["code"]: row for row in extract_discipline_rows(items)}
        self.assertEqual(rows["5.2"]["sanction"], rows["5.3"]["sanction"])
        self.assertIn("quy định pháp luật", rows["5.2"]["sanction"])

    def test_first_row_expulsion_and_note_are_kept(self):
        items = [
            observation(11, "5.4", 0.086, 0.695),
            observation(11, "Tàng trữ chất ma túy", 0.124, 0.687),
            observation(11, "Lần 1", 0.682, 0.685),
            observation(11, "Chuyển cho cơ quan chức năng", 0.743, 0.691),
            observation(11, "5.5", 0.087, 0.632),
            observation(11, "Môi giới mại dâm", 0.124, 0.623),
        ]
        rows = {row["code"]: row for row in extract_discipline_rows(items)}
        self.assertEqual(rows["5.4"]["first_violation"], "buộc thôi học")
        self.assertIn("cơ quan chức năng", rows["5.4"]["sanction"])
        self.assertEqual(rows["5.4"]["status"], "verified_manual")
        self.assertIn("tổ chức sử dụng", rows["5.4"]["behavior"])


if __name__ == "__main__":
    unittest.main()
