#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
证明材料zip解压工具：处理中文文件名乱码与超长路径问题。

核心逻辑：
1. 文件名解码：zip条目设置了UTF-8 flag（flag_bits & 0x800）时直接用；
   否则zipfile已按cp437误解码，需 encode('cp437') 还原原始字节后按 GBK→UTF-8 依次尝试解码
2. 超长路径组件：按UTF-8字节数截断（Linux单组件限制255字节），
   截断时保留开头的材料编号前缀（scan_proofs.py 依赖编号匹配）和末尾语义部分，追加短hash防重名
3. 防zip-slip：目标路径必须落在输出目录内
4. 输出 _name_mapping.json 记录被截断的原名↔短名映射，便于溯源

用法:
  python extract_zip.py <zip路径> --output-dir <解压目录> [--keep-root]

默认当zip只有一个顶层目录时自动剥离该层（顶层目录名通常极长且无编号）。
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import zipfile

# 单个路径组件的最大UTF-8字节数（Linux上限255，留余量）
MAX_COMPONENT_BYTES = 180


def decode_name(info):
    """解码zip条目文件名，返回(解码结果, 是否成功还原)"""
    raw = info.filename
    if info.flag_bits & 0x800:
        return raw, True
    try:
        raw_bytes = raw.encode('cp437')
    except UnicodeEncodeError:
        # 含cp437无法表示的字符，说明zipfile已按其他编码正确解码，直接用
        return raw, True
    for enc in ('gbk', 'utf-8'):
        try:
            return raw_bytes.decode(enc), True
        except UnicodeDecodeError:
            continue
    # 还原失败，保留cp437解码结果（可能乱码但不中断）
    return raw, False


def shorten_component(name):
    """
    截断超长路径组件（按UTF-8字节数）：
    保留编号前缀（如 "3 "、"13-1-1-5 "）+ 截断的头部 + "…" + 末尾语义 + 短hash + 扩展名
    """
    if len(name.encode('utf-8')) <= MAX_COMPONENT_BYTES:
        return name

    stem, ext = os.path.splitext(name)
    m = re.match(r'^(\d+[-\d]*[ 　.、]*)', stem)
    prefix = m.group(1) if m else ''
    body = stem[len(prefix):]
    tail = body[-15:]
    hash4 = hashlib.md5(name.encode('utf-8')).hexdigest()[:4]

    # 预算 = 上限 - 前缀/尾部/hash/扩展名等固定部分
    fixed = f"{prefix}…{tail}_{hash4}{ext}"
    budget = MAX_COMPONENT_BYTES - len(fixed.encode('utf-8'))
    head = ''
    for ch in body:
        ch_len = len(ch.encode('utf-8'))
        if budget - ch_len < 0:
            break
        head += ch
        budget -= ch_len

    return f"{prefix}{head}…{tail}_{hash4}{ext}"


def extract_zip(zip_path, output_dir, keep_root=False):
    """解压zip，返回统计信息"""
    zf = zipfile.ZipFile(zip_path)
    infos = [i for i in zf.infolist()]

    # 过滤压缩包垃圾条目
    def is_junk(name):
        base = os.path.basename(name.rstrip('/'))
        return '__MACOSX' in name or base.startswith('~$') or base == '.DS_Store'

    # 先全部解码
    decoded = []
    decode_failures = 0
    for info in infos:
        name, ok = decode_name(info)
        if not ok:
            decode_failures += 1
        if not is_junk(name):
            decoded.append((info, name.replace('\\', '/')))

    # 判断是否剥离唯一顶层目录
    top_levels = set(n.split('/', 1)[0] for _, n in decoded if n.strip('/'))
    strip_root = (not keep_root) and len(top_levels) == 1

    os.makedirs(output_dir, exist_ok=True)
    base_abs = os.path.abspath(output_dir)

    name_map = {}
    extracted = 0
    for info, name in decoded:
        rel = name
        if strip_root:
            parts = rel.split('/', 1)
            rel = parts[1] if len(parts) > 1 else ''
        if not rel or not rel.strip('/'):
            continue

        # 逐组件截断超长名
        components = [c for c in rel.split('/') if c]
        short_components = [shorten_component(c) for c in components]
        short_rel = '/'.join(short_components)
        if short_rel != '/'.join(components):
            name_map['/'.join(components)] = short_rel

        target = os.path.abspath(os.path.join(base_abs, *short_components))
        # 防zip-slip
        if not target.startswith(base_abs + os.sep) and target != base_abs:
            print(f"WARN: 跳过越界条目: {rel}", file=sys.stderr)
            continue

        if info.is_dir() or name.endswith('/'):
            os.makedirs(target, exist_ok=True)
        else:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zf.open(info) as src, open(target, 'wb') as dst:
                shutil.copyfileobj(src, dst)
            extracted += 1

    # 记录截断映射，便于溯源（放在输出目录根部，不影响scan_proofs两级目录扫描）
    map_path = ''
    if name_map:
        map_path = os.path.join(base_abs, '_name_mapping.json')
        with open(map_path, 'w', encoding='utf-8') as f:
            json.dump(name_map, f, ensure_ascii=False, indent=2)

    # ---- 解压完整性校验 ----
    zip_total = sum(1 for _, n in decoded if n.strip('/') and not n.endswith('/'))
    missing = zip_total - extracted
    integrity_ok = (missing == 0)

    result = {
        'zip_path': os.path.abspath(zip_path),
        'output_dir': base_abs,
        'extracted_files': extracted,
        'zip_total_files': zip_total,
        'missing_files': missing,
        'integrity_ok': integrity_ok,
        'stripped_root': strip_root,
        'decode_failures': decode_failures,
        'shortened_names': len(name_map),
        'name_mapping': map_path,
    }
    if not integrity_ok:
        result['warning'] = f'解压不完整：zip含{zip_total}个文件，实际解压{extracted}个，缺失{missing}个。请勿删除zip原文件。'
        print(f"\n⚠️ 警告: 解压不完整！zip含{zip_total}个文件，实际解压{extracted}个，缺失{missing}个。", file=sys.stderr)
        print(f"   常见原因：路径截断后同名文件互相覆盖、磁盘空间不足、权限问题。", file=sys.stderr)
        print(f"   建议：不要删除zip原文件，排查原因后重新解压。", file=sys.stderr)

    # 解压报告落盘到解压目录根部，供后续步骤（删zip前）核验完整性
    report_path = os.path.join(base_abs, '_extract_report.json')
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    result['report_path'] = report_path

    return result


def main():
    parser = argparse.ArgumentParser(description='证明材料zip解压（中文文件名+超长路径处理）')
    parser.add_argument('zip_path', help='zip文件路径')
    parser.add_argument('--output-dir', '-o', required=True, help='解压输出目录')
    parser.add_argument('--keep-root', action='store_true', help='保留zip顶层目录（默认单顶层目录时自动剥离）')

    args = parser.parse_args()

    if not os.path.isfile(args.zip_path):
        print(f"ERROR: zip not found: {args.zip_path}", file=sys.stderr)
        sys.exit(1)

    result = extract_zip(args.zip_path, args.output_dir, keep_root=args.keep_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    # 解压不完整时返回非零退出码，便于主agent检测
    if not result.get('integrity_ok', True):
        sys.exit(2)


if __name__ == '__main__':
    main()
