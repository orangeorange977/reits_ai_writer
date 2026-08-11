#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
阶段B数据提取覆盖率校验（进入第四步前的硬门槛）。

双重核验：
  1. 自报进度：extracted_data.json 的 _metadata.read_items（材料文件编号如 "4-1"，或文件名）
  2. 客观证据：extracted_data.json 全文递归收集的 _source 值（含图片路径，按PDF名模糊反查）
两者取并集判定"已读"；只有自报、无任何 _source 佐证的材料标记为"可疑"。

门槛（任一不满足 exit=1，不得进入第四步）：
  - 整体文件覆盖率 >= 阈值（默认80%）
  - 核心材料（文件名含 审计报告/评估报告/估值报告/营业执照/不动产权证/法律意见书）必须100%已读
  - **核心材料必须"整份读完"（页级门槛）**：其全部页图 + txt 全文单元都要有已读证据。
    防的是"读了评估报告前3页摘要就算这份读过"——实测事故：一份数十页的评估报告只读了
    摘要页，evaluation 只填出8个摘要字段，现金流预测表/运营费用参数/资本性支出明细/
    可比实例/CPI/客户财务全缺，表4-4~表4-15 全是空表。这些内容只存在于报告中后部。
    报告里另给 core_render_gaps（页图数 < PDF总页数 = 阶段A漏渲，必须回阶段A补渲）。

【门槛参数只能调高（全局红线第5条）】
  `--threshold` 低于 80 时**拒绝执行（exit=2）且不写报告**；`--critical-keywords` 只能追加、
  删默认项无效。原因：报告里的 threshold_pct 会被下游三处硬联锁继承，调低它等于无痕放低整条
  流水线的门槛。唯一合法越权通道是 `--force-low-coverage`（记 gate_bypasses + 第五步必 FAIL）。

输出报告含按材料项分组的未读清单 + 对应 key_fields 提示（来自 extraction_groups.json），
供主agent直接回阶段B按清单补读。

三种运行模式：
  ① 门槛模式（默认）：不达标 exit=1，用于阶段B收尾判定；
  ② 页级驱动模式（`--next-pages N`，**阶段B默认用这个**）：按**页图张数**配额给出下一批，
     并打印本批**全部页图的完整路径**（可直接一条消息里并行读完，不需要再 list 目录），
     exit 恒为 0。批次单位是"张"而不是"份"，避免一份上百页的审计/评估报告独占一轮导致
     并行退化成串行；
  ③ 文件级驱动模式（`--next N`，兼容旧口径）：打印下一批该读的 N **份**文件。

配套的进度登记（避免手工编辑大 JSON 造成的串行写盘瓶颈）：
  `--mark-read 4-1,4-2`   把材料编号/文件名追加进 `_metadata.read_items`
  `--mark-pages p1,p2`    把已读页图路径追加进 `_metadata.read_pages`（也接受 `@清单文件`）
  `--mark-batch`          把上一轮 `--next-pages` 打印的整批页图一次性登记为已读
  以上均由脚本原子写入，可与 `--next-pages` 串在同一条命令里：先登记、再取下一批。

⚠️ `read_pages` 只影响**页级队列**的推进，不影响文件级覆盖率门槛（门槛仍按
   `read_items ∪ 全文 _source` 双重核验，见 build_coverage），因此登记页码无法放低门槛：
   没写 `_source` 的材料照样会进"仅自报可疑"清单。

用法:
  python check_extraction_coverage.py --proofs-index <proofs_index.json> \
      --extracted <extracted_data.json> --output <coverage_report.json> [--threshold 80]
  python check_extraction_coverage.py --proofs-index <...> --extracted <...> --next-pages 8
  python check_extraction_coverage.py --proofs-index <...> --extracted <...> \
      --mark-batch --mark-read 4-1 --next-pages 8
"""

import argparse
import json
import math
import os
import re
import sys
import tempfile

# Windows GBK 控制台/管道下打印 ✅❌ 等字符不崩溃（无法编码的字符替换为 ?）
try:
    sys.stdout.reconfigure(errors='replace')
    sys.stderr.reconfigure(errors='replace')
except Exception:
    pass

script_dir = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CATALOG = os.path.join(script_dir, '..', 'templates', 'standard_proof_catalog.json')
DEFAULT_GROUPS = os.path.join(script_dir, '..', 'templates', 'extraction_groups.json')

DEFAULT_CRITICAL_KEYWORDS = ['审计报告', '评估报告', '估值报告', '营业执照', '不动产权证', '法律意见书']

# 【门槛参数下限保护｜红线第5条】阈值只允许调高（加严）。低于本值时**拒绝执行**（exit=2），
# 不写报告——因为一旦把低阈值写进 extraction_coverage.json 的 threshold_pct，下游三处硬联锁
# 会继承它，等于无痕降低整条流水线的门槛（比 --force-low-coverage 更危险）。
# 确有正当理由要低阈值，只能显式加 --force-low-coverage：留痕 + 第五步必 FAIL。
THRESHOLD_FLOOR = 80.0

# 合规材料（标准骨架第13~21项）：第五章表15~表22 逐份成行，漏一份就少一行 → 优先级仅次于核心材料
COMPLIANCE_ITEM_NOS = {str(n) for n in range(13, 22)}



def norm(s):
    """规范化字符串用于模糊匹配：小写、统一斜杠、去空白"""
    return re.sub(r'\s+', '', str(s).lower().replace('\\', '/'))


def file_stem(path):
    """取文件名主干（不含目录与扩展名）"""
    base = os.path.basename(str(path).replace('\\', '/'))
    stem, _ = os.path.splitext(base)
    return stem


def extract_file_number(filename):
    """与 scan_proofs.py 完全一致：'4-1 xxx.pdf' -> '4-1'（不做额外裁剪，保持编号口径统一）"""
    match = re.match(r'^(\d+[-\d]*)', str(filename).strip())
    if match:
        return match.group(1)
    return None


def collect_sources(obj, out):
    """递归收集 extracted_data.json 中所有 _source/_sources 的值"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ('_source', '_sources'):
                if isinstance(v, str):
                    out.append(v)
                elif isinstance(v, list):
                    out.extend(str(x) for x in v if x)
            else:
                collect_sources(v, out)
    elif isinstance(obj, list):
        for item in obj:
            collect_sources(item, out)


def build_stem_set(paths):
    """把每条路径按 / 拆成组件，各组件去扩展名+规范化后加入集合。
    精确集合匹配（而非拼接大字符串子串匹配），避免
    '4-1 审计报告' 误命中 '4-1 审计报告（更新版）' 这类结构性假阳性。
    组件级拆分可命中 images/<pdf主干>/page_003.png 中的目录名。"""
    stems = set()
    for p in paths:
        for comp in str(p).replace('\\', '/').split('/'):
            comp = comp.strip()
            if not comp:
                continue
            stem, _ = os.path.splitext(comp)
            n = norm(stem)
            if n:
                stems.add(n)
    return stems


PAGE_NAME_RE = re.compile(r'^page[_\-]?(\d+)$', re.I)

# txt 单元的等效"张数"折算：读一份大 txt 的开销远大于一张页图，
# 按体积折算权重（每 200KB 计 1 张，上限 4），避免一轮塞进 8 份大 txt。
TXT_KB_PER_UNIT = 200.0
TXT_MAX_WEIGHT = 4

LAST_BATCH_FILE = '.stageB_last_batch.json'

# 页级配额默认值：一轮 8 张（与 SKILL「一批并行读 6~8 张」口径一致）
DEFAULT_PAGE_QUOTA = 8
# 文件级模式下单份材料最多列几张页图路径（防止一份上百页的报告刷屏）
PAGE_LIST_CAP = 12


def page_num(path):
    """从 page_003.png 取出页序号；取不到返回 10**9（排到最后）"""
    m = PAGE_NAME_RE.match(file_stem(path))
    return int(m.group(1)) if m else 10 ** 9


def txt_weight(kb):
    """txt 单元的等效张数（1~TXT_MAX_WEIGHT）"""
    if not kb:
        return 1
    return max(1, min(TXT_MAX_WEIGHT, int(math.ceil(float(kb) / TXT_KB_PER_UNIT))))


def collect_page_pairs(paths):
    """把路径集合压成 {(材料目录名, 页文件名)} 的规范化对，用于判定"这一张读过没有"。

    页图实际路径可能是 images/<主干>/page_003.png，也可能多套一层安全名
    images/<主干>/<安全名>/page_003.png；而 agent 写进 _source / read_pages 的路径
    未必与磁盘层级完全一致。因此不做整串比较，而是把"页文件名"与该路径上**每一个**
    祖先目录名两两配对 —— 只要材料主干出现在路径任一层，就能命中。"""
    pairs = set()
    for p in paths:
        comps = [c.strip() for c in str(p).replace('\\', '/').split('/') if c.strip()]
        if not comps:
            continue
        page_comps = [c for c in comps if PAGE_NAME_RE.match(os.path.splitext(c)[0])]
        if not page_comps:
            continue
        for pc in page_comps:
            pn = norm(os.path.splitext(pc)[0])
            for other in comps:
                if other == pc:
                    continue
                on = norm(os.path.splitext(other)[0])
                if on:
                    pairs.add((on, pn))
    return pairs


def is_page_read(material_stem, page_path, page_pairs):
    """某材料的某一张页图是否已读（(材料主干, 页名) 命中即为已读）

    已知限制：同一材料若在 images/ 下有**两套**子目录含同名 page_001（多套安全名），
    两张会被同一 pair 判成都已读。batch_render_pdfs 对一份 PDF 只渲一个目录，
    正常流程不会出现；若真出现，靠文件级门槛（核心材料100%）与人工复核兜住。"""
    return (norm(material_stem), norm(file_stem(page_path))) in page_pairs


PAGE_NAME_FORMATS = ('page_%03d', 'page_%d', 'page_%04d', 'page%03d')


def collect_source_page_pairs(obj, out):
    """递归收集「`_source` + `_page` 组合」还原出的已读页对。

    **为什么必须有这一条**：SKILL 允许 `_source` 只写文件名（`_page` 单独记页码），
    此时页图路径根本不出现在 extracted_data.json 里。若只认路径，页级队列就拿不到
    任何证据 —— 核心材料（文件级已读但要求读完）的剩余页会被**反复重发同一批**，
    形成死循环。这里把 (材料名, 第N页) 还原成 (材料主干, page_00N) 对，与路径证据并集。"""
    if isinstance(obj, dict):
        src = obj.get('_source')
        page = obj.get('_page')
        if isinstance(src, str) and src.strip() and page not in (None, ''):
            nums = re.findall(r'\d+', str(page))[:8]        # 兼容 3 / "3" / "3-5" / "第3页"
            comps = [c for c in str(src).replace('\\', '/').split('/') if c.strip()]
            stems = {norm(os.path.splitext(c)[0]) for c in comps}
            stems.discard('')
            for n in nums:
                try:
                    i = int(n)
                except ValueError:
                    continue
                if not 0 < i <= 100000:
                    continue
                for fmt in PAGE_NAME_FORMATS:
                    pn = norm(fmt % i)
                    for st in stems:
                        out.add((st, pn))
        for k, v in obj.items():
            if k not in ('_source', '_page'):
                collect_source_page_pairs(v, out)
    elif isinstance(obj, list):
        for item in obj:
            collect_source_page_pairs(item, out)


def parse_read_items(metadata):
    """解析 _metadata.read_items / data_sources：拆成 材料项编号集合 + 文件编号集合 + 文件名列表。
    容错：metadata 非 dict 视为无自报（打警告）；列表元素跳过 None/空值。"""
    item_nos, file_nos, names = set(), set(), []
    if not isinstance(metadata, dict):
        if metadata not in (None, {}, []):
            print("WARNING: _metadata 应为对象(dict)，实际为 %s，自报进度按空处理"
                  % type(metadata).__name__, file=sys.stderr)
        return item_nos, file_nos, names
    raw = []
    for key in ('read_items', 'data_sources'):
        v = metadata.get(key)
        if isinstance(v, list):
            raw.extend(v)
        elif v not in (None, ''):
            raw.append(v)
    for entry in raw:
        if entry is None:
            continue
        s = str(entry).strip()
        if not s:
            continue
        if re.fullmatch(r'\d+', s):
            item_nos.add(s)
        elif re.fullmatch(r'\d+(-\d+)+', s):
            file_nos.add(s)
        else:
            names.append(s)
    return item_nos, file_nos, names


def build_coverage(proofs_index, extracted, catalog, groups, critical_keywords):
    material_index = proofs_index.get('material_index', {}) if isinstance(proofs_index, dict) else {}
    if not isinstance(material_index, dict):
        print("ERROR: proofs_index 的 material_index 应为对象(dict)，实际为 %s"
              % type(material_index).__name__, file=sys.stderr)
        material_index = {}

    # 材料项编号 -> (大类, 名称, optional)
    item_meta = {}
    for cat in catalog.get('categories', []):
        for item in cat.get('items', []):
            item_meta[str(item['no'])] = {
                'category': cat['category'],
                'name': item['name'],
                'optional': item.get('optional', False),
            }

    # 材料项编号 -> key_fields 提示
    # ⚠️ 同一材料项编号可能被**多个**提取组引用（如第25项既属「其他材料（含评估报告）」
    #    又属「发起人可扩募资产材料」）。这里必须**合并**而不是覆盖：早期实现用后者直接
    #    覆盖前者，导致读评估报告时提示的字段是 expandable_assets.*，评估报告真正该提的
    #    evaluation.cashflow_forecast/opex_params/capex_* 一个都不显示 —— 提示错了，
    #    agent 自然只提了摘要字段。
    item_fields = {}
    for g in groups.get('categories', []):
        for no in g.get('proof_nos', []):
            cur = item_fields.setdefault(str(no), [])
            for kf in g.get('key_fields', []):
                if kf not in cur:
                    cur.append(kf)

    metadata = extracted.get('_metadata', {}) if isinstance(extracted, dict) else {}
    claimed_item_nos, claimed_file_nos, claimed_names = parse_read_items(metadata)
    # 自报文件名：按主干精确匹配（去目录/扩展名+规范化），不做子串匹配——
    # 防止模糊自报（如"情况说明"）把多个相似命名的未读文件一起判成已读
    claimed_name_stems = build_stem_set(claimed_names)

    sources = []
    collect_sources(extracted, sources)
    source_stems = build_stem_set(sources)

    items_report = {}
    total_files = read_files = 0
    unread_list, suspicious_list, critical_unread = [], [], []

    for no, files in sorted(material_index.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 999):
        meta = item_meta.get(no, {'category': '（追加项）', 'name': '项目追加材料', 'optional': True})
        item_files = []
        for rel_path in files:
            total_files += 1
            fname = os.path.basename(rel_path.replace('\\', '/'))
            fno = extract_file_number(fname)
            stem_n = norm(file_stem(rel_path))

            evidence = []
            # 客观证据：文件名主干与任一 _source 路径组件主干精确相等
            if stem_n and stem_n in source_stems:
                evidence.append('source')
            # 自报：文件编号 / 材料项编号 精确匹配，或文件名主干精确相等
            if (fno and fno in claimed_file_nos) or (no in claimed_item_nos) \
                    or (stem_n and stem_n in claimed_name_stems):
                evidence.append('self_report')

            is_read = bool(evidence)
            is_critical = any(kw in fname for kw in critical_keywords)
            if is_read:
                read_files += 1
                if evidence == ['self_report']:
                    suspicious_list.append(rel_path)
            else:
                unread_list.append({'no': no, 'file': rel_path})
                if is_critical:
                    critical_unread.append(rel_path)
            item_files.append({'file': rel_path, 'read': is_read,
                               'evidence': evidence, 'critical': is_critical})

        n_read = sum(1 for f in item_files if f['read'])
        items_report[no] = {
            'category': meta['category'],
            'name': meta['name'],
            'optional': meta['optional'],
            'total': len(item_files),
            'read': n_read,
            'files': item_files,
            'key_fields_hint': item_fields.get(no, []),
        }

    overall = round(read_files / total_files * 100, 1) if total_files else 0.0
    return {
        'total_files': total_files,
        'read_files': read_files,
        'coverage_pct': overall,
        'critical_unread': critical_unread,
        'suspicious_self_report_only': suspicious_list,
        'unread': unread_list,
        'items': items_report,
    }


def locate_artifacts(work_dir, rel_path):
    """定位某份材料在阶段A的产物（供阶段B直接读）：
      文字层PDF → <work_dir>/images/<主干>.txt
      扫描件     → <work_dir>/images/<主干>/[<安全名>/]page_XXX.png
    返回 dict(kind, path, pages, samples)；找不到产物时 kind='none'。
    all_pages：本材料**全部**页图的完整路径（按页序排好）——页级驱动模式直接照它发读图调用，
    agent 不必再 list 目录（少一次串行往返，这是并行读图的前提）。"""
    stem = file_stem(rel_path)
    images_dir = os.path.join(work_dir, 'images')
    out = {'kind': 'none', 'path': None, 'pages': 0, 'samples': [], 'all_pages': []}

    txt = os.path.join(images_dir, stem + '.txt')
    img_dir = os.path.join(images_dir, stem)

    pngs = []
    deepest = None
    if os.path.isdir(img_dir):
        for root, _dirs, files in os.walk(img_dir):
            batch = sorted(f for f in files if f.lower().endswith('.png'))
            if batch:
                deepest = root
                pngs.extend(os.path.join(root, f) for f in batch)
    pngs.sort(key=lambda p: (page_num(p), p))

    has_txt = os.path.exists(txt)
    txt_kb = 0
    if has_txt:
        try:
            txt_kb = round(os.path.getsize(txt) / 1024.0, 1)
        except OSError:
            txt_kb = 0
    if pngs and has_txt:
        out.update({'kind': 'txt+images', 'path': txt, 'pages': len(pngs),
                    'samples': pngs[:3], 'all_pages': pngs, 'image_dir': deepest,
                    'text_kb': txt_kb})
    elif pngs:
        out.update({'kind': 'images', 'path': deepest, 'pages': len(pngs),
                    'samples': pngs[:3], 'all_pages': pngs, 'image_dir': deepest})
    elif has_txt:
        out.update({'kind': 'txt', 'path': txt, 'pages': 0, 'text_kb': txt_kb})
    return out


def unread_priority(item_no, filename, critical_keywords, optional):
    """未读文件的补读优先级（越小越先读）：
      0 核心材料（审计/评估/估值/营业执照/不动产权证/法律意见书）—— 第四章与表4-4~4-15 的唯一数据源
      1 合规材料第13~21项 —— 第五章表15~22 逐份成行
      2 其余必需项
      3 「如涉及」项与项目追加项
    """
    if any(kw in filename for kw in critical_keywords):
        return 0
    if str(item_no) in COMPLIANCE_ITEM_NOS:
        return 1
    return 3 if optional else 2


# ============================ 核心材料页级门槛 ============================
# 【为什么必须有这一层】文件级门槛只看"这份材料有没有被读过"：只要有一个字段的 _source
# 命中文件名，一份80页的评估报告读了前3页摘要也算"已读" → 覆盖率 PASS → 直接进第四步。
# 实测事故：evaluation 只填出8个摘要字段（报告编号/评估值/折现率/收益年限/机构…），
# 现金流预测表、运营费用参数、资本性支出明细、可比实例、CPI、客户财务全缺 →
# 表4-4~表4-15 全是空表。这些数据**只存在于报告中后部**，不读到就一定没有。
# 因此核心材料（审计/评估/估值/法律意见书/营业执照/不动产权证）必须**页级读完**：
# 全部页图 + txt 全文单元都要有已读证据，否则门槛 FAIL（唯一合法越权通道仍是
# --force-low-coverage，留痕 + 第五步必 FAIL）。
# 核心报告类（审计/评估/估值/法律意见）关键词——这几类的页数多、数据在中后部，
# 另外要与 batch_render_pdfs.CORE_REPORT_KEYWORDS 对齐（那边负责全量渲染）
CORE_REPORT_KEYWORDS = ['审计', '评估', '估值', '法律意见']
# 核心材料页级明细在报告里最多列多少条未读单元（防报告体积失控）
CORE_UNREAD_LIST_CAP = 200


def collect_txt_reads(paths):
    """从路径集合里挑出 `.txt` 类已读证据，返回规范化主干集合。

    txt 单元没有页码，无法用 (材料, page_00N) 表示。`--mark-batch` 会把本轮 txt 路径
    写进 `_metadata.read_pages`，因此这里按"路径以 .txt 结尾"识别，取其主干做匹配。"""
    out = set()
    for p in paths:
        s = str(p).replace('\\', '/')
        if s.lower().endswith('.txt'):
            n = norm(file_stem(s))
            if n:
                out.add(n)
    return out


def build_page_evidence(extracted):
    """汇总页级"已读"证据（三来源并集）+ txt 已读证据。

    ① `_metadata.read_pages`（--mark-batch/--mark-pages 登记的页图与 txt 路径）
    ② 全文 `_source` 里出现过的页图路径
    ③ 「`_source`（文件名）+ `_page`（页码）」组合还原出的页
       —— 缺了这条，沿用旧溯源口径（只写文件名+页码）的记录拿不到页级证据，
          核心材料的剩余页会被反复重发同一批。
    返回 (page_pairs, txt_reads)。"""
    paths = []
    collect_sources(extracted, paths)
    meta = extracted.get('_metadata') if isinstance(extracted, dict) else None
    if isinstance(meta, dict) and isinstance(meta.get('read_pages'), list):
        paths.extend(str(x) for x in meta['read_pages'] if x)
    page_pairs = collect_page_pairs(paths)
    collect_source_page_pairs(extracted, page_pairs)
    return page_pairs, collect_txt_reads(paths)


def load_render_totals(work_dir):
    """从 batch_render_report.json 取 {材料主干: PDF总页数}，用于识别"阶段A 只渲了前置页"。

    有了它，页级门槛能区分两种缺页：
      - 阶段B 没读完（页图在盘上，未读）→ 继续 --next-pages 补读
      - 阶段A 没渲全（页图根本不存在）→ 必须回去重跑 batch_render_pdfs.py
    """
    path = os.path.join(work_dir, 'batch_render_report.json')
    totals = {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            rep = json.load(f)
    except (OSError, ValueError):
        return totals
    if not isinstance(rep, dict):
        return totals
    for key in ('rendered', 'text_extracted', 'errors'):
        for st in rep.get(key) or []:
            if not isinstance(st, dict):
                continue
            name = st.get('pdf_name') or file_stem(st.get('file') or '')
            tp = st.get('total_pages')
            if name and isinstance(tp, int) and tp > 0:
                totals[norm(name)] = max(totals.get(norm(name), 0), tp)
    return totals


def is_core_file(item_no, filename, critical_keywords, optional, flagged=False):
    """该文件是否属核心材料（页级门槛适用对象）"""
    return bool(flagged) or unread_priority(item_no, filename, critical_keywords, optional) == 0


def enrich_core_pages(report, work_dir, critical_keywords, page_pairs, txt_reads):
    """给覆盖率报告补上**核心材料页级完成度**，并写入门槛用的 core_page_unread。

    产出（都挂在 report 上）：
      core_pages        —— 逐份核心材料的 {总单元/已读单元/未读单元清单/缺渲页数}
      core_page_unread  —— 未读完的核心材料清单（门槛判据，非空即 FAIL）
      core_page_stats   —— 汇总 {total_units, read_units, pct, files, files_done}
      core_render_gaps  —— 阶段A 漏渲清单（页图数 < PDF总页数），需回去重跑阶段A
    """
    totals = load_render_totals(work_dir)
    core_pages, core_unread, render_gaps = {}, [], []
    sum_total = sum_read = 0

    for no, item in report['items'].items():
        for f in item['files']:
            rel = f['file']
            fname = os.path.basename(str(rel).replace('\\', '/'))
            if not is_core_file(no, fname, critical_keywords, item['optional'], f.get('critical')):
                continue
            stem = file_stem(rel)
            art = locate_artifacts(work_dir, rel)
            all_pages = art.get('all_pages') or []
            has_txt = art['kind'] in ('txt', 'txt+images')

            unread_units = []
            n_total = n_read = 0
            if has_txt:
                n_total += 1
                if norm(stem) in txt_reads or f['read']:
                    n_read += 1
                else:
                    unread_units.append({'unit': 'txt', 'path': art['path']})
            for p in all_pages:
                n_total += 1
                if is_page_read(stem, p, page_pairs):
                    n_read += 1
                else:
                    unread_units.append({'unit': 'page', 'page_no': page_num(p), 'path': p})

            pdf_pages = totals.get(norm(stem))
            gap = None
            if pdf_pages and len(all_pages) < pdf_pages and art['kind'] != 'txt':
                gap = pdf_pages - len(all_pages)
            elif pdf_pages and not all_pages and any(kw in fname for kw in CORE_REPORT_KEYWORDS):
                # 核心报告类只有 txt、一张页图都没有 → 阶段A 未按核心报告全量渲染
                gap = pdf_pages

            entry = {
                'item_no': no,
                'artifact_kind': art['kind'],
                'total_units': n_total,
                'read_units': n_read,
                'unread_count': len(unread_units),
                'unread_units': unread_units[:CORE_UNREAD_LIST_CAP],
                'pdf_total_pages': pdf_pages,
                'rendered_pages': len(all_pages),
                'render_gap_pages': gap,
                'file_level_read': bool(f['read']),
            }
            core_pages[str(rel)] = entry
            sum_total += n_total
            sum_read += n_read

            if art['kind'] == 'none':
                entry['note'] = '阶段A 无产物（txt/页图都没有）—— 回去重跑 batch_render_pdfs.py'
                core_unread.append({'file': rel, 'no': no, 'reason': 'no_artifact',
                                    'unread_count': 0, 'total_units': 0})
            elif unread_units:
                core_unread.append({'file': rel, 'no': no, 'reason': 'pages_unread',
                                    'unread_count': len(unread_units),
                                    'total_units': n_total})
            if gap:
                render_gaps.append({'file': rel, 'no': no, 'rendered_pages': len(all_pages),
                                    'pdf_total_pages': pdf_pages, 'missing_pages': gap})
                # 【关键】漏渲也必须拦：页图不在盘上 → 阶段B 读不到 → "全部已渲页都读完"
                # 会假性通过（这正是"只读了前3页摘要却 PASS"的原路径）。
                if not unread_units and art['kind'] != 'none':
                    core_unread.append({'file': rel, 'no': no, 'reason': 'render_gap',
                                        'unread_count': gap, 'total_units': pdf_pages})

    report['core_pages'] = core_pages
    report['core_page_unread'] = core_unread
    report['core_render_gaps'] = render_gaps
    report['core_page_stats'] = {
        'files': len(core_pages),
        'files_done': len(core_pages) - len(core_unread),
        'total_units': sum_total,
        'read_units': sum_read,
        'pct': round(sum_read / sum_total * 100, 1) if sum_total else 0.0,
    }
    return report


def print_core_page_status(report, work_dir, proofs_index_path, extracted_path):
    """打印核心材料页级完成度（门槛模式与 FAIL 清单共用）"""
    stats = report.get('core_page_stats') or {}
    unread = report.get('core_page_unread') or []
    gaps = report.get('core_render_gaps') or []
    if not stats.get('files'):
        return
    print("\n核心材料页级完成度（审计/评估/估值/法律意见书/营业执照/不动产权证 必须整份读完）:")
    print("  %d/%d 份读完；页级单元 %d/%d = %s%%"
          % (stats.get('files_done', 0), stats['files'], stats.get('read_units', 0),
             stats.get('total_units', 0), stats.get('pct', 0)))
    for u in unread:
        e = (report.get('core_pages') or {}).get(str(u['file'])) or {}
        if u['reason'] == 'no_artifact':
            print("  ❌ %s：阶段A 无产物 —— 回去重跑 batch_render_pdfs.py"
                  % os.path.basename(str(u['file'])))
            continue
        if u['reason'] == 'render_gap':
            print("  ❌ %s：已渲页图都读了，但阶段A 只渲了 %s/%s 页 —— 缺的 %d 页从未落盘，"
                  "现金流预测表/参数表很可能就在这些页里"
                  % (os.path.basename(str(u['file'])), e.get('rendered_pages'),
                     e.get('pdf_total_pages'), u['unread_count']))
            continue
        first = [x for x in (e.get('unread_units') or []) if x['unit'] == 'page'][:3]
        txt_left = any(x['unit'] == 'txt' for x in (e.get('unread_units') or []))
        print("  ⚠️ %s：未读 %d/%d 个单元%s%s"
              % (os.path.basename(str(u['file'])), u['unread_count'], u['total_units'],
                 '（含 txt 全文未登记已读）' if txt_left else '',
                 '，如第 %s 页' % '/'.join(str(x['page_no']) for x in first) if first else ''))
    if gaps:
        print("\n⚠️ 阶段A 渲染不全（页图数 < PDF总页数）—— 缺的页阶段B 根本排不出来，"
              "必须先回阶段A补渲：")
        for g in gaps[:10]:
            print("   - %s：已渲 %d 页 / 共 %d 页（缺 %d 页）"
                  % (os.path.basename(str(g['file'])), g['rendered_pages'],
                     g['pdf_total_pages'], g['missing_pages']))
        print("   ▶ 重跑（已默认对核心报告全量渲染，按页续渲只补缺页）：")
        print('     python "%s" "<proof_dir>" --work-dir "%s" --time-budget 240'
              % (os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              'batch_render_pdfs.py'), work_dir))
    if unread:
        print("\n▶ 补读命令（页级配额，直给全部页图路径）：")
        print('   python "%s" --proofs-index "%s" --extracted "%s" --next-pages %d'
              % (os.path.abspath(__file__), proofs_index_path, extracted_path,
                 DEFAULT_PAGE_QUOTA))
        print("   评估报告要读到「附表/现金流量预测表」为止：现金流预测、运营费用参数、"
              "资本性支出、可比实例、CPI 都在中后部（表4-4~4-15 的唯一来源）。")



def collect_unread_queue(report, critical_keywords):
    """把覆盖率报告里的未读文件摊平成一个按优先级排序的补读队列。"""
    queue = []
    for no, item in report['items'].items():
        for f in item['files']:
            if f['read']:
                continue
            fname = os.path.basename(str(f['file']).replace('\\', '/'))
            queue.append({
                'no': no,
                'item_name': item['name'],
                'category': item['category'],
                'optional': item['optional'],
                'file': f['file'],
                'critical': f['critical'],
                'key_fields': item['key_fields_hint'],
                'priority': unread_priority(no, fname, critical_keywords, item['optional']),
            })
    queue.sort(key=lambda e: (e['priority'],
                              int(e['no']) if str(e['no']).isdigit() else 999,
                              str(e['file'])))
    return queue


PRIORITY_LABEL = {0: '核心材料', 1: '合规材料(13~21项)', 2: '必需项', 3: '如涉及/追加项'}

# 核心报告的"这份材料必须提到的深层结构"提醒（按文件名关键词命中）。
# 为什么写死在脚本里：extraction_groups 的 key_fields 很长，队列里只显示前3条，
# 而恰恰是这些深层结构（现金流预测表/费用参数/资本性支出/可比实例/客户财务）最容易被
# "读了摘要就当读完"漏掉 —— 每一轮清单都把它顶到眼前。
CORE_FIELD_REMINDERS = [
    (('评估', '估值'),
     '★评估报告必提（表4-7~4-14，多在报告中后部）：evaluation.cashflow_forecast（现金流预测表逐年）'
     '／revenue_assumptions（上架率·服务费单价·增长率+依据）／opex_params（运营费用参数）'
     '／non_operating_expenses（税费取费依据）／capex_summary+capex_equipment_top+capex_forecast'
     '／comparable_instances+comparable_projects+cpi_index／rack_contracts'
     '／valuation_params（折现率构成·期末价值·假设）'),
    (('审计',),
     '★审计报告必提（表4-1~4-6）：operating_performance.annual_rows（最近3年及一期营收/成本/'
     '净利润/EBITDA/经营性净现金流）／revenue_structure_rows／cash_flow_providers／subsidies／'
     'entities.*.financials[YYYY]，直接整理成生成端最终行模型'),
    (('法律意见',),
     '★法律意见书必提：sub_projects[*].underlying_asset（含排除项）／property_right_*'
     '／compliance.transferability.{restrictions,encumbrances}／legal_relations.*'),
]


def core_field_reminder(filename):
    """按文件名给出核心报告的深层字段提醒（无命中返回 None）"""
    for kws, text in CORE_FIELD_REMINDERS:
        if any(k in str(filename) for k in kws):
            return text
    return None


NO_SUBAGENT_BANNER = (
    '⛔ 本阶段禁止委派子agent（全局红线第1条）：以上路径必须由**主agent在同一条消息里**\n'
    '   一次性并行发出读取调用。委派子agent实测更慢（冷启动+上下文不共享+结果回传再解析），\n'
    '   且子agent常漏写 _source/_attachment_no 导致本脚本判为"未读/可疑"，等于白读一轮。'
)


def _mark_token(rel_path):
    """材料的登记令牌：优先用文件编号（如 4-1），无编号则用文件名主干"""
    fname = os.path.basename(str(rel_path).replace('\\', '/'))
    return extract_file_number(fname) or file_stem(fname)


def collect_page_queue(report, work_dir, critical_keywords, page_pairs, txt_reads=()):
    """把待读材料摊平成**页级**队列（一个元素 = 一张页图 / 一份 txt / 一条缺产物告警）。

    入队规则：
      - 文件级未读的材料：其全部产物单元入队（txt 单元 + 逐张页图）
      - 文件级已读**但属核心材料**（审计/评估/估值/营业执照/不动产权证/法律意见书）：
        剩余未读页图**仍然入队** —— SKILL 要求这类材料读完（第四章与表4-4~4-15 的唯一
        数据源），不能因为"读了第1页就算这份读过"而漏掉折现率/收益年限/财务三表所在页；
        **txt 全文单元同理**：若 txt 从未被登记已读（`read_pages` 里没有它）且该文件也没有
        任何 `_source` 佐证，就把 txt 排出来
      - 文件级已读的非核心材料：不再入队（与文件级门槛口径保持一致，不额外加码）
      - 已读页（`_metadata.read_pages` ∪ 全文 `_source` 里出现过该页）一律跳过
    """
    queue = []
    for no, item in report['items'].items():
        for f in item['files']:
            rel = f['file']
            fname = os.path.basename(str(rel).replace('\\', '/'))
            prio = unread_priority(no, fname, critical_keywords, item['optional'])
            is_critical = bool(f['critical']) or prio == 0
            if f['read'] and not is_critical:
                continue

            stem = file_stem(rel)
            base = {
                'no': no,
                'item_name': item['name'],
                'category': item['category'],
                'optional': item['optional'],
                'file': rel,
                'stem': stem,
                'critical': is_critical,
                'priority': prio,
                'key_fields': item['key_fields_hint'],
                'file_read': bool(f['read']),
            }
            art = locate_artifacts(work_dir, rel)

            if art['kind'] == 'none':
                if not f['read']:
                    e = dict(base)
                    e.update({'unit': 'missing', 'path': None, 'page_no': -1, 'weight': 0})
                    queue.append(e)
                continue

            # txt 单元：已登记过该 txt（read_pages 里有它）或该文件已有 _source 佐证 → 视为已读。
            # 与 enrich_core_pages 的判定保持同一口径，避免"队列已空但门槛仍报未读"的死循环。
            if art['kind'] in ('txt', 'txt+images'):
                txt_done = bool(f['read']) or (norm(stem) in set(txt_reads))
                if not txt_done:
                    e = dict(base)
                    e.update({'unit': 'txt', 'path': art['path'], 'page_no': -1,
                              'text_kb': art.get('text_kb'),
                              'weight': txt_weight(art.get('text_kb'))})
                    queue.append(e)

            for p in art.get('all_pages') or []:
                if is_page_read(stem, p, page_pairs):
                    continue
                e = dict(base)
                e.update({'unit': 'page', 'path': p, 'page_no': page_num(p), 'weight': 1})
                queue.append(e)

    queue.sort(key=lambda e: (e['priority'],
                              int(e['no']) if str(e['no']).isdigit() else 999,
                              str(e['file']),
                              e['page_no']))
    return queue


def pick_page_batch(queue, quota, max_per_file=0):
    """按"张数配额"取一批：按队列优先级顺序累加 weight 直到用完配额。
    max_per_file>0 时限制单份材料在一轮里最多占几张（用于把大报告摊到多轮、
    让一轮里同时覆盖多份材料）；默认 0 = 不限制（大报告优先读完，批次更连贯）。"""
    picked, used, per_file = [], 0, {}
    quota = max(1, int(quota or 1))
    for e in queue:
        if used >= quota:
            break
        w = max(1, int(e.get('weight') or 1))
        key = str(e['file'])
        if max_per_file and per_file.get(key, 0) >= max_per_file:
            continue
        if picked and used + w > quota:
            continue                      # 放不下的大单元先跳过，让小单元填满配额
        # picked 为空时**不做配额检查**：否则 quota 小于首个单元权重（如 quota=1 遇到
        # 权重4 的大 txt）会一张都取不到 → 队列永远不推进（空转死循环）
        picked.append(e)
        used += w
        per_file[key] = per_file.get(key, 0) + w
    return picked, used


class MarkAborted(Exception):
    """进度登记被中止（一个字节都不写盘）。

    存在的意义：extracted_data.json 是阶段B的全部成果（可达数 MB）。若它存在但解析
    失败（写盘被打断/编码损坏），绝不能"当空文件重建"——那等于用一个语法错误抹掉
    整份提取数据。这里改为显式失败，交人工修复。"""


def save_last_batch(work_dir, picked):
    """把本轮清单落盘，供下一轮 `--mark-batch` 一次性登记（免手打长路径）"""
    payload = {
        'pages': [e['path'] for e in picked if e['unit'] == 'page' and e['path']],
        'texts': [e['path'] for e in picked if e['unit'] == 'txt' and e['path']],
        'items': sorted({_mark_token(e['file']) for e in picked if e['unit'] != 'missing'}),
        'consumed': False,
    }
    try:
        path = os.path.join(work_dir, LAST_BATCH_FILE)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print('⚠️ 本轮清单落盘失败（--mark-batch 将不可用）：%s' % e, file=sys.stderr)
    return payload


def load_last_batch(work_dir):
    """读上一轮清单。已被消费过的清单返回 {'consumed': True}（不可再用）。"""
    path = os.path.join(work_dir, LAST_BATCH_FILE)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        if data.get('consumed'):
            return {'consumed': True}
        return data
    except (OSError, ValueError):
        return {}


def consume_last_batch(work_dir):
    """把上一轮清单标记为**已消费**（一次性凭据）。

    防的是这个事故：`--mark-batch --next-pages 8` 被空跑两次时，第二次会把刚打印出来、
    **实际还没读**的那一批页登记为已读，整批被静默跳过。清单一经消费即失效，重复跑只会
    收到"找不到可用清单"的告警。用标记而不是删除文件，符合"中途不删文件"的红线。"""
    path = os.path.join(work_dir, LAST_BATCH_FILE)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            data['consumed'] = True
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
    except (OSError, ValueError) as e:
        print('⚠️ 无法标记上一轮清单为已消费（%s）——请勿重复跑 --mark-batch' % e,
              file=sys.stderr)


def _split_list_arg(value):
    """解析逗号分隔的列表参数；支持 `@文件` 从文本文件按行读取（路径太长时用）"""
    if not value:
        return []
    out = []
    for chunk in str(value).split(','):
        chunk = chunk.strip().strip('"').strip("'")
        if not chunk:
            continue
        if chunk.startswith('@'):
            try:
                with open(chunk[1:], 'r', encoding='utf-8') as f:
                    out.extend(line.strip() for line in f if line.strip())
            except OSError as e:
                print('⚠️ 无法读取清单文件 %s：%s' % (chunk[1:], e), file=sys.stderr)
            continue
        out.append(chunk)
    return out


def mark_progress(extracted_path, items=(), pages=()):
    """把已读材料/已读页登记进 extracted_data.json 的 _metadata（脚本代写，原子替换）。

    这是为了卸掉"每轮手工编辑大 JSON"的串行开销：agent 只需并行读图 + 写本轮提取到的
    字段，进度登记交给脚本。**不会**放低门槛：文件级覆盖率仍按 read_items ∪ _source
    双重核验，只有 _source 才算客观佐证，纯自报仍会进"可疑"清单。
    返回 (新增材料数, 新增页数)；文件存在但解析失败时抛 MarkAborted 且不写盘。"""
    exists = os.path.exists(extracted_path)
    if exists:
        try:
            with open(extracted_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except ValueError as e:
            # 关键回退路径：**绝不**把解析失败当成空文件重建，否则一个语法错误就抹掉整份提取成果
            raise MarkAborted(
                '%s 存在但不是合法 JSON（%s）——已放弃写入，未改动任何字节。\n'
                '   请先修复该文件（常见原因：上一次写盘被打断），再重跑本命令。'
                % (extracted_path, e))
        except OSError as e:
            raise MarkAborted('%s 无法读取（%s）——已放弃写入。' % (extracted_path, e))
    else:
        data = {}                       # 仅"文件不存在"才允许建壳

    if not isinstance(data, dict):
        raise MarkAborted('%s 顶层不是 JSON 对象（实际 %s）——已放弃写入。'
                          % (extracted_path, type(data).__name__))

    meta = data.get('_metadata')
    if not isinstance(meta, dict):
        if meta not in (None, {}, []):
            print('⚠️ _metadata 原为 %s，已替换为对象（原值不可用于进度登记）'
                  % type(meta).__name__, file=sys.stderr)
        meta = {}
        data['_metadata'] = meta

    added = {}
    for key, values in (('read_items', items), ('read_pages', pages)):
        cur = meta.get(key)
        if not isinstance(cur, list):
            cur = [] if cur in (None, '') else [cur]
        seen = {norm(x) for x in cur}
        n = 0
        for v in values:
            v = str(v).strip()
            if not v or norm(v) in seen:
                continue
            cur.append(v)
            seen.add(norm(v))
            n += 1
        meta[key] = cur
        added[key] = n

    # 无新增就不重写（几 MB 的文件没必要为 0 条变更做一次全量落盘）；
    # 但文件不存在时必须落盘，否则后续 json.load 会失败
    if exists and not (added.get('read_items') or added.get('read_pages')):
        return 0, 0

    os.makedirs(os.path.dirname(os.path.abspath(extracted_path)), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(extracted_path)),
                               prefix='.extracted_', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, extracted_path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return added.get('read_items', 0), added.get('read_pages', 0)


def print_next_pages_batch(report, work_dir, quota, critical_keywords, output_path,
                           page_pairs, extracted_path, proofs_index_path, max_per_file=0,
                           txt_reads=()):
    """页级驱动模式：按张数配额给出下一批，并打印**全部页图完整路径**（可直接并行读）。"""
    queue = collect_page_queue(report, work_dir, critical_keywords, page_pairs, txt_reads)
    n_pages_left = sum(1 for e in queue if e['unit'] == 'page')
    n_txt_left = sum(1 for e in queue if e['unit'] == 'txt')
    missing = [e for e in queue if e['unit'] == 'missing']
    core_stats = report.get('core_page_stats') or {}
    core_unread = report.get('core_page_unread') or []

    print("=== 阶段B读图进度（现算，不依赖上下文记忆）===")
    print("文件级覆盖率 : %d/%d = %s%%（门槛 %s%%，%d 份未读）"
          % (report['read_files'], report['total_files'], report['coverage_pct'],
             report.get('threshold_pct', 80.0), len(report['unread'])))
    print("页级待读     : 页图 %d 张 + txt %d 份   核心材料未读 %d 份"
          % (n_pages_left, n_txt_left, len(report['critical_unread'])))
    if core_stats.get('files'):
        print("核心材料页级 : %d/%d 份读完，单元 %d/%d = %s%%（**必须100%%**：审计/评估报告的"
              "现金流预测表、运营费用参数、资本性支出、可比实例都在中后部）"
              % (core_stats.get('files_done', 0), core_stats['files'],
                 core_stats.get('read_units', 0), core_stats.get('total_units', 0),
                 core_stats.get('pct', 0)))
    if report.get('core_render_gaps'):
        print("⚠️ 阶段A 渲染不全 %d 份核心报告（页图数 < PDF总页数）—— 缺的页排不进队列，"
              "先回阶段A重跑 batch_render_pdfs.py 补渲"
              % len(report['core_render_gaps']))
        for g in report['core_render_gaps'][:5]:
            print("   - %s：已渲 %d/%d 页" % (os.path.basename(str(g['file'])),
                                             g['rendered_pages'], g['pdf_total_pages']))
    if report['suspicious_self_report_only']:
        print("⚠️ 仅自报已读、无 _source 佐证 %d 份（读了但漏写溯源？逐一复核，别当已读）"
              % len(report['suspicious_self_report_only']))

    if missing:
        print("\n⚠️ 以下材料在阶段A没有产物（txt/页图都没有）—— 回去重跑 "
              "batch_render_pdfs.py（断点续跑会自动补），不要当\"读不出来\"跳过：")
        for e in missing[:10]:
            print("   - [第%s项] %s" % (e['no'], e['file']))
        if len(missing) > 10:
            print("   ... 及另外 %d 份" % (len(missing) - 10))

    readable = [e for e in queue if e['unit'] != 'missing']
    if not readable:
        if core_unread:
            print("\n⚠️ 队列已空，但核心材料页级门槛仍未满足 —— 通常是阶段A漏渲（页图不在盘上）："
                  "\n   先回阶段A重跑 batch_render_pdfs.py 补渲，再回本命令继续。")
            print_core_page_status(report, work_dir, proofs_index_path, extracted_path)
            return
        print("\n✅ 全部产物均已读，跑门槛模式确认后即可进入阶段C：")
        print("   （去掉 --next-pages，加 --output %s）"
              % (output_path or '<work_dir>/extraction_coverage.json'))
        return

    # 防卡死：核心材料已被判"文件级已读"却拿不到任何页级证据 → 说明上一轮读完没登记，
    # 再跑下去只会把同一批页反复重发。这里直接点名，而不是让 agent 自己去悟。
    if not page_pairs and any(e['file_read'] for e in readable):
        print("\n⚠️ 检测到有材料已被判「文件级已读」，但 extracted_data.json 里**没有任何页级证据**"
              "（既无 `_metadata.read_pages`，也无 `_source`+`_page`）。"
              "\n   若上一轮确实读过，请在下一轮命令加 `--mark-batch` 登记；"
              "否则同一批页会被反复重发（队列不推进）。")

    picked, used = pick_page_batch(readable, quota, max_per_file)
    payload = save_last_batch(work_dir, picked)

    # 按材料分组展示（同一份材料的多张页图排在一起，便于 agent 归并到同一组字段）
    groups, order = {}, []
    for e in picked:
        key = str(e['file'])
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(e)

    print("\n=== 本轮清单（配额 %d 张，实取 %d 张 / 跨 %d 份材料）==="
          % (quota, used, len(order)))
    for i, key in enumerate(order, 1):
        units = groups[key]
        e0 = units[0]
        tag = '★核心' if e0['critical'] else PRIORITY_LABEL.get(e0['priority'], '')
        note = '（本份剩余页续读）' if e0['file_read'] else ''
        print("\n%2d. [第%s项 %s] %s%s 《%s》"
              % (i, e0['no'], e0['item_name'][:20], tag, note,
                 os.path.basename(key)))
        reminder = core_field_reminder(os.path.basename(key)) if e0['critical'] else None
        if reminder:
            print("    %s" % reminder)
        if e0['key_fields']:
            # 字段提示逐条截断：页级模式一轮可能列多份材料，完整字段串会把清单刷屏，
            # 淹没真正要用的页图路径（完整清单在 extraction_groups.json）
            hints = [str(k)[:56] + ('…' if len(str(k)) > 56 else '')
                     for k in e0['key_fields'][:3]]
            print("    提字段 : %s" % '、'.join(hints))
        for u in units:
            if u['unit'] == 'txt':
                print("    txt全文（%s KB，权重%d）: %s"
                      % (u.get('text_kb'), u['weight'], u['path']))
            else:
                print("    第%s页 : %s" % (u['page_no'] if u['page_no'] >= 0 else '?',
                                          u['path']))

    print("\n--- 本轮全部路径（复制即用，**同一条消息里逐个并行读**）---")
    for p in payload['texts'] + payload['pages']:
        print(p)

    print("\n" + NO_SUBAGENT_BANNER)

    rest_pages = n_pages_left + n_txt_left - len(picked)
    print("\n▶ 读完本轮：①把提取字段（含 _source/_page/_raw_text/_attachment_no/_doc_name）"
          "增量写入 extracted_data.json；②进度登记交给脚本，顺带取下一批：")
    print('   python "%s" --proofs-index "%s" --extracted "%s" --mark-batch --next-pages %d'
          % (os.path.abspath(__file__), proofs_index_path, extracted_path, quota))
    print("   （--mark-batch 会把本轮 %d 张页图 + %d 份材料编号写进 _metadata，"
          "不必手工编辑大 JSON）" % (len(payload['pages']), len(payload['items'])))
    print("   剩余待读约 %d 张/份；队列清空后去掉 --next-pages 跑门槛模式收尾。"
          % max(rest_pages, 0))


def print_next_batch(report, work_dir, batch_size, critical_keywords, output_path):
    """文件级驱动模式（兼容旧口径）：打印进度 + 下一批该读的 N 份文件。
    页多的材料建议改用 `--next-pages`：一份上百页的报告独占一轮会把并行读退化成串行。"""
    queue = collect_unread_queue(report, critical_keywords)
    n_crit_unread = len(report['critical_unread'])

    print("=== 阶段B读图进度（现算，不依赖上下文记忆）===")
    print("已读/总数 : %d/%d = %s%%   未读 %d 份   核心材料未读 %d 份"
          % (report['read_files'], report['total_files'], report['coverage_pct'],
             len(queue), n_crit_unread))
    if report['suspicious_self_report_only']:
        print("⚠️ 仅自报已读、无 _source 佐证 %d 份（读了但漏写溯源？逐一复核，别当已读）"
              % len(report['suspicious_self_report_only']))

    if not queue:
        print("\n✅ 全部材料文件均已读，跑门槛模式确认后即可进入阶段C：")
        print("   （去掉 --next，加 --output %s）" % (output_path or '<work_dir>/extraction_coverage.json'))
        return

    batch = queue[:batch_size]
    print("\n=== 下一批（%d 份，本条消息里一次性并行读完，读完立即写盘并追加 _metadata.read_items）==="
          % len(batch))
    total_pages = 0
    for i, e in enumerate(batch, 1):
        art = locate_artifacts(work_dir, e['file'])
        total_pages += art['pages']
        tag = '★核心' if e['critical'] else PRIORITY_LABEL.get(e['priority'], '')
        print("\n%2d. [第%s项 %s] %s   《%s》"
              % (i, e['no'], e['item_name'][:20], tag, os.path.basename(str(e['file']))))
        print("    原文件 : %s" % e['file'])
        if art['kind'] == 'none':
            print("    产物   : ❌ 未找到 txt/页图 —— 阶段A可能漏处理该文件，"
                  "先重跑 batch_render_pdfs.py（断点续跑会自动补）")
        elif art['kind'] == 'txt':
            print("    产物   : 文字层 txt（%s KB）→ %s" % (art.get('text_kb'), art['path']))
        else:
            if art['kind'] == 'txt+images':
                print("    产物   : txt（%s KB）→ %s ＋ 页图 %d 张"
                      % (art.get('text_kb'), art['path'], art['pages']))
            else:
                print("    产物   : 页图 %d 张 → %s" % (art['pages'], art['image_dir']))
            # 全路径直给（不再只给 3 张样例）：省掉 agent 先 list 目录再读图的串行往返
            pages = art.get('all_pages') or []
            for p in pages[:PAGE_LIST_CAP]:
                print("             %s" % p)
            if len(pages) > PAGE_LIST_CAP:
                print("             ... 及另外 %d 张（该份页数多，建议改用 "
                      "--next-pages %d 按张数配额分轮读）"
                      % (len(pages) - PAGE_LIST_CAP, DEFAULT_PAGE_QUOTA))
        if e['key_fields']:
            print("    提字段 : %s" % '、'.join(str(k) for k in e['key_fields'][:5]))
        reminder = core_field_reminder(os.path.basename(str(e['file']))) if e['critical'] else None
        if reminder:
            print("    %s" % reminder)

    if total_pages > DEFAULT_PAGE_QUOTA * 2:
        print("\n⚠️ 本批 %d 份材料合计 %d 张页图 —— 一条消息发不完，会退化成串行读。"
              "\n   建议改用页级配额模式：--next-pages %d（批次单位=张，且直给全部路径）"
              % (len(batch), total_pages, DEFAULT_PAGE_QUOTA))

    print("\n--- 待读队列后续（按优先级）---")
    rest = {}
    for e in queue[len(batch):]:
        rest.setdefault(PRIORITY_LABEL.get(e['priority'], '其他'), 0)
        rest[PRIORITY_LABEL[e['priority']]] += 1
    if rest:
        print("   " + "；".join("%s %d 份" % (k, v) for k, v in rest.items())
              + "（共 %d 份待读）" % (len(queue) - len(batch)))
    else:
        print("   无（本批读完即全部读完，届时跑门槛模式确认）")
    print("\n▶ 本批写盘后，重跑本命令取下一批；队列清空后去掉 --next 跑门槛模式收尾。")
    print("   注：文件级模式**不生成批次暂存**，`--mark-batch` 在本模式后不可用 ——"
          "请手工追加 `_metadata.read_items`，或改用 `--next-pages` 走脚本代写登记。")
    print("\n" + NO_SUBAGENT_BANNER)


def main():
    parser = argparse.ArgumentParser(description='阶段B数据提取覆盖率校验（第四步前硬门槛）')
    parser.add_argument('--proofs-index', required=True, help='scan_proofs.py 输出的索引JSON')
    parser.add_argument('--extracted', required=True, help='extracted_data.json 路径')
    parser.add_argument('--catalog', default=DEFAULT_CATALOG, help='标准材料骨架JSON（默认内置25项）')
    parser.add_argument('--groups', default=DEFAULT_GROUPS, help='extraction_groups.json（未读清单的字段提示）')
    parser.add_argument('--threshold', type=float, default=80.0,
                        help='整体文件覆盖率阈值%%%%（默认80）。**只能调高（加严）**：低于 %.0f 时拒绝执行'
                             '（exit=2），除非显式加 --force-low-coverage（留痕+第五步FAIL）'
                             % THRESHOLD_FLOOR)
    parser.add_argument('--critical-keywords', default=None,
                        help='核心材料关键词，逗号分隔（**只能追加，不能删默认项**；默认: %s）'
                             % '/'.join(DEFAULT_CRITICAL_KEYWORDS))
    parser.add_argument('--force-low-coverage', action='store_true',
                        help='唯一合法的越权通道：允许 --threshold 低于下限，也允许越过'
                             '**核心材料页级门槛**（核心材料未整份读完）。会记入 '
                             '<work_dir>/checkpoint.json 的 gate_bypasses，第五步全量校验必定 FAIL，'
                             '交付汇报必须向用户单列说明')
    parser.add_argument('--output', '-o', default=None,
                        help='覆盖率报告输出路径（默认 <work_dir>/extraction_coverage.json）')
    parser.add_argument('--next-pages', type=int, default=0, metavar='N',
                        help='【阶段B推荐】页级驱动模式：按**页图张数**配额给出下一批，并打印本批'
                             '全部页图完整路径（可直接一条消息里并行读完，无需再 list 目录）。'
                             'exit 恒为0。典型值 %d' % DEFAULT_PAGE_QUOTA)
    parser.add_argument('--max-per-file', type=int, default=0, metavar='N',
                        help='页级模式下单份材料每轮最多占几张（默认0=不限，大报告优先读完）。'
                             '设为 4 之类可让一轮同时覆盖多份材料')
    parser.add_argument('--next', type=int, default=0, metavar='N',
                        help='文件级驱动模式（兼容旧口径）：打印下一批该读的 N **份**文件。'
                             '页多的材料请改用 --next-pages，否则一份上百页报告会独占一轮')
    parser.add_argument('--mark-read', default=None, metavar='ITEMS',
                        help='把材料编号/文件名（逗号分隔，支持 @清单文件）追加进 '
                             '_metadata.read_items，由脚本原子写入，免手工编辑大 JSON')
    parser.add_argument('--mark-pages', default=None, metavar='PATHS',
                        help='把已读页图路径（逗号分隔，支持 @清单文件）追加进 _metadata.read_pages，'
                             '用于推进页级队列')
    parser.add_argument('--mark-batch', action='store_true',
                        help='把上一轮 --next-pages 打印的整批（页图+txt+材料编号）一次性登记为已读'
                             '（读取 <work_dir>/%s）' % LAST_BATCH_FILE)
    parser.add_argument('--work-dir', default=None,
                        help='工作目录（默认取 --extracted 所在目录）；用于定位 images/ 下的页图与 txt')
    args = parser.parse_args()


    work_dir = args.work_dir or os.path.dirname(os.path.abspath(args.extracted))
    output_path = args.output or os.path.join(work_dir, 'extraction_coverage.json')

    # ==== 门槛参数下限保护（全局红线第5条：严禁篡改门槛参数绕过校验）====
    # 阈值只能调高。低于下限时**直接拒绝执行且不写报告**，避免低阈值落盘后被下游硬联锁继承。
    threshold_lowered = args.threshold < THRESHOLD_FLOOR
    if threshold_lowered and not args.force_low_coverage:
        print('❌ 拒绝执行：--threshold %.1f 低于下限 %.1f%%（门槛参数只允许调高/加严）。'
              % (args.threshold, THRESHOLD_FLOOR), file=sys.stderr)
        print('   自行调低阈值是最危险的绕过方式：低阈值会写进 extraction_coverage.json 的 '
              'threshold_pct，被 gen_phase_fill_plan.py / fill_docx.py / validate_output.py '
              '继承，等于无痕放低整条流水线的门槛（比 --force-low-coverage 更严重，因为不留痕）。',
              file=sys.stderr)
        print('   ▶ 正确做法：回阶段B按 `--next-pages %d` 页级配额批次补读，把覆盖率做到 %.0f%% 以上。'
              % (DEFAULT_PAGE_QUOTA, THRESHOLD_FLOOR), file=sys.stderr)
        print('   ▶ 确有正当理由必须低阈值放行：显式加 --force-low-coverage（记入 checkpoint.json '
              '的 gate_bypasses，第五步全量校验必定 FAIL，交付汇报必须单列说明）。', file=sys.stderr)
        sys.exit(2)

    # 核心材料关键词只能追加，不能删默认项（删关键词=绕过"核心材料100%已读"，与调低阈值同类）
    critical_keywords = list(DEFAULT_CRITICAL_KEYWORDS)
    dropped = []
    if args.critical_keywords:
        given = [k.strip() for k in args.critical_keywords.split(',') if k.strip()]
        for k in given:
            if k not in critical_keywords:
                critical_keywords.append(k)
        dropped = [k for k in DEFAULT_CRITICAL_KEYWORDS if k not in given]
        if dropped:
            print('⚠️ --critical-keywords 试图删除默认核心关键词（%s）——已并回默认集，删减无效。'
                  % '/'.join(dropped), file=sys.stderr)

    with open(args.proofs_index, 'r', encoding='utf-8') as f:
        proofs_index = json.load(f)

    driving = bool(args.next_pages or args.next)
    marking = bool(args.mark_read or args.mark_pages or args.mark_batch)

    # 非法配额兜底：给了 0/负数会静默落到门槛模式（可能 exit=1），容易被误读成"覆盖率不达标"
    if args.next_pages is not None and args.next_pages < 0:
        print('⚠️ --next-pages %d 非法，已按 %d 处理。' % (args.next_pages, DEFAULT_PAGE_QUOTA),
              file=sys.stderr)
        args.next_pages = DEFAULT_PAGE_QUOTA
        driving = True
    if args.max_per_file and args.max_per_file < 0:
        print('⚠️ --max-per-file %d 非法，已按不限处理。' % args.max_per_file, file=sys.stderr)
        args.max_per_file = 0

    # ---- 进度登记（脚本代写 _metadata，卸掉"每轮手工编辑大 JSON"的串行开销）----
    if marking:
        mark_items = _split_list_arg(args.mark_read)
        mark_pages = _split_list_arg(args.mark_pages)
        batch_consumable = False
        if args.mark_batch:
            last = load_last_batch(work_dir)
            if last.get('consumed'):
                print('⚠️ 上一轮清单**已被登记过**（一次性凭据），本次 --mark-batch 不再重复登记。\n'
                      '   若确有漏登，请用 --mark-read/--mark-pages 显式指定实际读过的内容。',
                      file=sys.stderr)
            elif not last:
                print('⚠️ --mark-batch 找不到可用的上一轮清单（%s）——本轮请改用 --mark-read/--mark-pages 显式登记。'
                      % os.path.join(work_dir, LAST_BATCH_FILE), file=sys.stderr)
            else:
                batch_consumable = True
                mark_items.extend(last.get('items') or [])
                mark_pages.extend(last.get('pages') or [])
                mark_pages.extend(last.get('texts') or [])
        try:
            n_i, n_p = mark_progress(args.extracted, mark_items, mark_pages)
        except MarkAborted as e:
            print('❌ 进度登记已中止：%s' % e, file=sys.stderr)
            print('   （extracted_data.json 未被改动；修复后重跑即可，上一轮清单仍然有效）',
                  file=sys.stderr)
            sys.exit(2)
        if batch_consumable:
            consume_last_batch(work_dir)      # 消费即失效，防空跑一次吞掉一整批未读页
        print('✅ 进度已登记：read_items +%d（共登记 %d 条入参）、read_pages +%d（共 %d 条入参）'
              % (n_i, len(mark_items), n_p, len(mark_pages)))
        print('   注：登记只推进队列，不构成门槛佐证 —— 覆盖率仍按 read_items ∪ 全文 _source '
              '双重核验，漏写 _source 的材料会进"仅自报可疑"清单。')

    if not os.path.exists(args.extracted) and driving:
        with open(args.extracted, 'w', encoding='utf-8') as f:
            json.dump({'_metadata': {'read_items': [], 'read_pages': []}}, f,
                      ensure_ascii=False, indent=2)
        print('ℹ️ 已新建 %s（空壳）——本轮清单即"要读完的全量清单"' % args.extracted)

    with open(args.extracted, 'r', encoding='utf-8') as f:
        extracted = json.load(f)
    with open(args.catalog, 'r', encoding='utf-8') as f:
        catalog = json.load(f)
    with open(args.groups, 'r', encoding='utf-8') as f:
        groups = json.load(f)

    report = build_coverage(proofs_index, extracted, catalog, groups, critical_keywords)

    # 页级证据（三来源并集）+ txt 已读证据 —— 门槛模式与驱动模式共用同一份口径
    page_pairs, txt_reads = build_page_evidence(extracted)
    enrich_core_pages(report, work_dir, critical_keywords, page_pairs, txt_reads)
    core_page_unread = report.get('core_page_unread') or []
    # 核心材料页级门槛的唯一合法越权通道仍是 --force-low-coverage（留痕 + 第五步必 FAIL）
    core_gate_bypassed = bool(core_page_unread and args.force_low_coverage)

    def stamp(passed):
        """把门槛参数与判定结论盖进报告（含调低阈值的越权标记，供下游与第五步识别）。"""
        report['threshold_pct'] = args.threshold
        report['threshold_floor_pct'] = THRESHOLD_FLOOR
        report['threshold_lowered'] = threshold_lowered
        report['forced_low_coverage'] = bool(threshold_lowered and args.force_low_coverage)
        report['critical_keywords'] = critical_keywords
        report['critical_keywords_dropped'] = dropped
        report['core_page_gate_bypassed'] = core_gate_bypassed
        report['pass'] = passed

    if report['total_files'] == 0:
        print("❌ FAIL：proofs_index 的 material_index 为空（0 个材料文件）——"
              "请确认第二步扫描是否正常（scan_proofs.py），勿以空索引通过门槛。")
        stamp(False)
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        sys.exit(1)

    # 三条门槛：①整体文件覆盖率 ②核心材料文件级100%已读 ③**核心材料页级读完**
    # 第③条防的是"读了评估报告前3页摘要就算读完"（表4-4~4-15 全空的根因）
    passed = (report['coverage_pct'] >= args.threshold
              and not report['critical_unread']
              and (not core_page_unread or core_gate_bypassed))
    stamp(passed)

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # 越权留痕：调低阈值/越过核心材料页级门槛都只能靠 --force-low-coverage 放行，
    # 必须写进 checkpoint.json 的 gate_bypasses
    if (threshold_lowered or core_gate_bypassed) and args.force_low_coverage:
        if threshold_lowered:
            note = ('⚠️⚠️ 已用 --force-low-coverage 把阈值降到 %.1f%%（下限 %.1f%%）：本次越权已记入 '
                    '%s 的 gate_bypasses，下游硬联锁仍按下限判定，第五步全量校验会 FAIL，'
                    '交付汇报必须向用户列明。'
                    % (args.threshold, THRESHOLD_FLOOR,
                       os.path.join(work_dir, 'checkpoint.json')))
        else:
            note = ('⚠️⚠️ 已用 --force-low-coverage 越过**核心材料页级门槛**（%d 份核心材料未读完）：'
                    '本次越权已记入 %s 的 gate_bypasses，第五步全量校验会 FAIL。'
                    '注意：评估/审计报告没读完 → 表4-1~4-15 必然大面积空表，返工成本远高于补读。'
                    % (len(core_page_unread), os.path.join(work_dir, 'checkpoint.json')))
        try:
            from pipeline_state import record_bypass      # 延迟导入，避免与本模块循环导入
            record_bypass(work_dir,
                          'check_extraction_coverage(--threshold=%.1f%s)'
                          % (args.threshold, ',core_pages' if core_gate_bypassed else ''),
                          {'coverage_pct': report['coverage_pct'],
                           'read_files': report['read_files'],
                           'total_files': report['total_files'],
                           'critical_unread': report['critical_unread'],
                           'core_page_unread': [u['file'] for u in core_page_unread]})
        except Exception as e:
            note += '（留痕写入失败：%s，请向用户人工说明）' % e
        print(note, file=sys.stderr)

    # ---- 驱动模式：只给进度与下一批清单，不做门槛判定（exit 恒为0）----
    if args.next_pages and args.next_pages > 0:
        # 页级"已读"证据由 build_page_evidence 统一给出（read_pages ∪ _source 页图路径
        # ∪ 「_source+_page」还原页），与门槛模式同源，避免两套口径漂移
        print_next_pages_batch(report, work_dir, args.next_pages, critical_keywords,
                               output_path, page_pairs,
                               os.path.abspath(args.extracted),
                               os.path.abspath(args.proofs_index),
                               max_per_file=args.max_per_file,
                               txt_reads=txt_reads)
        print("\n报告文件: %s（每次运行都会刷新，不会过期）" % output_path)
        return

    if args.next and args.next > 0:
        print_next_batch(report, work_dir, args.next, critical_keywords, output_path)
        print("报告文件: %s（每次运行都会刷新，不会过期）" % output_path)
        return

    # ---- 人读摘要 ----
    print(f"覆盖率: {report['read_files']}/{report['total_files']} = {report['coverage_pct']}%  (阈值 {args.threshold}%)")
    if threshold_lowered:
        print(f"⚠️⚠️ 本次阈值 {args.threshold}% 低于下限 {THRESHOLD_FLOOR}%（--force-low-coverage 越权）："
              f"下游 gen_phase_fill_plan/fill_docx/validate_output 仍按 {THRESHOLD_FLOOR}% 判定，"
              f"本次 PASS 不代表能进第四步。")

    for no, item in report['items'].items():
        mark = '✅' if item['read'] == item['total'] else ('⚠️' if item['read'] else '❌')
        print(f"  {mark} 第{no}项 [{item['read']}/{item['total']}] {item['name'][:30]}")

    if report['critical_unread']:
        print(f"\n❌ 核心材料未读（必须100%读完）共 {len(report['critical_unread'])} 份:")
        for p in report['critical_unread']:
            print(f"   - {p}")

    # 核心材料页级完成度（第③条门槛）
    print_core_page_status(report, work_dir,
                           os.path.abspath(args.proofs_index),
                           os.path.abspath(args.extracted))
    if core_page_unread and core_gate_bypassed:
        print("\n⚠️⚠️ 以上核心材料未读完，但已用 --force-low-coverage 越权放行（已留痕，第五步必 FAIL）。")

    if report['suspicious_self_report_only']:
        print(f"\n⚠️ 仅自报已读、extracted_data 中无任何 _source 佐证 共 {len(report['suspicious_self_report_only'])} 份（请复核）")

    if not passed:
        if report['unread']:
            print(f"\n未读材料共 {len(report['unread'])} 份，按材料项分组的补读清单（含字段提示）:")
            by_item = {}
            for u in report['unread']:
                by_item.setdefault(u['no'], []).append(u['file'])
            for no, files in by_item.items():
                item = report['items'][no]
                print(f"  ▶ 第{no}项 {item['name'][:40]}（未读 {len(files)} 份）")
                for p in files[:5]:
                    print(f"      - {p}")
                if len(files) > 5:
                    print(f"      ... 及另外 {len(files) - 5} 份")
                for kf in item['key_fields_hint'][:3]:
                    print(f"      提取字段: {kf}")
        reasons = []
        if report['coverage_pct'] < args.threshold:
            reasons.append('整体覆盖率不达标')
        if report['critical_unread']:
            reasons.append('核心材料有未读文件')
        if core_page_unread and not core_gate_bypassed:
            reasons.append('核心材料未**整份读完**（页级门槛）')
        print("\n❌ FAIL：%s，不得进入第四步。请回阶段B按上方清单补读。" % '、'.join(reasons))
        print(f"   取下一批（页级配额，直给全部页图路径）：本命令加 --next-pages {DEFAULT_PAGE_QUOTA}")
        print(f"报告文件: {output_path}")
        sys.exit(1)

    print(f"\n✅ PASS：覆盖率达标、核心材料已全部读取且已整份读完（页级门槛通过），"
          f"可进入交叉验证与第四步。")
    print(f"   ⚠️ 阶段C 别忘了跑 data_crossref.json 的 validation_checks.chapter4_data_completeness："
          f"核对 operating_performance 的 annual_rows / forecast_rows / valuation_params.*_rows "
          f"是否真的提全，并运行 validate_handoff.py --strict（读完 ≠ 提全）。")
    print(f"报告文件: {output_path}")



if __name__ == '__main__':
    main()
