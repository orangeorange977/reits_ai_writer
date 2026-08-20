import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.services import data_foundation_service, pack_service, section_recompile_service as rc


class ValidateCompiledTest(unittest.TestCase):
    """_validate_compiled has to reject a malformed AI payload before it ever reaches disk —
    these are pure-function checks, no I/O needed."""

    def _valid_payload(self):
        return {
            "fields": [{"id": "originator.company_name", "label": "公司名称", "section_id": "2.3",
                        "source_role": "user_summary"}],
            "generation_templates": {"2.3": {"id": "3", "title": "（三）发起人（原始权益人）情况",
                "blocks": [{"type": "p", "template": "{{originator.company_name}}"}]}},
            "audit_checks": {"2.3": {"checklist": ["公司名称须与营业执照一致"]}},
        }

    def test_accepts_well_formed_payload(self):
        self.assertEqual(rc._validate_compiled(self._valid_payload(), "2.3"), [])

    def test_rejects_missing_fields_array(self):
        payload = self._valid_payload()
        payload["fields"] = []
        errors = rc._validate_compiled(payload, "2.3")
        self.assertTrue(any("fields" in e for e in errors))

    def test_rejects_field_missing_required_keys(self):
        payload = self._valid_payload()
        payload["fields"] = [{"id": "x"}]  # 缺 label/source_role
        errors = rc._validate_compiled(payload, "2.3")
        self.assertTrue(any("source_role" in e for e in errors))

    def test_rejects_field_with_wrong_section_id(self):
        payload = self._valid_payload()
        payload["fields"][0]["section_id"] = "1.1"
        errors = rc._validate_compiled(payload, "2.3")
        self.assertTrue(any("section_id" in e for e in errors))

    def test_rejects_bad_block_shape(self):
        payload = self._valid_payload()
        payload["generation_templates"]["2.3"]["blocks"] = [{"type": "not_a_real_block_type"}]
        errors = rc._validate_compiled(payload, "2.3")
        self.assertTrue(any("blocks[0]" in e for e in errors))

    def test_rejects_missing_audit_checklist(self):
        payload = self._valid_payload()
        payload["audit_checks"] = {}
        errors = rc._validate_compiled(payload, "2.3")
        self.assertTrue(any("audit_checks" in e for e in errors))

    def test_rejects_project_specific_path_and_year(self):
        payload = self._valid_payload()
        payload["fields"][0].update({
            "id": "finance.revenue.2024",
            "source_role": "audit_report_2024",
            "source_path": "2-2-4 某公司2024年财务报表.pdf",
        })
        errors = rc._validate_compiled(payload, "2.3")
        self.assertTrue(any("source_path" in e for e in errors))
        self.assertTrue(any("具体年份" in e or "audit_reports" in e for e in errors))

    def test_rejects_filename_bound_source_role(self):
        payload = self._valid_payload()
        payload["source_roles"] = [{
            "id": "license", "label": "营业执照", "filename_contains": ["1-1", "某公司"],
        }]
        errors = rc._validate_compiled(payload, "2.3")
        self.assertTrue(any("请使用 selector" in e for e in errors))


class ApplyCompiledTest(unittest.TestCase):
    """apply_compiled writes to the pack-level override layer, never the code-default
    rules.json, and must only ever touch the target section's own entries."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.overrides_dir = Path(self.tmp.name) / "skill_overrides"

    def tearDown(self):
        self.tmp.cleanup()

    def _payload_for(self, section_id: str, field_id: str, label: str):
        return {
            "fields": [{"id": field_id, "label": label, "section_id": section_id,
                        "source_role": "user_summary"}],
            "generation_templates": {section_id: {"id": "x", "title": "测试标题",
                "blocks": [{"type": "p", "template": f"{{{{{field_id}}}}}"}]}},
            "audit_checks": {section_id: {"checklist": ["测试检查项"]}},
        }

    def test_apply_replaces_only_target_section_and_bumps_version(self):
        with patch.object(pack_service, "OVERRIDES_DIR", self.overrides_dir):
            before = data_foundation_service.load_rules(None)
            before_23_ids = sorted(f["id"] for f in before["fields"] if f["section_id"] == "2.3")
            before_version = before["rule_version"]

            payload = self._payload_for("1.1", "project.name", "项目名称(重新编译)")
            rules = rc.apply_compiled("1.1", payload, None)

            self.assertNotEqual(rules["rule_version"], before_version)
            self.assertTrue(rules["rule_version"].startswith(before_version))
            new_11_ids = sorted(f["id"] for f in rules["fields"] if f["section_id"] == "1.1")
            self.assertEqual(new_11_ids, ["project.name"])
            new_23_ids = sorted(f["id"] for f in rules["fields"] if f["section_id"] == "2.3")
            self.assertEqual(new_23_ids, before_23_ids)  # 2.3 完全不受影响
            self.assertEqual(rules["generation_templates"]["2.3"], before["generation_templates"]["2.3"])

            # 写的是覆盖层，不是仓库里的默认文件。
            override_file = self.overrides_dir / (pack_service.default_pack_id() or "default") / "data-foundation" / "rules.json"
            self.assertTrue(override_file.exists())
            repo_default = json.loads(pack_service.pack_path("data-foundation/rules.json", None).read_text(encoding="utf-8"))
            self.assertNotEqual(
                sorted(f["id"] for f in repo_default["fields"] if f["section_id"] == "1.1"),
                new_11_ids,
            )

    def test_apply_is_rejected_for_invalid_payload(self):
        with patch.object(pack_service, "OVERRIDES_DIR", self.overrides_dir):
            with self.assertRaises(ValueError):
                rc.apply_compiled("1.1", {"fields": []}, None)

    def test_reset_reverts_to_repo_default(self):
        with patch.object(pack_service, "OVERRIDES_DIR", self.overrides_dir):
            rc.apply_compiled("1.1", self._payload_for("1.1", "project.name", "改过的标签"), None)
            rc.reset_pack_rules_override(None)
            rules = data_foundation_service.load_rules(None)
            repo_default = json.loads(pack_service.pack_path("data-foundation/rules.json", None).read_text(encoding="utf-8"))
            self.assertEqual(
                [f["id"] for f in rules["fields"] if f["section_id"] == "1.1"],
                [f["id"] for f in repo_default["fields"] if f["section_id"] == "1.1"],
            )

    def test_same_extraction_rule_is_merged_and_template_reference_rewritten(self):
        existing = [{"id": "company.name", "label": "公司名称", "section_id": "1.1",
                     "source_role": "license", "source_label": "名称", "strategy": "document_label"}]
        incoming = [{"id": "originator.name.copy", "label": "公司名称", "section_id": "2.3",
                     "source_role": "license", "source_label": "名称", "strategy": "document_label"}]
        fields, aliases, report = rc._merge_fields(existing, incoming, "2.3")
        self.assertEqual(len(fields), 1)
        self.assertEqual(aliases["originator.name.copy"], "company.name")
        self.assertEqual(fields[0]["used_by_sections"], ["1.1", "2.3"])
        rewritten = rc._replace_field_refs(
            {"template": "{{originator.name.copy}}", "src_fields": ["originator.name.copy"]}, aliases)
        self.assertEqual(rewritten["template"], "{{company.name}}")
        self.assertEqual(rewritten["src_fields"], ["company.name"])
        self.assertEqual(report["reused"], 1)

    def test_same_source_fact_is_merged_even_when_authors_use_different_labels(self):
        """字段复用应以 source_role+source_label（从哪里取）为准，不是 label 中文措辞。"""
        existing = [{"id": "company.name", "label": "公司名称", "section_id": "1.1",
                     "source_role": "license", "source_label": "名称", "strategy": "document_label"}]
        incoming = [{"id": "originator.name", "label": "原始权益人名称", "section_id": "2.3",
                     "source_role": "license", "source_label": "名称", "strategy": "document_label"}]
        fields, aliases, report = rc._merge_fields(existing, incoming, "2.3")
        self.assertEqual(len(fields), 1)
        self.assertEqual(aliases["originator.name"], "company.name")
        self.assertEqual(report["reused"], 1)

    def test_different_source_fact_is_not_merged_despite_identical_label(self):
        existing = [{"id": "company.name", "label": "名称", "section_id": "1.1",
                     "source_role": "license", "source_label": "公司名称", "strategy": "document_label"}]
        incoming = [{"id": "custodian.name", "label": "名称", "section_id": "2.3",
                     "source_role": "custody_agreement", "source_label": "受托人名称",
                     "strategy": "document_label"}]
        fields, aliases, report = rc._merge_fields(existing, incoming, "2.3")
        self.assertEqual(len(fields), 2)
        self.assertEqual(aliases, {})
        self.assertEqual(report["reused"], 0)

    def test_field_formats_keys_are_rewritten_when_field_is_aliased(self):
        """field_formats 是以字段 id 为键的字典；字段被合并改 id 后，键必须跟着改，
        否则 divide/decimals 这类格式化规则会静默失效。"""
        existing = {
            "fields": [{"id": "company.name", "label": "公司名称", "section_id": "1.1",
                        "source_role": "license", "source_label": "名称", "strategy": "document_label"}],
            "source_roles": [],
            "generation_templates": {"1.1": {"id": "1", "title": "x", "blocks": [
                {"type": "p", "template": "{{company.name}}",
                 "field_formats": {"company.name": {"decimals": 2}}},
            ]}},
            "audit_checks": {"1.1": {"checklist": ["x"]}},
        }
        payload = {
            "fields": [{"id": "originator.name", "label": "原始权益人名称", "section_id": "2.3",
                        "source_role": "license", "source_label": "名称", "strategy": "document_label"}],
            "generation_templates": {"2.3": {"id": "3", "title": "y", "blocks": [
                {"type": "p", "template": "{{originator.name}}",
                 "field_formats": {"originator.name": {"decimals": 2}}},
            ]}},
            "audit_checks": {"2.3": {"checklist": ["y"]}},
        }
        merged, report = rc._merge_payload_into_rules(existing, payload, "2.3")
        self.assertEqual(report["aliases"], {"originator.name": "company.name"})
        block = merged["generation_templates"]["2.3"]["blocks"][0]
        self.assertEqual(block["field_formats"], {"company.name": {"decimals": 2}})
        self.assertEqual(block["template"], "{{company.name}}")

    def test_same_material_selector_is_merged_even_when_role_ids_differ(self):
        selector = {
            "document_type": "originator_financial_report",
            "filename_keywords_any": ["审计报告", "财务报表"],
            "subject_ref": "originator.company_name",
            "repeat_by": "reporting_periods",
        }
        existing = [{"id": "audit_reports", "label": "原始权益人财务报告", "selector": selector}]
        incoming = [{"id": "originator_financials", "label": "最近三年及一期报表", "selector": selector}]
        roles, aliases, report = rc._merge_source_roles(existing, incoming)
        self.assertEqual(len(roles), 1)
        self.assertEqual(aliases, {"originator_financials": "audit_reports"})
        self.assertEqual(report["reused"], 1)

    def test_artifact_edits_are_parsed_back_into_runtime_rules(self):
        with patch.object(pack_service, "OVERRIDES_DIR", self.overrides_dir):
            audit_text = "# 审核 SKILL\n\n- 第一条业务审核规则\n- 第二条业务审核规则\n"
            rules = rc.save_artifact("2.3", "audit", audit_text, None)
            self.assertEqual(rules["audit_checks"]["2.3"]["checklist"],
                             ["第一条业务审核规则", "第二条业务审核规则"])
            generation = rc.artifact_text("2.3", "generation", None, rules=rules)
            self.assertIn("这是本小节生成时实际读取的运行文件", generation)
            self.assertIn("## 执行流程", generation)
            self.assertIn("## 写作规则", generation)
            self.assertIn("## 输出结构与顺序", generation)
            self.assertIn("## 参考示例（仅参考写法，禁止取值）", generation)
            self.assertIn("## 机器执行配置（必须保留）", generation)
            self.assertIn('"blocks"', generation)

    def test_apply_materializes_real_generation_skill(self):
        with patch.object(pack_service, "OVERRIDES_DIR", self.overrides_dir):
            payload = self._payload_for("1.1", "project.name", "项目名称")
            payload["generation_templates"]["1.1"].update({
                "style_instructions": ["使用正式申报材料文体"],
                "style_examples": [{"reference_only": True, "content": "仅供参考的示例正文。"}],
            })
            rc.apply_compiled("1.1", payload, None)
            rel = rc.artifact_rel("1.1", "generation", None)
            path = pack_service.override_path(rel, None)
            self.assertTrue(path.is_file())
            text = path.read_text(encoding="utf-8")
            self.assertIn("## 任务目标", text)
            self.assertIn("## 可用输入", text)
            self.assertIn("仅供参考的示例正文", text)
            self.assertIn('"template": "{{project.name}}"', text)

    def test_text_only_generation_skill_edit_does_not_invalidate_extracted_data(self):
        with patch.object(pack_service, "OVERRIDES_DIR", self.overrides_dir):
            payload = self._payload_for("1.1", "project.name", "项目名称")
            applied = rc.apply_compiled("1.1", payload, None)
            before_version = applied["rule_version"]
            text = rc.artifact_text("1.1", "generation", None, rules=applied)
            text = text.replace("## 写作规则", "## 写作规则\n\n- 人工补充：语言务必简洁")
            saved = rc.save_artifact("1.1", "generation", text, None)
            self.assertEqual(saved["rule_version"], before_version)
            self.assertIn("人工补充", pack_service.override_path(
                rc.artifact_rel("1.1", "generation", None), None).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
