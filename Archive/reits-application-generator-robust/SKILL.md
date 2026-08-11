---
name: reits-application-generator-robust
description: 基于已通过交接门禁的REITs结构化材料，稳定生成申报材料正文、表格、溯源和规范Word；以确定性表格映射、分阶段写入、版式归一化和成品硬校验防止漏章、错表、无来源及格式失控。适用于基础设施REITs申报材料生成和长任务断点续跑。
---

# REITs 申报材料稳健生成

本 SKILL 消费提取端的 `extracted_data.json` 和 `proofs_index.json`，生成可复核 Word。它不重新OCR、不联网补事实、不替业务作出交易结构或募集资金用途决策。

默认生成第一、二、四、五、六章；第三章为“REITs设立方案”，第七章为“募集资金用途情况”，保留业务版本或显式占位，不自动作出方案判断。

## 不可违反的执行协议

1. 先验收入，再生成。交接门禁未通过时，不启动正文或表格写入。
2. 事实只来自 `extracted_data.json`；没有来源的数据只能写统一占位符 `【待填写】`，并仅高亮这五个字。缺失原因和字段路径写入 todo/校验报告，不塞进 Word 占位符。
3. 表格由蓝图和脚本确定性生成，章节撰写过程只提供叙述和表标题，不得自行重排表头、补数字或手写表格JSON。
4. 一次只应用一个 fill plan，并保存新的 Word 版本；任何时候从磁盘状态恢复，不靠对话记忆判断当前HEAD。
5. 同一事实在正文和表格中必须引用同一路径；有冲突时停止该项生成并进入问题清单。
6. 表号统一写 `表#`，全部表落地后一次性重编号。禁止正文提前写死表号。
7. 格式归一化、内容校验、版式渲染检查是交付门禁，不是可选美化步骤。

## 输入和固定产物

必需输入：

```text
work_dir/extracted_data.json
work_dir/proofs_index.json
work_dir/extraction_coverage.json
draft_path: 推荐，业务已有内容的填充基底
template_path: 无初稿时使用；默认 assets/申报材料格式文本（2024年版）.docx
output_path
```

关键产物：

```text
handoff_validation.json        输入契约、字段、表格和溯源体检
table_validation.json          第四章表格模型与勾稽结果
fill_plan_phase*.json          确定性表格/固定段落计划
fill_plan_ch*.json             第一、二、四、五、六章正文计划
checkpoint.json                从磁盘产物推导的执行状态
*_sourced.docx                 正文内联来源版
*_review.docx                  来源批注/待确认高亮版
*_formatted.docx               统一版式后的交付候选稿
validate_report.json           内容和结构校验
layout_validation.json         页面、样式、表格和版式校验
```

## 第0步：恢复状态

新任务、换智能体、上下文压缩或中断后先运行：

```bash
python "<skill_dir>/scripts/pipeline_state.py" --work-dir "<work_dir>"
```

以输出的产物清单和下一条命令为准。若出现多个疑似HEAD，回到最近一个已经通过上一阶段校验的 Word，不选择文件名看起来最新但来源不明的版本。

## 第1步：输入契约硬门禁

```bash
python "<skill_dir>/scripts/validate_handoff.py" --work-dir "<work_dir>" --strict
```

必须满足：

- `extraction_coverage.json.pass=true`，阈值未降低，无核心文件或页缺口；
- 数据结构为 `reits_handoff/2.0` 口径，使用 `operating_performance`，不得混入旧版 `operation`；
- 第四章各表数据源能逐表定位，强制表不得缺失；
- 关键字段和关键表格行具有真实附件编号与文档名；
- 表格行模型没有重复标签、期间错位、非法行类型或公式勾稽错误。
- `specialized_extraction_validation.json` 必须为 `READY`：法律意见书、房地产/资产评估报告、第二章主体与财务三条专项通道均已验收。

校验不通过时，只向提取端返回 `handoff_validation.json` 中的精确字段路径和问题，不自行从原PDF重提、不用文字绕过空表。

## 第2步：基底和表格坐标对齐

用业务初稿作基底时先导出结构并对齐表索引：

```bash
python "<skill_dir>/scripts/dump_outline.py" "<draft_path>" --output "<work_dir>/draft_outline.json"
python "<skill_dir>/scripts/align_table_index.py" --template "<draft_path>" --out "<work_dir>/table_index_map.json"
```

不覆盖业务已经填写且非占位的单元格。坐标无法唯一匹配时停止该表，记录候选表标题、索引和差异，不凭行列数量猜测。

## 第3步：先生成并校验确定性表格

依次生成 phase0、phase1、phase2、phase5，第四章 phase4 最后生成但在正文前完成数据校验：

```bash
python "<skill_dir>/scripts/gen_phase_fill_plan.py" --blueprint "<skill_dir>/templates/phase0_blueprints.json" --extracted "<work_dir>/extracted_data.json" --output "<work_dir>/fill_plan_phase0.json" --base-docx "<draft_path>" --base-vars-out "<work_dir>/base_vars.json"
python "<skill_dir>/scripts/gen_phase_fill_plan.py" --blueprint "<skill_dir>/templates/phase1_blueprints.json" --extracted "<work_dir>/extracted_data.json" --output "<work_dir>/fill_plan_phase1.json" --base-docx "<draft_path>"
python "<skill_dir>/scripts/gen_phase_fill_plan.py" --blueprint "<skill_dir>/templates/phase2_blueprints.json" --extracted "<work_dir>/extracted_data.json" --output "<work_dir>/fill_plan_phase2.json" --base-docx "<draft_path>" --base-vars "<work_dir>/base_vars.json"
python "<skill_dir>/scripts/gen_phase_fill_plan.py" --blueprint "<skill_dir>/templates/phase5_blueprints.json" --extracted "<work_dir>/extracted_data.json" --output "<work_dir>/fill_plan_phase5.json" --base-docx "<draft_path>"
python "<skill_dir>/scripts/gen_phase_fill_plan.py" --blueprint "<skill_dir>/templates/phase4_blueprints.json" --extracted "<work_dir>/extracted_data.json" --output "<work_dir>/fill_plan_phase4.json" --base-docx "<draft_path>"
```

每个计划生成后检查同名 `.todo.json`。以下情况为阻断项：表格数据源为空、目标表匹配不唯一、列数与期间数不一致、来源缺失、公式错误。不得先写 Word 再看起来是否正确。

## 第4步：按章生成正文

只生成 ch1、ch2、ch4、ch5、ch6。每章都必须读取：

- `references/chapter_writer_prompt.md`
- 对应 `templates/chapter_guides/chN_guide.md`
- 对应 `templates/chapter_examples/chN_example.md`，仅学结构和粒度，不复制项目事实
- 由 `slice_chapter_data.py` 切出的本章数据

正文规则：

- 每段先确定事实路径和来源，再写句子；无法定位来源则写待确认项。
- 不把风险写成确定事实，不把第三方模糊数据写入正式财务表。
- 不引用不存在的表，不自行创造附件编号、法规版本或业务方案。
- 第四章必须逐表说明数据口径；表格数据由 phase4 蓝图提供，正文不得重复计算。
- 第三、七章保持基底原文；若基底为空，仅保留清晰占位和缺失输入清单。
- 第一章（二）不属于模板列举的4类特殊业态时，正文只写“不涉及。”，不解释、不列附属事项。
- 第一章（三）为首发时，开头只写“本项目为首次发行项目。”，然后直接写发起人业务布局和可扩募资产；不讨论“无扩募”、扩募豁免或其他不命中的分支。可扩募资产优先取第25项“其他证明材料/拟投资项目合规材料”。
- 第二章发起人财务分析不生成独立“同比增幅表”。正文只对大幅变动、异常值和正负转换的指标标明变化幅度，并从 `financial_analysis.change_reasons` 写明原因；找不到原因时用 `【待填写】`，不用行业常识代答。

章节输出只允许 `fill_plan_chN.json` 和可选 `chN_tables.md`，不得直接输出或修改 docx。

## 第5步：单链路写入Word

应用顺序固定：

```text
基底 → phase0 → phase1 → phase2 → phase5 → ch1 → ch2 → ch4 → ch5 → ch6
→ annex清理 → phase5.phase6 → phase4.phase7 → 全篇表号重排
```

每次先校验再应用：

```bash
python "<skill_dir>/scripts/fill_docx.py" --template "<HEAD.docx>" --fill-plan "<plan.json>" --validate-only
python "<skill_dir>/scripts/fill_docx.py" --template "<HEAD.docx>" --fill-plan "<plan.json>" --output "<NEXT.docx>"
```

任何一步失败都停在当前HEAD修复，不跳过、不并行写多个Word、不把多个分支Word合并。

全部表格完成后：

```bash
python "<skill_dir>/scripts/renumber_tables.py" --input "<HEAD.docx>" --output "<work_dir>/result_numbered.docx"
```

## 第6步：来源版、审阅版和版式归一化

先生成来源批注版，再做统一版式：

```bash
python "<skill_dir>/scripts/citations_to_comments.py" "<work_dir>/result_numbered.docx" --output "<work_dir>/result_review.docx"
python "<skill_dir>/scripts/normalize_docx_style.py" --input "<work_dir>/result_review.docx" --output "<work_dir>/result_formatted.docx" --profile reits
```

版式脚本统一A4、页边距、正文和标题字体字号、行距、段前段后、首行缩进、表题、表头、表格字体和单元格垂直对齐。它不修改正文文字和数字。

## 第7步：三层交付门禁

内容结构校验：

```bash
python "<skill_dir>/scripts/validate_output.py" "<work_dir>/result_formatted.docx" --proofs-index "<work_dir>/proofs_index.json" --work-dir "<work_dir>" --json "<work_dir>/validate_report.json"
```

Word版式校验：

```bash
python "<skill_dir>/scripts/validate_docx_layout.py" --input "<work_dir>/result_formatted.docx" --output "<work_dir>/layout_validation.json"
```

最后用内置渲染器生成逐页图片并检查每一页：

```bash
python "<skill_dir>/scripts/render_docx.py" "<work_dir>/result_formatted.docx" --output_dir "<work_dir>/rendered" --emit_pdf
```

至少检查：目录与分页、标题孤行、表格越界/跨页断裂、空白页、字体缺字、图示清晰度和黄色待确认项。若运行环境缺少 LibreOffice、Poppler 或 `pdf2image`，必须明确标记“视觉门禁未执行”，不得声称版式已验收。

只有以下条件同时满足才可交付：

- `validate_output.py` exit 0，无 FAIL；
- `validate_docx_layout.py` exit 0，无 FAIL；
- 视觉检查无阻断项；
- 所有表标题后存在真实表格，第四章强制表数量和数据源一致；
- 正文、表格、来源括注和附件编号相互一致；
- 第三、七章的业务维护状态已明确披露。

## 稳定性和长任务纪律

- 每完成一个阶段立即重跑 `pipeline_state.py`，让检查点从磁盘重建。
- 每次工具调用只做一个可核验动作；长任务按章、按表、按批次拆分。
- 不以“已生成Word”作为完成标准，只以三层门禁为准。
- 不因运行时间过长而降低表格、来源或版式阈值；需要暂停时保留当前HEAD和检查点，下一智能体可直接续跑。
