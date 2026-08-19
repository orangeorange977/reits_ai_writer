import unittest
from pathlib import Path

from backend.routers.projects import _resolve_preview_target


class MaterialPreviewNavigationTest(unittest.TestCase):
    def test_cited_page_wins_over_later_full_document_match(self):
        self.assertEqual(_resolve_preview_target(125, cited_page=8, detected_page=80), 8)

    def test_detected_page_is_used_only_without_cited_page(self):
        self.assertEqual(_resolve_preview_target(125, cited_page=0, detected_page=80), 80)

    def test_frontend_passes_and_prefers_the_cited_page(self):
        root = Path(__file__).resolve().parents[1]
        app = (root / "frontend/js/app.js").read_text(encoding="utf-8")
        api = (root / "frontend/js/api.js").read_text(encoding="utf-8")
        self.assertIn("quote, 0, '', citedPageHint", app)
        self.assertIn("const targetPage = citedPage || d.hit_page || 0", app)
        self.assertIn("params.page_hint = pageHint", api)

    def test_material_browser_files_open_the_original_preview(self):
        root = Path(__file__).resolve().parents[1]
        app = (root / "frontend/js/app.js").read_text(encoding="utf-8")
        self.assertIn('data-path="${_escHtmlAttr(f.path)}"', app)
        self.assertIn("openMaterialPreview(row.dataset.path, '', 0)", app)


if __name__ == "__main__":
    unittest.main()
