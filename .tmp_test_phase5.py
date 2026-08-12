# -*- coding: utf-8 -*-
"""阶段5验证：文档友好命名（项目名_日期_第n章_vN）+ 历史版本保留。
① 单测：文件名净化、项目名查询兜底；
② 生命周期：预览固化 v1 → 内容变更重新渲染固化 v2 → 缓存命中不产生新版本 → 历史全保留；
③ 文档管理列表：版本/日期/排序字段齐全；
④ 下载：缺省最新版、指定历史版本、不存在版本 404、文件名即新命名；
⑤ 老数据迁移：只有 ch{n}_output.docx 时自动固化 v1（日期取文件修改日）；
⑥ 回归：既有项目预览/下载接口不受破坏。"""
import json
import re
import shutil
import sys
import time
from urllib.parse import unquote
sys.path.insert(0, '.')

from fastapi.testclient import TestClient
from backend.main import app
from backend.services.auth import issue_token
from backend.services import skill_runner
from backend.config import PROJECTS_DIR

fails = []
def check(name, cond, detail=""):
    print(f"[{'OK' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail and not cond else ""))
    if not cond:
        fails.append(name)

client = TestClient(app)
H = {"Authorization": "Bearer " + issue_token(1, "admin", "admin")}
TODAY = time.strftime("%Y%m%d")


def p(text): return {"type": "p", "text": text}
def sections_of(txt):
    return [{"title": "（一）测试小节", "blocks": [p(txt)]}]


def write_ch(pid, n, txt):
    path = PROJECTS_DIR / pid / f"ch{n}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"chapter_heading": "一", "sections": sections_of(txt)},
                               ensure_ascii=False), encoding="utf-8")


# —— ① 单测 ——
check("净化-非法字符换下划线", skill_runner._safe_filename("甲/乙:丙*丁") == "甲_乙_丙_丁",
      skill_runner._safe_filename("甲/乙:丙*丁"))
check("净化-空值兜底", skill_runner._safe_filename("") == "未命名项目")
check("净化-超长截断", len(skill_runner._safe_filename("长" * 100)) <= 60)
check("项目名-真实项目", "万国" in skill_runner._project_name_sync("1"),
      skill_runner._project_name_sync("1"))
check("项目名-不存在项目兜底", skill_runner._project_name_sync("99999") == "项目99999")

# —— ② 生命周期（新建临时项目）——
r = client.post("/api/projects", headers=H, json={"name": "命名测试/项目"})
check("建临时项目", r.status_code in (200, 201), str(r.status_code) + r.text[:200])
PID = str(r.json()["id"])
SAFE = "命名测试_项目"  # / 被净化为 _

try:
    write_ch(PID, 1, "第一版正文")
    r = client.get("/api/skills/chapter/1/preview", headers=H, params={"project_id": PID})
    check("预览-首次200", r.status_code == 200, str(r.status_code) + r.text[:200])
    out = PROJECTS_DIR / PID / "output"
    vs = skill_runner.versioned_docx_files(1, PID)
    check("预览-固化v1", len(vs) == 1, str([f.name for f in vs]))
    expect = f"{SAFE}_{TODAY}_第1章_v1.docx"
    check("v1命名符合规则", vs and vs[0].name == expect, vs[0].name if vs else "无")

    # 缓存命中（内容没变）不应产生新版本
    r = client.get("/api/skills/chapter/1/preview", headers=H, params={"project_id": PID})
    check("预览-缓存命中200", r.status_code == 200)
    check("缓存命中不产生新版本", len(skill_runner.versioned_docx_files(1, PID)) == 1)

    # 内容变更 → 重新渲染 → v2，v1 保留
    time.sleep(0.05)
    write_ch(PID, 1, "第二版正文")
    r = client.get("/api/skills/chapter/1/preview", headers=H, params={"project_id": PID})
    check("预览-变更后200", r.status_code == 200, str(r.status_code) + r.text[:200])
    vs = skill_runner.versioned_docx_files(1, PID)
    check("变更固化v2", len(vs) == 2 and vs[-1].name == f"{SAFE}_{TODAY}_第1章_v2.docx",
          str([f.name for f in vs]))
    check("历史版本保留", (out / expect).exists())

    # 去重：正文未变时重复固化不产生新版本（避免重启后重复渲染出重复文件）
    got = skill_runner.snapshot_docx(1, PID)
    vs = skill_runner.versioned_docx_files(1, PID)
    check("去重-正文未变不重复出版本", len(vs) == 2 and got == vs[-1], str([f.name for f in vs]))

    # —— ③ 文档管理列表 ——
    r = client.get("/api/skills/documents", headers=H, params={"project_id": PID})
    docs = r.json().get("documents", [])
    check("列表200且2条", r.status_code == 200 and len(docs) == 2, str(len(docs)))
    check("列表新版本在前", docs and docs[0]["version"] == 2 and docs[1]["version"] == 1,
          str([(d["version"]) for d in docs]))
    d0 = docs[0] if docs else {}
    check("列表字段齐全", all(k in d0 for k in ("chapter", "version", "version_date", "filename", "size_formatted", "updated_at")))
    check("列表日期格式", re.match(r"^\d{4}-\d{2}-\d{2}$", d0.get("version_date", "")), str(d0.get("version_date")))

    # —— ④ 下载（主动下载=导出事件，必出新版本）——
    r = client.get("/api/skills/chapter/1/download", headers=H, params={"project_id": PID})
    cd = unquote(r.headers.get("content-disposition", ""))
    check("下载-缺省导出200", r.status_code == 200, str(r.status_code))
    check("下载-导出即新版本v3", "_v3.docx" in cd and SAFE in cd, cd)
    r = client.get("/api/skills/chapter/1/download", headers=H, params={"project_id": PID})
    cd = unquote(r.headers.get("content-disposition", ""))
    check("下载-再导出v4", "_v4.docx" in cd, cd)
    r = client.get("/api/skills/chapter/1/download", headers=H, params={"project_id": PID, "version": 1})
    cd = unquote(r.headers.get("content-disposition", ""))
    check("下载-指定v1", r.status_code == 200 and "_v1.docx" in cd, cd)
    check("下载-指定版本不产生新版本", len(skill_runner.versioned_docx_files(1, PID)) == 4,
          str([f.name for f in skill_runner.versioned_docx_files(1, PID)]))
    r = client.get("/api/skills/chapter/1/download", headers=H, params={"project_id": PID, "version": 99})
    check("下载-不存在版本404", r.status_code == 404, str(r.status_code))

    # —— ④b 删除 ——
    r = client.delete("/api/skills/chapter/1/document", headers=H, params={"project_id": PID, "version": 0})
    check("删除-未指定版本400", r.status_code == 400, str(r.status_code))
    r = client.delete("/api/skills/chapter/1/document", headers=H, params={"project_id": PID, "version": 1})
    check("删除-v1成功", r.status_code == 200, str(r.status_code) + r.text[:200])
    vs = skill_runner.versioned_docx_files(1, PID)
    check("删除后v1消失其余保留", len(vs) == 3 and all(not f.name.endswith("_v1.docx") for f in vs),
          str([f.name for f in vs]))
    r = client.delete("/api/skills/chapter/1/document", headers=H, params={"project_id": PID, "version": 1})
    check("删除-不存在版本404", r.status_code == 404, str(r.status_code))

    # —— ⑤ 老数据迁移 ——
    write_ch(PID, 2, "老文档")
    work = skill_runner.chapter_docx_path(2, PID)
    work.write_bytes(b"fake docx bytes")
    mt = time.time() - 86400 * 3  # 3 天前
    import os
    os.utime(work, (mt, mt))
    r = client.get("/api/skills/documents", headers=H, params={"project_id": PID})
    ch2docs = [d for d in r.json().get("documents", []) if d["chapter"] == 2]
    check("迁移-工作文件固化v1", len(ch2docs) == 1 and ch2docs[0]["version"] == 1, str(ch2docs))
    expect_date = time.strftime("%Y-%m-%d", time.localtime(mt))
    check("迁移-日期取文件修改日", ch2docs and ch2docs[0]["version_date"] == expect_date,
          ch2docs[0]["version_date"] if ch2docs else "")
    check("迁移-工作文件仍在", work.exists())
finally:
    client.delete(f"/api/projects/{PID}", headers=H)
    shutil.rmtree(PROJECTS_DIR / PID, ignore_errors=True)
check("清理-临时项目已删", not (PROJECTS_DIR / PID).exists())

# —— ⑥ 回归：真实项目 1 ——
r = client.get("/api/skills/chapter/2/preview", headers=H, params={"project_id": "1"})
check("回归-预览200", r.status_code == 200, str(r.status_code) + r.text[:200])
r = client.get("/api/skills/documents", headers=H, params={"project_id": "1"})
check("回归-文档列表200", r.status_code == 200, str(r.status_code))
docs = r.json().get("documents", [])
check("回归-列表含版本字段", all("version" in d for d in docs), str(len(docs)))
r = client.get("/api/skills/chapter/1/download", headers=H, params={"project_id": "1", "version": 1})
check("回归-下载200", r.status_code == 200, str(r.status_code) + r.text[:200])

print("\n结论:", "全部通过" if not fails else f"失败项: {fails}")
sys.exit(1 if fails else 0)
