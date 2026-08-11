# 法律意见书专项提取

本通道处理路径或文件名含“法律意见书”的全部文件，包括第19项转让合法性意见书以及其他材料目录中的项目专项法律意见。PDF 和 DOCX 内容重复时可以共用事实，但两个文件均要登记覆盖和版本关系。

## 执行顺序

1. 先读封面、目录、署名页，建立“标题→页码”索引，并提取报告名称、日期、律所、经办律师。
2. 按目录分节读完所有页，不先归纳结论。每节先记文件、主体、日期、文号、条款原文和页码，再归并到 schema。
3. 对附表、权属清单和手续清单逐行提取；续表与上一页表头合并，不只摘取法律结论。
4. 最后做“法律意见书→原证照/合同”反向核对。法律意见书是线索和确认性来源；证载数据以权证、批复、许可证、合同原件为准。冲突进 `_quality.conflicts`。

## 第五章必交字段矩阵

- 投资建设合规：`compliance.investment_procedures`、`procedure_notes`、`procedures_not_applicable`、`procedures_missing`。
- 行业手续：`compliance.industry_procedures`，尤其是持证主体、证号、业务种类、有效期和是否构成转让限制。
- 土地与房屋：`land_use`、`building_ownership`、`land_procedure_summary`、`land_contracts`、`asset_transfer_chain`。每本权证、每宗地、每栋房屋单独成行。
- 可转让性：`transferability.summary`、`restrictions`、`encumbrances`、`no_objection_letters`。限制条款必须保留原文、限定对象、解除条件和解除状态。
- 法律意见：`compliance.legal_opinions[]`，必须有发文主体、文件全称、日期、范围、结论原文、附件2要素覆盖情况和全文定位。
- 第二章回填：`legal_relations.project_company_equity`、`originator_relations`、`controlling_shareholder`、`actual_controller`，全部股东持股比例合计100%。
- 第一章回填：`sub_projects[*].underlying_asset`、权属起止、资产范围排除项。

## 完成判定

通道读完不代表矩阵已填。表15—20的数据路径要分别显示“有数据”或“有明确不涉及/缺失依据”。任意一表整体为空，都不得用法律意见的一句总结替代。
