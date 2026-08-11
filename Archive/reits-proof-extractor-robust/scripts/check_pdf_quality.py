#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PDF文字层质量检测脚本：检测PDF每页有效字符数，判断是否需要拆图读图。

核心逻辑：
  - 水印PDF有"文字层"但内容全是水印（如"仅限润泽""再次复印或转发无效"）
  - 这种PDF提取出来的txt几乎无价值，必须拆图后用视觉能力读图
  - 本脚本逐页检测有效字符数，低质量页面占比>40%则标记为"需拆图"

用法:
  python check_pdf_quality.py <pdf_path> [--sample-pages N] [--threshold 40]

输出JSON:
  {
    "pdf_path": "...",
    "total_pages": 100,
    "sampled_pages": 10,
    "low_quality_pages": [3, 7, 12],
    "low_quality_ratio": 0.30,
    "verdict": "ok" | "need_render",
    "watermark_hits": ["仅限润泽", "再次复印"],
    "avg_effective_chars": 2500
  }
"""

import argparse
import json
import os
import re
import sys

try:
    import fitz  # PyMuPDF
except ImportError:
    print("ERROR: PyMuPDF not installed. Run: pip install PyMuPDF", file=sys.stderr)
    sys.exit(1)

# 扩充水印关键词列表
WATERMARK_KEYWORDS = [
    '仅用于REIT项目',
    '仅用于REIT',
    '仅限润泽',
    '仅限润泽科技',
    '再次复印或转发',
    '再次复印',
    '复印无效',
    '仅供REIT',
    '仅供内部',
    '内部使用',
    '保密',
    '机密',
    '仅供参考',
    '仅供审批',
    '不得用于其他用途',
    '版权所有',
    '翻印必究',
    'watermark',
    'confidential',
]


def get_effective_text(text):
    """剔除水印、空白后的有效文本"""
    stripped = text
    for kw in WATERMARK_KEYWORDS:
        stripped = stripped.replace(kw, '')
    # 剔除纯空白行
    stripped = re.sub(r'\s+', '', stripped)
    return stripped


def assess_page_quality(page):
    """评估单页质量，返回 (effective_chars, watermark_hits, has_image)"""
    text = page.get_text()
    effective = get_effective_text(text)
    hits = [kw for kw in WATERMARK_KEYWORDS if kw in text]
    has_image = len(page.get_images()) > 0
    return len(effective), hits, has_image


def check_pdf_quality(pdf_path, sample_pages=10, threshold=40):
    """检测PDF文字层质量"""
    doc = fitz.open(pdf_path)
    try:
        total = len(doc)
        n = min(sample_pages, total)
        if n == 0:
            return {"pdf_path": pdf_path, "total_pages": 0, "verdict": "empty"}

        low_quality_pages = []
        all_watermark_hits = set()
        total_effective = 0

        for i in range(n):
            page = doc[i]
            eff_chars, hits, has_image = assess_page_quality(page)
            total_effective += eff_chars
            all_watermark_hits.update(hits)

            # 有效字符<50且有图片 → 低质量页
            if eff_chars < 50 and has_image:
                low_quality_pages.append(i + 1)  # 1-based
            # 有效字符<20（即使无图片）→ 低质量页
            elif eff_chars < 20:
                low_quality_pages.append(i + 1)

        avg_eff = total_effective // n
        low_ratio = len(low_quality_pages) / n * 100

        if low_ratio > threshold:
            verdict = "need_render"
        elif avg_eff < 100:
            verdict = "need_render"
        else:
            verdict = "ok"

        return {
            "pdf_path": os.path.abspath(pdf_path),
            "total_pages": total,
            "sampled_pages": n,
            "low_quality_pages": low_quality_pages,
            "low_quality_ratio": round(low_ratio, 1),
            "verdict": verdict,
            "watermark_hits": sorted(all_watermark_hits),
            "avg_effective_chars": avg_eff,
        }
    finally:
        doc.close()


def main():
    parser = argparse.ArgumentParser(description='PDF文字层质量检测')
    parser.add_argument('pdf_path', help='PDF文件路径')
    parser.add_argument('--sample-pages', type=int, default=10, help='采样页数（默认10）')
    parser.add_argument('--threshold', type=float, default=40, help='低质量页占比阈值%%（默认40）')
    args = parser.parse_args()

    result = check_pdf_quality(args.pdf_path, args.sample_pages, args.threshold)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
