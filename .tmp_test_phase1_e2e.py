# -*- coding: utf-8 -*-
"""阶段1验证②：端到端（进程内 TestClient，不起服务、不改真实账号）。
流程：签发 admin token → 回归旧接口 → 建临时项目 → 上传缺件材料包 →
校验 list_materials 的 catalog_check → 删临时项目恢复原状。"""
import io
import sys
sys.path.insert(0, '.')

from fastapi.testclient import TestClient
from backend.main import app
from backend.services.auth import issue_token

TOKEN = issue_token(1, "admin", "admin")
H = {"Authorization": "Bearer " + TOKEN}
client = TestClient(app)
fails = []


def check(name, cond, detail=""):
    print(f"[{'OK' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail and not cond else ""))
    if not cond:
        fails.append(name)


# —— 回归①：项目列表接口原有字段不受影响 ——
r = client.get("/api/projects", headers=H)
check("回归-项目列表 200", r.status_code == 200, str(r.status_code))
projs = r.json() if r.status_code == 200 else []
check("回归-预置项目1仍在", any(p.get("id") == 1 for p in projs))

# —— 回归②：项目1材料列表（无材料目录）→ 原字段齐全，catalog_check.available=False ——
r = client.get("/api/projects/1/materials", headers=H)
d = r.json() if r.status_code == 200 else {}
check("回归-项目1材料列表 200", r.status_code == 200, str(r.status_code))
check("回归-原字段未破坏", all(k in d for k in ("project_id", "total_files", "total_size", "files", "dirs")))
check("回归-无材料时不弹缺件提示", d.get("catalog_check", {}).get("available") is False)

# —— 建临时测试项目 ——
r = client.post("/api/projects", headers=H, json={"name": "缺件检测临时测试"})
check("建临时项目", r.status_code == 200, r.text[:120])
pid = r.json().get("id") if r.status_code == 200 else None

if pid:
    # —— 上传"缺件"材料包：只有营业执照/章程、审计报告、法律意见书 → 应报缺大量项 ——
    up = [
        ("files", ("一、参与主体情况/1 营业执照与章程/营业执照.pdf", io.BytesIO(b"%PDF fake"), "application/pdf")),
        ("files", ("一、参与主体情况/1 营业执照与章程/公司章程.pdf", io.BytesIO(b"%PDF fake"), "application/pdf")),
        ("files", ("一、参与主体情况/2 财务报告/审计报告2024.pdf", io.BytesIO(b"%PDF fake"), "application/pdf")),
        ("files", ("二、项目基本条件/法律意见书.pdf", io.BytesIO(b"%PDF fake"), "application/pdf")),
    ]
    r = client.post(f"/api/projects/{pid}/materials", headers=H, files=up)
    check("上传材料", r.status_code == 200, r.text[:120])

    r = client.get(f"/api/projects/{pid}/materials", headers=H)
    d = r.json() if r.status_code == 200 else {}
    cc = d.get("catalog_check", {})
    miss_nos = sorted(m["no"] for m in cc.get("missing", []))
    check("缺件体检 available=True", cc.get("available") is True, str(cc)[:200])
    check("已传4项被识别(第1/2/9项)", all(n not in miss_nos for n in (1, 2, 9)), f"missing={miss_nos}")
    check("未传项被报缺(含第4/12/22项)", all(n in miss_nos for n in (4, 12, 22)), f"missing={miss_nos}")
    check("message为业务语言", cc.get("message", "").startswith("缺少"), cc.get("message", "")[:60])
    check("原文件列表仍完整", d.get("total_files") == 4, f"total_files={d.get('total_files')}")

    # —— 补齐承诺函类材料后，缺件数应下降 ——
    up2 = [
        ("files", ("一、参与主体情况/4 运营情况承诺函.pdf", io.BytesIO(b"%PDF fake"), "application/pdf")),
        ("files", ("四、募集资金用途/回收资金使用承诺函.pdf", io.BytesIO(b"%PDF fake"), "application/pdf")),
    ]
    r = client.post(f"/api/projects/{pid}/materials", headers=H, files=up2)
    r = client.get(f"/api/projects/{pid}/materials", headers=H)
    cc2 = r.json().get("catalog_check", {})
    miss2 = sorted(m["no"] for m in cc2.get("missing", []))
    check("补传后第4/22项不再报缺", 4 not in miss2 and 22 not in miss2, f"missing={miss2}")
    check("补传后缺件数减少", len(miss2) < len(miss_nos), f"{len(miss_nos)}->{len(miss2)}")

    # —— 清理：删除临时项目 ——
    r = client.delete(f"/api/projects/{pid}", headers=H)
    check("清理-删除临时项目", r.status_code == 200, str(r.status_code))

# —— 回归③：项目1第二章内容接口仍可用（生成链路读取未受影响） ——
r = client.get("/api/skills/chapter/2/content", headers=H, params={"project_id": "1"})
check("回归-章节内容接口 200", r.status_code == 200, str(r.status_code))

print("\n结论:", "全部通过" if not fails else f"失败项: {fails}")
sys.exit(1 if fails else 0)
