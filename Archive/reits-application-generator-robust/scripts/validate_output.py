#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
产出自检脚本（硬校验门槛）：对生成的申报材料 docx 做结构化检查，不需要标准答案。
任何 FAIL 项存在时 exit code=1，主agent 必须修复后重跑，直到全部 PASS 才能交付。

检查项（依据标准答案结构提炼）：
  1. 释义表（仅提示，当前不要求自动生成）
  2. 各一级章节最低段落数/字数门槛（第四章/第六章是历史重灾区）
     ⚠️ 字数统计**先剔除来源括注**（提取自/参考自/据…计算），防止括注注水掩盖正文不足
  3. 第四章必备小节存在（估值方法/折现率/资本性支出）——按章内文本判断
  3b. **表标题 ↔ 表格实体配对**（悬空表标题）+ **各章表格实体数**（第四章≥12）
      实测事故：第四章正文写了 15 个表标题（表4-1~表4-15），文档里却只有 3 张表有表格实体。
      两层根因：①表4-4~表4-15 靠 phase7 的 insert_tables 按表标题段锚点新建，锚点不一致
      或该批次没应用 → 表不存在；②extracted_data 的 operating_performance.valuation_params
      等数据源为空（评估报告只提取了前几页摘要）→ 蓝图脚本整表跳过。
      这两种情况下段数/字数/要素/占位符全 PASS，只有本项能暴露。
  3.1 第五章必备要素（产业结构调整指导目录/外资准入/不涉及的情况/缺失的情况/连廊/无异议函/可转让性）
      **+ 分节篇幅门槛（一）~（四）**：整章达标但某节空/某节撑爆 = 内容被写进错误小节
      （本章头号历史事故：70个fill_plan条目挤30个可替换锚点 → 宏观政策段落落到（二））
      **+ 标题层级完整性**：按样式核对每个 H2 下的 Heading 3 个数
      （（一）5个/（二）5个/（三）3个/（四）3个/（五）0个/（六）2个；其中（一）（六）
        模版里没有 H3，须由 replace 用 \n 新建并声明 styles）
  3.2 第六章必备要素 + （三）保障措施编号要点数≥5（**按章内文本判断**，避免第五章的
      "关联交易/同业竞争"串味；本章模版只有1段指导文字，最容易整节漏写）
      **+ 标题层级**：4 个小节标题（一）~（四）必须是 Heading 2（模版锚点是 Normal，
        靠 `\n` 新建的标题不会自动升级 → 实测事故：全落成 Normal，导航窗格里第六章无子节）
      **+ 数据可信度**：拿 extracted_data.json 与正文对撞，抓三个「默认值顶替」陷阱——
        ①缺 tier_count 却编出费用层级名 ②缺战略配售比例却用监管下限 20% 顶替
        ③承诺函已出具（issue_date 非空）却写「将出具」（需 --work-dir 或可推断）
  4. 摘要表关键字段已填（建设规模/申报基准日/评估净值/资产范围非空非占位）
  5. 模版残留清理（指导性文字、附件1/附件2模版内容、"阐述部分/结论部分"等）
  5c. **正文误用标题样式**（净字数>45 却套 Heading 的段落）：这是"Word 里全是大号加粗、
      分不出层级"的直接检测项，同时说明导航窗格/自动目录已被污染
  6. 占位符统计（超阈值 FAIL）
  7. 表格总数（应≥30，模版26+补插）
  8. 关键表最低行数
  9. **来源标注覆盖率**：实质段落中含「提取自/参考自/据…计算」的比例（<50% FAIL）
 10. **附件编号真实性**：正文引用的「附件X-X」必须在 proofs_index.json 实际存在
     （需 --proofs-index；防子agent编造附件编号）
 11. **门槛参数完整性**：`--citation-threshold` 是否被调低（只能调高；调低已被下限保护抬回，
     但仍报 FAIL，交付汇报必须列明）
  0. **输入数据体检**（排在报告最前）：现算 extracted_data.json 的关键字段/结构化数据源/
     溯源字段覆盖率。本 SKILL 只负责内容填充，输入由上游提取方提供——**章节写不够、表格为空、
     占位符多的根因往往在输入侧**，故先报它，再决定"重跑章节"还是"找上游补数据"。
     本项只诊断不阻断（永不 FAIL），需 --work-dir 或可从 --proofs-index/docx 推断。

用法:
  python validate_output.py <生成的申报材料.docx> [--json <报告输出路径>]
      [--proofs-index <work_dir>/proofs_index.json]   # 启用附件编号真实性校验
      [--work-dir <work_dir>]                          # 输入数据体检 + 第六章数据可信度对撞
      [--citation-threshold 50]                        # 来源标注覆盖率 FAIL 阈值，**只能调高**
"""

import argparse
import json
import os
import re
import sys

try:
    from docx import Document
    from docx.text.paragraph import Paragraph
    from docx.table import Table
except ImportError:
    print("ERROR: python-docx not installed", file=sys.stderr)
    sys.exit(1)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CITATION_RULES = os.path.join(SCRIPT_DIR, "..", "templates", "citation_rules.json")

# 输入数据体检（同目录模块）；加载失败不阻断本脚本，仅降级为 INFO
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
try:
    from pipeline_state import check_inputs, infer_work_dir
    _HAS_PIPELINE_STATE = True
except Exception:      # pragma: no cover
    _HAS_PIPELINE_STATE = False

# ---- 来源标注相关正则（默认值与 citation_rules.json 保持一致，读不到文件时兜底）----
CIT_STRIP_DEFAULT = r"（(?:提取自|参考自|详见|据|按“|沿用)(?:[^（）]|（[^（）]*）)*）|【待填写：来源[^】]*】"
CIT_DETECT_DEFAULT = (r"(?:提取自附件|提取自|参考自|详见附件|沿用申报材料初稿|沿用初稿|【待填写：来源)"
                      r"|按“[^”]{2,60}”计算")
CIT_ATTACH_REF_DEFAULT = r"附件(\d+(?:-\d+)*)"
CIT_EXEMPT_HEADING_DEFAULT = [r"^[一二三四五六七八九十]+、", r"^（[一二三四五六七八九十]+）",
                              r"^\d+\.", r"^附件"]
CIT_EXEMPT_TEXT_DEFAULT = [r"^不涉及。?$", r"^无。?$", r"^综上", r"^具体如下",
                           r"^详见下表", r"^如下表所示"]
CIT_MIN_CHARS_DEFAULT = 40


def load_citation_rules(path=None):
    """读取 citation_rules.json；缺失则用内置默认值（不中断校验）"""
    p = path or DEFAULT_CITATION_RULES
    rules = {}
    try:
        with open(p, encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            rules = loaded
    except Exception:
        pass
    ex = rules.get("exemptions") or {}
    th = rules.get("coverage_thresholds") or {}

    def rx(pat, default):
        try:
            return re.compile(pat or default)
        except re.error:
            return re.compile(default)
    return {
        "strip": rx(rules.get("strip_pattern"), CIT_STRIP_DEFAULT),
        "detect": rx(rules.get("detect_pattern"), CIT_DETECT_DEFAULT),
        "attach_ref": rx(rules.get("attachment_ref_in_text_pattern"), CIT_ATTACH_REF_DEFAULT),
        "exempt": [rx(p, r"$^") for p in
                   ((ex.get("heading_patterns") or CIT_EXEMPT_HEADING_DEFAULT)
                    + (ex.get("text_patterns") or CIT_EXEMPT_TEXT_DEFAULT))],
        "min_chars": int(ex.get("min_chars") or CIT_MIN_CHARS_DEFAULT),
        "fail_below": float(th.get("fail_below") or 50.0),
        "warn_below": float(th.get("warn_below") or 80.0),
    }


# 各一级章节最低门槛（段落数, 字数）——按标准答案的约50-60%设保守线
# 第五章按两份标准答案实测（80段/7,915字 与 约60段/6,000字）上调到 70段/6,500字：
# 该章正文由 (一)宏观政策5小节(约25段) + (二)3./4.法规原文论证 + (三)3.土地分情形 +
# (四)2./3.转让限制与承诺函 构成，写不到这个量级基本等于漏了整个小节
CHAPTER_MIN = {
    "一、": (12, 800, "项目基本情况"),
    "二、": (40, 3000, "参与主体情况"),
    "四、": (80, 8000, "项目基本条件（运营收益/估值/风险，历史最大缺口）"),
    "五、": (70, 6500, "项目合规情况（表格多+法规原文论证多，历史易压缩）"),
    "六、": (30, 2600, "运营管理安排（历史严重缺失章节，两份标答实测36段/3700字、38段/3286字）"),
}

# 【业务人工维护的章节｜本 SKILL 不生成、不校验】
# 三、REITs设立方案 与 七、募集资金用途情况 都是业务决策（产品要素/架构/实施步骤、
# 募集规模与资金流向、拟投资项目），不是从证明材料能提取的内容 → 由业务在初稿里自己写。
# 因此这两章：①不进 CHAPTER_MIN（否则每次都报「内容不足」并要求重跑，掩盖真缺口）；
# ②其模版指导文字**允许原样保留**，故从「模版残留」与「指导文字未替换」两项检查里排除
# ——模版原文本身就含 3 处「（填写表11/24/25）」与 8 段以「说明」开头的指导文字，
# 不排除必然 FAIL。
MANUAL_CHAPTERS = ("三、", "七、")

# 附件区起始（「附件：1. …」/「附件1」/「附件2」）。附件**不属于任何章节**：
# 附件段落不匹配 ^[一二三四五六七]、，遍历时 cur 会一直停在「七、」——若不单独摘出来，
# 排除第七章会连带把附件2 的「法律意见书必备的内容」一起豁免，phase6 漏清理也不报错。
# 顺带修掉一个老问题：附件2 自带「一、阐述部分」「二、结论部分」会匹配章标题正则，
# 把附件段落算进第一章/第二章的段数与字数，让篇幅统计虚高。
ANNEX_START_PAT = re.compile(r"^附件\s*[：:1-9]")

# 第四章必备小节关键词（缺=评估报告没读全）
CH4_REQUIRED = ["估值方法", "折现率", "资本性支出"]

# ---- 纯占位符段落：不算正文 ----
# 整段就是一个占位符（`【待填写…】`/`【待确认…】`/`【注：…待核实】` 等）时，它是"等人来填"的
# 批注，不是已写成的正文 —— 既不该计入章节段数/字数门槛，也不该让「必备要素」的关键词命中
# （占位符里出现「折现率」不等于这一节写了折现率分析）。
# 【为何必须堵】本项目已有一条同源红线：字数统计前先剔除来源括注，防止用括注凑篇幅。
# 占位符同理，且新增的两类脚本自动插入内容会直接踩到它：①phase4 的「运营满3年」门槛提示段
# （约 250 字黄底批注）；②骨架表的【待填写：本表数据缺失…】。让它们计入篇幅，等于校验被
# 自己生成的批注注水，与"让校验变绿的唯一合法方式是把内容写够"直接冲突。
PLACEHOLDER_ONLY_PAT = re.compile(r"^【[^】]*】[。；;，,]?$")

# ---- 表标题 ↔ 表格实体配对（抓「有表标题、没有表格结构」）----
# 【为何需要这一项｜实测事故】第四章正文写了 15 个表标题（表4-1~表4-15），文档里却只有
# 3 张表有表格实体，另外 12 张只有标题文字。两层根因叠加：①官方模版本章只有 3 张表，
# 表4-4~表4-15 靠 phase7 的 insert_tables 按**表标题段锚点**新建 —— 锚点对不上或该批
# 没应用，表就不存在；②extracted_data 的 operating_performance.valuation_params 等
# 数据源为空（评估报告只提取了前几页摘要）→ 蓝图脚本整表跳过不生成。
# 这两种情况下**段数/字数/要素/占位符全部 PASS**，只有把「表标题段」和它后面的表格
# 实体配对才能暴露。图标题（图4-1 等）同理：图占位框是一张 1×1 表格。
TABLE_CAPTION_PAT = re.compile(r"^表\s*\d+(?:\s*[-－—~]\s*\d+)?\s*[\s　]")
FIG_CAPTION_PAT = re.compile(r"^图\s*\d+(?:\s*[-－—~]\s*\d+)?\s*[\s　]")
# 表标题与表格之间允许夹几段短说明（模版惯例：「单位：个、万元、%」「资料来源：…」）
CAPTION_GAP_MAX_PARAS = 2
CAPTION_GAP_MAX_CHARS = 40

# 各章最少表格实体数（章内实测表格数低于此值 = 该章表格整批漏做）
# 第四章标准答案 15 张（3 张模版表 + 12 张新建）；<12 即说明 phase7 整批没落地。
CHAPTER_MIN_TABLES = {
    "四、": (12, "第四章标准答案 15 张表（表4-1~表4-15）：3 张改模版表 + 12 张由 "
                 "fill_plan_phase4.phase7.json 的 insert_tables 新建"),
}


def check_caption_pairing(body_seq):
    """表/图标题段之后是否真有表格实体。

    body_seq = [('p', 文本, 章前缀), ('t', 列数, 章前缀)]，按文档顺序。
    返回 [(标题文本, 章前缀, 'table'|'figure')]，即**悬空标题**清单。
    """
    orphans = []
    for i, el in enumerate(body_seq):
        if el[0] != "p":
            continue
        text = el[1]
        is_tbl = bool(TABLE_CAPTION_PAT.match(text))
        is_fig = bool(FIG_CAPTION_PAT.match(text))
        if not (is_tbl or is_fig):
            continue
        gap, found = 0, False
        for nxt in body_seq[i + 1:]:
            if nxt[0] == "t":
                found = True
                break
            t2 = nxt[1]
            # 又一个标题 / 过长的正文 / 夹了太多段 → 判定本标题后面没有表
            if TABLE_CAPTION_PAT.match(t2) or FIG_CAPTION_PAT.match(t2):
                break
            if len(t2) > CAPTION_GAP_MAX_CHARS or gap >= CAPTION_GAP_MAX_PARAS:
                break
            gap += 1
        if not found:
            orphans.append((text[:44], el[2], "figure" if is_fig else "table"))
    return orphans


# 第一章分节专项：{小节前缀: (最少段, 最少净字, 是否强制括注, 说明)}
# 【为何需要这一项】历史事故：初稿里第一章**表格已填、三节正文全空**，agent 看表 1 有数
# 就当整章已完成直接跳过；而整章门槛（旧值 8段/500字）光靠小节标题+表标题+表下注就能蒙过
# ——实测“无正文”的产出 9段/880字 竟然 PASS。故改为**逐节**校实质正文。
# （二）的正常答案可能就是「不涉及。」四个字 → 门槛极低且**不强制括注**
# （它是判定结论而非数据，强制括注只会逗出编造的来源）。
CH1_SECTION_MIN = {
    "（一）": (2, 200, True, "项目概况——项目类型+标的资产范围一段、子项目详情一段（表格已填也要写正文）"),
    "（二）": (1, 4, False, "特殊限定情况说明——不属四类就写「不涉及。」，属于则写实质内容"),
    "（三）": (1, 120, True, "可扩募资产情况——发起人业务与城市布局正文（≤20字）；已运营资产还需补近3个会计年度运营收益"),
}

# 第五章必备要素（缺=对应小节被整段跳过；(一)5小节与(二)3./4.论证是历史高发缺口）
# ⚠️ 与第六章同理，**按章内文本判断**：用 full_text 会被其它章串味
#   （"无异议函""可转让性"在第二章/第七章的表述里也可能出现，会放过真正的缺口）。
CH5_REQUIRED = [
    ("产业结构调整指导目录", "（一）3.行业政策小节——须写到目录类别与条目号"),
    ("外资准入", "（一）4.外资准入小节——无外资也要写明不涉及的理由"),
    ("不涉及的情况", "（二）3.相关手续不涉及的情况——须逐条引法规条款原文"),
    ("缺失的情况", "（二）4.相关手续缺失的情况——须按三类情形分别说明"),
    ("连廊", "（二）5.连廊、夹层等建筑情况——承诺函必备表述"),
    ("无异议函", "（三）/（四）——无异议函办理过程与主要内容"),
    ("可转让性", "（四）可转让性有关情况"),
]

# 第五章**分节**篇幅门槛（段数, 净字数, 说明）
# 为什么需要分节而非只看整章：本章历史事故是"内容写进错误小节"——
# 70 个 fill_plan 条目挤 30 个可替换锚点，子agent编造 match，宏观政策段落被写进
# （二）投资管理手续说明段。这种错位下**整章段数/字数完全达标**，只有分节统计才暴露：
# 某节被撑爆、另一节空着。阈值按标准答案约 50% 设保守线。
CH5_SECTION_MIN = {
    "（一）": (20, 3000, "符合宏观管理政策要求情况（须5个编号小节；标答25段/3,700字）"),
    "（二）": (15, 1500, "投资管理手续合规情况（含3.不涉及/4.缺失的法规原文与函复原文论证）"),
    "（三）": (6, 600, "土地使用合规情况（含3.按土地取得方式分情形说明）"),
    "（四）": (6, 600, "可转让性有关情况（含2.转让限制具体情况/3.承诺函出具情况）"),
}
SECTION_HEAD_PAT = re.compile(r"^\s*（([一二三四五六七八九十]+)）")

# ---- 样式健全性（防"正文继承标题样式"）----
# fill_docx 的段落替换保留 <w:pStyle>，拆段时还会 deepcopy 整个 <w:p>。锚点若是
# Heading 样式（初稿常见，或子agent误拿小节标题当 match），替换出的正文就全部带标题样式：
# Word 里显示为大号加粗，并把几十段正文塞进导航窗格与自动目录。
HEADING_STYLE_PAT = re.compile(r"^(?:Heading\s*\d|标题\s*\d)", re.I)
# 净字数超过此值的"标题"几乎不可能是真标题（模版最长的真标题是
# "（五）政府和社会资本合作（PPP）项目合规情况" = 26 字）
HEADING_BODY_MAX_CHARS = 45
HEADING_BODY_FAIL_THRESHOLD = 3     # 超过这么多段即 FAIL

# 第五章期望的标题层级：{H2 前缀: (H3 个数, 说明)}
# 依据业务确认的层级树。⚠️ 模版现状与期望的差异（子agent必须补齐）：
#   （一）模版有 0 个 H3 → 5 个全需由 replace 用 \n 新建并声明 styles=["Heading 3",...]
#   （二）模版已有 5 个 H3，文字与期望一致
#   （三）模版已有 3 个 H3，但「2.土地使用手续办理情况（填写表19）」须清理后缀
#         （「（填写表」是 TEMPLATE_RESIDUE 的 FAIL 关键词，藏在 Heading 3 里）
#   （四）模版已有 3 个 H3，文字与期望一致
#   （六）模版有 0 个 H3 → 2 个全需新建
CH5_EXPECTED_H3 = {
    "（一）": (5, "1.国家重大战略与总体规划／2.专项规划和区域规划／3.《产业结构调整指导目录》和行业政策／"
                 "4.外资准入／5.其他专项政策（**模版无这5个H3，须由 replace 新建并声明 styles**）"),
    "（二）": (5, "1.各类项目均应取得的投资管理合规手续／2.特定行业投资管理合规手续／3.相关手续不涉及的情况／"
                 "4.相关手续缺失的情况／5.连廊、夹层等建筑情况（模版已有）"),
    "（三）": (3, "1.总体情况／2.土地使用手续办理情况／3.具体情况说明（模版已有，其中2须清理「（填写表19）」）"),
    "（四）": (3, "1.总体情况／2.具体情况／3.承诺函出具情况（模版已有）"),
    "（五）": (0, "无下级标题"),
    "（六）": (2, "1.承诺函出具情况／2.项目涉税情况（**模版无这2个H3，须由 replace 新建**）"),
}


def split_sections(texts):
    """把一章的段落列表按「（一）（二）…」小节标题切分为 {小节: [段落]}。

    小节标题之前的段落（章引言）归入 "" 键。与 fill_docx.build_doc_index 同口径。
    """
    out = {}
    cur = ""
    for t in texts:
        m = SECTION_HEAD_PAT.match(t)
        if m:
            cur = "（%s）" % m.group(1)
        out.setdefault(cur, []).append(t)
    return out


def count_h3_by_section(pairs):
    """统计一章内每个 H2 小节下的 H3 段落数。

    pairs = [(段落文本, 样式名)]，按文档顺序。
    以**样式**判定层级（而非文本形态），因为"层级对不对"问的就是样式。
    返回 ({H2前缀: H3文本列表}, [不是 Heading 2 样式的 H2 标题])
    """
    out = {}
    bad_h2_style = []
    cur = None
    for t, st in pairs:
        m = SECTION_HEAD_PAT.match(t)
        if m:
            cur = "（%s）" % m.group(1)
            out.setdefault(cur, [])
            if not HEADING_STYLE_PAT.match(st or "") or "2" not in (st or ""):
                bad_h2_style.append((cur, st or "(无样式)"))
            continue
        if cur and HEADING_STYLE_PAT.match(st or "") and "3" in (st or ""):
            out[cur].append(t)
    return out, bad_h2_style

# 第六章必备要素（缺=对应小节/要点被整段跳过）
# 依据两份过审标准答案的**并集**（段数/字数为本脚本实测口径，含小节标题段）：
#   A本 ch6_example.md  = 36段/约3,700字（**三层**收费、（三）4要点、（四）4+4项）
#   （第二份过审材料实测 38段/3,286字：两层收费、（三）6要点、（四）4+5项；要点并集已写入 ch6_guide.md）
# 两本均为首发项目而收费层级不同 → 层级按《运营管理服务协议》实际约定，不按首发/扩募推定。
# 本章模版只有 1 段指导文字，子agent没有段落锚点，最容易整节漏写 → 必须按要素硬查。
# ⚠️ 关键词**必须避开官方指导文字用词**——指导文字原文为
#   「运营管理机构与基金管理人之间的运营管理权责利关系、激励约束机制，促进项目持续健康平稳运营的
#     保障措施，以及运营管理机构防范关联交易和利益冲突的主要安排。」
#   若拿「权责利/激励/保障措施/关联交易」当检测词，**模版一字未改也会全部判 PASS**（假阳性，
#   实测 83 字残留文档能骗过 4 个要素）。因此下列检测词一律取"只可能出现在实质内容里"的词。
# 每个条目是 (候选词元组, 提示)，命中任一候选词即算存在（兼容两份标答的不同措辞）。
CH6_REQUIRED = [
    (("财产保险",),
     "（一）委托职责清单——须按《基金指引》第三十八/三十九条列全6项："
     "财产保险与公众责任保险／运营策略制定落实／签署执行运营协议／收取收益与追收欠款／日常运营服务／维修改造"),
    (("解聘",),
     "（一）基金管理人监督机制——须写委派管理人员与财务负责人、定期/不定期检查、限期整改、违约追责、解聘程序"),
    (("运营管理费", "运营管理服务费"),
     "（二）须写清运营管理费层级（两层：基本+激励／三层：基础+达标+激励，按《运营管理服务协议》实际约定）与各层计提基数"),
    (("审计报告",),
     "（二）激励费的实际值/目标值取数来源——惯例：实际值取每年审计报告、目标值取评估报告预测数据"),
    (("运营管理模式",),
     "（三）2.项目运营管理模式不变——原运营团队与核心团队不变、客户协议条款不变、法律文件明确奖惩机制"),
    (("同业竞争",),
     "（三）5./（四）2.避免同业竞争的主要安排（5项）"),
    (("优先购买权",),
     "（四）2.(4) 同类资产出售或转让时，REITs项下相关载体享有同等条件下的优先购买权"),
    (("回避表决",),
     "（三）6.基金层面项目管理机制 /（四）关联方回避表决安排——历史高发缺口，两份标答均有"),
]

# 第六章（三）保障措施的编号要点最低数量（A本4点、B本6点，取并集要求≥5）
CH6_MIN_NUMBERED_POINTS = 5
CH6_NUMBERED_PAT = re.compile(r"^\s*(\d+)\s*[.、．]")

# 第六章期望的 4 个 H2 小节（按**样式**核对，抓「层级塌平」）
# 为什么要单列一项：本章模版只有 1 段 **Normal** 指导文字，4 个小节标题全靠 fill_plan 的
# `\n` 新建 —— 新建出来的标题不会自己变成标题样式。实测事故：4 个小节标题全落成 Normal，
# Word 导航窗格与自动目录里第六章下面**一个子节都没有**；而整章段数/字数完全达标，
# 只有按样式检查才能暴露。对策见 fill_docx.py 的 auto_heading（默认 'h2' 自动升级）。
# 匹配用「小节序号 + 关键词」而非全句，兼容官方指导文字分句与标答简写两种写法
#   （标答：（一）运营管理权责利关系；模版分句：（一）运营管理机构与基金管理人之间的……）
CH6_EXPECTED_H2 = [
    ("（一）", ("权责利",), "（一）运营管理（机构与基金管理人之间的）运营管理权责利关系"),
    ("（二）", ("激励",), "（二）激励（及）约束机制"),
    ("（三）", ("保障措施",), "（三）促进项目（持续健康）平稳运营的保障措施"),
    ("（四）", ("关联交易", "利益冲突"), "（四）防范关联交易和利益冲突的主要安排"),
]

# ---- 第六章「数据可信度」：三个默认值顶替陷阱（拿 extracted_data 与正文对撞）----
# 为什么单列：这三类错误**不会**表现为占位符多、篇幅不足或要素缺失，全部检查都能 PASS，
# 但写出来的是"语气确定、还带来源括注"的错数据，评审端根本分辨不出。
#   ① 缺 fee_structure.tier_count 却套了 fee_two_tier/fee_three_tier 编出层级名
#      （实测：协议实为三层「基础管理费+达标管理费+激励管理费」，写成两层「基本收费+激励收费」）
#   ② 缺 project_info.originator_subscription_ratio 却用《基金指引》监管下限 20% 顶替
#      （实测：实际 34% 写成「不低于20%」并标 knowledge；34% 还被第五章表22 的
#       企业所得税公式 ×(1-34%) 引用，两处自相矛盾）
#   ③ conflict_commitments.issue_date 非空却写「将根据监管要求出具」
#      （实测：承诺函 2024-12-31 已出具，正文写成将来时）
CH6_FEE_TIER_WORDS = ("基本收费", "激励收费", "基础管理费", "达标管理费", "激励管理费")
CH6_FUTURE_TENSE_WORDS = ("将根据监管要求出具", "将出具", "将根据监管要求签署")
CH6_RATIO_PAT = re.compile(r"(?:不低于\s*)?(\d{1,2}(?:\.\d+)?)\s*%\s*的?基础设施REITs基金份额|"
                           r"持有\s*(?:不低于\s*)?(\d{1,2}(?:\.\d+)?)\s*%|"
                           # 标答同款句式「参与战略配售的比例不低于20%」此前认不出→误报要点缺失
                           r"战略配售[^。%]{0,12}?(?:不低于\s*)?(\d{1,2}(?:\.\d+)?)\s*%")
LEGAL_FLOOR_RATIO = "20"


def _dig(obj, path):
    """按 'a.b.c' 取嵌套值；任一层缺失/为 None 返回 None"""
    cur = obj
    for k in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
        if cur is None:
            return None
    return cur


def check_ch6_data_fidelity(ch6_texts, work_dir):
    """第六章数据可信度：把正文里的关键数据与 extracted_data.json 对撞。

    返回 [(level, name, detail)]；没有 extracted_data.json 时返回一条 INFO（不阻断）。
    """
    name = "第六章数据可信度"
    if not ch6_texts:
        return []
    ed_path = os.path.join(work_dir or "", "extracted_data.json") if work_dir else None
    if not ed_path or not os.path.exists(ed_path):
        return [("INFO", name,
                 "未找到 extracted_data.json（--work-dir 未给或路径不对），"
                 "跳过「费用层级/战略配售比例/承诺函时态」三项对撞检查")]
    try:
        with open(ed_path, "r", encoding="utf-8") as f:
            ed = json.load(f)
    except Exception as e:
        return [("INFO", name, "extracted_data.json 读取失败（%s），跳过对撞检查" % e)]

    text = "\n".join(ch6_texts)
    out = []

    # ① 费用层级不得推定
    tier_count = _dig(ed, "operation_management.fee_structure.tier_count")
    hit_words = [w for w in CH6_FEE_TIER_WORDS if w in text]
    if tier_count in (None, "", 0):
        if hit_words:
            out.append(("FAIL", name + "[费用层级]",
                        "extracted_data 的 operation_management.fee_structure.tier_count 为空，"
                        "但（二）正文写出了确定的层级名称（%s）—— 这是**套默认模板编出来的**。"
                        "正确做法：改用 text_templates.json 的 "
                        "paragraph_templates.operating_arrangement.fee_unknown 分支"
                        "（层级与计提基数以《运营管理服务协议》约定为准 + 占位符 + pending 来源）；"
                        "或向上游提取方补数据《运营管理服务协议》收费条款后重跑 ch6"
                        % "、".join(hit_words)))
        else:
            out.append(("PASS", name + "[费用层级]",
                        "tier_count 为空且正文未编造层级名称（应已走 fee_unknown 分支）"))
    else:
        try:
            n_tier = int(tier_count)
        except Exception:
            n_tier = None
        two = {"基本收费", "激励收费"} & set(hit_words)
        three = {"基础管理费", "达标管理费", "激励管理费"} & set(hit_words)
        if n_tier == 2 and three and not two:
            out.append(("FAIL", name + "[费用层级]",
                        "tier_count=2（两层）但正文写的是三层口径（%s）—— 层级与协议不符，重跑 ch6"
                        % "、".join(sorted(three))))
        elif n_tier == 3 and two and not three:
            out.append(("FAIL", name + "[费用层级]",
                        "tier_count=3（三层）但正文写的是两层口径（%s）—— 层级与协议不符，重跑 ch6"
                        % "、".join(sorted(two))))
        elif not hit_words:
            out.append(("FAIL", name + "[费用层级]",
                        "tier_count=%s 但（二）正文里找不到任何层级名称（基本收费/激励收费/"
                        "基础管理费/达标管理费/激励管理费）—— （二）疑似被压缩，重跑 ch6" % tier_count))
        else:
            out.append(("PASS", name + "[费用层级]",
                        "tier_count=%s，正文层级口径一致（%s）" % (tier_count, "、".join(hit_words))))

    # ② 战略配售比例不得用监管下限顶替
    ratio = _dig(ed, "project_info.originator_subscription_ratio")
    found = [g for mm in CH6_RATIO_PAT.finditer(text) for g in mm.groups() if g]
    if ratio in (None, ""):
        if found:
            out.append(("FAIL", name + "[战略配售比例]",
                        "extracted_data 的 project_info.originator_subscription_ratio 为空，"
                        "但（三）4 正文写了具体比例 %s%% —— %s。"
                        "唯一合法写法是「不低于【待填写：原始权益人及其关联方战略配售比例】」+ pending；"
                        "或向上游提取方要该比例（基金合同/招募说明书战略配售章节）后重跑本章"
                        % ("/".join(sorted(set(found))),
                           "这正是用《基金指引》监管下限 20% 顶替实际比例的典型表现"
                           if LEGAL_FLOOR_RATIO in found else "该数字无数据来源，属编造")))
        else:
            out.append(("PASS", name + "[战略配售比例]",
                        "该字段为空且正文未编造具体比例（应为占位符 + pending）"))
    else:
        rs = str(ratio)
        digits = re.findall(r"\d{1,2}(?:\.\d+)?", rs)
        want = digits[0] if digits else None
        if want and found and want not in found:
            out.append(("FAIL", name + "[战略配售比例]",
                        "extracted_data 的实际比例是 %s，但（三）4 正文写的是 %s%% —— 数据错误。"
                        "%s该比例同时被第五章表22 企业所得税递延公式 ×(1-比例) 引用，两处必须同值"
                        % (rs, "/".join(sorted(set(found))),
                           "尤其注意：20% 是《基金指引》的法定下限、不是本项目实际值。"
                           if LEGAL_FLOOR_RATIO in found else "")))
        elif want and not found:
            out.append(("FAIL", name + "[战略配售比例]",
                        "extracted_data 有实际比例 %s，但（三）4 正文里找不到它 —— "
                        "该要点疑似被跳过或写成了占位符，重跑 ch6" % rs))
        else:
            out.append(("PASS", name + "[战略配售比例]", "正文与 extracted_data 一致（%s）" % rs))

    # ③ 承诺函时态
    issue_date = _dig(ed, "operation_management.conflict_commitments.issue_date")
    future_hits = [w for w in CH6_FUTURE_TENSE_WORDS if w in text]
    if issue_date:
        if future_hits and str(issue_date) not in text:
            out.append(("FAIL", name + "[承诺函时态]",
                        "承诺函落款日期为 %s（已出具），但正文用的是将来时「%s」且未写出该日期 "
                        "—— 属事实性错误。改用 text_templates 的 "
                        "paragraph_templates.operating_arrangement.safeguard_5_issued："
                        "「%s，{出具主体}已出具《…》」，并引述关键承诺表述原文 150~300 字"
                        % (issue_date, "、".join(future_hits), issue_date)))
        elif str(issue_date) not in text:
            out.append(("WARN", name + "[承诺函时态]",
                        "承诺函落款日期为 %s，但正文未写出该日期 —— 建议在（三）5 明确"
                        "「%s，{出具主体}已出具《…》」" % (issue_date, issue_date)))
        else:
            out.append(("PASS", name + "[承诺函时态]", "已按「已出具」口径写明落款日期 %s" % issue_date))
    else:
        out.append(("INFO", name + "[承诺函时态]",
                    "conflict_commitments.issue_date 为空，正文用「将出具」口径合规；"
                    "若承诺函实际已出具，请向上游提取方补录落款日期"))
    return out


# 章节前缀 → ch号（轻量模式下给出“重跑哪几章子agent”的建议）
CHAPTER_NO = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7}

# 模版指导文字/附件残留（不应出现在最终产出正文中）
TEMPLATE_RESIDUE = [
    "（填写表",
    "分别列明各子项目情况",
    "法律意见书必备的内容",
    "阐述部分",
    "结论部分",
    "根据项目实际情况如实叙述",
]

# 摘要表必填字段（左列关键词 → 右列必须非空且非占位）
SUMMARY_REQUIRED = ["建设规模", "申报基准日", "评估净值", "资产范围", "资产所在地", "原始权益人", "基金管理人"]

PLACEHOLDER_PAT = re.compile(r"【待填写】")
NONCANONICAL_PLACEHOLDER_PAT = re.compile(
    r"【(?:待填写[^】]+|待确认[^】]*|待补充[^】]*|待核实[^】]*|"
    r"需人工填写[^】]*|需人工确认[^】]*|数据缺失[^】]*)】|\{待填写[^}]*\}|"
    r"（待定）|\(待定\)")
PLACEHOLDER_FAIL_THRESHOLD = 60  # 超过即FAIL（合理的needs_human约20-45个）

# 【最大问题】模版“指导性文字”段落特征：模版告诉你“该写什么”的说明文，
# 标准答案中已全部替换为实质内容；若仍大量残留，说明 fill_plan 的 paragraphs 条目写少了。
GUIDANCE_PAT = re.compile(
    r"^(说明|应说明|应详细说明|应分别说明|需说明|请说明|本部分应|此处应|应列明|应描述|应提供|应披露|应包含|应就)"
    r"|函中应包含|应包含以下内容|分别列明|如实叙述|按照下述要求|按下述格式"
)
# 指导文字残留段数阈值：超过即 FAIL（标准答案为0；留少量容差防误判）
GUIDANCE_FAIL_THRESHOLD = 8

# 关键表最低行数（首行关键词, 最低行数, 修复提示）
# 阈值按标准答案的约70%设，防“只填几行就交”
KEY_TABLE_MIN_ROWS = [
    ("中介机构", 24, "中介机构基本情况表（标准35行）：需对财务顾问/律所/会计师/评估/税务每家逐行填全名称、注册地、联系人、执业资格等"),
    ("手续名称", 30, "投资管理手续情况表（标准38行）：需逐项列全备案/规划/用地/施工/消防/环评/能评/竣工等全部手续，含签发时间/机构/文件编号/处理方式"),
    ("税种", 12, "拟缴纳税情况表（标准16行）：需按阶段（资产重组/股权转让/基金发行）×税种逐行列全"),
]


def check(docx_path, citation_rules=None, proofs_index_path=None, citation_threshold=None,
          work_dir=None):
    doc = Document(docx_path)
    results = []  # (level, name, detail)  level in PASS/INFO/WARN/FAIL（仅 FAIL 阻断交付）
    cit = citation_rules or load_citation_rules()
    if citation_threshold is not None:
        # 【门槛参数下限保护｜红线第5条】来源标注覆盖率阈值只允许**调高**（加严）。
        # 低于 citation_rules.json 的 fail_below（50）一律被抬回，防止用 --citation-threshold 0
        # 把"来源标注覆盖率"这道 FAIL 直接调没（与调低 --threshold 同类的绕过手法）。
        cit = dict(cit, fail_below=max(float(citation_threshold), float(cit["fail_below"])))

    # ---- 遍历 body：章节统计 + 表格收集 ----
    chapters = {}  # prefix -> [paras, chars]
    chapter_texts = {}  # prefix -> [段落原文]（章内要素/编号要点检查用，避免跨章串味）
    chapter_pairs = {}  # prefix -> [(段落原文, 样式名)]（标题层级检查用）
    heading_like_body = []  # 长度明显超标却套了标题样式的段落（= 正文继承标题样式）
    placeholder_only_paras = []  # 纯占位符段（不计入篇幅，仅作 INFO 报出）
    body_seq = []  # [('p', 文本, 章前缀) / ('t', 列数, 章前缀)]：表标题↔表格实体配对用
    chapter_tables = {}  # prefix -> 该章内的表格实体数
    cur = None
    in_annex = False
    tables = []
    full_text_parts = []
    auto_text_parts = []  # 排除「业务人工维护章节」后的段落（模版残留/指导文字/占位符检查用）
    last_texts = []
    for el in doc.element.body:
        if el.tag.endswith("}p"):
            p = Paragraph(el, doc)
            t = p.text.strip()
            if not t:
                continue
            style = p.style.name if p.style else ""
            if ANNEX_START_PAT.match(t):
                # 进入附件区后不再归属任何章（且不可回退——附件2 的「一、阐述部分」不算章标题）
                in_annex = True
                cur = None
            m = re.match(r"^([一二三四五六七])、", t)
            if (style.startswith("Heading 1") or m) and m and not in_annex:
                cur = m.group(1) + "、"
                chapters.setdefault(cur, [0, 0])
                chapter_texts.setdefault(cur, [])
                chapter_pairs.setdefault(cur, [])
            if cur:
                chapters[cur][0] += 1
                # 【关键】字数按**剔除来源括注后**的净正文统计。
                # 否则「（提取自附件25-1《资产评估报告》第43页）」这类括注会把字数撑上去，
                # 掩盖"正文写不够"——而正文篇幅不足正是本项目历史最大事故
                # （第六章只写83字），这个检测能力不能被标注功能削弱。
                chapters[cur][1] += len(cit["strip"].sub("", t))
                chapter_texts[cur].append(t)
                chapter_pairs[cur].append((t, style))
                # 纯占位符段（含脚本自动插入的门槛提示段）不是正文：段数/字数都回退掉，
                # 也不进 chapter_texts（否则占位符里的关键词会假装满足「必备要素」）
                if PLACEHOLDER_ONLY_PAT.match(t):
                    chapters[cur][0] -= 1
                    chapters[cur][1] -= len(cit["strip"].sub("", t))
                    chapter_texts[cur].pop()
                    placeholder_only_paras.append((cur, t[:44]))
            # 样式健全性：标题样式却是长段落 → 正文被套了标题样式
            if HEADING_STYLE_PAT.match(style or ""):
                net = len(cit["strip"].sub("", t))
                if net > HEADING_BODY_MAX_CHARS:
                    heading_like_body.append((style, net, t[:44]))
            full_text_parts.append(t)
            body_seq.append(("p", t, cur))
            if cur not in MANUAL_CHAPTERS:
                auto_text_parts.append(t)
            last_texts.append(t)
        elif el.tag.endswith("}tbl"):
            tb = Table(el, doc)
            nr = len(tb.rows)
            nc = len(tb.rows[0].cells) if tb.rows else 0
            first = " ".join(c.text.strip() for c in tb.rows[0].cells) if tb.rows else ""
            tables.append((nr, nc, first, tb))
            body_seq.append(("t", nc, cur))
            if cur:
                chapter_tables[cur] = chapter_tables.get(cur, 0) + 1
    full_text = "\n".join(full_text_parts)
    # 人工维护章节（三、七）的模版原文允许保留，故这些检查一律走 auto_text
    auto_text = "\n".join(auto_text_parts)


    # ---- 1. 释义表（当前不要求生成，仅提示）----
    # 释义表已按决策暂不自动生成（术语定义易乱，由业务在初稿中自行维护），因此不再判 FAIL
    glossary = [t for t in tables if t[0] >= 40 and t[1] == 3 and "简称" in t[2]]
    if glossary:
        results.append(("PASS", "释义表", "存在（%d行×3列，来自初稿）" % glossary[0][0]))
    else:
        results.append(("INFO", "释义表", "未检测到（当前不要求自动生成，由业务在初稿中维护；不阻断交付）"))

    # ---- 2. 章节门槛 ----
    for prefix, (min_p, min_c, name) in CHAPTER_MIN.items():
        got = chapters.get(prefix)
        if not got:
            results.append(("FAIL", "章节 %s%s" % (prefix, name), "章节缺失"))
            continue
        p_n, c_n = got
        if p_n < min_p or c_n < min_c:
            results.append(("FAIL", "章节 %s%s" % (prefix, name),
                            "内容不足：%d段/%d字（门槛≥%d段/%d字；字数已剔除来源括注）"
                            "——正文需按 mapping_rules+段落模板写实" % (p_n, c_n, min_p, min_c)))
        else:
            results.append(("PASS", "章节 %s%s" % (prefix, name), "%d段/%d字（净正文）" % (p_n, c_n)))

    # 人工维护章节：只报当前存量，不判门槛（本 SKILL 不生成它们）
    for prefix in MANUAL_CHAPTERS:
        got = chapters.get(prefix)
        if got:
            results.append(("INFO", "章节 %s（业务维护）" % prefix,
                            "%d段/%d字——本 SKILL 不生成也不判门槛；若内容仍为模版原文，"
                            "需向用户说明「本章待业务自行填写」" % (got[0], got[1])))
        else:
            results.append(("INFO", "章节 %s（业务维护）" % prefix,
                            "未检测到——本 SKILL 不生成；交付时需向用户说明该章待业务填写"))

    # 纯占位符段（不计入上面的段数/字数）——含 phase4 自动插入的「运营满3年」门槛提示段。
    # 只报 INFO：它们是"等人来填/待核实"的批注，交付前应逐条处置并删除提示段。
    if placeholder_only_paras:
        by_ch = {}
        for ch, txt in placeholder_only_paras:
            by_ch.setdefault(ch or "（附件/无章）", []).append(txt)
        results.append(("INFO", "占位符批注段（未计入篇幅）",
                        "共 %d 段整段为占位符的批注，**已从章节段数/字数中剔除**（占位符不能凑篇幅）："
                        "%s。其中「【待确认：运营时间…】」是 phase4 对「运营满3年」门槛的自动提示"
                        "（只提示不阻断），核实后应删除该段"
                        % (len(placeholder_only_paras),
                           "；".join("%s %s" % (ch, "、".join(v[:3]))
                                    for ch, v in by_ch.items()))))


    # ---- 3. 第一章逐节实质正文（按章内文本判断）----
    # 无论初稿是否已填表格，本项都要看：表格已填 ≠ 本章已完成。
    ch1_texts = chapter_texts.get("一、", [])
    if not ch1_texts:
        results.append(("FAIL", "第一章分节", "第一章无正文——派 ch1 子agent按 ch1_guide.md 撑写"))
    else:
        ch1_secs = split_sections(ch1_texts)
        ch1_project = {}
        try:
            if work_dir:
                with open(os.path.join(work_dir, "extracted_data.json"), "r", encoding="utf-8-sig") as f:
                    ch1_project = (json.load(f).get("project_info") or {})
        except Exception:
            ch1_project = {}
        for sk, (min_p, min_c, need_cit, sname) in CH1_SECTION_MIN.items():
            # 小节标题本身不算实质正文；表标题（「表N …」）与表下注（「注N：…」）也不算
            # ——历史事故正是“只有表标题和注释、一段正文没有”却蒙过了整章门槛。
            body = [t for t in (ch1_secs.get(sk) or [])
                    if not re.match(r"^（[一二三四五六七八九十]+）", t)
                    and not re.match(r"^表\s*[\d－\-一-鿿]{0,8}[\s　]", t)
                    and not re.match(r"^注\s*\d*\s*[：:]", t)]
            n_p = len(body)
            n_c = sum(len(cit["strip"].sub("", t)) for t in body)
            if not body:
                results.append(("FAIL", "第一章分节[%s]" % sk,
                                "**本节无实质正文**（只有小节标题/表标题/表下注）——%s。"
                                "表格已填不等于本章已完成，派 ch1 子agent补正文（match=小节标题的 paragraphs 条目，"
                                "replace 首段为标题原文 + styles 声明标题样式，见 ch1_guide.md）" % sname))
            elif n_p < min_p or n_c < min_c:
                results.append(("FAIL", "第一章分节[%s]" % sk,
                                "内容不足：%d段/%d字（门槛≥%d段/%d字，已剔除标题/表标题/表下注与来源括注）"
                                "——%s" % (n_p, n_c, min_p, min_c, sname)))
            else:
                results.append(("PASS", "第一章分节[%s]" % sk, "%d段/%d字（净正文）" % (n_p, n_c)))
            # 来源括注：本节至少有一处（（二）写「不涉及。」时免检）
            if need_cit and body:
                if any(cit["detect"].search(t) for t in body):
                    results.append(("PASS", "第一章来源[%s]" % sk, "已标注"))
                else:
                    results.append(("FAIL", "第一章来源[%s]" % sk,
                                    "本节正文**未标任何来源括注**——第一章每个小节都必须在段末注明"
                                    "数据/说法的引用来源（话术见 citation_rules.json，取不到写 【待填写】）；"
                                    "汇报时不得以“数据来自初稿/摘要表”为由省略"))

        industry = str(ch1_project.get("industry") or "")
        if "数据中心" in industry or "新型基础设施" in industry:
            sec2_body = [cit["strip"].sub("", t).strip() for t in (ch1_secs.get("（二）") or [])
                         if not t.startswith("（二）")]
            sec2_body = [t for t in sec2_body if t]
            if sec2_body == ["不涉及。"]:
                results.append(("PASS", "第一章特殊限定简洁性", "仅写「不涉及。」"))
            else:
                results.append(("FAIL", "第一章特殊限定简洁性",
                                "数据中心/新型基础设施不命中4类特殊业态，（二）正文必须只有「不涉及。」；"
                                "当前为：%s" % " / ".join(sec2_body[:3])))

        if str(ch1_project.get("issuance_type") or "").strip() == "首发":
            sec3_text = "\n".join(cit["strip"].sub("", t) for t in (ch1_secs.get("（三）") or []))
            forbidden = [x for x in ("无扩募", "不涉及扩募", "扩募项目可不提供", "扩募豁免") if x in sec3_text]
            if "本项目为首次发行项目。" not in sec3_text or forbidden:
                results.append(("FAIL", "第一章首发分支简洁性",
                                "首发项目（三）应以「本项目为首次发行项目。」开头并直接写可扩募资产；"
                                "不得展开无扩募/扩募豁免分支%s" %
                                ("；命中：" + "、".join(forbidden) if forbidden else "")))
            else:
                results.append(("PASS", "第一章首发分支简洁性", "首发口径简洁"))

    # ---- 3b. 第四章必备小节（按章内文本判断，避免第二章/第七章串味）----
    ch4_text = "\n".join(chapter_texts.get("四、", []))
    if not ch4_text:
        results.append(("FAIL", "第四章要素", "第四章无正文——重跑 ch4 子agent"))
    else:
        for kw in CH4_REQUIRED:
            if kw in ch4_text:
                results.append(("PASS", "第四章要素[%s]" % kw, "存在"))
            else:
                results.append(("FAIL", "第四章要素[%s]" % kw,
                                "本章内缺失——评估报告可能没读全，回补提取后撰写"))

    # ---- 3b-2. 表标题 ↔ 表格实体配对（抓「有表标题、没有表格结构」）----
    # 这是第四章的实测事故：正文提到 15 张表（表4-1~表4-15），实际只有 3 张有表格实体。
    # 段数/字数/要素/占位符全 PASS，唯一能暴露它的就是本项。
    orphans = check_caption_pairing(body_seq)
    if orphans:
        by_ch = {}
        for cap, ch, kind in orphans:
            by_ch.setdefault(ch or "（附件/无章）", []).append(cap)
        results.append(("FAIL", "表标题配对",
                        "有 %d 个表/图标题**后面没有表格实体**（悬空引用，评审端一眼可见）：%s。"
                        "两个根因逐个查：①**表没生成**——`fill_plan_phase4.phase7.json` 的 "
                        "insert_tables 靠表标题段锚点定位，若 ch4 子agent写的标题与 "
                        "phase4_blueprints.json 的 $anchor_contract.$table_titles 不逐字一致、"
                        "或该批次没应用（必须在 ch4 之后、phase5.phase6 之后、**全局最后**），"
                        "表就插不进去（fill_docx.py 现在会以 exit=1 + structure_not_applied 报出）；"
                        "②**数据源为空**——extracted_data 的 operating_performance.* 缺该表数据"
                        "（第四章 valuation_params.* 的典型根因是评估报告只提取了前几页摘要），"
                        "蓝图脚本会整表跳过并在 .todo.json 写 table_new_skipped/table_new_placeholder。"
                        "处置：能补数据就补齐后重跑 phase4 并重新应用 .phase7.json；"
                        "确实不涉及的表，**必须连同正文里的表标题段与「下表列示…」一并删除**，"
                        "不允许留悬空标题"
                        % (len(orphans),
                           "；".join("%s %s" % (ch, "、".join(caps[:6]))
                                    for ch, caps in by_ch.items()))))
    else:
        results.append(("PASS", "表标题配对", "所有表/图标题后面都有表格实体"))

    # ---- 3b-2b. 表号编号连续性 + 正文硬编码表号（rebuild_tables/「表#」纪律配套）----
    # 生成侧一律写「表#」占位，交付前由 renumber_tables.py 全篇统一赋号（跨章连续）。
    # 因此：①caption 编号应为 表1..表N 连续无重复，残留「表#」或复合式「表4-1」= 还没跑重排；
    # ②正文引用一律写「下表」「如下表所示」，硬编码表号在重排后必然指错 → WARN 列样例。
    # 两项均 WARN 不阻断（中途校验时重排尚未执行属正常），但交付前应清零。
    _cap_simple = re.compile(r"^表\s*(\d+)\s*[\s　]")
    _cap_hash = re.compile(r"^表\s*[#＃]")
    _body_ref = re.compile(r"(?<![报附])表\d+")
    cap_nums, cap_odd, hardcoded = [], [], []
    for el in body_seq:
        if el[0] != "p":
            continue
        text = el[1].strip()
        if TABLE_CAPTION_PAT.match(text) or _cap_hash.match(text):
            m = _cap_simple.match(text)
            if m:
                cap_nums.append(int(m.group(1)))
            else:
                cap_odd.append(text[:24])   # 表# / 表4-1 等非终态编号
        elif _body_ref.search(text):
            hardcoded.append((text[:40], el[2] or "（附件/无章）"))
    if cap_odd:
        results.append(("WARN", "表号重排",
                        "有 %d 个表标题仍是「表#」占位或复合式编号（%s…）——交付前必须跑 "
                        "`python renumber_tables.py --input <成稿> --output <终稿>` 全篇统一赋号"
                        % (len(cap_odd), "、".join(cap_odd[:4]))))
    elif cap_nums:
        if cap_nums == list(range(1, len(cap_nums) + 1)):
            results.append(("PASS", "表号连续性", "表1~表%d 按出现顺序连续无重复" % len(cap_nums)))
        else:
            dup = sorted({n for n in cap_nums if cap_nums.count(n) > 1})
            results.append(("WARN", "表号连续性",
                            "caption 编号不是 1..%d 连续序列（实际：%s%s）——跑 renumber_tables.py 重排"
                            % (len(cap_nums),
                               ",".join(str(n) for n in cap_nums[:20]),
                               "；重复：%s" % dup if dup else "")))
    if hardcoded:
        by_ch = {}
        for txt, ch in hardcoded:
            by_ch.setdefault(ch, []).append(txt)
        results.append(("WARN", "正文硬编码表号",
                        "有 %d 段正文（非表标题）出现「表N」引用——表号由 renumber_tables.py 统一重排，"
                        "硬编码引用重排后会指错表；正文应改写「下表」「如下表所示」。样例：%s"
                        % (len(hardcoded),
                           "；".join("%s %s" % (ch, "、".join(v[:3])) for ch, v in list(by_ch.items())[:5]))))
    else:
        results.append(("PASS", "正文硬编码表号", "正文无硬编码「表N」引用"))

    # ---- 3b-3. 各章表格实体数（第四章 <12 = phase7 新建表整批漏做）----
    for prefix, (min_t, hint) in CHAPTER_MIN_TABLES.items():
        if prefix not in chapters:
            continue
        got = chapter_tables.get(prefix, 0)
        if got < min_t:
            results.append(("FAIL", "表格实体[%s]" % prefix.rstrip("、"),
                            "本章只有 %d 张表（门槛≥%d）——%s。"
                            "先看 fill_plan_phase4.phase7.json 是否生成且已应用、"
                            "`gen_phase_fill_plan.py` 的 .todo.json 里有多少 "
                            "table_new_skipped/table_new_placeholder（数据源为空），"
                            "再看 fill_docx.py 执行时的 structure_not_applied（锚点对不上）"
                            % (got, min_t, hint)))
        else:
            results.append(("PASS", "表格实体[%s]" % prefix.rstrip("、"),
                            "本章 %d 张表" % got))

    # ---- 3.1 第五章必备要素 + 分节篇幅（缺=对应小节整段跳过或内容写错节）----
    # 同第六章：按**章内文本**判断。用 full_text 会被其它章的"无异议函/可转让性"串味。
    ch5_texts = chapter_texts.get("五、", [])
    ch5_text = "\n".join(ch5_texts)
    if not ch5_text:
        results.append(("FAIL", "第五章要素", "第五章无正文——按 chapter_writer_prompt.md 第五章特殊说明重跑 ch5 子agent"))
    else:
        for kw, hint in CH5_REQUIRED:
            if kw in ch5_text:
                results.append(("PASS", "第五章要素[%s]" % kw, "存在"))
            else:
                results.append(("FAIL", "第五章要素[%s]" % kw,
                                "本章内缺失——%s；重跑 ch5 子agent 时按 chapter_writer_prompt.md 的第五章特殊说明补写" % hint))
        # 分节篇幅：整章达标但某节空/某节撑爆 = 内容写进了错误小节（本章头号历史事故）
        ch5_secs = split_sections(ch5_texts)
        for sk, (min_p, min_c, sname) in CH5_SECTION_MIN.items():
            ps = ch5_secs.get(sk) or []
            n_p = len(ps)
            n_c = sum(len(cit["strip"].sub("", t)) for t in ps)
            if not ps:
                results.append(("FAIL", "第五章分节[%s]" % sk,
                                "本节完全缺失（找不到「%s」小节标题）——%s" % (sk, sname)))
            elif n_p < min_p or n_c < min_c:
                results.append(("FAIL", "第五章分节[%s]" % sk,
                                "内容不足：%d段/%d字（门槛≥%d段/%d字）——%s。"
                                "若整章总量达标而本节不足，说明**内容被写进了错误的小节**："
                                "检查 fill_plan_ch5.json 的条目数是否超过 30（模版可替换锚点数），"
                                "match 是否取自 manifest.json 的 anchor_map，"
                                "并用 fill_docx.py --validate-only 复核 section 归属"
                                % (n_p, n_c, min_p, min_c, sname)))
            else:
                results.append(("PASS", "第五章分节[%s]" % sk, "%d段/%d字（净正文）" % (n_p, n_c)))
        # 标题层级完整性：按**样式**核对每个 H2 下的 H3 个数
        h3_map, bad_h2 = count_h3_by_section(chapter_pairs.get("五、", []))
        for cn, st in bad_h2:
            results.append(("FAIL", "第五章层级[%s]" % cn,
                            "小节标题的样式是 %s，不是 Heading 2 —— Word 导航窗格里层级会错。"
                            "若基底是初稿，需在 fill_plan 中用 style 字段修正" % st))
        for sk, (want, hint) in CH5_EXPECTED_H3.items():
            got = h3_map.get(sk)
            if got is None:
                continue  # 该小节整体缺失，已由「第五章分节」报出，不重复报
            if len(got) < want:
                results.append(("FAIL", "第五章层级[%s]" % sk,
                                "Heading 3 子标题只有 %d 个（期望 %d 个）：%s。应有：%s"
                                % (len(got), want,
                                   "、".join(g[:16] for g in got) or "无", hint)))
            else:
                results.append(("PASS", "第五章层级[%s]" % sk,
                                "%d 个 Heading 3 子标题" % len(got)))

    # ---- 3.2 第六章必备要素 + （三）保障措施编号要点数 ----
    # 本章模版只有1段指导文字，最容易整节漏写 → 必须按要素硬查。
    # 与第四/五章一致，一律按**章内文本**判断（"关联交易""同业竞争"在第五章也会出现，
    # 用 full_text 会把第五章的内容误当成第六章已写，放过真正的缺口）。
    ch6_texts = chapter_texts.get("六、", [])
    ch6_text = "\n".join(ch6_texts)
    if not ch6_text:
        results.append(("FAIL", "第六章要素", "第六章无正文——按 chapter_writer_prompt.md 第六章特殊说明重跑 ch6 子agent"))
    else:
        for kws, hint in CH6_REQUIRED:
            if any(k in ch6_text for k in kws):
                results.append(("PASS", "第六章要素[%s]" % kws[0], "存在"))
            else:
                results.append(("FAIL", "第六章要素[%s]" % kws[0],
                                "缺失——%s；重跑 ch6 子agent 时按 chapter_writer_prompt.md 的第六章特殊说明补写" % hint))
        # （三）保障措施是本章最大篇幅所在，被压缩时表现为编号要点只剩 1~2 个
        pts = sorted({int(mm.group(1)) for mm in
                      (CH6_NUMBERED_PAT.match(t) for t in chapter_texts.get("六、", [])) if mm})
        if len(pts) >= CH6_MIN_NUMBERED_POINTS:
            results.append(("PASS", "第六章编号要点", "%d 个（≥%d）" % (len(pts), CH6_MIN_NUMBERED_POINTS)))
        else:
            results.append(("FAIL", "第六章编号要点",
                            "仅 %d 个（门槛≥%d）——（三）保障措施被压缩了。标准要点清单：①战略定位与品牌效应延续 "
                            "②运营管理模式与核心团队不变 ③收费具奖惩效果 ④原始权益人继续持有X%%份额 "
                            "⑤承诺降低同业竞争风险 ⑥基金层面项目管理机制（职能分离/重大事项基金层面决策/关联方回避表决）"
                            % (len(pts), CH6_MIN_NUMBERED_POINTS)))
        # 标题层级：4 个小节标题必须是 Heading 2（抓「层级塌平」）
        # 本章模版只有 1 段 Normal 指导文字，4 个小节标题全靠 fill_plan 的 `\n` 新建，
        # 新建的标题不会自己变成标题样式 → 实测事故：4 个全落成 Normal，Word 导航窗格里
        # 第六章下面一个子节都没有；而整章段数/字数完全达标，只有按样式检查才暴露。
        ch6_pairs = chapter_pairs.get("六、", [])
        for prefix, kws, want in CH6_EXPECTED_H2:
            hits = [(t, st) for t, st in ch6_pairs
                    if t.startswith(prefix) and any(k in t for k in kws)]
            if not hits:
                results.append(("FAIL", "第六章层级[%s]" % prefix,
                                "找不到小节标题「%s」——该小节疑似整段缺失，重跑 ch6" % want))
                continue
            t, st = hits[0]
            if HEADING_STYLE_PAT.match(st or "") and "2" in (st or ""):
                results.append(("PASS", "第六章层级[%s]" % prefix, "Heading 2（%s）" % t[:20]))
            else:
                results.append(("FAIL", "第六章层级[%s]" % prefix,
                                "小节标题「%s」的样式是 %s，不是 Heading 2 —— Word 导航窗格与"
                                "自动目录里第六章下面看不到这个子节（层级塌平）。"
                                "根因：本章模版锚点段是 Normal，靠 `\\n` 新建的标题不会自动升级。"
                                "对策：fill_plan_ch6.json 的条目**不要**写 auto_heading:\"off\""
                                "（fill_docx.py 默认 \"h2\" 会自动把「（一）xxx」升为 Heading 2），"
                                "或用 styles 显式声明该分段为 \"Heading 2\"，然后重新应用"
                                % (t[:24], st or "(无样式)")))
        # 数据可信度：三个「默认值顶替」陷阱（费用层级/战略配售比例/承诺函时态）
        results.extend(check_ch6_data_fidelity(ch6_texts, work_dir))

    # ---- 4. 摘要表关键字段 ----
    if tables:
        summary = tables[0][3]
        filled, missing = [], []
        # 同一关键词可能子串命中多行（如「原始权益人」同时命中「原始权益人」行和
        # 「原始权益人及相关方认购基金比例」行——后者是人工填写项），
        # 只取键最短（最贴合关键词本义）的那一行判定，避免误报 FAIL。
        best = {}  # req -> (键长度, 是否已填)
        for row in summary.rows:
            if len(row.cells) < 2:
                continue
            k = row.cells[0].text.strip()
            v = row.cells[1].text.strip()
            for req in SUMMARY_REQUIRED:
                if req in k:
                    ok_v = bool(v) and not PLACEHOLDER_PAT.search(v)
                    if req not in best or len(k) < best[req][0]:
                        best[req] = (len(k), ok_v)
        for req, (_, ok_v) in best.items():
            (filled if ok_v else missing).append(req)
        for req in set(missing):
            results.append(("FAIL", "摘要表[%s]" % req, "为空或占位符——该数据在评估/审计/权属材料中有，必须提取填入"))
        if not missing:
            results.append(("PASS", "摘要表关键字段", "已填：%s" % "、".join(sorted(set(filled)))))
    else:
        results.append(("FAIL", "摘要表", "文档无表格"))

    # ---- 5. 模版残留 ----
    residues = [kw for kw in TEMPLATE_RESIDUE if kw in auto_text]
    if residues:
        results.append(("FAIL", "模版残留", "存在指导文字/附件模版内容：%s ——用 replace_ranges 清理（附件1证明材料目录、附件2法律意见书必备内容整段删除）" % "、".join(residues)))
    else:
        results.append(("PASS", "模版残留", "无"))

    # ---- 5b. 【最大问题】指导性文字逐段检测 ----
    guidance_hits = [t for t in auto_text_parts if GUIDANCE_PAT.search(t)]
    n_gd = len(guidance_hits)
    if n_gd > GUIDANCE_FAIL_THRESHOLD:
        sample = "；".join(t[:28] for t in guidance_hits[:5])
        results.append(("FAIL", "指导文字未替换",
                        "残留 %d 段模版指导文字（>%d）——这是漏写正文的最大信号：fill_plan 的 paragraphs 条目写少了，"
                        "需逐段用实质内容替换（套 text_templates 段落模板 + extracted_data）。示例：%s" % (n_gd, GUIDANCE_FAIL_THRESHOLD, sample)))
    elif n_gd > 0:
        results.append(("WARN", "指导文字未替换", "残留 %d 段，逐段确认是否应改写为实质内容" % n_gd))
    else:
        results.append(("PASS", "指导文字未替换", "无残留"))

    # ---- 5c. 样式健全性：正文被套了标题样式 ----
    # fill_docx 段落替换保留 <w:pStyle>、拆段时 deepcopy 整个 <w:p>，所以锚点若是
    # Heading 样式（初稿常见，或子agent误拿小节标题当 match），替换出的正文会全部带标题样式：
    # Word 里显示为大号加粗、层级分不出来，还把几十段正文塞进导航窗格与自动目录。
    n_hb = len(heading_like_body)
    if n_hb > HEADING_BODY_FAIL_THRESHOLD:
        sample = "；".join("%s(%d字)「%s…」" % (s, n, t) for s, n, t in heading_like_body[:3])
        results.append(("FAIL", "正文误用标题样式",
                        "有 %d 段正文套了标题样式（净字数>%d，几乎不可能是真标题）——Word 里显示为"
                        "大号加粗、分不出层级，并污染导航窗格/自动目录。根因二选一：①基底初稿本身把"
                        "指导文字设成了 Heading（在 fill_plan 用 style 字段改回正文）；"
                        "②fill_plan 拿小节标题当了 match（预检的 paragraph_style 会拦，"
                        "应改用该小节内的指导文字段落作锚点）。示例：%s"
                        % (n_hb, HEADING_BODY_MAX_CHARS, sample)))
    elif n_hb:
        results.append(("WARN", "正文误用标题样式",
                        "有 %d 段标题样式段落净字数>%d，逐段确认是否为正文误用：%s"
                        % (n_hb, HEADING_BODY_MAX_CHARS,
                           "；".join("%s「%s…」" % (s, t) for s, _, t in heading_like_body[:3]))))
    else:
        results.append(("PASS", "正文误用标题样式", "无"))

    # ---- 6. 占位符 ----
    noncanonical = NONCANONICAL_PLACEHOLDER_PAT.findall(auto_text)
    for _, _, _, tb in [(0, 0, "", t[3]) for t in tables]:
        for row in tb.rows:
            for c in row.cells:
                noncanonical += NONCANONICAL_PLACEHOLDER_PAT.findall(c.text)
    if noncanonical:
        results.append(("FAIL", "占位符格式",
                        "存在%d个非规范占位符；Word中只允许精确文字【待填写】，"
                        "缺失原因/字段路径应放入todo。示例：%s" %
                        (len(noncanonical), "、".join(noncanonical[:5]))))
    else:
        results.append(("PASS", "占位符格式", "全部统一为【待填写】"))
    ph_list = PLACEHOLDER_PAT.findall(auto_text)
    for _, _, _, tb in [(0, 0, "", t[3]) for t in tables]:
        for row in tb.rows:
            for c in row.cells:
                ph_list += PLACEHOLDER_PAT.findall(c.text)
    n_ph = len(ph_list)
    if n_ph > PLACEHOLDER_FAIL_THRESHOLD:
        results.append(("FAIL", "占位符数量", "%d 个（>%d）——大量数据缺失，先看「输入数据体检」是否有缺口，"
                                          "缺口需向上游提取方补数据" % (n_ph, PLACEHOLDER_FAIL_THRESHOLD)))
    elif n_ph > 45:
        results.append(("WARN", "占位符数量", "%d 个，偏多，检查 data_missing 类是否可从材料回补" % n_ph))
    else:
        results.append(("PASS", "占位符数量", "%d 个" % n_ph))

    # ---- 7. 表格总数 ----
    if len(tables) < 30:
        results.append(("WARN", "表格总数", "%d 个（标准约37）——第四章估值/客户表格（表4-4~4-15类）可能未补插" % len(tables)))
    else:
        results.append(("PASS", "表格总数", "%d 个" % len(tables)))

    # ---- 8. 关键表行数完整性（防“只填几行就交”） ----
    for kw, min_rows, hint in KEY_TABLE_MIN_ROWS:
        matched = [t[0] for t in tables if kw in t[2]]
        if not matched:
            results.append(("WARN", "关键表[%s]" % kw, "未找到该表（首行含'%s'），确认是否应存在" % kw))
            continue
        got = max(matched)
        if got < min_rows:
            results.append(("FAIL", "关键表[%s]行数" % kw,
                            "仅 %d 行（门槛≥%d）——%s" % (got, min_rows, hint)))
        else:
            results.append(("PASS", "关键表[%s]行数" % kw, "%d 行" % got))

    # ---- 8b. 疑似结构损坏表（合并单元格错乱）----
    # 背景：初稿自带的表12（经营收益表）标签列被合并错位撕裂——数据值「40,804.96」
    # 独占标签列一行、「实际服务量」等行标签散落在不相邻的多行——这种表 5 轮迭代都
    # 没被任何检查报出来。校验器修不了它，但必须报出来让人工重排或整表替换。
    broken_tables = []
    for nr, nc, first, tb in tables:
        if nr < 6:
            continue
        firsts = [row.cells[0].text.strip() for row in tb.rows]
        # 症状1：纯数据值落在行首标签列（排除手续表的「1」「2」序号列，故要求长度>2）
        numeric_first = [f for f in firsts[1:]
                         if re.fullmatch(r"[\d,，.。%%\-]+", f or "") and len(f) > 2]
        # 症状2：同一行标签在**不相邻**的行重复出现（正常的纵向合并只会连续重复）
        pos = {}
        for i, f in enumerate(firsts):
            if f and not any(ch.isdigit() for ch in f) and 2 <= len(f) <= 14:
                pos.setdefault(f, []).append(i)
        scattered = [k for k, v in pos.items() if len(v) > 1 and v[-1] - v[0] > len(v) - 1]
        if numeric_first or len(scattered) >= 2:
            broken_tables.append("「%s…」(%d行,数值窜标签列%s,散落标签%s)"
                                 % (first[:16], nr, numeric_first[:2], scattered[:3]))
    if broken_tables:
        results.append(("WARN", "疑似结构损坏表",
                        "%d 张：%s——多为初稿自带的合并单元格错乱，AI 填表无法修复，"
                        "需人工重排结构或按官方模版整表重建后再填数"
                        % (len(broken_tables), "；".join(broken_tables))))
    else:
        results.append(("PASS", "疑似结构损坏表", "无"))

    # ---- 9. 来源标注覆盖率（所有AI填充内容必须标明来源）----
    # 实质段落 = 净正文≥min_chars 且不命中豁免（标题/衔接句/「不涉及」类短句）
    substantive, cited, uncited = 0, 0, []
    for t in full_text_parts:
        if any(pat.search(t) for pat in cit["exempt"]):
            continue
        if len(cit["strip"].sub("", t)) < cit["min_chars"]:
            continue
        substantive += 1
        if cit["detect"].search(t):
            cited += 1
        elif len(uncited) < 10:
            uncited.append(t[:40])
    cov = round(cited / substantive * 100, 1) if substantive else 100.0
    cit_audit = {"substantive_paragraphs": substantive, "cited_paragraphs": cited,
                 "coverage_pct": cov, "uncited_samples": uncited}
    if substantive == 0:
        results.append(("WARN", "来源标注覆盖率", "未识别到实质段落，无法评估（正文可能为空）"))
    elif cov < cit["fail_below"]:
        results.append(("FAIL", "来源标注覆盖率",
                        "仅 %.1f%%（%d/%d 段，门槛≥%.0f%%）——本项目要求所有AI填充内容在正文标明来源"
                        "（提取自/参考自/据…计算，话术见 templates/citation_rules.json）。"
                        "重跑对应章节子agent时须为每个 paragraphs 条目补 citations 字段。未标注示例：%s"
                        % (cov, cited, substantive, cit["fail_below"], "；".join(uncited[:3]))))
    elif cov < cit["warn_below"]:
        results.append(("WARN", "来源标注覆盖率",
                        "%.1f%%（%d/%d 段）——建议补齐；未标注示例：%s"
                        % (cov, cited, substantive, "；".join(uncited[:3]))))
    else:
        results.append(("PASS", "来源标注覆盖率", "%.1f%%（%d/%d 段）" % (cov, cited, substantive)))

    # ---- 10. 附件编号真实性（防子agent编造附件编号）----
    ref_nos = set()
    for t in full_text_parts:
        ref_nos.update(cit["attach_ref"].findall(t))
    for _, _, _, tb in tables:
        for row in tb.rows:
            for c in row.cells:
                ref_nos.update(cit["attach_ref"].findall(c.text))
    bogus = []
    if proofs_index_path:
        real = load_real_attachment_nos(proofs_index_path)
        if real is None:
            results.append(("WARN", "附件编号真实性",
                            "无法读取 proofs_index.json（%s），跳过校验" % proofs_index_path))
        elif not real:
            results.append(("WARN", "附件编号真实性",
                            "proofs_index.json 的 material_index 为空，跳过校验"))
        else:
            # 允许「父级编号」引用（如正文写 附件13-1 而实际文件为 13-1-5-1）
            bogus = sorted(n for n in ref_nos
                           if n not in real and not any(r.startswith(n + "-") for r in real))
            if bogus:
                results.append(("FAIL", "附件编号真实性",
                                "正文引用了 %d 个证明材料中不存在的附件编号：%s ——"
                                "这是编造来源，必须逐个核对改为真实编号（取自 proofs_index.json），"
                                "取不到就写 pending 话术【待填写：来源】"
                                % (len(bogus), "、".join("附件" + b for b in bogus[:12]))))
            else:
                results.append(("PASS", "附件编号真实性",
                                "正文引用的 %d 个附件编号均真实存在" % len(ref_nos)))
    else:
        results.append(("INFO", "附件编号真实性",
                        "未提供 --proofs-index，跳过（交付前建议加上，可拦截编造的附件编号）"))

    return results, n_ph, cit_audit, sorted(ref_nos), bogus


def load_real_attachment_nos(proofs_index_path):
    """从 proofs_index.json 读出全部真实材料编号（含文件级编号与其各级父编号）。

    正文既可能引用文件级编号「附件13-1-5-1」，也可能引用材料项级「附件13」，
    因此把每个文件编号的各级前缀都视为合法。
    """
    try:
        with open(proofs_index_path, encoding="utf-8") as f:
            idx = json.load(f)
    except Exception:
        return None
    mi = idx.get("material_index") if isinstance(idx, dict) else None
    if not isinstance(mi, dict):
        return None
    real = set()
    for no, files in mi.items():
        real.add(str(no))
        for rel in (files if isinstance(files, list) else []):
            # 编号可能挂在任意一级目录名上（如「28-1 绿电凭证/100张.jpg」），只看文件名会漏掉
            # 目录级编号；且需要求编号后接空格/结尾，防「100张.jpg」误提出假编号「100」
            for seg in str(rel).replace("\\", "/").split("/"):
                m = re.match(r"^(\d+(?:-\d+)*)(?=\s|$)", seg.strip())
                if not m:
                    continue
                parts = m.group(1).split("-")
                for k in range(1, len(parts) + 1):
                    real.add("-".join(parts[:k]))
    return real



def check_input_quality(docx_path, work_dir=None, proofs_index=None):
    """输入数据体检复核（第一步的交接校验，在交付前再看一眼）。

    本 SKILL 只负责内容填充，extracted_data.json 由上游提取方提供。章节写不够、
    表格为空、占位符多——**根因往往在输入数据本身**，所以把它放在报告最前面：
    先看输入有没有缺口，再决定"重跑章节"还是"找上游补数据"。

    返回 (level, name, detail, info)。判定一律现算自 extracted_data.json，
    不采信任何自报状态文件；**只诊断、不阻断**（本项永不 FAIL）。
    """
    name = "输入数据体检"
    if not _HAS_PIPELINE_STATE:
        return ("INFO", name, "无法加载 pipeline_state.py，跳过体检", {})
    wd = work_dir or infer_work_dir([proofs_index, docx_path])
    info = check_inputs(wd)
    if not info.get("extracted_data_ok"):
        return ("INFO", name,
                "未在 %s 找到可解析的 extracted_data.json（%s），跳过体检"
                "（可用 --work-dir 指定工作目录）" % (wd, info.get("extracted_data_error")), info)
    base = ("extracted_data %.1fKB；关键字段缺 %d 个；结构化数据源缺 %d 处；"
            "溯源字段覆盖率 %.1f%%"
            % (info["extracted_data_kb"], len(info["key_fields_missing"]),
               len(info["struct_sources_missing"]), info["prov_pct"]))
    if info["gaps"]:
        return ("WARN", name,
                "%s。数据缺口 %d 项：%s —— 这些缺口处只能是占位符，**属输入侧问题，重跑章节也补不出来**，"
                "须向用户/上游提取方列明"
                % (base, len(info["gaps"]), "；".join(info["gaps"])), info)
    return ("PASS", name, base, info)


def check_threshold_integrity(citation_requested=None, citation_floor=50.0):
    """门槛参数完整性（全局红线：严禁篡改校验门槛绕过校验）。

    本 SKILL 现在只有一个可调门槛：`--citation-threshold`（来源标注覆盖率）。
    它只允许**调高（加严）**；给出低于 citation_rules.json 的 fail_below 时会被抬回，
    并在本项报 FAIL —— 调低本身不影响判定（下限保护已化解），但属红线行为，
    交付汇报必须列明。章节篇幅/要素门槛写死在本脚本内，不接受命令行下调。
    """
    name = "门槛参数完整性"
    issues = []
    if citation_requested is not None and float(citation_requested) < float(citation_floor):
        issues.append("--citation-threshold %s 低于 citation_rules.json 的 fail_below %s（已抬回）"
                      % (citation_requested, citation_floor))
    if not issues:
        return ("PASS", name, "未发现门槛参数被调低（来源标注阈值 %s%%）" % citation_floor)
    return ("FAIL", name,
            "检测到 %d 处门槛参数被调低：%s。门槛只允许调高（加严），调低一律无效。"
            "本次交付汇报必须向用户列明" % (len(issues), "；".join(issues)))


def main():
    ap = argparse.ArgumentParser(description="申报材料产出自检（硬校验门槛）")
    ap.add_argument("docx", help="生成的申报材料docx")
    ap.add_argument("--json", default=None, help="JSON报告输出路径")
    ap.add_argument("--chapters-only", action="store_true",
                    help="轻量模式（默认推荐）：只看各章段数/字数是否达标 + 来源标注覆盖率，直接给出建议重跑的章号；不做其余校验，exit 恒为0")
    ap.add_argument("--proofs-index", default=None,
                    help="proofs_index.json 路径；提供后启用「附件编号真实性」校验（拦截编造的附件编号）")
    ap.add_argument("--citation-rules", default=None,
                    help="来源标注规则文件（默认 templates/citation_rules.json）")
    ap.add_argument("--citation-threshold", type=float, default=None,
                    help="来源标注覆盖率 FAIL 阈值%%（默认取 citation_rules.json 的 50）。"
                         "**只能调高（加严）**：低于默认值一律被抬回，并在「门槛参数完整性」项报 FAIL")
    ap.add_argument("--work-dir", default=None,
                    help="工作目录（默认由 --proofs-index / docx 所在目录推断）；用于复核输入数据完备性")
    args = ap.parse_args()

    rules = load_citation_rules(args.citation_rules)
    cit_floor = float(rules["fail_below"])
    # work_dir 供「第六章数据可信度」读 extracted_data.json 与正文对撞；
    # 未显式给出时与覆盖率复核同口径推断（--proofs-index / docx 所在目录）
    eff_work_dir = args.work_dir
    if not eff_work_dir and _HAS_PIPELINE_STATE:
        try:
            eff_work_dir = infer_work_dir([args.proofs_index, args.docx])
        except Exception:
            eff_work_dir = None
    results, n_ph, cit_audit, ref_nos, bogus = check(
        args.docx, citation_rules=rules, proofs_index_path=args.proofs_index,
        citation_threshold=args.citation_threshold, work_dir=eff_work_dir)

    # 输入数据体检：放在最前面报，因为它是"章节写不够/表格为空/占位符多"的根因项
    inp_lv, inp_name, inp_detail, inp_info = check_input_quality(
        args.docx, work_dir=eff_work_dir, proofs_index=args.proofs_index)
    results.insert(0, (inp_lv, inp_name, inp_detail))

    # 门槛参数完整性：揭发"调低来源标注阈值"这类绕过（红线：门槛只能调高）
    thr_lv, thr_name, thr_detail = check_threshold_integrity(
        citation_requested=args.citation_threshold, citation_floor=cit_floor)
    results.insert(1, (thr_lv, thr_name, thr_detail))

    # ---- 轻量模式：只看章节完备性 + 来源标注，决定要不要重跑某章子agent ----
    if args.chapters_only:
        ch_results = [r for r in results if r[1].startswith("章节 ")]
        # 必备要素虽属全量校验，但它能直接指出"某小节整段没写"，对"要不要重跑该章"最有判断力，
        # 故在轻量模式下一并输出（仅列出缺失项）
        elem_results = [r for r in results
                        if r[1].startswith("第四章要素") or r[1].startswith("第五章要素")
                        or r[1] == "表标题配对" or r[1].startswith("表格实体[")
                        or r[1].startswith("第五章分节") or r[1].startswith("第五章层级")
                        or r[1].startswith("第六章要素") or r[1] == "第六章编号要点"
                        or r[1].startswith("第六章层级") or r[1].startswith("第六章数据可信度")
                        # 第一章逐节门槛与逐节括注是硬校验，轻量模式漏显示会让 ch1 缺口不可见
                        or r[1].startswith("第一章分节") or r[1].startswith("第一章来源")
                        or r[1] == "正文误用标题样式"]
        cit_results = [r for r in results if r[1] in ("来源标注覆盖率", "附件编号真实性")]
        need_rerun = []
        print("=== 章节完备性检查: %s ===" % args.docx)
        inp_mark = {"PASS": "  [OK]  ", "INFO": "  [INFO]"}.get(inp_lv, "  ⚠ 缺口")
        print("%s %s: %s" % (inp_mark, inp_name, inp_detail))
        if inp_lv == "WARN":
            print("     ↑ 这是根因项：输入侧有缺口时，缺口处重跑章节也写不出内容——先找上游补数据")
        if thr_lv != "PASS":
            print("❌[FAIL] %s: %s" % (thr_name, thr_detail))
        print("")
        for lv, name, detail in ch_results:
            prefix = name.replace("章节 ", "")[:1]
            ch_no = CHAPTER_NO.get(prefix)
            # INFO = 人工维护章（三、七）的存量播报，不算不足、更不能建议重跑
            mark = "  [OK]  " if lv == "PASS" else ("  [INFO]" if lv == "INFO" else "  ⚠ 不足")
            print("%s %s: %s" % (mark, name, detail))
            if lv == "FAIL" and ch_no:
                need_rerun.append(ch_no)
        missing_elems = [r for r in elem_results if r[0] not in ("PASS", "INFO")]
        if missing_elems:
            print("\n必备要素/层级/样式问题（说明对应小节被整段跳过、内容写错节、或样式套错）：")
            for _, name, detail in missing_elems:
                print("  ⚠ %s: %s" % (name, detail))
                if name.startswith("第一章"):
                    ch_no = 1
                elif name.startswith("第四章"):
                    ch_no = 4
                elif name.startswith("第五章"):
                    ch_no = 5
                elif name.startswith("第六章"):
                    ch_no = 6
                else:
                    # 「正文误用标题样式」等非章节专属项：不归任何章，避免误判要重跑 ch6
                    ch_no = None
                if ch_no and ch_no not in need_rerun:
                    need_rerun.append(ch_no)
        print("\n来源标注：")
        for lv, name, detail in cit_results:
            mark = "  [OK]  " if lv == "PASS" else ("  [INFO]" if lv == "INFO" else "  ⚠ ")
            print("%s %s: %s" % (mark, name, detail))
        print("\n占位符共 %d 处" % n_ph)
        if need_rerun:
            print("建议重跑以下章节的子agent（用 chapter_writer_prompt.md，强调对标 ch{N}_example.md 篇幅"
                  "、每个 paragraphs 条目必须带 citations）：%s"
                  % "、".join("ch%d" % n for n in sorted(need_rerun)))
        else:
            print("✅ 各章篇幅与必备要素均达标，可交付。")
        if args.json:
            with open(args.json, "w", encoding="utf-8") as f:
                json.dump({"mode": "chapters-only",
                           "results": [{"level": l, "name": n, "detail": d}
                                       for l, n, d in [(inp_lv, inp_name, inp_detail),
                                                       (thr_lv, thr_name, thr_detail)]
                                       + ch_results + missing_elems + cit_results],
                           "input_quality": {"level": inp_lv, "detail": inp_detail,
                                             "inputs": inp_info},
                           "threshold_integrity": {"level": thr_lv, "detail": thr_detail},
                           "need_rerun": sorted(need_rerun), "placeholders": n_ph,
                           "citation_audit": cit_audit,
                           "bogus_attachment_nos": bogus},
                          f, ensure_ascii=False, indent=2)
        return

    n_fail = sum(1 for lv, _, _ in results if lv == "FAIL")
    n_warn = sum(1 for lv, _, _ in results if lv == "WARN")

    print("=== 产出自检: %s ===" % args.docx)
    for lv, name, detail in results:
        mark = {"PASS": "  [PASS]", "INFO": "  [INFO]", "WARN": "  [WARN]", "FAIL": "❌[FAIL]"}.get(lv, "  [%s]" % lv)
        print("%s %s: %s" % (mark, name, detail))
    print("\n合计: FAIL=%d WARN=%d 占位符=%d 来源标注覆盖率=%.1f%%（%d/%d段）"
          % (n_fail, n_warn, n_ph, cit_audit["coverage_pct"],
             cit_audit["cited_paragraphs"], cit_audit["substantive_paragraphs"]))
    if cit_audit["uncited_samples"]:
        print("未标注来源的实质段落（前%d条）：" % len(cit_audit["uncited_samples"]))
        for s in cit_audit["uncited_samples"]:
            print("  - %s" % s)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"results": [{"level": l, "name": n, "detail": d} for l, n, d in results],
                       "fail": n_fail, "warn": n_warn, "placeholders": n_ph,
                       "input_quality": {"level": inp_lv, "detail": inp_detail,
                                         "inputs": inp_info},
                       "threshold_integrity": {"level": thr_lv, "detail": thr_detail},
                       "citation_audit": cit_audit,
                       "attachment_refs": ref_nos,
                       "bogus_attachment_nos": bogus},
                      f, ensure_ascii=False, indent=2)

    if n_fail > 0:
        print("\n❌ 存在 FAIL 项：不得交付。逐项修复（向上游补数据/补写章节/清理模版残留/补来源标注）"
              "后重新生成并再次自检，直到全部 PASS。")
        sys.exit(1)
    print("\n✅ 自检通过，可进入交付复核。")



if __name__ == "__main__":
    main()
