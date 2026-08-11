#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
文件清理工具：交付后安全删除中间产物（fill_plan/*.todo.json/中间版 docx 等），释放磁盘空间。

⚠️ 只在**第三步全部 PASS、成品已交付之后**使用（详见 SKILL.md「交付后清理」）。
永久保留、任何时候禁止删除：输入的 extracted_data.json 与 proofs_index.json、
base_vars.json、checkpoint.json、validate_report.json、输出 docx、初稿 docx。

用法:
  python purge_file.py <文件或目录路径> [--confirm]
  python purge_file.py <文件1> <文件2> ... [--confirm]

--confirm: 不加此参数只打印将删除什么，不实际删除（dry-run预览）
"""

import argparse
import os
import sys


def get_files_to_purge(paths):
    """展开路径列表，返回所有待删除的文件（递归目录）"""
    files = []
    for p in paths:
        if os.path.isfile(p):
            files.append(p)
        elif os.path.isdir(p):
            for root, dirs, fnames in os.walk(p):
                for f in fnames:
                    files.append(os.path.join(root, f))
    return files


def main():
    parser = argparse.ArgumentParser(description='交付后安全删除中间产物')
    parser.add_argument('paths', nargs='+', help='要删除的文件或目录路径')
    parser.add_argument('--confirm', action='store_true', help='实际执行删除（不加则dry-run预览）')
    parser.add_argument('--allow-zip', action='store_true',
                        help='允许删除zip压缩包（默认保护不删：zip 多半是用户提供的原始材料包，本 SKILL 无权处置）')

    args = parser.parse_args()

    files = get_files_to_purge(args.paths)

    # zip保护：zip 多半是用户/上游提供的原始材料包，默认拒绝删除
    zips = [f for f in files if f.lower().endswith('.zip')]
    if zips and not args.allow_zip:
        for z in zips:
            print(f"  跳过zip(保护): {z}")
        files = [f for f in files if not f.lower().endswith('.zip')]
        print("提示: zip压缩包默认受保护不删除。它通常是用户提供的原始材料包，如确需删除请由用户确认后加 --allow-zip。")

    if not files:
        print("无文件可删除")
        return

    total_size = sum(os.path.getsize(f) for f in files if os.path.exists(f))

    for f in files:
        if os.path.exists(f):
            if args.confirm:
                os.remove(f)
                print(f"  已删除: {f}")
            else:
                print(f"  待删除: {f}")

    if args.confirm:
        # 尝试删除空目录
        for p in args.paths:
            if os.path.isdir(p):
                for root, dirs, fnames in os.walk(p, topdown=False):
                    if not os.listdir(root) and root != p:
                        os.rmdir(root)
                if not os.listdir(p):
                    os.rmdir(p)
                    print(f"  已删除空目录: {p}")
        print(f"\n完成: 删除 {len(files)} 个文件，释放 {total_size/1024/1024:.1f}MB")
    else:
        print(f"\nDry-run: 将删除 {len(files)} 个文件，释放 {total_size/1024/1024:.1f}MB")
        print("加 --confirm 执行实际删除")


if __name__ == '__main__':
    main()

