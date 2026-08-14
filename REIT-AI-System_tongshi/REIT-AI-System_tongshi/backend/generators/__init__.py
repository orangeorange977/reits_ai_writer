"""文档生成器模块

提供发改委申报材料的核心生成逻辑，包括：
- NDRCGenerator: 发改委材料生成引擎（数据提取+模板渲染）
- ChapterComposer: 章节组装器（排序+目录+交叉引用）
- DocxExporter: DOCX文档导出器（格式化Word输出）
- TemplateDocxGenerator: 基于官方DOCX模板的文档生成器（模板克隆+智能填充）
"""

from .ndrc_generator import NDRCGenerator
from .chapter_composer import ChapterComposer
from .docx_exporter import DocxExporter
from .template_docx_generator import TemplateDocxGenerator

__all__ = [
    'NDRCGenerator',
    'ChapterComposer',
    'DocxExporter',
    'TemplateDocxGenerator',
]
