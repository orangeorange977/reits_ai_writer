# -*- coding: utf-8 -*-
"""阶段2 真实模型端到端复查：真实调 Kimi 生成一章，验证阅读台账真实落盘。
会消耗少量 token（约几分钟）。"""
import json
import sys
import time
sys.path.insert(0, '.')

from fastapi.testclient import TestClient
from backend.main import app
from backend.services.auth import issue_token
from backend.config import PROJECTS_DIR

client = TestClient(app)
H = {"Authorization": "Bearer " + issue_token(1, "admin", "admin")}
fails = []
def check(name, cond, detail=""):
    print(f"[{'OK' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail and not cond else ""), flush=True)
    if not cond:
        fails.append(name)

# 1. 建临时项目
r = client.post("/api/projects", headers=H, json={"name": "台账真实验证临时项目"})
check("建临时项目", r.status_code in (200, 201), str(r.status_code) + r.text[:200])
pid = r.json().get("id") or r.json().get("project", {}).get("id")
print("  临时项目 id =", pid, flush=True)

try:
    # 2. 上传两份 txt 材料（真实可读）
    files = {
        "运营情况承诺函.txt": "运营情况承诺函\n落款单位：某某基金管理有限公司\n落款日期：2026年8月1日\n"
                          "本公司承诺基础设施项目运营情况良好，不存在重大不利变化。",
        "审计报告2024.txt": "审计报告正文：2024年度营业收入 5,000 万元，净利润 1,200 万元。",
    }
    for fname, content in files.items():
        rr = client.post(f"/api/projects/{pid}/materials", headers=H,
                         files={"files": (fname, content.encode("utf-8"), "text/plain")})
        check(f"上传 {fname}", rr.status_code in (200, 201), str(rr.status_code) + rr.text[:200])

    # 3. 触发第六章真实生成（异步）
    rr = client.post("/api/skills/chapter/6/run", headers=H,
                     params={"project_id": str(pid)})
    check("触发生成", rr.status_code == 200, str(rr.status_code) + rr.text[:200])

    # 4. 轮询状态（最多 12 分钟）
    final = None
    for i in range(144):
        time.sleep(5)
        rs = client.get("/api/skills/chapter/6/status", headers=H,
                        params={"project_id": str(pid)})
        st = rs.json()
        status = st.get("status", "")
        if i % 6 == 0:
            print(f"  ...{i*5}s status={status}", flush=True)
        if status in ("done", "error", "failed", "completed"):
            final = st
            break
    check("生成完成", final is not None and final.get("status") not in ("error", "failed"),
          str(final)[:300] if final else "超时未完成")

    # 5. 台账落盘验证
    led_path = PROJECTS_DIR / str(pid) / "read_ledger.json"
    led = json.loads(led_path.read_text(encoding="utf-8-sig")) if led_path.exists() else None
    check("read_ledger.json 已落盘", led is not None, str(led_path))
    if led:
        paths = [e["path"] for e in led]
        print("  台账条目:", json.dumps(led, ensure_ascii=False)[:600], flush=True)
        check("台账至少登记1份材料", len(led) >= 1, str(paths))
        check("台账路径形如真实材料文件", any(("承诺函" in p) or ("审计报告" in p) for p in paths), str(paths))
        check("条目含章节号6与时间戳", all(e.get("chapter") == 6 and e.get("last_at") for e in led))

    # 6. 章节 JSON 里的 read_stats 与 refs
    ch = PROJECTS_DIR / str(pid) / "ch6.json"
    if ch.exists():
        data = json.loads(ch.read_text(encoding="utf-8-sig"))
        rs2 = data.get("read_stats") or {}
        print("  read_stats:", rs2, flush=True)
        if led:
            check("read_stats 份数与台账一致", rs2.get("files") == len(led),
                  f"stats={rs2.get('files')} ledger={len(led)}")
        check("refs 含申报材料条目", any(r.startswith("申报材料：") for r in data.get("refs", [])),
              str(data.get("refs"))[:300])
    else:
        check("ch6.json 落盘", False)

finally:
    # 7. 清理临时项目
    rr = client.delete(f"/api/projects/{pid}", headers=H)
    check("清理-删除临时项目", rr.status_code == 200, str(rr.status_code))

print("\n结论:", "全部通过" if not fails else f"失败项: {fails}")
sys.exit(1 if fails else 0)
