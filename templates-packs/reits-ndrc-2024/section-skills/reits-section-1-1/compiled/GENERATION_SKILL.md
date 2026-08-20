# 1.1 （一）项目概况 · 生成 SKILL

> 这是本小节生成时实际读取的运行文件，由 Know-how 编译而来。Know-how 的版本、日期、修订人、审核人和状态不属于写作指令，已排除。

## 任务目标

使用当前项目已提取并通过来源定位的数据，生成“（一）项目概况”小节。不得读取 Know-how 原文，不得把方法论示例当作当前项目事实。

## 可用输入

- 只允许使用当前数据中间层中的字段：`const.NOTE`、`project.appraisal_net_value`、`project.asset_scope`、`project.building_area`、`project.cabinet_count`、`project.land_area`、`project.location`、`project.name`、`project.operation_start`、`project.scale_total`、`project.subproject_name`、`project.type`、`project.valuation_date`。
- 表格、金额、日期、比例、主体名称及引文保持数据中间层原值；不得自行补数、改数或推断。
- 数据缺失或冲突时执行下方缺失处理，不得使用示例补齐。

## 执行流程

1. 读取当前小节的数据中间层快照，并确认主体、期间、单位和来源。
2. 严格按“输出结构与顺序”生成，不得遗漏固定段落、表格或循环主体。
3. 仅对叙述段落进行正式申报文体整理；确定性表格及事实值不得改写。
4. 完成后检查所有数字、日期、名称和结论均可回指当前项目来源。

## 写作规则

- 参考示例的正式申报材料文体、事实组织顺序和量化表达
- 仅使用当前项目数据，不得复制示例中的名称、地址、日期、金额或结论
- 表格和事实字段保持原值，缺失信息按生成规则保留待补充提示
- 示例只用于学习语言、结构和分析方式，禁止复制其中任何项目事实。
- 不得增加无底稿支持的原因、评价、结论或附件编号。

## 输出结构与顺序

1. 正文/标题段：`本项目名称是{{project.name}}，标的资产为{{project.subproject_name}}，位于{{project.location}}。资产范围为{{project.asset_scope}}，总体建设规模为{{project.scale_total}}万元，土地面积为{{project.land_area}}㎡，建筑面积为{{project.building_area}}㎡，机柜共{{project.cabinet_count}}个，于{{project.operation_start}}开始运营。{{project.subproject_name}}于价值时点{{project.valuation_date}}的不动产评估净值为{{project.appraisal_net_value}}万元。本项目类型为{{project.type}}。`
2. 项目概况整表：从 `project_overview_table` 原样复制，不得重排、摘要或改写
3. 正文/标题段：`注：{{const.NOTE}}`

## 缺失与冲突处理

- 必填数据缺失时，保留机器配置中的待补充提示，不得删除提示后强行成文。
- 来源冲突时保留冲突事实并提示业务人员确认，不得自动选择或拼接。
- 最近一期并非完整年度时，不得直接与完整年度形成同比结论。

## 参考示例（仅参考写法，禁止取值）

本项目的基础设施资产为国金数据中心，位于江苏省苏州市昆山市花桥镇远创路558号。土地使用权面积19,999.50平方米，建筑面积35,376.70平方米，净机房面积15,181.00平方米，机柜数量4,192个，机柜电力设计容量29,044kw。基础设施资产于价值时点2024年9月30日的资产评估价值为21.95亿元人民币。本项目为拥有土地使用权的所有权类项目。

表1-1 项目概况（基准日2024年9月30日）

| 字段 | 填写内容 |
| --- | --- |
| 项目总体情况 |  |
| 项目名称 | 万国数据数据中心基础设施领域不动产投资信托基金（REITs）项目 |
| 所属基础设施REITs行业领域 | 新型基础设施 （数据中心类） |
| 子项目名称 | 国金数据云计算数据中心 |
| 资产所在地（明确到县区级） | 江苏省苏州市昆山市 |
| 资产范围（线性工程填写起止地点；非线性工程填写项目四至） | 国金数据云计算数据中心位于昆山市花桥镇远创路558号，东侧临远创路，南侧临空地、西侧邻万国数据昆山一期项目、北侧临河流，包含两栋建筑物的房屋所有权、其占用范围内的国有建设用地使用权及相关附属设备设施所有权（不含110kV变电站进线工程） |
| 建设内容和规模 | 土地面积为15,274.05㎡，建筑面积为42,076.98㎡，机柜共5,897个，机柜总功率42,530.84kW，建设规模为77,510.12万元，包括地下一层至地上七层的数据中心建筑，以及无法从建筑物剥离、与房屋使用功能相适应且影响建筑物使用价值的核心设备如供配电系统（包含高压柴油发电机组、UPS、蓄电池、机柜等）、暖通系统（包含蓄冷罐、冷却塔、冷冻水型精密空调、冷机、板式换热器、冷冻水一级泵、冷冻水二级泵、冷却水泵、新风机组等）、弱电系统（动环监控系统、视频监控系统、蓄电池监控系统、电气火灾系统等）等 |
| 开竣工时间 | 开工时间为2018年11月13日 竣工时间为2020年4月20日 |
| 决算总投资（万元） | 81,664.97（包含土建及及其设备投资金额，不含土地使用权出让金额） |
| 当期目标不动产评估值（万元） | 219,500.00 |
| 当期目标不动产评估净值（万元） | 219,500.00 |
| 运营起始时间 | 1号楼：2020年5月20日；2号楼：2020年7月1日 |
| 项目权属起止时间及剩余年限（剩余年限为权属到期年限与基准年限之差） | 2012年6月9日至2062年6月8日 剩余年限：37.68年（基准日为2024年9月30日） |

注：目标不动产评估净值=目标不动产评估值-基础设施基金直接或间接对外借入款项中拟用于基础设施项目收购的部分。

## 机器执行配置（必须保留）

> 系统使用本 JSON 确定字段绑定、条件和输出块；正文说明用于指导 AI 写作。修改字段绑定或结构后需要重新提取数据。

```json
{
  "id": "1",
  "title": "（一）项目概况",
  "style_instructions": [
    "参考示例的正式申报材料文体、事实组织顺序和量化表达",
    "仅使用当前项目数据，不得复制示例中的名称、地址、日期、金额或结论",
    "表格和事实字段保持原值，缺失信息按生成规则保留待补充提示"
  ],
  "style_examples": [
    {
      "source": "Know-how #示例#",
      "reference_only": true,
      "content": "本项目的基础设施资产为国金数据中心，位于江苏省苏州市昆山市花桥镇远创路558号。土地使用权面积19,999.50平方米，建筑面积35,376.70平方米，净机房面积15,181.00平方米，机柜数量4,192个，机柜电力设计容量29,044kw。基础设施资产于价值时点2024年9月30日的资产评估价值为21.95亿元人民币。本项目为拥有土地使用权的所有权类项目。"
    }
  ],
  "blocks": [
    {
      "type": "p",
      "template": "本项目名称是{{project.name}}，标的资产为{{project.subproject_name}}，位于{{project.location}}。资产范围为{{project.asset_scope}}，总体建设规模为{{project.scale_total}}万元，土地面积为{{project.land_area}}㎡，建筑面积为{{project.building_area}}㎡，机柜共{{project.cabinet_count}}个，于{{project.operation_start}}开始运营。{{project.subproject_name}}于价值时点{{project.valuation_date}}的不动产评估净值为{{project.appraisal_net_value}}万元。本项目类型为{{project.type}}。",
      "field_formats": {
        "project.asset_scope": {}
      },
      "src_fields": [
        "project.name",
        "project.subproject_name",
        "project.location",
        "project.asset_scope",
        "project.scale_total",
        "project.land_area",
        "project.building_area",
        "project.cabinet_count",
        "project.operation_start",
        "project.valuation_date",
        "project.appraisal_net_value",
        "project.type"
      ]
    },
    {
      "type": "overview_table",
      "caption_prefix": "表 1-1 ",
      "caption_fallback": "项目概况",
      "src_source_role": "project_overview_table",
      "src_quote": "项目概况表整表直接复制"
    },
    {
      "type": "p",
      "template": "注：{{const.NOTE}}",
      "src_fields": []
    }
  ]
}
```

