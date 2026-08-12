# -*- coding: utf-8 -*-
"""阶段2验证：阅读台账。
① 单测：页码归一、同章合并、重跑覆盖、统计；
② 集成：monkeypatch 掉模型调用，真实走 run_chapter 的工具执行器路径，
   触发 read_document → 验证台账登记与 read_stats（不调用大模型、不耗 token）。
③ 回归：projects 路由/材料接口仍正常。"""
import json
import shutil
import sys
sys.path.insert(0, '.')

from backend.services import read_ledger
from backend.config import PROJECTS_DIR

fails = []
def check(name, cond, detail=""):
    print(f"[{'OK' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail and not cond else ""))
    if not cond:
        fails.append(name)

PID = "999"
pdir = PROJECTS_DIR / PID
if pdir.exists():
    shutil.rmtree(pdir)

# —— ① 单测 ——
check("页码归一 '1-3'→[1,2,3]", read_ledger._parse_pages("1-3") == [1, 2, 3])
check("页码归一 '2,4'→[2,4]", read_ledger._parse_pages("2,4") == [2, 4])
check("页码归一 空→整篇", read_ledger._parse_pages("") == "整篇")
check("页码归一 乱序'5,1'→[1,5]", read_ledger._parse_pages("5,1") == [1, 5])

read_ledger.record_read(PID, 2, "一、参与主体情况/2 财务报告/审计报告2023.pdf", "1-3")
read_ledger.record_read(PID, 2, "一、参与主体情况/2 财务报告/审计报告2023.pdf", "5")
led = read_ledger.get_ledger(PID)
e = led[0] if led else {}
check("同章同文件合并为1条", len(led) == 1, str(led))
check("页码并集[1,2,3,5]", e.get("pages") == [1, 2, 3, 5], str(e.get("pages")))
check("读取次数=2", e.get("reads") == 2)

read_ledger.record_read(PID, 4, "二、项目基本条件/法律意见书.pdf", "")
# 同章不同文件不互相覆盖
read_ledger.record_read(PID, 2, "一、参与主体情况/3 信用记录/查询截图.png", "")
led2 = [x for x in read_ledger.get_ledger(PID) if x["chapter"] == 2]
check("同章不同文件各计1条", len(led2) == 2, str([x["path"] for x in led2]))
st2 = read_ledger.chapter_stats(PID, 2)
st4 = read_ledger.chapter_stats(PID, 4)
check("ch2统计=2份", st2["files"] == 2 and "查阅材料 2 份" in st2["message"], str(st2))
check("ch4统计=1份/整篇", st4["files"] == 1)

# 重跑覆盖：章节开跑前 reset_chapter 清掉该章旧条目
read_ledger.reset_chapter(PID, 2)
read_ledger.record_read(PID, 2, "一、参与主体情况/3 信用记录/新截图.png", "")
led = [x for x in read_ledger.get_ledger(PID) if x["chapter"] == 2]
check("重跑覆盖旧记录", len(led) == 1 and "新截图" in led[0]["path"], str(led))
check("他章记录不受影响", any(x["chapter"] == 4 for x in read_ledger.get_ledger(PID)))

# —— ② 集成：真实执行器路径（拦截模型调用）——
from backend.services import skill_runner

calls = {"exec": []}
def fake_chat_with_tools(messages, tools, tool_executor, model=None, temperature=1.0):
    # 模拟模型两次调用 read_document（带页 / 整篇）
    r1 = tool_executor("read_document", {"path": "测试材料/审计报告2024.txt", "pages": "1-2"})
    r2 = tool_executor("read_document", {"path": "测试材料/承诺函.txt"})
    calls["exec"] = [r1[:40], r2[:40]]
    assert "读取失败" not in r1 and "读取失败" not in r2, "测试文件应可真实读出内容"
    return json.dumps({"chapter_heading": "二、参与主体情况", "sections": []}, ensure_ascii=False)

skill_runner.chat_with_tools = fake_chat_with_tools
skill_runner.chat = lambda *a, **k: "{}"   # 兜底：ensure_write_config 等路径不真调模型

# 准备一个真实材料目录（含两个真实可读的 txt 文件）
mat = pdir / "materials"
(mat / "测试材料").mkdir(parents=True, exist_ok=True)
(mat / "测试材料" / "审计报告2024.txt").write_text("审计报告正文：货币资金 1,000 万元。", encoding="utf-8")
(mat / "测试材料" / "承诺函.txt").write_text("承诺函正文：落款日期 2026年1月1日。", encoding="utf-8")
# 清掉单测留下的台账，从空台账开始跑集成
(pdir / "read_ledger.json").unlink(missing_ok=True)

data = skill_runner.run_chapter(2, subtitles=None, materials_path=str(mat),
                                project_id=PID, pack_id=None)
led = read_ledger.get_ledger(PID)
paths = sorted(x["path"] for x in led)
check("执行器触发后台账登记2条", len(led) == 2, str(paths))
check("登记路径与工具入参一致", paths == ["测试材料/审计报告2024.txt", "测试材料/承诺函.txt"], str(paths))
pg = next((x["pages"] for x in led if "审计报告" in x["path"]), None)
check("页码随工具入参登记", pg == [1, 2], str(pg))
check("结果含read_stats且为业务语言", data.get("read_stats", {}).get("message") == "本章生成时共查阅材料 2 份", str(data.get("read_stats")))
check("refs同步含材料条目", any("审计报告2024.txt" in r for r in data.get("refs", [])), str(data.get("refs")))

# 重跑覆盖端到端：第二次生成只读一份文件 → 台账只剩这一份
def fake_chat_one(messages, tools, tool_executor, model=None, temperature=1.0):
    tool_executor("read_document", {"path": "测试材料/承诺函.txt"})
    return json.dumps({"chapter_heading": "二、参与主体情况", "sections": []}, ensure_ascii=False)
skill_runner.chat_with_tools = fake_chat_one
data2 = skill_runner.run_chapter(2, subtitles=None, materials_path=str(mat),
                                 project_id=PID, pack_id=None)
led = read_ledger.get_ledger(PID)
check("重跑后台账只剩本次1条", len(led) == 1 and led[0]["path"] == "测试材料/承诺函.txt", str(led))
check("重跑后统计同步更新", data2.get("read_stats", {}).get("files") == 1, str(data2.get("read_stats")))

# —— ③ 回归：projects 路由与缺件检测不受影响 ——
from fastapi.testclient import TestClient
from backend.main import app
from backend.services.auth import issue_token
client = TestClient(app)
H = {"Authorization": "Bearer " + issue_token(1, "admin", "admin")}
r = client.get("/api/projects/1/materials", headers=H)
check("回归-材料接口200", r.status_code == 200, str(r.status_code))
r = client.get("/api/skills/chapter/2/content", headers=H, params={"project_id": "1"})
check("回归-章节内容接口200", r.status_code == 200, str(r.status_code))

# —— 清理临时项目目录 ——
shutil.rmtree(pdir, ignore_errors=True)
check("清理-临时项目目录已删", not pdir.exists())

print("\n结论:", "全部通过" if not fails else f"失败项: {fails}")
sys.exit(1 if fails else 0)
