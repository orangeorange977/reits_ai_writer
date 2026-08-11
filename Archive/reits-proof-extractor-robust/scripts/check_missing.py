#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
缺件核对工具：对照25项标准材料骨架（templates/standard_proof_catalog.json），
检查扫描结果（proofs_index.json）中缺失的材料，输出缺件清单。

用法:
  python check_missing.py --proofs-index <proofs_index.json> --output <missing_materials.json>
"""

import argparse
import json
import os
import sys

script_dir = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CATALOG = os.path.join(script_dir, '..', 'templates', 'standard_proof_catalog.json')


def check_missing(proofs_index, catalog):
    """对照标准骨架输出缺件清单"""
    scanned_nos = set(proofs_index.get('material_index', {}).keys())
    missing = []
    for cat in catalog.get('categories', []):
        for item in cat.get('items', []):
            if str(item['no']) not in scanned_nos:
                missing.append({
                    'no': item['no'],
                    'category': cat['category'],
                    'name': item['name'],
                    'optional': item.get('optional', False),
                })
    return missing


def main():
    parser = argparse.ArgumentParser(description='证明材料缺件核对')
    parser.add_argument('--proofs-index', required=True, help='scan_proofs.py输出的索引JSON')
    parser.add_argument('--catalog', default=DEFAULT_CATALOG, help='标准材料骨架JSON（默认内置25项）')
    parser.add_argument('--output', '-o', required=True, help='缺件清单输出路径')

    args = parser.parse_args()

    with open(args.proofs_index, 'r', encoding='utf-8') as f:
        proofs_index = json.load(f)
    with open(args.catalog, 'r', encoding='utf-8') as f:
        catalog = json.load(f)

    missing = check_missing(proofs_index, catalog)

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(missing, f, ensure_ascii=False, indent=2)

    required = [m for m in missing if not m['optional']]
    optional = [m for m in missing if m['optional']]
    print(f"缺件核对完成: 共缺 {len(missing)} 项（必需 {len(required)} 项，如涉及 {len(optional)} 项）")
    for m in missing:
        tag = '（如涉及）' if m['optional'] else '【必需】'
        print(f"  缺件{tag}: {m['no']} {m['name']}")
    print(f"输出文件: {args.output}")


if __name__ == '__main__':
    main()
