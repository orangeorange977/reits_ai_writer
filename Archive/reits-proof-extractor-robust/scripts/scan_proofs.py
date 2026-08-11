#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
扫描证明材料目录，建立编号到文件路径的索引。
用法: python scan_proofs.py <proof_dir> --output <output_json>
"""

import argparse
import json
import os
import re
import sys


def extract_material_number(dirname):
    """从目录名中提取证明材料编号，如 '3 反映...' -> 3"""
    match = re.match(r'^(\d+)', dirname.strip())
    if match:
        return int(match.group(1))
    return None


def extract_file_number(filename):
    """从文件名中提取编号前缀，如 '4-1 xxx.pdf' -> '4-1'"""
    match = re.match(r'^(\d+[-\d]*)', filename.strip())
    if match:
        return match.group(1)


def scan_proofs(proof_dir):
    """
    扫描证明材料目录。
    目录结构为两级：
      一级: 中文序号目录（如「一、参与主体情况」「二、项目基本条件」...）
      二级: 阿拉伯数字开头目录（如「1 发起人...」「2 底层资产...」...）
    从二级目录名提取材料编号。
    """
    index = {}
    sub_index = {}

    for top_entry in os.listdir(proof_dir):
        top_path = os.path.join(proof_dir, top_entry)
        if not os.path.isdir(top_path):
            continue

        for sub_entry in os.listdir(top_path):
            sub_path = os.path.join(top_path, sub_entry)
            if not os.path.isdir(sub_path):
                continue

            material_num = extract_material_number(sub_entry)
            if material_num is None:
                continue

            for root, dirs, files in os.walk(sub_path):
                for f in files:
                    if f.startswith('~$') or f.startswith('.'):
                        continue

                    file_rel_path = os.path.relpath(os.path.join(root, f), proof_dir)
                    file_num = extract_file_number(f)

                    if material_num not in index:
                        index[material_num] = []
                    index[material_num].append(file_rel_path)

                    if file_num:
                        if file_num not in sub_index:
                            sub_index[file_num] = []
                        sub_index[file_num].append(file_rel_path)

    for k in index:
        index[k].sort()
    for k in sub_index:
        sub_index[k].sort()

    return {
        'proof_dir': os.path.abspath(proof_dir),
        'material_index': {str(k): v for k, v in sorted(index.items())},
        'file_index': {k: v for k, v in sorted(sub_index.items())},
    }


def main():
    parser = argparse.ArgumentParser(description='扫描证明材料目录')
    parser.add_argument('proof_dir', help='证明材料文件夹路径')
    parser.add_argument('--output', '-o', required=True, help='输出JSON路径')

    args = parser.parse_args()

    if not os.path.isdir(args.proof_dir):
        print(f"ERROR: Directory not found: {args.proof_dir}", file=sys.stderr)
        sys.exit(1)

    result = scan_proofs(args.proof_dir)

    total_files = sum(len(v) for v in result['material_index'].values())
    print(f"扫描完成: {len(result['material_index'])} 个材料类别, {total_files} 个文件")
    print(f"输出文件: {args.output}")

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
