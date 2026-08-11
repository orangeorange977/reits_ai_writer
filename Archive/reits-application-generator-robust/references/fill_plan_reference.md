# fill_plan.json 参考手册（第二步用到时查，不必预读）

配套脚本：`scripts/fill_docx.py`（完整参数见其文件头注释）。

## 一、支持的操作

| 操作 | 说明 |
|---|---|
| `chapter`（顶层） | **本 plan 所属章的中文序号**（`"二"`~`"七"`）。预检据此校验每个 `paragraphs` 条目命中的段落都落在该章内，跨章误命中报 ERROR。按章撰写的 fill_plan 应当声明 |
| `paragraphs` | 段落替换。`match` 为**子串包含匹配**（取原文前15~25字准确子串即可命中），`replace` 为新内容，`occurrence` 指定第几处（默认1），`section` 声明所属小节（见第一之二节），`style`/`styles` 声明段落样式（见第一之三节） |
| `rebuild_tables` | **表格重建（ch2 及之后章节表格的首选路径）**：按表标题段文字定位 → 读旧表按字段名合并已填内容 → 按数据整表新建替换。行数/列数/合并由数据决定，不碰 R#C# 坐标与 table_index。详见第三之二节 |
| `tables[].cells` | 填单元格：`{"row": N, "col": M, "text": "..."}`（0-based） |
| `tables[].append_rows` | 追加行（复制最后一个非合并行的格式，并自动解除纵向合并延续） |
| `tables[].insert_rows` | 在指定行之后插入新行（比 append_rows 灵活，同样自动解除纵向合并） |
| `tables[].delete_rows` | 删除指定行（如模版的「……」占位行） |
| `tables[].merge_cells` | 跨列/跨行合并：`{"row":R,"from_col":A,"to_col":B,["to_row":R2],["text":"合并后文字"]}`。用于表22 交易环节小标题行、阶段小计/合计标签跨列 |
| `tables[].delete_table` | 删除整张表（`true`）。**会使其后所有 table_index 减1**，仅限最后一批（phase6）使用；同批多个删除按索引降序自动执行 |
| `tables[].clean_headers` | 清理表头提示语（去「（如有）」等） |
| `replace_ranges` | 范围替换：从 `start_match` 到 `end_match` 之间整段替换，`delete_tables` 控制是否删范围内表格；`clear_tables_if_not_deleted`（默认true）在 delete_tables=false 时清空并移除残留表格；`to_end`（默认false）显式声明后允许删到文档末尾（仅用于清理文末附件模版） |
| `insert_tables` | 在指定段落后插入全新表格（补模版缺失的表、或重建结构不匹配的模版表）。支持 `merge_cells`（新建表无历史合并，可自由合并） |
| `insert_image_placeholders` | 在指定段落后插入图片占位框（1×1带边框表格），支持 `width_cm`/`height_cm` |
| `insert_paragraphs` | 在指定段落后（`after_paragraph`）或指定表格后（`after_table_index`）插入独立段落。主要用于窄表的「表下注」来源标注 |
| `citations` / `citation` | **来源标注**：`paragraphs[]`/`replace_ranges[]` 用 `citations`（数组），`tables[].cells[]`/`insert_tables[].cells[]` 用 `citation`（单条）或 `citations`。脚本按 `templates/citation_rules.json` 渲染成括注写入文本，详见第八节 |

**执行顺序（脚本内部固定）**：
1. `paragraphs`
2. `rebuild_tables`（原位替换不改表数，不影响存量 `table_index`；`create_after` 新建会增表——预检已拦「同 plan 混用 rebuild 新建 + 位置在其后的 table_index」）
3. 每张表内部：`delete_rows` → `insert_rows` → `append_rows` → `cells` → `merge_cells` → `clean_headers`
   ——即**先调整行结构、后写单元格**，所以 `cells`/`merge_cells` 的 `row` 必须按**结构调整后的最终表**计算
4. `delete_table`（延迟到本批全部表处理完，按索引降序）
5. `insert_tables` / `insert_image_placeholders` / `insert_paragraphs`
6. **`replace_ranges` 最后**（这样范围删除不会让前面的 `table_index` 偏移）


⚠️ **纵向合并（vMerge）陷阱**：官方模版表15（序号/手续名称按9大类纵向合并）、表22（阶段列按阶段纵向合并）里，合并块的行数是写死的。原地插行时新行若继承 vMerge 延续，会出现①写入该格的文字实际落到合并区首格 ②后续跨列合并报 `requested span not rectangular`。fill_docx 已在 append/insert 时自动解除 vMerge；但**合并块与实际数据行数对不上**这件事无法靠插行解决 → 这类表一律走 `rebuild_tables` 整表重建（见第三之二节；旧的 insert_tables+delete_table 配方见第四节，仅存量路径适用）。

**`\n` 的行为**：`paragraphs` / `replace_ranges` 的 `replace` 中的 `\n` 会被 fill_docx.py **拆分为多个独立段落**（新段落继承被替换段落的样式/缩进/编号，计入 validate_output 的段数统计）；连续 `\n` 产生的空行自动跳过。长内容分段直接用 `\n` 即可，无需拆成多个条目。例外：表格单元格（`tables`/`insert_tables` 的 `text`）中的 `\n` 仍是同单元格内软换行。

## 一之二、锚点纪律与章节归属（防「内容写进错误小节」）

**这是第五章的头号历史事故**：ch5 的 fill_plan 有 70 个 `paragraphs` 条目，而模版本章只有 **30 段可替换的指导文字**（另 30 段是小节标题/表格标题/表下注）。条目超订 2.3 倍 → 子agent编造 `match` → 宏观政策段落命中并覆盖了（二）投资管理手续说明段。

之所以能一路蒙到交付，是因为三道防线都有盲区：

| 环节 | 原来的盲区 | 现在 |
|---|---|---|
| 预检 | 只问「能不能找到」，不问「找到的是不是该找的那一段」→ `issues=0` | 新增 `chapter`/`section` 归属校验、歧义 match 校验、顺序逆序 WARN |
| 执行 | 同段冲突条目被丢弃，只记进 `failures`，仍 `exit=0` | 段落类失败 → **`exit=1`**（文档照常落盘便于核对） |
| 第三步校验 | 关键词只查「全文有没有」；整章段数/字数达标即 PASS | 要素改按**章内文本**判断；新增**第五章分节篇幅门槛** |

### 三条硬规则

1. **条目数 ≤ 模版本章可替换段数**。条目数超了就是在编造 `match`。各章可替换段数见 `templates/chapter_examples/manifest.json`（ch5 已标 `replaceable_paras: 30`）。
2. **一节的多段内容用 `\n` 装进同一个条目的 `replace`**，不要为凑段数而多开条目。「30 个条目写出 70+ 段」是正确形态。
3. **声明 `chapter` + `section`，让脚本替你把错位拦下来**。

```json
{
  "chapter": "五",
  "paragraphs": [
    {"match": "（2）以协议出让方式取得土地使用权的所有权类项目",
     "section": "（三）3",
     "replace": "本项目属于以协议出让方式取得土地使用权的所有权类项目。\n2010年3月12日……\n2024年5月22日……",
     "citations": [{"type": "material", "attachment_no": "15-3", "anchor": "2024年5月22日"}]}
  ]
}
```

- `chapter`：章中文序号，`"五"` / `"五、项目合规情况"` / `5` 均可，脚本会归一化。
- `section`：小节路径，`"（一）"`（节级）或 `"（三）3"`（节内编号级）。`"(三)3."` 这类半角/带尾点写法也接受。
- 脚本按文档里的 `^([一二三四五六七]+)、` 切章、`^（[一二三四五六七八九十]+）` 切节、`^\d+[.、]` 切节内编号，**与 `validate_output.py` 同口径**。
- 两个字段都**可选**（不写则跳过对应校验，旧 plan 完全兼容），但不写就等于放弃这道闸门；预检会打 WARNING 提醒。

### 预检新增的四类问题

| type | 级别 | 触发条件与含义 |
|---|---|---|
| `paragraph_section` | ERROR | 命中段落不在声明的 `chapter` 区间内，或不属于声明的 `section` —— **内容会被写进错误章节/小节** |
| `paragraph_ambiguous` | ERROR | `match` 命中多处却未写 `occurrence`。脚本会静默取第一处，这是错位主因。修法：把 `match` 加长到全文唯一，或显式写 `occurrence` |
| `paragraph_conflict` | ERROR | 多个条目命中同一段，只有第一个生效，其余内容**被丢弃**。修法：合并进一个 `replace`（用 `\n` 分段） |
| `paragraph_order` | WARN | 后写的条目命中了明显更靠前的段落（逆序>3段），通常意味着 `match` 选错。不阻断，但要核对 |

预检输出把 ERROR 放 `detail`、WARN 放 `warn_detail`，**只有 ERROR 会让 `--validate-only` 退出码为 1**。

### 已知的 ch5 歧义陷阱

`manifest.json` 的 ch5 条目里有完整清单（`$ambiguous_matches`）与 31 个锚点对照表（`anchor_map`）。最坑的三个：

- `1.总体情况` 在（三）1 与（四）1 **完全重复出现两次**，绝不能当 `match`
- （三）3 的（1）划拨 与（2）协议出让两段**尾部完全相同**，`match` 必须含开头的情形描述
- `说明发起人（原始权益人）` 这 12 字**同时命中 4 段**（连廊/经营收益权类/承诺函/税收）

## 一之三、段落样式（`style` / `styles` / `auto_heading`）——防「正文变成大号加粗标题」与「层级塌平」

段落替换**只改 `run.text`**，`<w:pPr>`（含 `<w:pStyle>`）原样保留；`\n` 拆段时还会 `copy.deepcopy` 整个 `<w:p>`。所以：

- 锚点若是 Heading 样式（**初稿基底常见**，或子agent误拿小节标题当 `match`），替换出的正文**全部带标题样式** → Word 里大号加粗、层级分不清，并把几十段正文塞进导航窗格与自动目录；
- `\n` 合并写法会把「1 段被污染」放大成「N 段被污染」；
- 反过来，**模版里没有的小标题也不会自己出现**——必须由 `replace` 新建；新建的标题默认仍是正文样式（这就是「层级塌平」，第六章 4 个小节标题全落成 Normal 的实测事故），由 `auto_heading` 兜住。

### 三个字段

| 字段 | 类型 | 作用 |
|---|---|---|
| `style` | 字符串 | 本条**所有**分段统一套该样式。典型用途：只改标题文字、保持标题样式 |
| `styles` | 数组 | 与 `replace` 按 `\n` 拆出的**非空分段一一对应**；元素为样式名或 `null`（`null` = 按 `auto_heading` / 默认规则）。典型用途：一个 `replace` 里既有小标题又有正文 |
| `auto_heading` | 字符串 | **默认 `"h2"`**：按文本形态把「（一）xxx」这类新建的小节标题分段自动升为 `Heading 2`（判定收得很紧：长度 ≤40 且不以句读/引号/右括号收尾）。`"h2+h3"` 另认「1.xxx」→ `Heading 3`；`"off"` 关闭。**第六章的 4 个小节标题全靠它**，⛔ 不要写 `"off"` |

`replace_ranges` 同样支持这三个字段。

### 优先级与默认规则

1. `style` 给定 → 所有分段统一套它；
2. 否则 `styles[i]` 给定 → 第 i 个分段套它；
3. 否则 `auto_heading`（默认 `"h2"`）按文本形态判定；
4. 都不命中 → 首段保留源段落样式；**首段（套完样式后）是标题样式时，其后 `\n` 拆出的段落一律降为正文**——一个标题下不可能跟 N 个同级标题。正文样式按 `Normal` → `正文` → `Body Text` 依次尝试，都不存在则直接移除 `<w:pStyle>` 回落到文档默认样式。（被 `auto_heading` 升级的标题分段之后的正文段同样会自动降回正文，不会连锁继承标题样式。）

### 写法示例

新建模版没有的 H3（第五章（六）需要 `1.承诺函出具情况`、`2.项目涉税情况`，模版里 0 个 H3）：

```json
{"match": "说明发起人（原始权益人）对项目依法依规缴纳税收",
 "section": "（六）",
 "styles": ["Heading 3", null, null],
 "replace": "1.承诺函出具情况\n2024年12月31日，原始权益人……出具承诺函，承诺：“……”。\n相关情况详见附件21-1。"}
```

只改标题文字、保持标题样式（清理（三）2 标题里的 `（填写表19）`——它是交付校验的 FAIL 关键词，藏在 Heading 3 里）：

```json
{"match": "2.土地使用手续办理情况（填写表19）",
 "section": "（三）2",
 "style": "Heading 3",
 "replace": "2.土地使用手续办理情况"}
```

### 预检新增的样式问题（`type: paragraph_style`）

| 触发条件 | 级别 | 含义 |
|---|---|---|
| `match` 命中**标题样式**段落，但未声明 `style`/`styles` | ERROR | 替换后正文会带标题样式。要么显式声明（标题清理场景），要么换用该小节内的指导文字段落作锚点 |
| `style`/`styles` 里的样式名文档中不存在 | ERROR | 运行时会被忽略，静默失效。报错信息会列出文档实有的标题/正文样式名 |
| `styles` 长度 > `replace` 的非空分段数 | ERROR | 多出的项不会生效，说明分段与样式对不上 |
| `styles` 不是数组 | ERROR | 整条统一样式应用 `style` |
| `style` 是标题样式且 `replace` 有多个分段 | WARN | `style` 会让**全部**分段变标题；只想给部分分段套标题请改用 `styles` |
| `auto_heading` 取值不在 `h2`/`h2+h3`/`off` 内 | ERROR | 会被回落到默认 `"h2"`，但先报错以免误以为已生效 |
| `auto_heading:"off"` 却有标题形态的分段 | WARN | 这些分段会保持正文样式，导航窗格/自动目录里看不到层级；除非确有理由，删掉该字段用默认值 |
| 本条将有分段被自动升为 `Heading 2` | INFO | 出现在 `--validate-only` 输出的 `info_detail`（不阻断），用于人工核对升级的是不是真标题 |

### 交付前的样式审计

`validate_output.py` 新增三项：

- **`正文误用标题样式`**：净字数 > 45 却套 Heading 的段落（模版最长的真标题是「（五）政府和社会资本合作（PPP）项目合规情况」= 26 字）。超过 3 段即 FAIL —— 这是「Word 里全是大号加粗」的直接检测项。
- **`第五章层级[（一）~（六）]`**：按**样式**核对每个 H2 下的 Heading 3 个数（期望 5/5/3/3/0/2，见 `manifest.json` 的 `$expected_outline`）。（一）（六）模版里没有 H3，若子agent没用 `styles` 新建，这里会 FAIL。
- **`第六章层级[（一）~（四）]`**：按**样式**核对 4 个小节标题是否为 `Heading 2`。本章模版锚点段是 Normal、标题全靠 `\n` 新建 → 若 fill_plan 写了 `auto_heading:"off"` 或文档标题样式名不同，这里会 FAIL（实测事故：4 个全落成 Normal，而整章段数/字数完全达标）。


## 二、表格定位

`tables[].locate` 支持：
- `table_index`（0-based序号，优先级最高）——**跑 `scripts/read_chapter.py` 从实际填充基底现读**（`docx_outline.json` 里的是官方模版序号，用初稿作基底会整体偏移）。按章撰写的表格**不该手写这个字段**，交给 `md_table_to_fill_plan.py` 算
- `title_keyword`（表格上方标题段落文本）——**必须用完整标题**如 `"表3  项目公司基本信息"`，不要用 `"表3"`（会命中"表30"）
- `header_hint`（表格首行应含的关键词）——**强烈建议每个表都填**，作为二次校验防误命中

```json
{
  "locate": {"title_keyword": "表5  发起人（原始权益人）最近3个会计年度及一期主要财务指标", "header_hint": "总资产"},
  "cells": [{"row": 1, "col": 1, "text": "..."}]
}
```

## 三、字段填写规范

1. **行业领域**：发改委分类表述，格式「大类（子类）」。常见映射：数据中心/智算中心→「新型基础设施（数据中心类）」；收费公路→「交通基础设施（收费公路）」；产业园→「园区基础设施」；仓储物流→「仓储物流」；污水/垃圾处理→「生态环保」；清洁能源→「能源基础设施」
2. **建设规模合计（万元）**：填**金额**（对应"决算总投资"口径），不是建筑面积或机柜数
3. **数字格式**：金额保留2位小数 + 千分位逗号（`757,700.00`）
4. **财务数据单位换算**：审计报告金额单位通常是「元」，填表须换算为「万元」（÷10000，保留2位小数）。常见错误：把 23,400,737,830.00元 填成 23,400,737.83（应为 2,340,073.78 万元）
5. **评估净值**：无对外借款时 = 评估值；有借款时 = 评估值 − 借款金额；材料未提借款则默认 = 评估值
6. **发起人**：原始权益人自身即为发起人时填「-」（标准答案惯例）
7. **原始权益人**：含全部原始权益人，多个用顿号分隔
8. **律师事务所**：格式「XX律师事务所，主办律师：姓名1、姓名2」
9. **上市场所**：按基金管理人所在交易所（南方基金→深圳证券交易所；华夏/嘉实→上海证券交易所），**不可默认填上交所**
10. **子项目名称**：不带「栋」字（「A-7数据中心」而非「A-7栋数据中心」），与评估报告原文一致
11. **运营起始时间**：精确到日（「2021年4月11日」），只填年份属精度不足，须从评估报告/权属材料查
12. **资产所在地**：摘要表填到区/县级（「河北省廊坊经济技术开发区」），表1中可更简略
13. **子项目标题格式**：模版「子项目1」应改为「子项目 1」（数字前加空格）
14. **表头处理**：摘要表的「资产所在地」「资产范围」需去掉提示语「（明确到县区级）」「（线性工程填写起止地点…）」；**表1中相同字段的提示语保留**（仅逗号改分号）。改表头用 `cells` 直接写 `{"row": N, "col": 0, "text": "新表头"}`
15. **占位行清理**：表1的「……」行用 `delete_rows` 删除，不得保留
16. **扩募子项目2**：先 `append_rows` 追加数据行，**再** `delete_rows` 删「……」行（顺序反了会复制到占位行格式）
17. **附件编号**：正文引用附件必须填实际编号（如「附件1-4-1」），不得残留 `{附件编号}`

## 三之二、表格重建（rebuild_tables）—— ch2 及之后章节表格的首选路径

「操作态填空」（cells/insert_rows/delete_rows + table_index）要求撰写方计算 R#C# 坐标与删插行顺序，是表格错位/返工的总根源。`rebuild_tables` 换成**结果态**：声明表的最终内容，行数/列数/合并由数据决定——vMerge 陷阱、行号位移、table_index 偏移、phase6/phase7 顺序铁律对本操作全部失效。ch1 三张表（摘要表/表1/表2）维持现有 `md_table_to_fill_plan --emit cells` 路线不动。

```json
{"rebuild_tables": [{
  "locate": {"title_keyword": "表5  发起人（原始权益人）最近3个会计年度及一期主要财务指标", "occurrence": 1},
  "create_after": "（2）财务状况",
  "caption": "表#  发起人（原始权益人2）最近3个会计年度及一期主要财务指标",
  "mode": "kv",
  "rows": [{"label": "公司名称", "value": "……", "citation": {"type": "material", "attachment_no": "3-1"}}],
  "merge_existing": true,
  "citations": [{"type": "material", "attachment_no": "25-1", "doc_name": "资产评估报告"}]
}, {
  "locate": {"title_keyword": "表3  项目公司基本信息"},
  "mode": "grid",
  "headers": ["阶段", "税种", "应纳税额（万元）", "备注"],
  "rows": [[{"text": "资产重组", "rowspan": 2}, "增值税", "1,000.00", "免税"],
           ["契税", "0.00", "/"],
           [{"text": "合计", "colspan": 2}, {"text": "1,000.00", "colspan": 2}]]
}]}
```

### 定位与新建

- `locate.title_keyword`：表标题段文字（**命中的段落后面必须紧跟表格**，天然跳过正文里提及表名的句子）；`occurrence` 1-based；预检会报子串歧义（如「表3」命中「表30」）。
- `create_after`：locate 找不到时的建表锚（多主体副本/模版缺失表用），在该锚点段后新建 caption 段+表；不给则找不到即 failure（exit=1）。
- `caption`：改写/新建表标题段，**编号一律写「表#」**，交付前由 `renumber_tables.py` 全篇重排（见下）。
- `mode`：`kv`（rows=[{label,value}]，左字段右值）或 `grid`（headers + rows，单元格可带 `{text,colspan,rowspan}`）。
- `citations`：渲染为表后 table_note（挂新表实体，无索引依赖）；kv 行/grid 可用 `citation` 短式进指定列。

### 续填语义（merge_existing，默认 true）

重建前读旧表，旧格**非占位**内容一律胜出（占位判定：空/纯空白、「……」、含「【待填写」「【需人工填写」、「（待定）」、官方模版提示语，清单见 `table_match.py` 的 `PLACEHOLDER_PATTERNS`）：

- **kv 合并**：新行 label 三级模糊匹配旧表左列（同名标签按出现序对齐，表10 「每家中介7行一组」不串组）；旧值非占位且与新值不同 → 旧值胜记 `conflict`；新值占位 → 旧值补入记 `filled_from_doc`；旧表独有已填行 → 追加末尾记 `appended`（预检 WARN）。最终行序=新数据声明序。
- **grid 合并**：行键=首列值（`merge_options.key_cols` 可改单列/复合键，如表22 的 `[0,1]`=阶段+税种）、列对齐=表头名；同键行（rowspan 展开后行键重复）按出现序对齐；旧独有已填行插在「合计」行前。**旧列无法映射到新列且该列有已填内容 → 该表放弃重建、原表不动、报 ERROR**——宁可不动也不静默丢内容。
- `merge_options.scaffold_headers`：声明哪些列的模版预印文字不算已填（表15/22 预印了手续菜单/税种骨架，不声明会被当成已填行压掉新数据）。
- `merge_existing: false` = 无条件重建，**仅官方模版基底可用**。
- 所有 kept/conflict/appended 决策进执行报告（stdout + `--report-json`）；有 conflict 不阻断（旧值已胜出，属人工可核对项）。

### 预检（--validate-only）与约束

- title_keyword 命中检查（含 occurrence、子串歧义）、`create_after` 锚存在性、kv 字段名 dry-run 匹配率、合并冲突预览、grid 表头映射检查。
- ⛔ 同一张表禁止在同一个 plan 里既走 `tables[]` 又走 `rebuild_tables`（预检 ERROR）。
- rebuild 新建（create_after）与存量 `table_index` 同 plan 混用：所有新建锚点位于全部 table_index 目标之后 → INFO 放行；否则 ERROR。
- 定位失败/合并放弃 → `failures` + **exit=1**（与段落类失败同级）。

### 表号统一重排（renumber_tables.py）与正文纪律

交付前最后一步（所有章节填充、rebuild 完成之后）执行：

```bash
python scripts/renumber_tables.py --input <基底稿.docx> --output <编号后.docx>
python scripts/renumber_tables.py --input <基底稿.docx> --dry-run   # 只列映射不落盘
```

全篇按表格出现顺序把 caption 的 `表#`/`表N` 重排为 表1、表2、表3…（跨章连续），只改编号数字不动样式，重复执行幂等。配套正文纪律：**生成侧一律写「下表」「如下表所示」，禁止硬编码表号**；`validate_output.py` 会检查非 caption 段出现 `表\d+` 引用打 WARN、caption 编号需连续无重复。

### 两条产出通道

| 通道 | 适用 | 入口 |
|---|---|---|
| 蓝图自动生成 | phase2/4/5 结构化数据表 | `gen_phase_fill_plan.py --blueprint templates/phaseN_blueprints.json`（蓝图 `type: table_rebuild`，支持 headers data_dep、row_sets foreach、多主体自动展开：第1个 locate 官方表、第2个及以后自动 create_after + caption 带主体序号） |
| 子agent Markdown 表 | ch2~ch7 子agent产表 | `md_table_to_fill_plan.py --md <章节.md> --emit rebuild`（kv/grid 自动识别；grid 数据行支持合并语法 `^`=上合并、`<`=左合并；子agent只写 Markdown 表 + 「表# 标题」行） |

数据源为空时蓝图侧产出骨架表（一行跨列【待填写：…】+ todo），由 rebuild_tables 承载，不再悬空。

## 四、模版缺失表格的补全（insert_tables）【仅存量路径适用】

> ⚠️ 本节及四之二/四之三描述的是**旧路径**：phase2/4/5 蓝图已全部迁移到 `rebuild_tables`（见第三之二节），新增表格一律走重建范式。以下内容保留供存量 fill_plan 回溯与回退使用，新写的 plan 不要再用。

官方模版固定表格数量常不足以承载项目数据，用 `insert_tables` 在指定段落后插表：

```json
{
  "insert_tables": [
    {
      "after_paragraph": "表14  项目最近3个会计年度及一期收益情况",
      "rows": 8, "cols": 8,
      "cells": [{"row": 0, "col": 0, "text": "类别（万元）"}, {"row": 0, "col": 1, "text": "2023年"}],
      "style": "Table Grid"
    }
  ]
}
```

常见需补全场景：
- **运营收益章节**：模版仅表12(历史经营)+表13(历史vs未来对比)，通常还需分栋经营表、收入来源表、客户集中度表
- **资产估值章节**：需补估值参数表（机柜单价/功率）、市场法比较表、设备价值明细表、资本性支出预测表
- **投资管理手续**：模版仅表15(通用手续)，扩募项目可能需行业特有手续表、手续不涉及表
- **税收处理**：模版仅表22，实际需按阶段（资产重组/股权转让/基金发行）分别列表

可选插入表的结构定义见 `templates/docx_outline.json` 的 `optional_tables` 段（释义表、荣誉奖项表、客户集中度表、估值比较因素表）。

### 四之二、结构不匹配模版表的「整表重建」（insert_tables + delete_table）【仅存量路径适用，新写走 rebuild_tables】

当模版表的**列数**或**纵向合并结构**与实际产出对不上时，插行/填格都救不回来，必须重建。第五章有三张：

| 表 | 模版结构 | 实际需要 | 原因 |
|---|---|---|---|
| 表15 项目投资管理手续 | 26行×8列，C0/C1 按9大类 vMerge，另有1行 gridSpan=5 | 行数随手续份数变（标准答案33行） | 合并块行数写死，插行必错位 |
| 表16 特定行业手续 | 21行×**3**列（行业↔手续对照参考表） | 按表15格式的**7**列实际手续表（标准答案2行×7列） | python-docx 不能改列数 |
| 表22 拟纳税情况 | 11行×8列，C0 按阶段 vMerge | 50行，含交易环节跨列小标题行+阶段小计+合计 | 合并结构与行数都不匹配 |

这三张由 `gen_phase_fill_plan.py --blueprint templates/phase5_blueprints.json` 自动生成到 **`fill_plan_phase5.phase6.json`**（蓝图条目标了 `apply_stage: phase6`），配方形如：

```json
{
  "insert_tables": [
    {"after_paragraph": "表15  项目投资管理手续情况", "rows": 39, "cols": 8, "style": "Table Grid",
     "cells": [{"row": 0, "col": 0, "text": "序号"}, "…每行每列…"],
     "merge_cells": [{"row": 13, "from_col": 3, "to_col": 7, "text": "招拍挂出让"}]}
  ],
  "tables": [{"locate": {"table_index": 15}, "delete_table": true}]
}
```

⚠️ **应用顺序铁律【仅存量 phase6/phase7 产物适用；rebuild_tables 路径下本铁律对表格失效】**：`delete_table` 会让其后所有 `table_index` **减1**，`insert_tables` 会让其后所有 `table_index` **加1**，所以

```
各章 fill_plan（含 fill_plan_phase2/phase4/phase5.json 里按 table_index 定位的批次）
  → fill_plan_annex.json（附件1/附件2 清理，靠段落锚点，不受表索引影响）
  → fill_plan_phase5.phase6.json（整表重建：insert_tables + delete_table，-1）
  → fill_plan_phase4.phase7.json（**最后**！第四章 12 张新表 + 表4-1 重建，+1）
```

**`phase6` 先、`phase7` 后**这个次序也是硬的：phase7 的新表全部用段落锚点定位、不依赖 table_index，所以放最后安全；反过来把 phase7 提前，phase6 里按 table_index 删表就会全部错位。

`insert_tables` 用段落锚点定位、不依赖 `table_index`，所以「先插新表、再删原表」是安全的；锚点必须用**完整表标题**（如 `"表15  项目投资管理手续情况"`，两个空格），用 `"表15"` 会命中前面指导文字里的「（填写表15）」。

### 四之三、`apply_stage: phase7` —— 模版缺失表的批量新建（第四章）【仅存量路径适用，phase4 蓝图已迁 rebuild_tables，phase7 产物现为空】

`build_stage6_entry` 同时支持两种用法，靠 `target.table_index` **有无**区分：

| 用法 | 蓝图写法 | 产出 |
|---|---|---|
| **整表重建** | 有 `table_index` | `insert_tables` 新建 + `tables[].delete_table` 删原表 |
| **纯新建**（模版没有这张表） | **不写** `table_index` | 只有 `insert_tables` |

第四章标准答案 15 张表、模版只有 3 张 → `phase4_blueprints.json` 里 12 条是纯新建、1 条（表4-1）是整表重建，全部标 `apply_stage: "phase7"`，脚本单独输出到 `fill_plan_phase4.phase7.json`。

新增的表头能力（两种 stage 都可用）：

```json
{
  "new_table_header_rows": [
    ["客户名称", "接受服务数量", "数量占比", "贡献收入情况", "", "…"],
    ["", "", "", {"data_dep": "operating_performance.period_labels.history[0]"}, "…"],
    ["", "", "", "金额", "占比", "…"]
  ],
  "header_merges": [
    {"row": 0, "from_col": 3, "to_col": 10, "text": "贡献收入情况"},
    {"row": 0, "from_col": 0, "to_col": 0, "to_row": 2, "text": "客户名称"}
  ],
  "data_start_row": 3,
  "new_table_cells": [{"row": 0, "col": 12, "text": {"data_dep": "..."}}]
}
```

- **表头单元格文本支持与 `rows_map` 相同的四种写法**（纯字符串 / `{"value"}` / `{"data_dep","format"}` / `{"template","vars"}`）→ 年份这类随基准日变化的表头可以用 `data_dep` 取，不必写死。
- `header_merges` 支持 `to_row`（纵向合并），第四章表4-4 的 3 行表头 + 5 列跨 3 行就靠它。
- `data_start_row` 默认等于表头行数。
- **数据源为空时改插「骨架表」**（默认行为，`--no-placeholder-tables` 关闭）：表头照常 + 一行跨列的 `【待填写：本表数据缺失——extracted_data 的 <path> 为空…】`，`.todo.json` 记 `table_new_placeholder`，终端逐张播报。理由见下方事故复盘。**骨架表不是交付形态**：要么补数据重跑，要么连同正文表标题一并删除。

### 四之三之一、事故复盘：「正文提到 15 张表，实际只有 3 张有表格实体」

现象：第四章正文写了 表4-1~表4-15 十五个表标题，交付稿里只有 表4-1/4-2/4-3 有表格，其余 12 张**只有标题文字、没有表格结构**。

两层缺失叠加：

| 层 | 内容 |
|---|---|
| **① 模版本身没有这些表** | 2024年版格式文本第四章只有 3 个表格（表12/13/14）。「（四）资产估值情况」等小节只给了指导文字（「说明项目当期目标不动产评估净值，以及估值方法、主要估值参数和参数选取的合理性」），没有预留 表4-4~表4-15 的表格结构 → 这 12 张只能由 phase7 的 `insert_tables` **按 ch4 子agent写的表标题段作锚点**新建 |
| **② extracted_data 缺评估报告详细数据** | 评估报告（附件28-x）有数十页，但提取阶段只读了前 3 页摘要 → `evaluation` 段只有报告编号/评估机构/评估总值/单价/面积/机柜数等基础字段，`operating_performance.valuation_params.*`（现金流预测、运营费用参数、资本性支出明细、可比实例、客户财务数据、CPI）全空 → 蓝图脚本对这些表整表跳过 |

现在四道闸门（任一报出都不许绕过）：

1. **第一步输入体检**（`pipeline_state.py`）：`STRUCT_SOURCES` 补入 `operating_performance.annual_rows` 与 `valuation_params`（此前漏列，所以缺口在第一步一句都不报），并新增**第四章 15 张表逐表数据源体检**，打印「可生成 N/15 张」+ 逐表指名缺哪个字段、取自哪份材料。
2. **生成期**（`gen_phase_fill_plan.py`）：数据源为空的新建表改插骨架表（`table_new_placeholder`），终端逐张播报，正文表标题不再悬空。
3. **应用期**（`fill_docx.py`）：`insert_tables` 等结构操作失败 → **exit=1 + `structure_not_applied`**，并打印「锚点段落不存在」的修法。此前只有段落丢失才 exit=1，插表全失败也 exit=0。
4. **交付校验**（`validate_output.py`）：新增 `表标题配对`（每个「表/图 X-Y …」标题段后必须紧跟表格实体，允许夹 ≤2 段短说明如「单位：个、万元、%」）与 `表格实体[四]`（第四章表格数 ≥12）。这两项是该事故的唯一检测手段——事故状态下段数/字数/要素/占位符全部 PASS。

### 四之四、模版表的表头/行结构改写（`header_cells` / `insert_rows`）

`table_fill` 与 `table_rowset` 都支持 `target.header_cells`：

```json
{"header_cells": {
  "R0C1": {"data_dep": "operating_performance.period_labels.history[0]"},
  "R0C4": {"data_dep": "operating_performance.period_labels.base"}
}}
```

这一条治的是**占位式表头**：官方模版第四章表12/13/14 的表头是「第n-3年」「第n-2年」…「收入类型1」，不换成实际年份/实际科目名，整张表填了数也读不懂（实测事故：交付文档里 3 张表表头全是「第n-x年」）。取不到值时**保留模版原文**并进 `.todo.json` 的 `table_header_unresolved`（宁可留占位让校验抓到，也不写成空白）。

`table_fill` 现在也支持 `insert_rows` / `append_rows` / `delete_rows`（条目可带 `when_exists`/`when_missing` 条件门）。⚠️ 但**模版表里有「单格跨全部列」的分节标题行时不要原地插行** —— 第四章表12 的 r1/r6 就是这种行，插行会把数据挤进合并格（python-docx 把该行所有列映射到同一个 tc，最后写入的值覆盖前面的），实测读回来是「行6 | -0.04%」这种烂格。这类表一律走整表重建（表4-1 即如此）。

## 五、图片占位（insert_image_placeholders）

需插图位置（法律关系图、产品架构图、资金流向图等）不自行绘制，插占位框：

```json
{
  "insert_image_placeholders": [
    {"after_paragraph": "图 2-1 项目主要法律关系图",
     "placeholder_text": "【需人工填写：项目主要法律关系图】",
     "width_cm": 15, "height_cm": 8}
  ]
}
```

尺寸参考标准答案（一般 15cm×8cm）。占位文字必须写明图应包含的内容，便于人工制作时明确需求。占位文案取 `text_templates.json` 的 `needs_human` 类。数据类图表（如资金流向图）可尝试用 matplotlib 生成初版后插入。

## 六、"不涉及"章节处理

章节不适用于本项目时（扩募项目的「可扩募资产情况」、数据中心项目的「特殊限定情况说明」），**不保留模版指导文字**，用 `replace_ranges` 整段替换：

```json
{
  "replace_ranges": [
    {"start_match": "（二）特殊限定情况说明", "end_match": "（三）可扩募资产情况",
     "replace": "不涉及。", "delete_tables": false},
    {"start_match": "（三）可扩募资产情况", "end_match": "二、参与主体情况",
     "replace": "本项目属于已发行基础设施REITs新购入项目，不涉及。", "delete_tables": true}
  ]
}
```

⚠️ `end_match` **必须真实存在于 start_match 之后**——脚本在找不到 end_match 时会拒绝执行该条（防止从起点一路删到文档末尾，历史事故：26个表格被删到只剩2个）。用 `--validate-only` 可提前发现。

常见「不涉及」场景：
- （二）特殊限定情况说明：数据中心/智算中心不涉及燃煤发电、园区配套、文化旅游、消费基础设施等特殊限定 → 「不涉及。」
- （三）可扩募资产情况：扩募项目本身是新购入项目 → 「本项目属于已发行基础设施REITs新购入项目，不涉及。」+ 删除表2

## 七、附件1/附件2 模版清理（主agent自己做，不发子agent）

模版末尾的附件1（证明材料目录框架）和附件2（法律意见书必备内容）是**格式文本自带的模版框架**，不清理会命中第三步残留校验（「阐述部分」「结论部分」「法律意见书必备的内容」均为 FAIL 关键词）。**处理方式：整体删除、不写材料目录**（证明材料目录不在申报正文里维护，无需按 `proofs_index.json` 展开）。各章 fill_plan 应用完成后，由主agent生成并执行 `fill_plan_annex.json`——**一条 `replace_ranges` 即可**，不要交给 ch7 子agent，也不要用 paragraphs 逐段替换（40+段容易漏）：

```json
{
  "replace_ranges": [
    {"start_match": "附件：1. 基础设施REITs项目相关证明材料",
     "replace": "", "to_end": true, "delete_tables": true}
  ]
}
```

要点（定位经过实测，不要改动）：
- 起点锚点「附件：1. 基础设施REITs项目相关证明材料」**全文唯一**（官方模版第225段，正文末尾的附件列示行），从它删到文档末尾（`to_end: true` 是唯一允许删到末尾的显式开关）——两行附件列示、附件1 整个目录框架、附件2 全部内容一次清干净。
- ⛔ **不要用「附件2」「关于转让行为合法性…」「一、阐述部分」当 start_match**：「附件2」在第五章正文有同串（「本格式文本的附件2有关表述」）；「关于转让行为合法性…」与列示行互为子串会命中错误段落；「一、阐述部分」只能删掉附件2，会把附件1 框架整个残留下来。
- 附件区内没有表格实体（全文最后一个表格在第六章），`delete_tables: true` 只是兜底。
- 执行后跑 `validate_output.py`，确认「模版残留」项 PASS。

## 八、来源标注（citations）

本项目要求**最终 docx 中所有AI填充的实质内容都在正文标明来源**。话术、落位、豁免清单统一由 `templates/citation_rules.json` 定义，fill_plan 只写**结构化来源对象**，渲染由 `fill_docx.py` 完成——不要自己在 `replace` 里手打括注文字（话术会不一致）。

### 8.1 四类来源

| `type` | 用途 | 必填 | 可选 | 渲染示例 |
|---|---|---|---|---|
| `material` | 证明材料（绝大多数） | `attachment_no` | `doc_name`、`page`、`section` | `（提取自附件25-1《资产评估报告》第43页）` |
| `knowledge` | 政策法规/产业目录/行业公开资料 | `name` | `doc_number`、`issuer`、`clause`、`source_desc` | `（参考自国家发展改革委《产业结构调整指导目录（2024年本）》第一类鼓励类第四十六条）` |
| `computed` | 公式取数（资产负债率/评估净值；**EBITDA 除外：不计算，直接【待填写】**） | `formula` | `basis`、`attachment_no` | `（据2024年审计报告数据按“资产负债率=总负债/总资产×100”计算）` |
| `draft` | 沿用初稿、未从材料复核 | — | `note` | `（沿用申报材料初稿，未从证明材料复核）` |
| `pending` | **取不到来源时的唯一合法写法** | — | `field` | `【待填写：来源（evaluation.total_value）】` |

字段格式硬约束（预检会按 ERROR 拦截）：
- `attachment_no` 只写编号本身（`13-1-5-1`、`25-1`），**不带「附件」二字**
- `page` 只写整数或区间（`43` / `"43-45"`），不写「第43页」
- **无页码材料（docx/xlsx，如天眼查专业版企业信用报告）用 `section` 替代 `page`**：填报告内小节名（`"2.1工商信息"`），渲染为「（提取自附件28-4《…专业版企业信用报告》，2.1工商信息）」。它与 `page` 互斥（有 `page` 就不带 `section`），由 `gen_phase_fill_plan.py` 从 extracted_data 的 `_section` 自动取。**取不到就都不写，严禁为了括注"看起来完整"而编页码**（可选段丢弃后不留残渣）
- 必填字段缺失时**不要硬凑**——改用 `type: "pending"`。脚本内部也会兜底成 pending，**绝不会静默省略标注**（静默丢标注会让"没标"伪装成"已标"，是最危险的失败模式）

### 8.2 五种落位

| `placement` | 用在哪 | 行为 |
|---|---|---|
| `sentence_suffix`（默认，需给 `anchor`） | 正文数据句 | 括注插到 `anchor` 子串**之后** |
| `paragraph_suffix`（无 `anchor` 时自动） | 整段同一来源 | 插到最后一个非空分段的**末尾句号之前**（`……（提取自附件X）。`） |
| `table_note` | 摘要表/表1/表3~表10 等 **≤6列窄表** | 不动表格，改由 `insert_paragraphs` 在表后加一段「注：本表数据提取自……」 |
| `remark_col` | 表15/16/20 等**有「备注」列的宽表** | 短式括注拼进 `citation_col` 指定列（蓝图配置，脚本生成时完成） |
| `cell_suffix` | 个别宽单元格 | 单元格内追加**短式**括注 |

⚠️ **窄表绝不逐格加括注**——2~6 列的表加了必破版。预检会对「≤6列表却有>2个单元格带 citation」打 WARNING 提示改用 `insert_paragraphs`。

### 8.3 写法示例

```json
{
  "paragraphs": [
    {"match": "说明项目评估情况",
     "replace": "本项目评估值为757,700.00万元，评估基准日为2024年6月30日。资产负债率为45.67%。",
     "citations": [
       {"type": "material", "attachment_no": "25-1", "doc_name": "资产评估报告",
        "page": 43, "anchor": "757,700.00万元"},
       {"type": "computed", "basis": "2024年审计报告数据",
        "formula": "资产负债率=总负债/总资产×100", "anchor": "45.67%"}
     ]}
  ],
  "tables": [
    {"locate": {"table_index": 15, "header_hint": "手续名称"},
     "cells": [{"row": 1, "col": 6, "text": "首次立项备案",
                "citation": {"type": "material", "attachment_no": "13-1-5-1"}}]}
  ],
  "insert_paragraphs": [
    {"after_table_index": 0,
     "citations": [{"type": "material", "attachment_no": "25-1",
                    "doc_name": "资产评估报告", "page": 43}]}
  ]
}
```

`insert_paragraphs` 的 `text` 可省略——只给 `citations` 时脚本按 `note_template`（`注：本表数据{citation_body}。`）自动组装；给了 `text` 则作为前缀。

### 8.4 关键行为与陷阱

- **幂等**：渲染结果已出现在文本中则不重复追加。所以子agent若自己写了标准话术，不会被叠加第二遍。
- **按 `\n` 逐段分配（防「尾段垃圾桶」）**：`apply_citations_to_text` 先把 `replace` 按 `\n` 拆段，再逐段落位——①有 `anchor` 且 anchor 落在某段里 → 插到**该段**内 anchor 之后（不受条数限制，这是显式意图）；②无 anchor / anchor 找不到 → 按「**每段最多 `CITATIONS_PER_SEGMENT_MAX`=2 条**」的容量顺序分配到各**实质段**（净长≥40字的非空段）；③所有候选段都满 → 该条**丢弃并 WARNING**。
  > 旧实现是对整条 `replace`（含全部分段）做替换、无 anchor 的全部追加到**最后一个非空分段**末尾 —— 第四章实测事故：一个条目承载整节几十段 + 20 多条 citations，交付文档里出现**单段连续 22 个括注、光括注 800 多字**的垃圾段。
- **同一份材料只标一次**：按 `(type, attachment_no|name|formula|field)` 在**本条目内**去重。这比按渲染文本去重更严——实测同一附件被标了 3 次，只因 `doc_name` 一次带书名号一次不带，渲染文本不同而漏过。
- **一个条目承载整节内容时，务必给每条 citation 写 `anchor`**：这是唯一能精确落位、且不被限流丢弃的写法。日志里出现「citations 限流：本条目有 N 条括注未写入」就是在提醒这件事。
- **`anchor` 必须真实存在于本条 `replace`**，否则预检报 ERROR（不报错地落到段末会让括注位置错误且难发现）。
- **括注不计入字数门槛**：`validate_output.py` 统计章节字数前会按 `strip_pattern` 剔除全部括注。**不能用括注凑字数**——正文篇幅不足是本项目历史最大事故（第六章只写 83 字），这个检测能力不允许被标注功能削弱。
- **超长自动降级**：单条括注渲染后超过 `max_len`（默认60字）自动改用 `short` 短式；表格单元格内一律短式。
- **重建表（phase6）只支持 `remark_col`**：`insert_tables` 用段落锚点插表，若同时用 `table_note` 锚同一段落，注会插到表**之前**，位置错误。蓝图未给 `citation_col` 时脚本会在 todo 里报 `missing_citation_col`。
- **表22 显式关闭标注**（`citation_placement: "none"`）：它是税务测算结果、不是材料提取所得，逐行挂「提取自附件X」会误导审阅者。

### 8.5 相关命令

```bash
# 预检（含 citations schema 校验：type/必填/编号格式/页码/anchor/placement）
python fill_docx.py --template <基底.docx> --fill-plan <plan.json> --validate-only

# 执行并输出来源标注覆盖率审计
python fill_docx.py --template <基底.docx> --fill-plan <plan.json> --output <out.docx> --citation-audit

# 交付前：覆盖率门槛 + 附件编号真实性（拦截编造的编号）
python validate_output.py <out.docx> --proofs-index <work_dir>/proofs_index.json --json <报告.json>
```

