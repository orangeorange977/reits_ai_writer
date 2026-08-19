import unittest
from unittest.mock import call, patch

from backend.services import section_skill_service as service


class GenerateChapterSectionsTest(unittest.TestCase):
    def setUp(self):
        self.official = [
            {"id": "2.1", "title": "第一节", "chapter_n": 2,
             "chapter_title": "项目情况", "configured": True},
            {"id": "2.2", "title": "第二节", "chapter_n": 2,
             "chapter_title": "项目情况", "configured": False},
            {"id": "2.3", "title": "第三节", "chapter_n": 2,
             "chapter_title": "项目情况", "configured": True},
            {"id": "3.1", "title": "其他章", "chapter_n": 3,
             "chapter_title": "其他", "configured": True},
        ]

    @patch.object(service.data_foundation_service, "load_foundation",
                  return_value={"stale": False})
    @patch.object(service, "generate_section")
    @patch.object(service, "list_all_official_sections")
    def test_generates_configured_sections_and_reports_skips(
            self, list_sections, generate_section, _load_foundation):
        list_sections.return_value = self.official
        generate_section.return_value = {"config": {}, "section": {}, "chapter": {}}

        result = service.generate_chapter_sections("p1", 2, "pack")

        self.assertEqual(result["generated_total"], 2)
        self.assertEqual(result["skipped_total"], 1)
        self.assertEqual(result["failed_total"], 0)
        self.assertEqual([x["id"] for x in result["generated_sections"]], ["2.1", "2.3"])
        self.assertEqual([x["id"] for x in result["skipped_sections"]], ["2.2"])
        self.assertEqual(generate_section.call_args_list, [
            call("p1", "2.1", "pack"), call("p1", "2.3", "pack")])

    @patch.object(service.data_foundation_service, "load_foundation",
                  return_value={"stale": False})
    @patch.object(service, "generate_section")
    @patch.object(service, "list_all_official_sections")
    def test_one_section_failure_does_not_block_the_rest(
            self, list_sections, generate_section, _load_foundation):
        list_sections.return_value = self.official
        generate_section.side_effect = [RuntimeError("缺少草稿"), {"config": {}}]

        result = service.generate_chapter_sections("p1", 2, "pack")

        self.assertEqual(result["generated_total"], 1)
        self.assertEqual(result["failed_total"], 1)
        self.assertEqual(result["failed_sections"][0]["id"], "2.1")
        self.assertIn("缺少草稿", result["failed_sections"][0]["error"])

    @patch.object(service, "list_all_official_sections")
    def test_rejects_chapter_without_configured_knowhow(self, list_sections):
        list_sections.return_value = [
            {"id": "4.1", "title": "未配置", "chapter_n": 4,
             "chapter_title": "第四章", "configured": False},
        ]
        with self.assertRaisesRegex(RuntimeError, "尚未配置"):
            service.generate_chapter_sections("p1", 4, "pack")


if __name__ == "__main__":
    unittest.main()
