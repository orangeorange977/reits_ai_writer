#!/usr/bin/env python3
"""为法律意见书、房地产/资产评估报告和第二章材料生成独立页级队列。

队列只处理“读哪些页”；字段映射分别见 references/ 下的专项指南。
先成功写入 extracted_data.json，再使用 --mark-batch 登记上一批。
"""

import argparse
import json
import os
import re
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in os.sys.path:
    os.sys.path.insert(0, SCRIPT_DIR)

from check_extraction_coverage import (  # noqa: E402
    build_page_evidence, collect_sources, file_stem, is_page_read,
    locate_artifacts, norm, page_num,
)


PASS_CONFIG = {
    "legal-opinion": {
        "include": [r"法律意见书"],
        "exclude": [],
        "guide": "references/legal_opinion_extraction.md",
    },
    "real-estate-appraisal": {
        "include": [r"房地产估价报告", r"资产评估报告", r"估值报告"],
        "exclude": [r"节能.*评估报告", r"节能分析专项报告"],
        "guide": "references/real_estate_appraisal_extraction.md",
    },
    "chapter2": {
        "include": [r"审计报告", r"财务报表", r"年度报告", r"年报",
                    r"营业执照", r"公司章程", r"法律意见书", r"股权结构",
                    r"信用记录", r"企业信用报告", r"天眼查", r"承诺函"],
        "exclude": [],
        "guide": "references/chapter2_extraction.md",
    },
}


def load_json(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def atomic_dump(path, data):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".specialized_", suffix=".json",
                               dir=os.path.dirname(os.path.abspath(path)))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def all_paths(proofs):
    out = []
    for values in (proofs.get("material_index") or {}).values():
        if isinstance(values, list):
            out.extend(str(x) for x in values if x)
    return sorted(set(out))


def matched(path, cfg):
    text = str(path).replace("\\", "/")
    return (any(re.search(p, text, re.I) for p in cfg["include"])
            and not any(re.search(p, text, re.I) for p in cfg["exclude"]))


def source_stems(extracted):
    paths = []
    collect_sources(extracted, paths)
    meta = extracted.get("_metadata") if isinstance(extracted, dict) else {}
    if isinstance(meta, dict):
        paths.extend(str(x) for x in (meta.get("read_pages") or []) if x)
    return {norm(file_stem(x)) for x in paths if x}


def units_for(rel_path, proofs, work_dir, extracted, page_pairs, txt_reads, src_stems):
    stem = file_stem(rel_path)
    art = locate_artifacts(work_dir, rel_path)
    units = []
    if art.get("kind") in ("txt", "txt+images"):
        txt_path = art.get("path")
        if norm(stem) not in txt_reads and txt_path:
            units.append({"kind": "txt", "path": txt_path, "weight": 2})
    for path in art.get("all_pages") or []:
        if not is_page_read(stem, path, page_pairs):
            units.append({"kind": "page", "path": path, "page": page_num(path), "weight": 1})

    # DOCX/XLSX/图片等不经 PDF 渲染的材料直接作为一个文件单元。
    if art.get("kind") == "none" and norm(stem) not in src_stems:
        proof_dir = proofs.get("proof_dir") or ""
        direct = os.path.join(proof_dir, rel_path) if proof_dir else rel_path
        units.append({"kind": "file", "path": direct, "weight": 2})
    return units


def mark_batch(extracted_path, batch_path):
    data = load_json(extracted_path)
    batch = load_json(batch_path)
    meta = data.setdefault("_metadata", {})
    current = [str(x) for x in (meta.get("read_pages") or []) if x]
    seen = set(current)
    for unit in batch.get("units") or []:
        path = str(unit.get("path") or "").strip()
        if path and path not in seen:
            current.append(path)
            seen.add(path)
    meta["read_pages"] = current
    atomic_dump(extracted_path, data)
    return len(batch.get("units") or [])


def main():
    ap = argparse.ArgumentParser(description="REITs专项提取页级队列")
    ap.add_argument("--pass", dest="pass_name", required=True, choices=sorted(PASS_CONFIG))
    ap.add_argument("--proofs-index", required=True)
    ap.add_argument("--extracted", required=True)
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--next-pages", type=int, default=6)
    ap.add_argument("--mark-batch", action="store_true")
    args = ap.parse_args()

    if args.next_pages < 1 or args.next_pages > 12:
        ap.error("--next-pages 必须在1~12之间")
    cfg = PASS_CONFIG[args.pass_name]
    proofs = load_json(args.proofs_index)
    extracted = load_json(args.extracted)
    batch_path = os.path.join(os.path.abspath(args.work_dir),
                              ".specialized_%s_last_batch.json" % args.pass_name)
    if args.mark_batch:
        if not os.path.exists(batch_path):
            print("ERROR: 找不到上一批清单 %s" % batch_path)
            return 2
        print("已登记上一批 %d 个单元" % mark_batch(args.extracted, batch_path))
        extracted = load_json(args.extracted)

    candidates = [p for p in all_paths(proofs) if matched(p, cfg)]
    page_pairs, txt_reads = build_page_evidence(extracted)
    stems = source_stems(extracted)
    queue = []
    for rel in candidates:
        for unit in units_for(rel, proofs, args.work_dir, extracted, page_pairs, txt_reads, stems):
            queue.append(dict(unit, document=rel))

    selected, used = [], 0
    for unit in queue:
        weight = int(unit.get("weight") or 1)
        if selected and used + weight > args.next_pages:
            break
        selected.append(unit)
        used += weight
        if used >= args.next_pages:
            break
    report = {
        "pass": args.pass_name,
        "guide": os.path.join(os.path.dirname(SCRIPT_DIR), cfg["guide"]),
        "candidate_documents": candidates,
        "remaining_units": len(queue),
        "units": selected,
    }
    atomic_dump(batch_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not candidates:
        print("WARNING: 未识别到该专项通道的候选材料，请核对文件命名。")
    elif not queue:
        print("READY: 该通道页/文件队列已清空；还需运行专项字段校验。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
