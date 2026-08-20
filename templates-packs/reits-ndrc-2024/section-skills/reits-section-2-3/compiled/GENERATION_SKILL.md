# 2.3 （三）发起人（原始权益人）情况 · 生成 SKILL

> 这是本小节生成时实际读取的运行文件，由 Know-how 编译而来。Know-how 的版本、日期、修订人、审核人和状态不属于写作指令，已排除。

## 任务目标

使用当前项目已提取并通过来源定位的数据，生成“（三）发起人（原始权益人）情况”小节。不得读取 Know-how 原文，不得把方法论示例当作当前项目事实。

## 可用输入

- 只允许使用当前数据中间层中的字段：`compliance.commitment_attachment`、`compliance.commitment_date`、`compliance.commitment_name`、`compliance.commitment_quote`、`compliance.safety_conclusion`、`compliance.violation_conclusion`、`credit.attachment`、`credit.conclusion`、`credit.cutoff_date`、`credit.sites`、`finance.analysis`、`originator.actual_controller`、`originator.company_name`、`originator.contact_details`、`originator.contact_name_title`、`originator.established_date`、`originator.issued_reits`、`originator.legal_representative`、`originator.main_business`、`originator.registered_address`、`originator.registered_capital`、`originator.returned_projects`、`originator.short_name`。
- 表格、金额、日期、比例、主体名称及引文保持数据中间层原值；不得自行补数、改数或推断。
- 数据缺失或冲突时执行下方缺失处理，不得使用示例补齐。

## 执行流程

1. 读取当前小节的数据中间层快照，并确认主体、期间、单位和来源。
2. 严格按“输出结构与顺序”生成，不得遗漏固定段落、表格或循环主体。
3. 仅对叙述段落进行正式申报文体整理；确定性表格及事实值不得改写。
4. 完成后检查所有数字、日期、名称和结论均可回指当前项目来源。

## 写作规则

- 参考示例的正式申报材料文体、分层结构和财务分析方式
- 仅使用当前项目数据，不得复制示例中的名称、地址、日期、金额、结论或附件号
- 最近一期不是完整年度时不得与完整年度直接比较，原因没有底稿支持时只描述客观变化
- 示例只用于学习语言、结构和分析方式，禁止复制其中任何项目事实。
- 不得增加无底稿支持的原因、评价、结论或附件编号。

## 输出结构与顺序

- 按字段 `originator.company_name` 中的主体顺序循环；每个主体都必须完整执行全部输出结构，不得跨主体复用数据或结论。
1. 正文/标题段：`{{repeat.index}}.【{{originator.company_name}}】`
2. 正文/标题段：`（1）基本信息`
3. 正文/标题段：`以下表格信息通过天眼查查询后填写；表格中已经写明填写要求的，按照填写要求填写。`
4. 两列表格“表# 发起人（{{originator.company_name}}）基本信息”，字段顺序：公司名称、法定代表人、实际控制人、成立日期、注册资本、注册地址、主营业务、已发行基础设施REITs情况、最近12个月内申报的基础设施REITs项目被国家发展改革委退回情况、项目联系人姓名及职务、联系方式
5. 正文/标题段：`（2）财务状况`
6. 财务指标表“表# 发起人（{{originator.company_name}}）最近3个会计年度及一期主要财务指标”：按申报基准日绑定最近三年及一期
7. 正文/标题段：`{{finance.analysis}}`；仅当 finance.analysis 均有值时输出
   - 条件不满足时：`【待补充重大财务波动原因说明；在原因底稿缺失时仅描述客观变动】`
8. 正文/标题段：`（3）违法违规和信用情况`
9. 正文/标题段：`1）违法违规情况`
10. 正文/标题段：`{{originator.short_name}}近3年在投资建设、生产运营、市场监管、税务等方面{{compliance.violation_conclusion}}，{{compliance.safety_conclusion}}重大安全生产事故。{{compliance.commitment_date}}，{{originator.short_name}}出具《{{compliance.commitment_name}}》，就自身运营情况承诺：“{{compliance.commitment_quote}}”。相关情况详见附件{{compliance.commitment_attachment}}。`；仅当 originator.short_name、compliance.violation_conclusion、compliance.safety_conclusion、compliance.commitment_quote 均有值时输出
   - 条件不满足时：`【待补充或提取原始权益人运营情况承诺函及监管查询结果，不输出无违法违规结论】`
11. 正文/标题段：`2）信用状况`
12. 正文/标题段：`截至{{credit.cutoff_date}}，经查询{{credit.sites}}，基于查询结果，最近三年，{{originator.short_name}}{{credit.conclusion}}因严重违法失信行为被有权部门认定为失信被执行人、重大税收违法案件当事人或涉金融严重失信人的记录，详见附件{{credit.attachment}}。`；仅当 originator.short_name、credit.conclusion 均有值时输出
   - 条件不满足时：`【待更新或提取信用查询结果，不沿用方法论示例日期和结论】`

## 缺失与冲突处理

- 必填数据缺失时，保留机器配置中的待补充提示，不得删除提示后强行成文。
- 来源冲突时保留冲突事实并提示业务人员确认，不得自动选择或拼接。
- 最近一期并非完整年度时，不得直接与完整年度形成同比结论。

## 参考示例（仅参考写法，禁止取值）

以下主体、人员、地址、日期、金额和附件编号均为虚构，仅用于展示输出格式；正式生成时不得从#示例#区域取值。

1.【星河数字基础设施有限公司（虚构）】

（1）基本信息

表2-X 发起人（原始权益人1）基本信息

| 字段 | 填写内容 |
| --- | --- |
| 公司名称 | 星河数字基础设施有限公司（虚构） |
| 法定代表人 | 赵某 |
| 实际控制人 | 赵某 |
| 成立日期 | 2015年3月18日 |
| 注册资本 | 50,000万元 |
| 注册地址 | 华中某省某市高新区示范大道100号（虚构） |
| 主营业务 | 数据中心基础设施运营及技术服务 |
| 已发行基础设施REITs情况 | 无（虚构示例） |
| 最近12个月内申报的基础设施REITs项目被国家发展改革委退回情况 | 无（虚构示例） |
| 项目联系人姓名及职务 | （待定） |
| 联系方式 | （待定） |

（2）财务状况

星河数字2022年度、2023年度、2024年度及2025年1-6月财务报表按照企业会计准则编制。2022年至2024年度财务报表经某会计师事务所审计并出具标准无保留意见，2025年1-6月财务报表未经审计，各期数据采用合并口径。（以上均为虚构示例）

表2-X 发起人（原始权益人1）最近3个会计年度及一期主要财务指标

| （万元、%） | 2022年/2022年12月31日 | 2023年/2023年12月31日 | 2024年/2024年12月31日 | 2025年1-6月/2025年6月30日 |
| --- | --- | --- | --- | --- |
| 总资产 | 286,400.00 | 318,700.00 | 361,900.00 | 382,600.00 |
| 总负债 | 143,200.00 | 153,600.00 | 166,500.00 | 178,900.00 |
| 资产负债率 | 50.00 | 48.20 | 46.01 | 46.76 |
| 营业收入 | 68,500.00 | 79,800.00 | 91,600.00 | 49,300.00 |
| 净利润 | 8,200.00 | 10,100.00 | 12,400.00 | 6,700.00 |
| 息税折旧摊销前利润（EBITDA） | 18,600.00 | 21,900.00 | 25,300.00 | 13,800.00 |
| 经营活动产生的现金流量净额 | 13,100.00 | 15,400.00 | 17,900.00 | 8,600.00 |

虚构示例分析：2022年至2024年，星河数字资产规模、营业收入和净利润持续增长，资产负债率总体下降，经营活动产生的现金流量净额保持为正。2025年1-6月为非完整年度，不与2024年全年直接比较；如需解释指标波动，应读取对应业务说明底稿。

（3）违法违规和信用情况

1）违法违规情况

星河数字近3年在投资建设、生产运营、市场监管、税务等方面无重大违法违规记录，未发生重大安全生产事故。2025年7月8日，星河数字出具《星河数字基础设施有限公司关于自身运营情况的承诺函》（虚构文件），就自身运营情况作出相关承诺，相关情况详见附件4-X。

2）信用状况

截至2025年7月15日，经查询信用中国、国家企业信用信息公示系统和中国执行信息公开网，基于虚构示例查询结果，星河数字不存在相关严重违法失信记录，详见附件3-X。

## 机器执行配置（必须保留）

> 系统使用本 JSON 确定字段绑定、条件和输出块；正文说明用于指导 AI 写作。修改字段绑定或结构后需要重新提取数据。

```json
{
  "id": "3",
  "title": "（三）发起人（原始权益人）情况",
  "style_instructions": [
    "参考示例的正式申报材料文体、分层结构和财务分析方式",
    "仅使用当前项目数据，不得复制示例中的名称、地址、日期、金额、结论或附件号",
    "最近一期不是完整年度时不得与完整年度直接比较，原因没有底稿支持时只描述客观变化"
  ],
  "style_examples": [
    {
      "source": "Know-how #示例#",
      "reference_only": true,
      "content": "虚构示例分析：2022年至2024年，星河数字资产规模、营业收入和净利润持续增长，资产负债率总体下降，经营活动产生的现金流量净额保持为正。2025年1-6月为非完整年度，不与2024年全年直接比较；如需解释指标波动，应读取对应业务说明底稿。违法违规和信用情况分别成段说明，并分别引用承诺函和信用查询材料。"
    }
  ],
  "repeat_by": {
    "field_id": "originator.company_name",
    "separator_regex": "[、,，;；/\\n]+",
    "scoped_prefixes": [
      "originator.",
      "finance.",
      "compliance.",
      "credit."
    ]
  },
  "blocks": [
    {
      "type": "p",
      "template": "{{repeat.index}}.【{{originator.company_name}}】",
      "src_fields": [
        "originator.company_name"
      ]
    },
    {
      "type": "p",
      "template": "（1）基本信息",
      "src_fields": []
    },
    {
      "type": "p",
      "template": "以下表格信息通过天眼查查询后填写；表格中已经写明填写要求的，按照填写要求填写。",
      "src_fields": []
    },
    {
      "type": "kv",
      "caption": "表# 发起人（{{originator.company_name}}）基本信息",
      "src_fields": [
        "originator.company_name",
        "originator.legal_representative",
        "originator.actual_controller",
        "originator.established_date",
        "originator.registered_capital",
        "originator.registered_address",
        "originator.main_business",
        "originator.issued_reits",
        "originator.returned_projects"
      ],
      "rows": [
        {
          "field_id": "originator.company_name",
          "label": "公司名称"
        },
        {
          "field_id": "originator.legal_representative",
          "label": "法定代表人"
        },
        {
          "field_id": "originator.actual_controller",
          "label": "实际控制人"
        },
        {
          "field_id": "originator.established_date",
          "label": "成立日期"
        },
        {
          "field_id": "originator.registered_capital",
          "label": "注册资本"
        },
        {
          "field_id": "originator.registered_address",
          "label": "注册地址"
        },
        {
          "field_id": "originator.main_business",
          "label": "主营业务"
        },
        {
          "field_id": "originator.issued_reits",
          "label": "已发行基础设施REITs情况"
        },
        {
          "field_id": "originator.returned_projects",
          "label": "最近12个月内申报的基础设施REITs项目被国家发展改革委退回情况"
        },
        {
          "field_id": "originator.contact_name_title",
          "label": "项目联系人姓名及职务"
        },
        {
          "field_id": "originator.contact_details",
          "label": "联系方式"
        }
      ]
    },
    {
      "type": "p",
      "template": "（2）财务状况",
      "src_fields": []
    },
    {
      "type": "financial_grid",
      "caption": "表# 发起人（{{originator.company_name}}）最近3个会计年度及一期主要财务指标",
      "src_role_prefix": "audit_report_",
      "src_quote": "合并财务报表及附注"
    },
    {
      "type": "p",
      "if_all": [
        "finance.analysis"
      ],
      "template": "{{finance.analysis}}",
      "else_template": "【待补充重大财务波动原因说明；在原因底稿缺失时仅描述客观变动】",
      "src_fields": [
        "finance.analysis"
      ]
    },
    {
      "type": "p",
      "template": "（3）违法违规和信用情况",
      "src_fields": []
    },
    {
      "type": "p",
      "template": "1）违法违规情况",
      "src_fields": []
    },
    {
      "type": "p",
      "if_all": [
        "originator.short_name",
        "compliance.violation_conclusion",
        "compliance.safety_conclusion",
        "compliance.commitment_quote"
      ],
      "template": "{{originator.short_name}}近3年在投资建设、生产运营、市场监管、税务等方面{{compliance.violation_conclusion}}，{{compliance.safety_conclusion}}重大安全生产事故。{{compliance.commitment_date}}，{{originator.short_name}}出具《{{compliance.commitment_name}}》，就自身运营情况承诺：“{{compliance.commitment_quote}}”。相关情况详见附件{{compliance.commitment_attachment}}。",
      "else_template": "【待补充或提取原始权益人运营情况承诺函及监管查询结果，不输出无违法违规结论】",
      "src_source_role": "originator_commitment",
      "src_quote": "运营情况承诺函"
    },
    {
      "type": "p",
      "template": "2）信用状况",
      "src_fields": []
    },
    {
      "type": "p",
      "if_all": [
        "originator.short_name",
        "credit.conclusion"
      ],
      "template": "截至{{credit.cutoff_date}}，经查询{{credit.sites}}，基于查询结果，最近三年，{{originator.short_name}}{{credit.conclusion}}因严重违法失信行为被有权部门认定为失信被执行人、重大税收违法案件当事人或涉金融严重失信人的记录，详见附件{{credit.attachment}}。",
      "else_template": "【待更新或提取信用查询结果，不沿用方法论示例日期和结论】",
      "src_source_role": "originator_credit",
      "src_quote": "原始权益人信用记录查询结果"
    }
  ]
}
```

