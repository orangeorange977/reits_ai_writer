#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
批量拆图/提取脚本：遍历证明材料目录下所有PDF，文字层PDF提取txt、扫描件拆图。
阶段A专用——为阶段B（读数据）准备 txt / 图片。

v3优化（解决“误判扫描件疯狂拆图 + 慢 + 跑一半丢文件”三大问题）：
  A. 分布式采样：分类时全文均匀采样（含中/尾段），封面稀疏不再主导判定
  B. 改进扫描判定：按“整页图像面积占比”判扫描页，文字页带小logo/公章不再误判
  C. 文字优先：文字充足即走文字提取，绝不拆图
  D. 文字提取并行：文字层PDF多线程并行提取（轻量安全）；渲染仍串行（内存安全）
  E. 整批成功后统一删除原件：处理中不删PDF，全部成功才删（中途超时不丢文件，靠输出存在性断点续跑）

v4（解决"核心报告只渲前置页 → 表4-1~4-15 永久缺数据"）：
  F. 核心报告（文件名含 审计/评估/估值/法律意见）**全量渲染**：不受 --text-front-pages 的
     前置页限制，也不受 --max-pages 与智能限页影响。原因：现金流预测表、运营费用参数、
     资本性支出明细、可比实例、CPI 指数都在报告中后部（常在30~80页），只渲前置页 →
     阶段B页级队列最多排出前置页那么多张 → 读空队列也读不到这些表
  G. 页级断点续渲：已存在的页图跳过；--time-budget 到点时在页边界让出，剩余页下次补渲
  H. has_output 对核心报告增加"页图张数 ≥ PDF总页数"校验：修掉"有txt就算完成"导致的
     重跑也补不出缺页（旧行为下这份材料被永久跳过）


用法:
  python batch_render_pdfs.py <proof_dir> --work-dir <work_dir>
      [--max-pages N] [--dpi 120] [--workers 4]
      [--batch-size N] [--time-budget 240] [--purge-pdf] [--dry-run]

流程:
  1. 扫描 proof_dir 下所有 .pdf（已有输出的自动跳过=断点续跑）
  2. 阶段1（并行）：分类；文字PDF直接提取txt，扫描/水印PDF标记待渲染
  3. 阶段2（串行）：渲染待渲染PDF（内存安全，逐页释放）
  4. PDF原件默认保留（中途不删，保回溯）；--purge-pdf 才在整批成功后删
  5. 输出 batch_render_report.json
"""

import argparse
import gc
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 导入质量检测模块（复用水印关键词和有效文本提取）
from check_pdf_quality import get_effective_text, WATERMARK_KEYWORDS

try:
    import fitz  # PyMuPDF
except ImportError:
    print("ERROR: PyMuPDF not installed. Run: pip install PyMuPDF", file=sys.stderr)
    sys.exit(1)

# 控制台编码兜底：避免非UTF-8环境（如Windows GBK）下emoji输出崩溃
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# 低质量页面占比超过此值 → 拆图
QUALITY_THRESHOLD = 40  # %
# 分类采样页数（全文均匀分布采样，而非只看前N页）
QUALITY_SAMPLE_PAGES = 12
# 单页判定为“扫描页”的图像覆盖面积占比阈值（大图覆盖整页才算扫描页）
SCAN_IMAGE_AREA_RATIO = 0.6
# 文字页有效字符下限：达到即视为有效文字页
TEXT_PAGE_MIN_CHARS = 50
# 并行文字提取默认线程数
DEFAULT_WORKERS = 4
# 文字PDF额外渲染前置页的文件名关键词（审计/评估报告前页多图表，需读图）
FRONT_RENDER_KEYWORDS = ["审计", "评估", "估值", "报告"]
# 文字PDF额外渲染的前置页数（0=关闭）
DEFAULT_FRONT_PAGES = 50
# 【核心报告：必须全量渲染，不受前置页数与 --max-pages 限制】
# 审计报告/评估（估值）报告/法律意见书是第四章与表4-1~表4-15 的唯一数据源，其关键数据
# （现金流预测表、运营费用参数、资本性支出明细、可比实例、CPI、客户财务）全在报告
# **中后部**（收益法测算/市场调研/附表章节，常在30~80页）。
# 历史事故：文字层评估报告只额外渲染前20页 → 中后部没有页图 → 阶段B页级队列最多排出20张，
# 读空队列也读不到现金流预测表 → evaluation 只填出8个摘要字段，表4-4~4-15 全空。
# 因此这类文件一律全量渲染（可用 --no-core-full-render 关闭，仅在磁盘极度紧张时使用）。
CORE_REPORT_KEYWORDS = ["审计", "评估", "估值", "法律意见"]

_print_lock = None  # 延迟初始化（threading）


def _safe_print(msg, err=False):
    global _print_lock
    if _print_lock is None:
        import threading
        _print_lock = threading.Lock()
    with _print_lock:
        print(msg, file=sys.stderr if err else sys.stdout)


def _sample_indices(total, k):
    """全文均匀分布采样：返回k个分布在[0,total)的页码（含首、中、尾）"""
    if total <= k:
        return list(range(total))
    return sorted(set(round(i * (total - 1) / (k - 1)) for i in range(k)))


def _page_scan_like(page, eff_len):
    """判断单页是否为扫描页：大图覆盖整页（面积占比高）且有效文字极少。
    区分“整页扫描图”与“文字页带小logo/公章”。"""
    try:
        page_area = abs(page.rect.width * page.rect.height)
        max_img_area = 0.0
        for im in page.get_image_info():
            b = im.get("bbox")
            if b:
                max_img_area = max(max_img_area, abs(b[2] - b[0]) * abs(b[3] - b[1]))
        big_image = page_area > 0 and (max_img_area / page_area) > SCAN_IMAGE_AREA_RATIO
    except Exception:
        # 老版本PyMuPDF无 get_image_info 时退化为“有图”判定
        big_image = len(page.get_images()) > 0
    return big_image and eff_len < TEXT_PAGE_MIN_CHARS


def classify_pdf(doc):
    """全文分布采样分类：判断是否需要拆图。不做渲染/提取。"""
    total_pages = len(doc)
    idxs = _sample_indices(total_pages, QUALITY_SAMPLE_PAGES)
    low_quality = 0
    scan_like = 0
    watermark_hits = set()
    total_eff = 0
    for i in idxs:
        page = doc[i]
        text = page.get_text()
        eff_len = len(get_effective_text(text))
        total_eff += eff_len
        watermark_hits.update(kw for kw in WATERMARK_KEYWORDS if kw in text)
        if _page_scan_like(page, eff_len):
            scan_like += 1
            low_quality += 1
        elif eff_len < 20:
            low_quality += 1
        page = None
    n = len(idxs) or 1
    avg_eff = total_eff // n
    low_ratio = low_quality / n * 100
    is_scan = scan_like / n > 0.6
    # 只有“大部分采样页是扫描页”或“低质量页占比超阈值”才拆图；
    # 文字充足的报告（哪怕有零星logo/公章图）一律走文字提取，不再误判
    need_render = is_scan or low_ratio > QUALITY_THRESHOLD
    return {
        "need_render": need_render,
        "is_scan": is_scan,
        "sampled_pages": n,
        "low_quality_ratio": round(low_ratio, 1),
        "avg_effective_chars": avg_eff,
        "watermark_hits": sorted(watermark_hits),
        "total_pages": total_pages,
    }


def extract_text_layer(doc, txt_path):
    """按页序提取全部文字层，写入txt，返回字符数"""
    parts = []
    for i in range(len(doc)):
        page = doc[i]
        parts.append(page.get_text())
        page = None
    text = "\n\n--- PAGE BREAK ---\n\n".join(parts)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(text)
    return len(text)


def is_core_report(pdf_name):
    """文件名是否命中核心报告（审计/评估/估值/法律意见）——这类必须全量渲染+整份读完"""
    return any(kw in str(pdf_name) for kw in CORE_REPORT_KEYWORDS)


def render_pdf(doc, img_dir, max_pages=None, dpi=120, page_indices=None,
               skip_existing=True, deadline=None):
    """逐页渲染为PNG（内存安全：每页立即释放pixmap）。
    逐页容错：某页渲染失败（坏页/损坏）则跳过并记录，不拖垮整份PDF。
    page_indices：指定渲染的0-based页码列表（智能限页用）；为None时按 max_pages 顺序渲染。
    skip_existing：已存在的页图直接跳过（**页级断点续渲**）——核心报告动辄上百页，
      单次运行可能被 --time-budget 截断，重跑时不该把已渲好的页重新画一遍。
      跳过的页仍计入成功数（返回值代表"磁盘上该有的页图数"，而非"本次新渲页数"）。
    deadline：绝对时间戳，超过则**在页边界停下**（不是硬杀），剩余页留给下次运行补渲。
      有了它，一份200页的评估报告不会因为"全量渲染"把单条命令拖到超时。
    返回 (成功页数, 失败页号列表, 是否被时间预算截断)。"""
    os.makedirs(img_dir, exist_ok=True)
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    total_pages = len(doc)
    if page_indices is not None:
        render_list = [i for i in page_indices if 0 <= i < total_pages]
    else:
        render_count = min(max_pages, total_pages) if max_pages else total_pages
        render_list = list(range(render_count))
    count = 0
    failed_pages = []
    stopped_early = False
    for i in render_list:
        img_path = os.path.join(img_dir, f'page_{i + 1:03d}.png')
        if skip_existing and os.path.exists(img_path) and os.path.getsize(img_path) > 0:
            count += 1
            continue
        if deadline is not None and count > 0 and time.time() >= deadline:
            # count>0 保证每份任务至少推进一页（否则 front 模式会误判成 "0 images" 错误）
            stopped_early = True
            break
        try:
            page = doc[i]
            pix = page.get_pixmap(matrix=matrix)
            pix.save(img_path)
            pix = None
            page = None
            count += 1
        except Exception:
            # 坏页跳过，继续渲染后续页，不卡住（页号记入failed_pages）
            failed_pages.append(i + 1)
            continue
    return count, failed_pages, stopped_early


# 智能限页策略：低价值多页扫描件只渲关键页（关键词元组, 前N页, 末M页）
# 合同/协议：首页有编号双方，尾页有签署盖章日期；报告/验收/检测：结论在首页；证照/截图：关键信息在前几页
SMART_PAGE_POLICIES = [
    (("合同", "协议"), 2, 2),
    (("审查", "验收", "检测", "备案", "批复", "报告书"), 2, 0),
    (("许可证", "执照", "证书"), 2, 0),
    (("截图", "信用记录", "查询结果"), 2, 0),
    (("凭证",), 1, 0),
]
# 智能限页豁免（永不裁剪，全量渲染到max_pages）：第四章唯一数据源，不得缺页
SMART_EXEMPT_KEYWORDS = ["审计", "评估", "估值", "法律意见"]


def smart_page_indices(pdf_name, total_pages):
    """按文件名策略返回应渲染的0-based页码列表；不匹配/豁免/页数已很少 → None（正常渲染）"""
    for kw in SMART_EXEMPT_KEYWORDS:
        if kw in pdf_name:
            return None
    for kws, head, tail in SMART_PAGE_POLICIES:
        if any(k in pdf_name for k in kws):
            if total_pages <= head + tail:
                return None  # 本来就少，不裁
            idx = set(range(head))
            if tail:
                idx |= set(range(total_pages - tail, total_pages))
            return sorted(idx)
    return None


def _img_subdir(images_dir, pdf_name):
    safe_name = re.sub(r'[\\/:*?"<>|]', '_', pdf_name)
    return os.path.join(images_dir, pdf_name, safe_name)


def _count_pngs(images_dir, pdf_name):
    """该PDF目录下已有的页图张数"""
    img_dir = os.path.join(images_dir, pdf_name)
    n = 0
    if os.path.isdir(img_dir):
        for _r, _d, fs in os.walk(img_dir):
            n += sum(1 for f in fs if f.lower().endswith(".png"))
    return n


def _pdf_page_count(pdf_path):
    """取PDF总页数（只开文档不渲染，代价极小）；失败返回 None"""
    try:
        doc = fitz.open(pdf_path)
        try:
            return len(doc)
        finally:
            doc.close()
    except Exception:
        return None


def has_output(images_dir, pdf_name, pdf_path=None, core_full_render=True):
    """判断该PDF是否已完成（断点续跑跳过依据）。
    - 最终txt存在 → 完成（纯文字，或文字+前置图渲染后已rename）
    - 图片目录有png 且 无 .txt.partial → 完成（纯扫描件全渲染）
    - 仅 .txt.partial（文字+前置图渲染中断）→ 未完成，需重跑

    ⚠️ 核心报告（审计/评估/估值/法律意见）额外要求 **页图张数 ≥ PDF总页数**：
    否则历史上"txt 已存在即算完成"会让只渲了前20页（或被 --max-pages 截断）的评估报告
    永远补不上中后部页图 —— 重跑本脚本也会被跳过，阶段B 根本排不出那些页。
    """
    final_txt = os.path.join(images_dir, pdf_name + ".txt")
    partial = os.path.join(images_dir, pdf_name + ".txt.partial")
    img_dir = os.path.join(images_dir, pdf_name)
    has_png = False
    if os.path.isdir(img_dir):
        for _r, _d, fs in os.walk(img_dir):
            if any(f.lower().endswith(".png") for f in fs):
                has_png = True
                break

    base_done = os.path.exists(final_txt) or (has_png and not os.path.exists(partial))
    if not base_done:
        return False

    # 核心报告的页图完整性复核（PDF原件不在了就无法核对，按 base_done 处理）
    if core_full_render and is_core_report(pdf_name) and pdf_path and os.path.exists(pdf_path):
        total = _pdf_page_count(pdf_path)
        if total and _count_pngs(images_dir, pdf_name) < total:
            return False
    return True



def classify_and_extract_text(pdf_path, images_dir, pdf_name,
                              front_pages=0, front_keywords=None,
                              core_full_render=True):
    """并行阶段单元：分类；文字PDF提取txt，需渲染的只标记（渲染在串行阶段）。
    文件名命中报告关键词的文字PDF，额外标记前置页读图（txt先写 .partial，渲染后rename）。
    **核心报告（审计/评估/估值/法律意见）改为全量页读图**（front_pages = 总页数），
    因为表4-1~4-15 需要的现金流预测表/参数表/可比实例全在中后部。
    不删除原件（删除统一在整批成功后进行）。"""
    pdf_size_mb = os.path.getsize(pdf_path) / (1024 * 1024)
    status = {"file": pdf_path, "pdf_name": pdf_name, "size_mb": round(pdf_size_mb, 1)}
    doc = fitz.open(pdf_path)
    try:
        if len(doc) == 0:
            status["action"] = "error"
            status["error"] = "empty PDF (0 pages)"
            return status
        q = classify_pdf(doc)
        status["is_scan"] = q["is_scan"]
        status["total_pages"] = q["total_pages"]
        status["core_report"] = bool(core_full_render and is_core_report(pdf_name))
        status["quality"] = {
            "sampled_pages": q["sampled_pages"],
            "low_quality_ratio": q["low_quality_ratio"],
            "watermark_hits": q["watermark_hits"],
            "avg_effective_chars": q["avg_effective_chars"],
        }
        if q["need_render"]:
            status["action"] = "pending_render"
            status["render_reason"] = ("scan" if q["is_scan"]
                                       else f"low_quality({q['low_quality_ratio']}% low, {q['avg_effective_chars']} avg chars)")
            if status["core_report"]:
                status["render_reason"] += "｜核心报告全量渲染（不受 --max-pages 限制）"
        else:
            need_front = (front_pages > 0 and front_keywords
                          and any(kw in pdf_name for kw in front_keywords))
            if status["core_report"]:
                need_front = True          # 核心报告一律读图，且读全份
            if need_front:
                # 文字+页图：txt先写 .partial，待串行阶段渲染后rename为最终txt
                partial = os.path.join(images_dir, pdf_name + ".txt.partial")
                n = extract_text_layer(doc, partial)
                status["action"] = "text_pending_front"
                status["text_length"] = n
                status["txt_partial"] = partial
                status["txt_final"] = os.path.join(images_dir, pdf_name + ".txt")
                if status["core_report"]:
                    status["front_pages"] = len(doc)      # 全量
                    status["render_reason"] = (
                        "文字层+**全部%d页**读图（核心报告：审计/评估/估值/法律意见；"
                        "表4-1~4-15 的参数与预测表在中后部，只渲前置页会永久缺数据）" % len(doc))
                else:
                    status["front_pages"] = min(front_pages, len(doc))
                    status["render_reason"] = f"文字层+前{status['front_pages']}页读图(文件名含报告关键词)"
            else:
                txt_path = os.path.join(images_dir, pdf_name + ".txt")
                n = extract_text_layer(doc, txt_path)
                status["action"] = "text_extracted"
                status["text_length"] = n
                status["txt_path"] = txt_path
        return status
    finally:
        doc.close()
        gc.collect()


def _images_has_output(images_dir):
    """images/ 下是否已有阶段A产物（最终txt或png）——用于区分“已完成”与“真丢失”"""
    if not os.path.isdir(images_dir):
        return False
    for _r, _d, fs in os.walk(images_dir):
        for f in fs:
            if f.endswith(".txt") or f.lower().endswith(".png"):
                return True
    return False


def _do_render(p, name, status, mode, images_dir, max_pages, dpi, smart_pages=True,
               core_full_render=True, deadline=None):
    """执行单个渲染任务（全渲染或前置页），更新status，返回(status, n_img)。
    各任务独立 doc，可安全在线程中执行（仅渲染，报告写入由主线程做）。
    smart_pages：对合同/验收报告/证照等低价值多页扫描件只渲关键页（见 SMART_PAGE_POLICIES）。
    core_full_render：核心报告（审计/评估/估值/法律意见）**忽略 --max-pages**，全量渲染
      —— 否则 `--max-pages 30` 会把一份80页的扫描版评估报告截到30页，中后部的现金流预测表
      永远拿不到页图（阶段B 排不出、门槛也查不到，属静默数据丢失）。
    deadline：渲染在页边界让出时间（剩余页下次续渲），避免单份大报告拖爆命令超时。"""
    is_core = bool(core_full_render and is_core_report(name))
    page_cap = status.get("front_pages") if mode == "front" else max_pages
    if is_core and mode != "front":
        page_cap = None                      # 全量渲染，不受限页参数影响
    t_pdf = time.time()
    doc = fitz.open(p)
    try:
        img_dir = _img_subdir(images_dir, name)
        indices = None
        if mode != "front" and smart_pages and not is_core:
            indices = smart_page_indices(name, len(doc))
            if indices is not None:
                status["smart_pages"] = "%d/%d页" % (len(indices), len(doc))
        n_img, failed_pages, stopped_early = render_pdf(
            doc, img_dir, page_cap, dpi, page_indices=indices, deadline=deadline)
        if is_core:
            status["core_report"] = True
            status["core_full_render"] = "%d/%d页" % (n_img, len(doc))
        if stopped_early:
            status["render_truncated"] = ("时间预算用尽，已渲 %d/%d 页，剩余页下次运行续渲"
                                          % (n_img, len(doc)))
    except Exception as e:
        n_img = 0
        failed_pages = []
        status["error"] = str(e)

    finally:
        doc.close()
        gc.collect()
    if failed_pages:
        status["failed_pages"] = failed_pages
    if mode == "front":
        if n_img > 0:
            try:
                os.replace(status["txt_partial"], status["txt_final"])
            except OSError as e:
                status["_rename_error"] = str(e)
            status["action"] = "text_extracted"
            status["images_front"] = n_img
            status["image_dir"] = os.path.join(images_dir, name)
            status["txt_path"] = status["txt_final"]
        else:
            status["action"] = "error"
            status.setdefault("error", "front render 0 images")
    else:
        if n_img > 0:
            status["action"] = "rendered" if status.get("is_scan") else "rendered_low_quality_text"
            status["images"] = n_img
            status["image_dir"] = os.path.join(images_dir, name)
        else:
            status["action"] = "render_failed"
            status.setdefault("error", "rendered 0 images")
    status["_render_secs"] = round(time.time() - t_pdf, 1)
    return status, n_img


def batch_render(proof_dir, work_dir, max_pages=None, dry_run=False, dpi=120,
                 batch_size=None, time_budget=None, workers=DEFAULT_WORKERS, keep_pdf=True,
                 front_pages=DEFAULT_FRONT_PAGES, front_keywords=None, render_workers=1,
                 smart_pages=True, core_full_render=True):
    """批量处理所有PDF。两阶段：并行分类+文字提取；串行渲染。
    PDF原件默认保留（中途不删原则，保回溯）；keep_pdf=False（--purge-pdf）才在整批成功后删。"""
    if front_keywords is None:
        front_keywords = FRONT_RENDER_KEYWORDS
    images_dir = os.path.join(work_dir, "images")
    os.makedirs(images_dir, exist_ok=True)
    marker_path = os.path.join(work_dir, "_stage_a_complete.json")  # 阶段A完成标记

    # 扫描所有PDF（排序保证多次运行顺序一致；小文件优先，大文件置后）
    pdfs = []
    for root, dirs, files in os.walk(proof_dir):
        for f in files:
            if f.lower().endswith(".pdf"):
                pdfs.append(os.path.join(root, f))
    pdfs.sort(key=lambda p: (os.path.getsize(p), p))

    report = {"rendered": [], "text_extracted": [], "skipped": [], "errors": []}

    # ---- 完成状态自检：区分“已完成（PDF按设计删除）”与“真丢失/误用系统unzip” ----
    extract_report_path = os.path.join(proof_dir, "_extract_report.json")
    imgs_have_output = _images_has_output(images_dir)
    if len(pdfs) == 0:
        if os.path.exists(marker_path) or imgs_have_output:
            # 【已完成】状态：PDF已在上次成功处理后按设计删除，绝非丢失。明确提示，阻止误重解。
            print("✅ proof_dir 无PDF，但检测到阶段A产物"
                  + ("（存在完成标记 _stage_a_complete.json）" if os.path.exists(marker_path) else "（images/ 下有 txt/图片）") + "。")
            print("   这是【已完成】状态：PDF原件已在上次成功处理后按设计删除，【并非丢失】。")
            print("   请直接进入阶段B（读 images/ 下的 txt/图片），切勿重新解压 zip。")
            return report
        # 无任何产物也无PDF → 可疑（可能系统unzip静默丢文件）
        if not os.path.exists(extract_report_path):
            _safe_print("⚠️ 警告: proof_dir 有0个PDF、无阶段A产物、无 _extract_report.json。", err=True)
            _safe_print("   极可能是用了系统 unzip 解压（对中文+括号+超长路径 exit code=0 但静默丢PDF）。请勿删 zip，用 extract_zip.py 重解并校验 integrity_ok。", err=True)
        else:
            _safe_print("⚠️ 警告: proof_dir 有0个PDF且无阶段A产物，但已用extract_zip解压。请确认解压目录与参数是否正确。", err=True)
        return report

    # 有PDF待处理：解压来源自检（防系统unzip静默丢文件）
    if not os.path.exists(extract_report_path):
        _safe_print("⚠️ 警告: 未找到 _extract_report.json，可能未用 extract_zip.py 解压（如用了系统 unzip）。建议用 extract_zip.py 重解并校验 integrity_ok。", err=True)
    else:
        try:
            with open(extract_report_path, encoding="utf-8") as _f:
                _er = json.load(_f)
            if not _er.get("integrity_ok", True):
                _safe_print(f"⚠️ 警告: _extract_report.json 显示解压不完整（缺失{_er.get('missing_files')}个文件），请勿删 zip，修复后重解。", err=True)
        except Exception:
            pass

    # 断点续跑：跳过已有输出的PDF（核心报告还要求页图张数 ≥ 总页数，见 has_output）
    worklist = []
    skipped = 0
    core_resume = []
    for p in pdfs:
        name = os.path.splitext(os.path.basename(p))[0]
        if has_output(images_dir, name, p, core_full_render):
            skipped += 1
            report["skipped"].append(name)
        else:
            worklist.append((p, name))
            if core_full_render and is_core_report(name) and _count_pngs(images_dir, name) > 0:
                core_resume.append((name, _count_pngs(images_dir, name), _pdf_page_count(p)))

    print(f"共 {len(pdfs)} 个PDF；已完成跳过 {skipped} 个，待处理 {len(worklist)} 个"
          + (f"（并行{workers}线程）" if workers > 1 else ""))
    if core_resume:
        print("🔁 核心报告页图不全，本次续渲（表4-1~4-15 的参数与预测表在中后部，缺页=永久缺数据）：")
        for name, have, total in core_resume:
            print("   - %s：已有 %d 张 / 共 %s 页" % (name[:50], have, total if total else '?'))

    if dry_run:
        for p, name in worklist:
            print(f"  [DRY-RUN] {os.path.basename(p)}")
        return report

    t0 = time.time()

    # ================= 阶段1：并行分类 + 文字提取 =================
    pending_render = []      # [(pdf_path, pdf_name, status)] 全渲染（扫描/水印）
    front_render = []        # [(pdf_path, pdf_name, status)] 文字+前置页读图
    total1 = len(worklist)
    done1 = 0
    if worklist:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            futs = {ex.submit(classify_and_extract_text, p, images_dir, name,
                              front_pages, front_keywords, core_full_render): (p, name)
                    for p, name in worklist}
            for fut in as_completed(futs):
                p, name = futs[fut]
                try:
                    status = fut.result()
                except Exception as e:
                    status = {"file": p, "pdf_name": name, "action": "error", "error": str(e)}
                done1 += 1
                pct = done1 / total1 * 100 if total1 else 100
                prefix = f"  [阶段1 {done1}/{total1} {pct:.0f}%]"
                action = status.get("action")
                if action == "text_extracted":
                    report["text_extracted"].append(status)
                    _safe_print(f"{prefix} [文字] {os.path.basename(p)}: {status.get('text_length', 0)} 字")
                elif action == "text_pending_front":
                    front_render.append((p, name, status))
                    _safe_print(f"{prefix} [文字+前页图] {os.path.basename(p)}: {status.get('text_length', 0)} 字, 待渲染前{status.get('front_pages')}页")
                elif action == "pending_render":
                    pending_render.append((p, name, status))
                    _safe_print(f"{prefix} [待拆图] {os.path.basename(p)}: {status.get('render_reason', '')}")
                else:
                    report["errors"].append(status)
                    _safe_print(f"{prefix} [错误] {os.path.basename(p)}: {status.get('error', 'unknown')}", err=True)

    phase1_time = time.time() - t0
    print(f"\n阶段1完成（{phase1_time:.0f}s）：纯文字 {len(report['text_extracted'])} 个，"
          f"文字+前页图 {len(front_render)} 个，待拆图 {len(pending_render)} 个，错误 {len(report['errors'])} 个")

    # ================= 阶段2：渲染（默认串行保内存；--render-workers>1 可选并行，OOM自负） =================
    # mode: 'full'=扫描件全渲染; 'front'=文字PDF前置页读图
    serial_jobs = [(p, name, st, "full") for (p, name, st) in pending_render]
    serial_jobs += [(p, name, st, "front") for (p, name, st) in front_render]
    # 核心报告（审计/评估/估值/法律意见）**优先渲**：它们是第四章与表4-1~4-15 的唯一数据源，
    # 页级续渲已保证不会白干；小文件先渲的原则在同优先级内保留（尽量在超时前多完成）
    serial_jobs.sort(key=lambda x: (0 if (core_full_render and is_core_report(x[1])) else 1,
                                    os.path.getsize(x[0])))
    total_jobs = len(serial_jobs)
    rendered_count = 0
    stopped_reason = None
    # 单份渲染的页边界让出时刻（避免一份200页的核心报告把整条命令拖到超时）
    render_deadline = (t0 + time_budget) if time_budget is not None else None

    def _record(status, n_img, mode):
        """汇总一个渲染结果到report并打印（仅主线程调用）"""
        base = os.path.basename(status.get("file", ""))
        secs = status.get("_render_secs", "?")
        trunc = status.get("render_truncated")
        if mode == "front":
            if status.get("action") == "text_extracted":
                report["text_extracted"].append(status)
                _safe_print(f"  完成(front): {base} 文字层+{status.get('images_front', 0)}页图 ({secs}s)"
                            + (f" ⚠️ {trunc}" if trunc else ""))
            else:
                report["errors"].append(status)
                _safe_print(f"  前置页渲染失败: {base}: {status.get('error')}", err=True)
        else:
            if status.get("action") in ("rendered", "rendered_low_quality_text"):
                report["rendered"].append(status)
                _safe_print(f"  完成: {base} {status.get('images', 0)}张图 ({secs}s) [{status.get('render_reason', '')}]"
                            + (f" ⚠️ {trunc}" if trunc else ""))
            else:
                report["errors"].append(status)
                _safe_print(f"  渲染失败: {base}: {status.get('error')}", err=True)


    if render_workers and render_workers > 1 and total_jobs > 1:
        _safe_print(f"⚠️ 已启用并行渲染 --render-workers {render_workers}（内存红线：并发渲染大文件可能OOM导致沙箱崩溃；若崩溃请降回1。建议配合 --max-pages 限页）。", err=True)
        jobs = serial_jobs if batch_size is None else serial_jobs[:batch_size]
        submitted = 0
        with ThreadPoolExecutor(max_workers=render_workers) as ex:
            futs = {}
            for job in jobs:
                if time_budget is not None and (time.time() - t0) >= time_budget:
                    stopped_reason = f"达到 time-budget 上限 {time_budget}s（未提交剩余任务）"
                    break
                p, name, status, mode = job
                futs[ex.submit(_do_render, p, name, status, mode, images_dir, max_pages, dpi,
                               smart_pages, core_full_render, render_deadline)] = mode
                submitted += 1
            for fut in as_completed(futs):
                mode = futs[fut]
                status, n_img = fut.result()
                _record(status, n_img, mode)
                rendered_count += 1
                rpct = rendered_count / max(1, submitted) * 100
                _safe_print(f"  [渲染 {rendered_count}/{submitted} {rpct:.0f}%]")
                _write_report(work_dir, report)
    else:
        for p, name, status, mode in serial_jobs:
            if batch_size is not None and rendered_count >= batch_size:
                stopped_reason = f"达到 batch-size 上限 {batch_size}"
                break
            if time_budget is not None and (time.time() - t0) >= time_budget:
                stopped_reason = f"达到 time-budget 上限 {time_budget}s"
                break
            pdf_size_mb = os.path.getsize(p) / (1024 * 1024)
            elapsed = time.time() - t0
            rpct = (rendered_count + 1) / total_jobs * 100 if total_jobs else 100
            print(f"\n[渲染 {rendered_count + 1}/{total_jobs} {rpct:.0f}%] {os.path.basename(p)} "
                  f"({pdf_size_mb:.1f}MB, {status.get('total_pages', '?')}页, {mode})  [本次已用时{elapsed:.0f}s]")
            status, n_img = _do_render(p, name, status, mode, images_dir, max_pages, dpi,
                                       smart_pages, core_full_render, render_deadline)
            _record(status, n_img, mode)
            rendered_count += 1
            _write_report(work_dir, report)

    # 未完成的渲染任务记为 remaining
    not_rendered = total_jobs - rendered_count

    # ================= 删除原件（整批成功后统一删） =================
    # 仅当本轮无错误、且所有PDF都已有输出（全部成功）时，统一删除原件
    # ⚠️ 核心报告还要求页图张数≥总页数（has_output 内已校验）：被时间预算截断的核心报告
    #    不会被误判为"整批成功"，因此也不会被 --purge-pdf 提前删掉原件
    all_have_output = all(has_output(images_dir, os.path.splitext(os.path.basename(p))[0],
                                     p, core_full_render) for p in pdfs)
    truncated = [st.get("pdf_name") for st in (report["rendered"] + report["text_extracted"])
                 if isinstance(st, dict) and st.get("render_truncated")]
    fully_done = all_have_output and not_rendered == 0 and len(report["errors"]) == 0
    deleted = 0
    if fully_done and not keep_pdf:
        for p in pdfs:
            try:
                if os.path.exists(p):
                    os.remove(p)
                    deleted += 1
            except OSError as e:
                _safe_print(f"  删除原件失败: {p}: {e}", err=True)
        print(f"\n整批成功，已统一删除 {deleted} 个PDF原件。")
    elif fully_done and keep_pdf:
        print(f"\n整批成功（PDF原件默认保留，供回溯；任务最后统一清理）。")

    total_time = time.time() - t0
    report["total_pdfs"] = len(pdfs)
    report["remaining_pdfs"] = 0 if fully_done else (not_rendered + len(report["errors"]))
    report["stage_a_complete"] = bool(fully_done)
    _write_report(work_dir, report)

    # 写阶段A完成标记（区分“按设计删除”与“丢失”的权威信号）
    if fully_done:
        marker = {
            "stage_a_complete": True,
            "total_pdfs": len(pdfs),
            "rendered": len(report["rendered"]),
            "text_extracted": len(report["text_extracted"]),
            "originals_deleted": deleted,
            "originals_kept": bool(keep_pdf),
            "note": "PDF原件默认保留在proof_dir（中途不删，保回溯）；若originals_kept=false则已按--purge-pdf删除（非丢失）。产物在 images/ 下，切勿重新解压zip，直接进入阶段B。",
        }
        with open(marker_path, "w", encoding="utf-8") as f:
            json.dump(marker, f, ensure_ascii=False, indent=2)

    print(f"\n=== 本次处理完成 (总用时 {total_time:.0f}s) ===")
    print(f"文字层提取: {len(report['text_extracted'])}  拆图: {len(report['rendered'])}  "
          f"跳过(已完成): {skipped}  错误: {len(report['errors'])}")
    print(f"报告: {os.path.join(work_dir, 'batch_render_report.json')}")

    if fully_done:
        print(f"\n✅ 所有PDF已处理完毕（已写完成标记 _stage_a_complete.json）。")
        if not keep_pdf:
            print("   注：PDF原件已按 --purge-pdf 删除（非丢失）。后续若发现proof_dir无PDF，这是正常的，【切勿重新解压zip】，直接进入阶段B。")
    else:
        hint = stopped_reason or ("存在错误项" if report["errors"] else "本次未全部完成")
        print(f"\n⚠️ 尚未全部完成（{hint}），原件保留。"
              f"请**再次运行相同命令**继续（已完成的会自动跳过，核心报告按页续渲）。")
        if truncated:
            print("   其中被时间预算截断的核心报告（下次运行会从缺页处继续，已渲页不会重画）：")
            for n in truncated[:10]:
                print("     - %s" % n)

    return report


def _write_report(work_dir, report):
    report_path = os.path.join(work_dir, "batch_render_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description="批量拆图/提取文字层（分类改进+并行+整批删+断点续跑）")
    parser.add_argument("proof_dir", help="证明材料目录")
    parser.add_argument("--work-dir", required=True, help="工作目录")
    parser.add_argument("--max-pages", type=int, default=None, help="每个PDF最多渲染页数（大文件限页防单个超时）")
    parser.add_argument("--dry-run", action="store_true", help="只扫描不处理")
    parser.add_argument("--dpi", type=int, default=120, help="渲染DPI（默认120，图更小→视觉读图更快且内存更低；如图表/小字读不清可调高到150）")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                        help=f"阶段1文字提取并行线程数（默认{DEFAULT_WORKERS}；渲染始终串行保内存）")
    parser.add_argument("--batch-size", type=int, default=None,
                        help="本次最多渲染N个PDF后停止（分块运行，重跑自动续跑）")
    parser.add_argument("--time-budget", type=int, default=None,
                        help="跑满N秒后不再开始新的渲染（建议设为命令超时上限的8成，如240）")
    parser.add_argument("--keep-pdf", action="store_true",
                        help="（已是默认行为，保留兼容）PDF原件默认保留，不删除")
    parser.add_argument("--purge-pdf", action="store_true",
                        help="整批成功后删除PDF原件（默认不删，中途不删原则保回溯；仅磁盘实在吃紧时使用）")
    parser.add_argument("--text-front-pages", type=int, default=DEFAULT_FRONT_PAGES,
                        help=f"文字PDF（文件名含报告关键词）额外渲染前N页供读图表（默认{DEFAULT_FRONT_PAGES}，0=关闭）")
    parser.add_argument("--front-keywords", default=None,
                        help="触发前置页读图的文件名关键词（逗号分隔，默认：审计,评估,估值,报告）")
    parser.add_argument("--render-workers", type=int, default=1,
                        help="渲染并行线程数（默认1=串行保内存）。>1 可并行拆图加速，但并发渲染大文件可能OOM崩沙箱，建议配 --max-pages 限页，崩溃则降回1")
    parser.add_argument("--no-smart-pages", action="store_true",
                        help="关闭智能限页（默认开启：合同/验收报告/证照/截图等低价值多页扫描件只渲关键页；审计/评估/法律意见豁免永不裁剪）")
    parser.add_argument("--no-core-full-render", action="store_true",
                        help="关闭核心报告全量渲染（默认开启）。默认行为：文件名含 审计/评估/估值/法律意见 的PDF"
                             "**渲染全部页**，且不受 --text-front-pages 与 --max-pages 限制，"
                             "断点续渲只补缺页。原因：表4-1~4-15 需要的现金流预测表/运营费用参数/"
                             "资本性支出/可比实例都在报告中后部，只渲前置页会导致这些数据永久缺失。"
                             "仅在磁盘极度紧张时关闭（关闭后阶段B的核心材料页级门槛仍会报缺页）")
    args = parser.parse_args()

    front_keywords = None
    if args.front_keywords:
        front_keywords = [k.strip() for k in args.front_keywords.split(",") if k.strip()]

    batch_render(args.proof_dir, args.work_dir, args.max_pages, args.dry_run, args.dpi,
                 batch_size=args.batch_size, time_budget=args.time_budget,
                 workers=args.workers, keep_pdf=not args.purge_pdf,
                 front_pages=args.text_front_pages, front_keywords=front_keywords,
                 render_workers=args.render_workers, smart_pages=not args.no_smart_pages,
                 core_full_render=not args.no_core_full_render)



if __name__ == "__main__":
    main()
