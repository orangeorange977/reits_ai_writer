---
name: reits-proof-extractor-robust
description: 稳健提取基础设施REITs证明材料，建立文件与页级清单，把PDF、扫描件、Word及表格转为带溯源的统一结构化数据，并通过防漏提、表格完整性和交接契约硬门禁。适用于材料包原样输入、长任务断点续跑、提取结果供REITs申报材料生成使用。
---

# REITs 证明材料稳健提取

本 SKILL 只做证明材料解析和结构化提取，不撰写申报材料、不生成 Word。目标不是“读过材料”，而是交付一份可被生成端确定性消费、可复核且能证明没有静默漏提的数据包。

## 不可违反的执行协议

1. 所有进度以工作目录内的清单和报告为准，不以对话记忆或自述为准。
2. 不抽样替代全量处理。审计报告、评估/估值报告、法律意见书、权属证照和营业执照必须达到 100% 文件覆盖；核心报告必须达到 100% 页覆盖。
3. “文件已读”不等于“字段已提”。文件覆盖、页覆盖、字段覆盖、表格覆盖分别验收。
4. 禁止猜测、补写或用常识顶替底稿。缺失值写 `null`，同时登记到 `_quality.issues`；冲突值保留全部候选及来源，不擅自选一个。
5. 表格必须逐行逐列提取，保留表头、单位、期间、合并单元格语义、合计行和续表关系。不得只摘摘要或关键数字。
6. 每个事实对象必须带 `_source`、`_attachment_no`、`_doc_name`、`_page`（无页码填 `null`）、`_raw_text`；无页码文件用 `_section` 定位。
7. 每完成一个小批次，先原子写入 `extracted_data.json`，再登记已读页。没有成功落盘的数据不得标记为已读。
8. 不修改阈值、不手工编辑 `checkpoint.json` 或覆盖率报告来放行。任何越权均视为未完成。

## 输入和固定产物

输入可以是原始目录或 zip，不要求业务预先整理。先确认：

```text
proof_materials_dir 或 proof_zip
work_dir
project_name
project_type: 产权类 / 经营收益权类
issuance_type: 首发 / 扩募
draft_path: 可选，仅作为补充事实来源
```

最终必须生成：

```text
proofs_index.json              文件清单与材料编号
missing_materials.json         25项材料缺件清单
batch_render_report.json       PDF文字层/页图处理报告
extracted_data.json            统一结构化数据，契约版本 reits_handoff/2.0
extraction_coverage.json       文件和核心页覆盖报告
handoff_validation.json        字段、表格、溯源及契约体检
checkpoint.json                从磁盘产物推导的状态
images/                        页图和全文txt
```

`templates/extracted_data_schema.json` 是唯一现行字段结构。旧版 `operation`/扩展 `evaluation` 结构只保存在 `extracted_data_schema_v1_legacy.json` 供迁移参考，禁止新任务继续写旧结构。第四章表格统一写入 `operating_performance`。

## 第0步：恢复状态，禁止凭记忆续跑

新任务、换智能体、上下文压缩或中断恢复时，第一条命令固定为：

```bash
python "<skill_dir>/scripts/pipeline_state.py" --work-dir "<work_dir>"
```

按输出的 `next_command` 继续。如果工作目录不存在，则进入第1步。一次只推进一个阶段；阶段报告未落盘，不得进入下一阶段。

## 第1步：安全展开和全量建账

zip 必须用内置脚本，避免中文长路径静默丢文件：

```bash
python "<skill_dir>/scripts/extract_zip.py" "<proof_zip>" --output-dir "<proof_dir>"
python "<skill_dir>/scripts/scan_proofs.py" "<proof_dir>" --output "<work_dir>/proofs_index.json"
python "<skill_dir>/scripts/check_missing.py" --proofs-index "<work_dir>/proofs_index.json" --output "<work_dir>/missing_materials.json"
```

硬检查：

- `_extract_report.json.integrity_ok` 必须为 `true`。
- `proofs_index.material_index` 不能为空；文件数量应与原目录可见文件数量一致。
- 压缩包、原件和页图在交付前不得删除。
- 缺件不等于无事可做：继续提取已提供材料，但缺件必须进入问题清单。

## 第2步：确定性解析和页级任务队列

PDF 分批处理，反复运行直到报告明确显示无待处理项：

```bash
python "<skill_dir>/scripts/batch_render_pdfs.py" "<proof_dir>" --work-dir "<work_dir>" --time-budget 240 --max-pages 30
```

`.doc`/`.docx` 天眼查报告单独解析，先 dry-run 后合并：

```bash
python "<skill_dir>/scripts/parse_tianyancha.py" <报告文件...> --work-dir "<work_dir>" --proof-dir "<proof_dir>" --extracted "<work_dir>/extracted_data.json" --dry-run
python "<skill_dir>/scripts/parse_tianyancha.py" <报告文件...> --work-dir "<work_dir>" --proof-dir "<proof_dir>" --extracted "<work_dir>/extracted_data.json"
```

阶段A硬门禁：核心报告渲染页数必须等于PDF总页数，`batch_render_report.json.errors` 必须为空。无法解析的文件进入 `_quality.issues`，不得静默跳过。

## 第3步：三条专项通道先行，再读其余材料

法律意见书、房地产/资产评估报告和第二章主体及财务材料不得混在通用页队列里“顺手读”。它们分别使用三个独立通道，顺序不可合并：

```bash
python "<skill_dir>/scripts/specialized_queue.py" --pass legal-opinion --proofs-index "<work_dir>/proofs_index.json" --extracted "<work_dir>/extracted_data.json" --work-dir "<work_dir>" --next-pages 6
python "<skill_dir>/scripts/specialized_queue.py" --pass real-estate-appraisal --proofs-index "<work_dir>/proofs_index.json" --extracted "<work_dir>/extracted_data.json" --work-dir "<work_dir>" --next-pages 6
python "<skill_dir>/scripts/specialized_queue.py" --pass chapter2 --proofs-index "<work_dir>/proofs_index.json" --extracted "<work_dir>/extracted_data.json" --work-dir "<work_dir>" --next-pages 6
```

- 法律意见书通道每批必须读 `references/legal_opinion_extraction.md`，重点交付第五章表15—20及正文的结构化事实。
- 房地产/资产评估通道每批必须读 `references/real_estate_appraisal_extraction.md`。按文件名语义识别，不限材料编号；第28项“其他应当出具的证明材料”也必须扫描。
- 第二章通道每批必须读 `references/chapter2_extraction.md`，按“主体×年度×报表”建账，同时提取大幅变动的原因。

每批成功写入 `extracted_data.json` 并重读确认后，再用同一命令加 `--mark-batch`。命令再次返回空队列才算该通道读完。文件同时命中两个通道时，两个通道的字段矩阵都要完成，不得以“已读页”替代“已提字段”。

## 第4步：其余材料按页小批提取，写后再登记

每轮先取得下一批：

```bash
python "<skill_dir>/scripts/check_extraction_coverage.py" \
  --proofs-index "<work_dir>/proofs_index.json" \
  --extracted "<work_dir>/extracted_data.json" \
  --work-dir "<work_dir>" --next-pages 8 --max-per-file 4
```

逐页读取本轮所有页图和对应全文文本，按 `templates/extraction_groups.json` 提取，并写入现行 schema。已在专项通道完成的页由队列自动跳过。建议每轮 6–10 页；遇到宽表或跨页表时减到 2–4 页，不要为追求速度扩大批次。

每轮执行两遍：

- 第一遍“事实提取”：正文事实、数字、日期、主体、条款及表格原貌。
- 第二遍“遗漏审计”：只核对标题、脚注、续表、单位、合计、负号、百分号、期间和溯源，不重写第一遍结果。

表格记录至少包含：

```json
{
  "label": "营业收入",
  "v1": "30,282.92",
  "v2": "52,281.70",
  "_unit": "万元",
  "_periods": ["2021年", "2022年"],
  "_source": "...",
  "_attachment_no": "...",
  "_doc_name": "...",
  "_page": "43-44",
  "_raw_text": "..."
}
```

成功保存并重新读取 JSON 确认无损后，才可登记：

```bash
python "<skill_dir>/scripts/check_extraction_coverage.py" \
  --proofs-index "<work_dir>/proofs_index.json" \
  --extracted "<work_dir>/extracted_data.json" \
  --work-dir "<work_dir>" --mark-batch
```

重复“取下一批→读取→双遍检查→落盘→登记”，直到队列为空。不得因为耗时长、上下文变短或已有部分结果而提前停止。

## 第5步：交叉验证和表格勾稽

按 `templates/data_crossref.json` 完成多来源核对。发现冲突时：

- 在目标字段保留权威值及其来源；
- 在 `_quality.conflicts[]` 记录所有候选值、来源、采用理由和待人工确认项；
- 历史实际、预测数据、第三方模糊财务值必须隔离，禁止相互顶替；
- 70%测试、评估净值、资产负债率等公式必须从基础值重算，并保留计算式和各输入来源。

第四章15张表的数据必须按 `operating_performance` 的最终行模型写入，不把原始二维表直接交给生成端猜列：

- `annual_rows`、`forecast_rows`、`revenue_structure_rows`
- `cash_flow_providers`、`end_customer_financial_rows`、`subsidies`
- `valuation_params.*_rows`
- `self_built_rows`（不涉及可为空，但须记录不涉及依据）

第二章财务表以“主体×期间×指标”核对。每个发起人至少最近3个会计年度及一期；指标大幅变动、由负转正/由正转负或异常值必须登记到 `financial_analysis.change_reasons`。材料可解释的写原因与来源；材料不能解释的显式标 `unexplained`，不用行业常识补。

第五章表15—20逐表核对。必须分别验收 `investment_procedures` / `industry_procedures` / `land_use` / `building_ownership` / `land_procedure_summary` / `transferability.summary`，不得以 `compliance` 顶层非空就宣称第五章可生成。

## 第6步：三门禁交付

先跑文件/页覆盖门禁：

```bash
python "<skill_dir>/scripts/check_extraction_coverage.py" \
  --proofs-index "<work_dir>/proofs_index.json" \
  --extracted "<work_dir>/extracted_data.json" \
  --work-dir "<work_dir>" --output "<work_dir>/extraction_coverage.json"
```

再跑字段/表格/溯源交接门禁：

```bash
python "<skill_dir>/scripts/validate_specialized_extraction.py" --work-dir "<work_dir>" --strict
python "<skill_dir>/scripts/validate_handoff.py" --work-dir "<work_dir>" --strict
```

只有覆盖率、专项提取和交接契约三个命令均为 exit 0，且 `handoff_validation.json.verdict` 为 `READY` 才能交付。`READY_WITH_GAPS`、`BLOCKED`、校验脚本缺失或报告过期，都不得声称“提取完成”。

## 防漏提验收口径

同时满足以下条件才算完成：

- 全体文件覆盖率不低于80%，核心材料文件和页覆盖率100%；
- 核心表格数据源无未解释缺口；
- 表格没有重复行标签、列数漂移、期间错位、单位缺失或70%测试勾稽错误；
- 有值事实对象的完整溯源率不低于80%，关键字段和表格行必须100%溯源；
- 所有无法读取、材料缺失、来源冲突和人工确认项均进入结构化问题清单；
- 交接数据不存在旧版 `operation` 与现行 `operating_performance` 混用。

## 给下游的交接说明

交付时只报告事实：输入文件数、已读文件数、核心页完成度、关键表格可生成数、溯源率、未解决问题数和两个门禁结论。不得用“基本完成”“大部分已提取”等模糊表述替代数字。
