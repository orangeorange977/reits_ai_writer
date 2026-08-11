#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PDF拆图工具：将PDF逐页渲染为PNG图片，供主agent用自带视觉能力直接阅读。
本脚本不调用任何外部OCR/LLM接口。

内存安全设计：
- fitz.open() 为流式打开，不会将整个PDF加载到内存（与pypdf不同）
- 每页渲染后立即释放pixmap（pix=None），任何时刻内存中仅保留1页
- 支持 --pages 参数选择性渲染指定页，避免大文件全页渲染
- ≥50MB文件建议分批渲染（如 --pages 1-20，然后 --pages 21-40）

用法:
  python pdf_to_images.py <pdf_path> --output-dir <图片输出目录> [--max-pages N] [--dpi 150]
  python pdf_to_images.py <pdf_path> -o <目录> --pages 1-5        # 只渲染第1~5页
  python pdf_to_images.py <pdf_path> -o <目录> --pages 3,7,10-15  # 渲染指定页

输出:
  <output-dir>/<pdf文件名>/page_001.png ...
  同时在stdout打印JSON清单（含是否判定为扫描件、每页图片路径）。
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

# 水印关键词：如果PDF文字层只包含这些内容，也判定为扫描件
# 已扩充：覆盖润泽水印、通用保密水印、英文水印
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
    stripped = re.sub(r'\s+', '', stripped)
    return stripped


def assess_page_quality(page):
    """评估单页质量，返回 (effective_chars, watermark_hits, has_image)"""
    text = page.get_text()
    effective = get_effective_text(text)
    hits = [kw for kw in WATERMARK_KEYWORDS if kw in text]
    has_image = len(page.get_images()) > 0
    return len(effective), hits, has_image


def is_scan_pdf(pdf_path, sample_pages=5):
    """
    判断PDF是否为扫描件（图片型PDF）：
    1. 页面文字极少（<50字符）且有图片 → 该页视为扫描页
    2. 页面文字剔除水印关键词后所剩无几 → 该页视为扫描页
    3. 超过60%的采样页为扫描页 → 判定为扫描件
    """
    doc = fitz.open(pdf_path)
    try:
        n = min(sample_pages, len(doc))
        if n == 0:
            return False
        scan_like = 0
        for i in range(n):
            page = doc[i]
            text = page.get_text().strip()
            stripped = get_effective_text(text)
            has_image = len(page.get_images()) > 0
            if (len(text) < 50 and has_image) or (len(stripped) < 20 and has_image):
                scan_like += 1
        return scan_like / n > 0.6
    finally:
        doc.close()


def parse_page_ranges(spec, total):
    """
    解析页码范围字符串，返回0-based页码列表。
    支持格式: "1-5", "3,7,10-15", "1-3,8,20-25"
    页码为1-based（用户友好），内部转为0-based。
    """
    pages = []
    for part in spec.split(','):
        part = part.strip()
        if '-' in part:
            start, end = part.split('-', 1)
            start, end = int(start), int(end)
            pages.extend(range(max(1, start) - 1, min(end, total)))
        else:
            p = int(part)
            if 1 <= p <= total:
                pages.append(p - 1)
    return sorted(set(pages))


def pdf_to_images(pdf_path, output_dir, max_pages=None, dpi=150, page_indices=None):
    """
    将PDF逐页渲染为PNG，返回图片路径列表。
    内存安全：每页渲染后立即释放pixmap，任何时刻仅保留1页。
    page_indices: 0-based页码列表（由--pages参数解析），为None时渲染全部。
    """
    pdf_name = os.path.splitext(os.path.basename(pdf_path))[0]
    # 文件名中的非法字符替换，避免建目录失败
    safe_name = re.sub(r'[\\/:*?"<>|]', '_', pdf_name)
    img_dir = os.path.join(output_dir, safe_name)
    os.makedirs(img_dir, exist_ok=True)

    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)

    images = []
    doc = fitz.open(pdf_path)
    try:
        total = len(doc)
        if page_indices is not None:
            render_list = [i for i in page_indices if i < total]
        else:
            n = min(max_pages, total) if max_pages else total
            render_list = list(range(n))
        for i in render_list:
            img_path = os.path.join(img_dir, f'page_{i + 1:03d}.png')
            page = doc[i]
            pix = page.get_pixmap(matrix=matrix)
            pix.save(img_path)
            pix = None   # 显式释放pixmap，防止内存累积
            page = None  # 释放页面引用
            images.append(img_path)
    finally:
        doc.close()
    return images, total


def main():
    parser = argparse.ArgumentParser(description='PDF拆图工具（无OCR调用，内存安全）')
    parser.add_argument('pdf_path', help='PDF文件路径')
    parser.add_argument('--output-dir', '-o', required=True, help='图片输出目录')
    parser.add_argument('--max-pages', type=int, default=None, help='最多渲染页数（默认全部）')
    parser.add_argument('--pages', type=str, default=None,
                        help='指定渲染页码（1-based），如 "1-5" 或 "3,7,10-15"。大文件建议分批')
    parser.add_argument('--dpi', type=int, default=150, help='渲染DPI（默认150）')

    args = parser.parse_args()

    if not os.path.isfile(args.pdf_path):
        print(f"ERROR: PDF not found: {args.pdf_path}", file=sys.stderr)
        sys.exit(1)

    file_size_mb = os.path.getsize(args.pdf_path) / (1024 * 1024)
    if file_size_mb >= 50 and not args.pages and not args.max_pages:
        print(f"WARN: 文件{file_size_mb:.0f}MB≥50MB，建议使用 --pages 分批渲染"
              f"（如 --pages 1-20）以避免内存峰值过高", file=sys.stderr)

    scan = is_scan_pdf(args.pdf_path)

    page_indices = None
    if args.pages:
        # 先获取总页数用于解析范围
        tmp_doc = fitz.open(args.pdf_path)
        total_for_parse = len(tmp_doc)
        tmp_doc.close()
        page_indices = parse_page_ranges(args.pages, total_for_parse)

    images, total_pages = pdf_to_images(args.pdf_path, args.output_dir,
                                        max_pages=args.max_pages, dpi=args.dpi,
                                        page_indices=page_indices)

    manifest = {
        'pdf_path': os.path.abspath(args.pdf_path),
        'file_size_mb': round(file_size_mb, 1),
        'is_scan_pdf': scan,
        'total_pages': total_pages,
        'rendered_pages': len(images),
        'images': [os.path.abspath(p) for p in images],
    }
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
