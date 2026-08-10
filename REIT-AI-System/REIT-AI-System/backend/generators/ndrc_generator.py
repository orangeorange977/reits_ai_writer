"""发改委申报材料生成引擎

根据数据源文件、章节映射配置和Jinja2模板，
自动提取数据并生成规范的发改委申报材料。
"""

import re
import logging
from pathlib import Path
from typing import Dict, List, Optional, Union

logger = logging.getLogger(__name__)


class NDRCGenerator:
    """发改委申报材料生成引擎

    工作流程：
    1. scan_data_sources() - 扫描指定文件夹，识别可用数据文件
    2. extract_chapter_data() - 按映射规则从文件中提取章节数据
    3. generate_chapter() - 渲染Jinja2模板生成章节内容
    4. generate_full_document() - 串联七章生成完整文档
    """

    # 章节标题关键词映射（用于文件名匹配）
    CHAPTER_KEYWORDS = {
        "chapter1": ["项目基本情况", "项目概况", "基本情况"],
        "chapter2": ["参与主体", "主体情况", "项目公司", "发起人", "原始权益人"],
        "chapter3": ["设立方案", "产品要素", "产品架构", "REITs设立"],
        "chapter4": ["项目基本条件", "权属", "运营收益", "资产估值", "基本条件"],
        "chapter5": ["项目合规", "合规情况", "土地使用", "税收处理"],
        "chapter6": ["运营管理", "运营管理安排", "激励约束"],
        "chapter7": ["募集资金", "资金用途", "资金流向"],
    }

    def __init__(self, data_source_path: str):
        """初始化，指定数据源文件夹路径

        Args:
            data_source_path: 包含源数据文件的文件夹路径
        """
        self.data_source_path = Path(data_source_path)
        self._mapping = None
        self._jinja_env = None
        self._progress = {
            "status": "idle",
            "current_step": "",
            "progress_percent": 0,
            "chapters_completed": 0,
            "total_chapters": 7,
            "message": "就绪",
        }

        # 加载映射配置
        self._load_mapping()
        # 初始化Jinja2环境
        self._init_jinja_env()

    def _load_mapping(self) -> None:
        """加载章节映射配置"""
        try:
            from ..mappings import load_ndrc_chapter_mapping
            self._mapping = load_ndrc_chapter_mapping()
            logger.info("章节映射配置加载成功")
        except Exception as e:
            logger.error(f"加载章节映射配置失败: {e}")
            self._mapping = {"chapters": []}

    def _init_jinja_env(self) -> None:
        """初始化Jinja2模板环境"""
        try:
            import jinja2
            from ..config import TEMPLATES_DIR

            template_dir = TEMPLATES_DIR
            if not template_dir.exists():
                # 回退：相对路径查找
                template_dir = Path(__file__).parent.parent / "templates"

            self._jinja_env = jinja2.Environment(
                loader=jinja2.FileSystemLoader(str(template_dir)),
                undefined=jinja2.Undefined,
                trim_blocks=True,
                lstrip_blocks=True,
            )
            logger.info(f"Jinja2模板环境初始化成功，模板目录: {template_dir}")
        except ImportError:
            logger.error("jinja2库未安装，请运行: pip install jinja2")
            self._jinja_env = None
        except Exception as e:
            logger.error(f"初始化Jinja2环境失败: {e}")
            self._jinja_env = None

    def _get_chapter_config(self, chapter_id: str) -> Optional[Dict]:
        """根据ID获取章节映射配置"""
        if not self._mapping:
            return None
        for chapter in self._mapping.get("chapters", []):
            if chapter["id"] == chapter_id:
                return chapter
        return None

    def scan_data_sources(self) -> Dict:
        """扫描数据源文件夹，返回可用文件列表和章节匹配情况

        Returns:
            {
                "total_files": int,
                "files": [{name, path, type, size, matched_chapters: []}],
                "chapter_coverage": {chapter_id: {status, matched_files: []}}
            }
        """
        self._update_progress("scanning", "正在扫描数据源文件夹...", 5)

        try:
            from ..parsers.utils import scan_folder

            # 扫描文件
            raw_files = scan_folder(str(self.data_source_path))

            files = []
            chapter_coverage = {}

            # 初始化所有章节覆盖状态
            for chapter in self._mapping.get("chapters", []):
                chapter_coverage[chapter["id"]] = {
                    "title": chapter["title"],
                    "status": "未匹配",
                    "matched_files": [],
                }

            # 尝试将文件匹配到对应章节
            for file_info in raw_files:
                matched_chapters = self._match_file_to_chapters(file_info)
                files.append({
                    "name": file_info["name"],
                    "path": file_info["path"],
                    "type": file_info["file_type"],
                    "size": file_info["size_formatted"],
                    "matched_chapters": matched_chapters,
                })

                # 更新章节覆盖状态
                for ch_id in matched_chapters:
                    if ch_id in chapter_coverage:
                        chapter_coverage[ch_id]["status"] = "已匹配"
                        chapter_coverage[ch_id]["matched_files"].append(file_info["name"])

            result = {
                "total_files": len(files),
                "files": files,
                "chapter_coverage": chapter_coverage,
            }

            self._update_progress("idle", "扫描完成", 10)
            logger.info(f"扫描完成: 共{len(files)}个文件")
            return result

        except Exception as e:
            logger.error(f"扫描数据源时出错: {e}")
            self._update_progress("error", f"扫描出错: {e}", 0)
            return {"total_files": 0, "files": [], "chapter_coverage": {}}

    def _match_file_to_chapters(self, file_info: Dict) -> List[str]:
        """将文件匹配到可能的章节

        通过文件名中的关键词判断该文件属于哪些章节。
        """
        filename = file_info["name"]
        relative_path = file_info.get("relative_path", "")
        matched = []

        search_text = f"{filename} {relative_path}"

        for chapter_id, keywords in self.CHAPTER_KEYWORDS.items():
            for keyword in keywords:
                if keyword in search_text:
                    if chapter_id not in matched:
                        matched.append(chapter_id)
                    break

        # 如果文件名包含"申报材料"或"项目申报"，认为可能涵盖所有章节
        if "申报材料" in filename or "项目申报" in filename:
            for chapter in self._mapping.get("chapters", []):
                if chapter["id"] not in matched:
                    matched.append(chapter["id"])

        return matched

    def extract_chapter_data(self, chapter_id: str, source_files: List[str] = None) -> Dict:
        """从源文件提取指定章节的数据

        根据ndrc_chapter_mapping.json中该章节的fields定义，
        从源文件中尝试匹配并提取对应字段值。

        Args:
            chapter_id: 章节ID (如 "chapter1")
            source_files: 指定要解析的文件路径列表，为None时自动扫描

        Returns:
            {
                "chapter_id": str,
                "chapter_title": str,
                "sections": [{section_id, title, fields: [{id, label, value, source, confidence}]}],
                "extraction_summary": {total_fields, filled_fields, coverage_rate}
            }
        """
        self._update_progress("extracting", f"正在提取{chapter_id}章节数据...", 20)

        chapter_config = self._get_chapter_config(chapter_id)
        if not chapter_config:
            logger.error(f"未找到章节配置: {chapter_id}")
            return self._empty_chapter_result(chapter_id)

        # 确定数据源文件
        if source_files is None:
            source_files = self._find_source_files_for_chapter(chapter_id)

        # 解析源文件内容
        parsed_contents = self._parse_source_files(source_files)

        # 按section提取字段
        sections_data = []
        total_fields = 0
        filled_fields = 0

        for section in chapter_config.get("sections", []):
            section_fields = []
            for field_def in section.get("fields", []):
                total_fields += 1
                extracted = self._extract_field_value(
                    field_def, parsed_contents, chapter_config["title"], section["title"]
                )
                section_fields.append(extracted)
                if extracted.get("value"):
                    filled_fields += 1

            sections_data.append({
                "section_id": section["id"],
                "title": section["title"],
                "fields": section_fields,
            })

        coverage_rate = filled_fields / total_fields if total_fields > 0 else 0.0

        result = {
            "chapter_id": chapter_id,
            "chapter_title": chapter_config["title"],
            "sections": sections_data,
            "extraction_summary": {
                "total_fields": total_fields,
                "filled_fields": filled_fields,
                "coverage_rate": round(coverage_rate, 4),
            },
        }

        self._update_progress("idle", f"{chapter_id}数据提取完成", 30)
        logger.info(
            f"章节{chapter_id}数据提取完成: {filled_fields}/{total_fields} "
            f"字段已填充 ({coverage_rate:.1%})"
        )
        return result

    def _find_source_files_for_chapter(self, chapter_id: str) -> List[str]:
        """自动查找与章节相关的源文件"""
        try:
            from ..parsers.utils import scan_folder

            all_files = scan_folder(str(self.data_source_path))
            matched_files = []
            for file_info in all_files:
                matched_chapters = self._match_file_to_chapters(file_info)
                if chapter_id in matched_chapters:
                    matched_files.append(file_info["path"])
            return matched_files
        except Exception as e:
            logger.error(f"查找源文件时出错: {e}")
            return []

    def _parse_source_files(self, file_paths: List[str]) -> List[Dict]:
        """解析多个源文件，返回解析结果列表"""
        parsed_list = []
        for fp in file_paths:
            path = Path(fp)
            if not path.exists():
                logger.warning(f"文件不存在，跳过: {fp}")
                continue

            try:
                file_type = path.suffix.lower()
                if file_type in ('.docx', '.doc'):
                    from ..parsers.docx_parser import parse_docx
                    parsed = parse_docx(fp)
                    parsed_list.append({
                        "filename": parsed.filename,
                        "path": fp,
                        "type": "docx",
                        "raw_text": parsed.raw_text,
                        "sections": parsed.sections,
                    })
                elif file_type == '.pdf':
                    try:
                        from ..parsers.pdf_parser import parse_pdf
                        parsed = parse_pdf(fp)
                        parsed_list.append({
                            "filename": Path(fp).name,
                            "path": fp,
                            "type": "pdf",
                            "raw_text": getattr(parsed, 'raw_text', '') or '',
                            "sections": getattr(parsed, 'sections', []),
                        })
                    except Exception as e:
                        logger.warning(f"PDF解析失败: {fp}, {e}")
                elif file_type in ('.xlsx', '.xls'):
                    try:
                        from ..parsers.xlsx_parser import parse_xlsx
                        parsed = parse_xlsx(fp)
                        parsed_list.append({
                            "filename": Path(fp).name,
                            "path": fp,
                            "type": "xlsx",
                            "raw_text": str(parsed) if parsed else '',
                            "sections": [],
                        })
                    except Exception as e:
                        logger.warning(f"Excel解析失败: {fp}, {e}")
            except Exception as e:
                logger.warning(f"解析文件时出错: {fp}, {e}")

        return parsed_list

    def _extract_field_value(
        self, field_def: Dict, parsed_contents: List[Dict],
        chapter_title: str, section_title: str
    ) -> Dict:
        """从解析后的内容中提取单个字段的值

        策略：
        1. 按字段label在文本中搜索关键词
        2. 找到关键词后提取后面的内容作为值
        3. 对表格字段特殊处理
        """
        field_id = field_def["id"]
        field_label = field_def["label"]
        field_type = field_def.get("type", "text")

        result = {
            "id": field_id,
            "label": field_label,
            "type": field_type,
            "value": None,
            "source": "",
            "confidence": 0.0,
        }

        if not parsed_contents:
            return result

        # 对表格类型字段做特殊处理
        if field_type == "table":
            return self._extract_table_field(field_def, parsed_contents, result)

        # 文本类型字段：通过关键词匹配提取
        for parsed in parsed_contents:
            raw_text = parsed.get("raw_text", "")
            if not raw_text:
                continue

            # 策略1：精确匹配 "label：value" 或 "label:value" 模式
            patterns = [
                rf'{re.escape(field_label)}[：:]\s*(.+?)(?:\n|$)',
                rf'{re.escape(field_label)}[：:]\s*(.+?)(?=\n[^\s]|\Z)',
            ]

            for pattern in patterns:
                match = re.search(pattern, raw_text, re.DOTALL)
                if match:
                    value = match.group(1).strip()
                    # 清理值：去除多余空白行
                    value = re.sub(r'\n{3,}', '\n\n', value)
                    if value and value != "[待填写]":
                        result["value"] = value
                        result["source"] = parsed["filename"]
                        result["confidence"] = 0.85
                        return result

            # 策略2：通过section结构匹配
            sections = parsed.get("sections", [])
            extracted = self._search_sections_for_field(sections, field_label)
            if extracted:
                result["value"] = extracted
                result["source"] = parsed["filename"]
                result["confidence"] = 0.7
                return result

        return result

    def _extract_table_field(
        self, field_def: Dict, parsed_contents: List[Dict], result: Dict
    ) -> Dict:
        """提取表格类型字段"""
        field_label = field_def["label"]
        columns = field_def.get("columns", [])

        for parsed in parsed_contents:
            # 在docx解析结果的sections中查找表格
            sections = parsed.get("sections", [])
            table_data = self._find_table_in_sections(sections, field_label, columns)
            if table_data:
                result["value"] = table_data
                result["source"] = parsed["filename"]
                result["confidence"] = 0.75
                return result

        return result

    def _find_table_in_sections(
        self, sections, label: str, expected_columns: List[str]
    ) -> Optional[Dict]:
        """在sections中查找与标签关联的表格"""
        for section in sections:
            # 检查section标题是否包含关键词
            if label in getattr(section, 'title', '') or label in str(getattr(section, 'title', '')):
                tables = getattr(section, 'tables', [])
                if tables:
                    table = tables[0]
                    return {
                        "headers": getattr(table, 'headers', expected_columns),
                        "rows": getattr(table, 'rows', []),
                    }
            # 递归搜索子节点
            children = getattr(section, 'children', [])
            if children:
                found = self._find_table_in_sections(children, label, expected_columns)
                if found:
                    return found
        return None

    def _search_sections_for_field(self, sections, field_label: str) -> Optional[str]:
        """递归搜索sections查找字段值"""
        for section in sections:
            title = getattr(section, 'title', '')
            content = getattr(section, 'content', '')

            # 如果section标题包含字段标签
            if field_label in title:
                if content:
                    return content.strip()

            # 在section内容中搜索
            if content and field_label in content:
                # 尝试提取标签后的内容
                pattern = rf'{re.escape(field_label)}[：:]\s*(.+?)(?:\n[^\s]|\Z)'
                match = re.search(pattern, content, re.DOTALL)
                if match:
                    return match.group(1).strip()

            # 递归搜索子节点
            children = getattr(section, 'children', [])
            if children:
                result = self._search_sections_for_field(children, field_label)
                if result:
                    return result

        return None

    def _empty_chapter_result(self, chapter_id: str) -> Dict:
        """返回空的章节提取结果"""
        return {
            "chapter_id": chapter_id,
            "chapter_title": "",
            "sections": [],
            "extraction_summary": {
                "total_fields": 0,
                "filled_fields": 0,
                "coverage_rate": 0.0,
            },
        }

    def generate_chapter(self, chapter_id: str, data: Dict) -> str:
        """渲染Jinja2模板，生成章节文本内容

        Args:
            chapter_id: 章节ID (如 "chapter1")
            data: 该章节的字段数据（来自extract_chapter_data的结果或用户手动输入）

        Returns:
            渲染后的章节文本内容
        """
        self._update_progress("generating", f"正在生成{chapter_id}...", 50)

        if self._jinja_env is None:
            logger.error("Jinja2环境未初始化，无法生成章节")
            return f"[错误: Jinja2环境未初始化，无法生成{chapter_id}]"

        template_name = f"ndrc/{chapter_id}.j2"

        try:
            template = self._jinja_env.get_template(template_name)
        except Exception as e:
            logger.error(f"加载模板失败: {template_name}, 错误: {e}")
            return f"[错误: 模板{template_name}加载失败]"

        # 将提取数据展开为模板变量
        template_vars = self._flatten_chapter_data(data)

        try:
            rendered = template.render(**template_vars)
            logger.info(f"章节{chapter_id}渲染成功，共{len(rendered)}字符")
            return rendered
        except Exception as e:
            logger.error(f"渲染模板失败: {template_name}, 错误: {e}")
            return f"[错误: 渲染{chapter_id}模板失败: {e}]"

    def _flatten_chapter_data(self, data: Dict) -> Dict:
        """将章节数据展开为模板变量字典

        将 sections->fields 结构展平为 {field_id: field_value} 的扁平字典，
        供Jinja2模板直接使用。
        """
        template_vars = {}

        sections = data.get("sections", [])
        for section in sections:
            fields = section.get("fields", [])
            for field_item in fields:
                field_id = field_item.get("id", "")
                value = field_item.get("value")
                if field_id:
                    template_vars[field_id] = value

        return template_vars

    def generate_full_document(self, project_data: Dict, use_template: bool = False) -> Union[str, Dict]:
        """生成完整文档

        Args:
            project_data: 所有章节数据的汇总
            {
                "project_name": str,
                "chapters": {chapter_id: chapter_data_dict}
            }
            use_template: True则返回结构化数据供模板填充器使用，False则返回渲染文本（原有行为）

        Returns:
            use_template=False: str (渲染后的完整文档文本)
            use_template=True: dict (结构化数据)
        """
        if use_template:
            return self._generate_structured_data(project_data)

        # 以下为原有的Jinja2渲染逻辑，保持不变
        self._update_progress("generating", "正在生成完整文档...", 40)

        project_name = project_data.get("project_name", "")
        chapters_data = project_data.get("chapters", {})

        # 导入ChapterComposer
        from .chapter_composer import ChapterComposer
        composer = ChapterComposer()

        chapters_content = {}
        chapter_ids = [f"chapter{i}" for i in range(1, 8)]

        for idx, chapter_id in enumerate(chapter_ids):
            chapter_data = chapters_data.get(chapter_id, {})
            if chapter_data:
                content = self.generate_chapter(chapter_id, chapter_data)
            else:
                # 无数据的章节使用空数据渲染
                empty_data = self._build_empty_chapter_data(chapter_id)
                content = self.generate_chapter(chapter_id, empty_data)

            chapters_content[chapter_id] = content
            self._progress["chapters_completed"] = idx + 1
            progress = 40 + int((idx + 1) / 7 * 50)
            self._update_progress("generating", f"已完成{idx + 1}/7章", progress)

        # 使用ChapterComposer组装
        full_document = composer.compose(chapters_content, project_name=project_name)

        self._update_progress("completed", "文档生成完成", 100)
        logger.info(f"完整文档生成成功，共{len(full_document)}字符")
        return full_document

    def _generate_structured_data(self, project_data: Dict) -> Dict:
        """生成结构化数据（供官方模板填充器使用）

        Returns:
            {
                "summary_fields": {field_id: value, ...},  # 摘要表数据
                "narrative_fields": {field_id: value, ...},  # 各章节叙述性字段
                "table_data": {table_id: {"headers": [...], "rows": [[...], ...]}, ...}  # 表格数据
            }
        """
        self._update_progress("generating", "正在准备结构化数据...", 40)

        chapters_data = project_data.get("chapters", {})

        # 1. 构建摘要表数据
        summary_fields = self._build_summary_data(project_data)

        # 2. 收集所有叙述性字段（非表格类型的字段）
        narrative_fields = {}

        # 3. 收集所有表格数据
        table_data = {}

        for chapter_id in [f"chapter{i}" for i in range(1, 8)]:
            chapter_data = chapters_data.get(chapter_id, {})
            sections = chapter_data.get("sections", [])

            for section in sections:
                fields = section.get("fields", [])
                for field_item in fields:
                    field_id = field_item.get("id", "")
                    field_type = field_item.get("type", "text")
                    value = field_item.get("value")

                    if not field_id or value is None:
                        continue

                    if field_type == "table":
                        # 表格类型字段 -> table_data
                        # value应该是 {"headers": [...], "rows": [[...]]}
                        if isinstance(value, dict) and "headers" in value:
                            table_data[field_id] = value
                        elif isinstance(value, list):
                            # 如果value是行列表，尝试从mapping获取headers
                            table_data[field_id] = {
                                "headers": field_item.get("columns", []),
                                "rows": value
                            }
                    else:
                        # 非表格字段 -> narrative_fields
                        narrative_fields[field_id] = str(value) if value else ""

        self._update_progress("generating", "结构化数据准备完成", 60)

        return {
            "summary_fields": summary_fields,
            "narrative_fields": narrative_fields,
            "table_data": table_data,
        }

    def _build_summary_data(self, project_data: Dict) -> Dict:
        """从项目数据中组装摘要表字段

        摘要表字段主要来源于chapter1的项目概况和整体项目配置
        """
        chapters_data = project_data.get("chapters", {})
        chapter1 = chapters_data.get("chapter1", {})

        # 从chapter1的sections中提取相关字段
        all_fields = {}
        for section in chapter1.get("sections", []):
            for field_item in section.get("fields", []):
                fid = field_item.get("id", "")
                val = field_item.get("value")
                if fid and val:
                    all_fields[fid] = val

        # 映射到摘要表字段
        summary = {
            "summary_project_name": all_fields.get("project_name", ""),
            "summary_industry_field": all_fields.get("asset_type", ""),
            "summary_asset_location": all_fields.get("project_location", ""),
            "summary_asset_scope": all_fields.get("total_area", ""),
            "summary_construction_scale": "",
            "summary_first_or_new": "首次发行项目",
            "summary_base_date": all_fields.get("base_date", ""),
            "summary_appraisal_net_value": all_fields.get("asset_valuation", ""),
            "summary_fund_total": all_fields.get("fund_scale", ""),
            "summary_originator_subscribe_ratio": "",
            "summary_net_recovery_fund": "",
            "summary_reinvestment_amount": "",
            "summary_listing_venue": "深圳证券交易所",
            "summary_sponsor": all_fields.get("originator", ""),
            "summary_originator": all_fields.get("originator", ""),
            "summary_fund_manager": all_fields.get("fund_manager", ""),
            "summary_abs_manager": all_fields.get("abs_manager", ""),
            "summary_law_firm": "",
            "summary_accounting_firm": "",
            "summary_appraisal_firm": "",
            "summary_tax_consultant": "",
            "summary_securities_consultant": "",
        }

        # 也从project_data顶层获取可能的覆盖值
        project_name = project_data.get("project_name", "")
        if project_name and not summary["summary_project_name"]:
            summary["summary_project_name"] = project_name

        return summary

    def _build_empty_chapter_data(self, chapter_id: str) -> Dict:
        """构建空数据的章节结构（用于无数据章节渲染）"""
        chapter_config = self._get_chapter_config(chapter_id)
        if not chapter_config:
            return {"sections": []}

        sections = []
        for section in chapter_config.get("sections", []):
            fields = []
            for field_def in section.get("fields", []):
                fields.append({
                    "id": field_def["id"],
                    "label": field_def["label"],
                    "type": field_def.get("type", "text"),
                    "value": None,
                    "source": "",
                    "confidence": 0.0,
                })
            sections.append({
                "section_id": section["id"],
                "title": section["title"],
                "fields": fields,
            })

        return {
            "chapter_id": chapter_id,
            "chapter_title": chapter_config["title"],
            "sections": sections,
        }

    def get_generation_progress(self) -> Dict:
        """获取当前生成进度

        Returns:
            {
                "status": "idle"|"scanning"|"extracting"|"generating"|"exporting"|"completed"|"error",
                "current_step": str,
                "progress_percent": int (0-100),
                "chapters_completed": int,
                "total_chapters": int,
                "message": str
            }
        """
        return dict(self._progress)

    def _update_progress(self, status: str, message: str, percent: int) -> None:
        """更新生成进度"""
        self._progress["status"] = status
        self._progress["message"] = message
        self._progress["current_step"] = message
        self._progress["progress_percent"] = min(max(percent, 0), 100)
