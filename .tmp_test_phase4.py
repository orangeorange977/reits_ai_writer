# -*- coding: utf-8 -*-
"""阶段4验证：JSON 健康门禁 + 失败保护。
① 门禁单测：好数据放行零提醒、坏块剔除+业务提醒、畸形小节跳过；
② 抢救单测：截断 JSON 补尾/回退抢救、垃圾输出返回 None；
③ 失败保护端到端：monkeypatch 模型返回截断输出 → partial+notice 落盘；
   纯垃圾输出 → 报错 + ch{n}.last_failed.json 留证；
④ 预览接口回归：gate_warnings 字段存在、200。"""
import json
import shutil
import sys
sys.path.insert(0, '.')

from backend.services import json_gate
from backend.services import skill_runner

fails = []
def check(name, cond, detail=""):
    print(f"[{'OK' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail and not cond else ""))
    if not cond:
        fails.append(name)

def p(text): return {"type": "p", "text": text}
def grid(): return {"type": "grid", "caption": "表1", "headers": ["项目", "2024年"], "rows": [["收入", "1"]]}

# —— ① 门禁 ——
good = [{"title": "（一）甲", "blocks": [p("正文"), grid()]},
        {"title": "（二）乙", "blocks": [p("另一段")]}]
cleaned, warns = json_gate.check_and_clean(good)
check("门禁-好数据放行", len(cleaned) == 2 and warns == [], str(warns))

bad = [{"title": "（一）甲", "blocks": [p("正文"), {"type": "video", "src": "x"}, {"type": "p", "text": ""}, grid()]},
       {"title": "", "blocks": []},
       "不是字典"]
cleaned, warns = json_gate.check_and_clean(bad)
check("门禁-坏块剔除", len(cleaned) == 1 and all(b.get("type") in ("p", "grid") for b in cleaned[0]["blocks"]), str(cleaned))
check("门禁-提醒条数", len(warns) == 3, str(warns))
check("门禁-提醒为业务语言", all("请" in w for w in warns) and "type" not in "".join(warns), str(warns))
check("门禁-空输入", json_gate.check_and_clean([]) == ([], []) and json_gate.check_and_clean(None) == ([], []))

# —— ② 抢救 ——
full = '{"chapter_heading": "六", "sections": [{"title": "本章正文", "blocks": [{"type": "p", "text": "完整"}]}]}'
d = skill_runner._salvage_truncated(full)
check("抢救-完整JSON可用", d and len(d["sections"]) == 1)

trunc_mid_obj = '{"chapter_heading": "六", "sections": [{"title": "一", "blocks": [{"type": "p", "text": "第一节完整"}]}, {"title": "二", "blocks": [{"type": "p", "text": "写到一'
d = skill_runner._salvage_truncated(trunc_mid_obj)
# 抢救目标：第一节完整保住、已写部分不丢（半句被闭合保留也算保住）
check("抢救-截断后第一节完整保住",
      d and d["sections"][0]["blocks"][0]["text"] == "第一节完整", str(d)[:200])
check("抢救-已写部分不丢", d and any("写到一" in str(s) for s in d["sections"]), str(d)[:200])

trunc_mid_str = '{"chapter_heading": "六", "sections": [{"title": "一", "blocks": [{"type": "p", "text": "内容含括号{和引号\\"正常"}]}, {"title": "二", "blocks": [{"type": "p", "text": "半截字符串未闭'
d = skill_runner._salvage_truncated(trunc_mid_str)
check("抢救-截断在字符串中间", d and len(d["sections"]) >= 1, str(d)[:200])

check("抢救-垃圾输出返回None", skill_runner._salvage_truncated("抱歉，我无法生成内容") is None)
check("抢救-空输出返回None", skill_runner._salvage_truncated("") is None)

# —— ③ 失败保护端到端 ——
from backend.config import PROJECTS_DIR
PID = "998"
pdir = PROJECTS_DIR / PID
if pdir.exists():
    shutil.rmtree(pdir)

# 场景A：两次都是截断输出 → 抢救成功，partial + notice 落盘
skill_runner.chat_with_tools = lambda msgs, tools, ex, model=None, temperature=1.0: trunc_mid_obj
skill_runner.chat = lambda *a, **k: trunc_mid_obj
data = skill_runner.run_chapter(6, subtitles=None, materials_path=None, project_id=PID, pack_id=None)
check("场景A-partial标记", data.get("partial") is True, str(data.get("partial")))
check("场景A-已保留小节", len(data.get("sections", [])) >= 1)
check("场景A-业务提示", "已保留前" in data.get("generation_notice", ""), str(data.get("generation_notice")))
saved = json.loads((pdir / "ch6.json").read_text(encoding="utf-8-sig"))
check("场景A-notice随JSON落盘", saved.get("generation_notice") == data["generation_notice"])

# 场景B：纯垃圾输出 → 报错 + 留证
skill_runner.chat_with_tools = lambda msgs, tools, ex, model=None, temperature=1.0: "抱歉，我无法完成这个任务。"
skill_runner.chat = lambda *a, **k: "还是抱歉。"
try:
    skill_runner.run_chapter(6, subtitles=None, materials_path=None, project_id=PID, pack_id=None)
    check("场景B-抛出业务错误", False, "未抛异常")
except RuntimeError as e:
    check("场景B-抛出业务错误", "请重试生成" in str(e), str(e))
lf = pdir / "ch6.last_failed.json"
check("场景B-失败输出留证", lf.exists())
if lf.exists():
    payload = json.loads(lf.read_text(encoding="utf-8-sig"))
    check("场景B-留证含原始输出", "抱歉" in payload.get("raw", ""), str(list(payload.keys())))

# 场景C：重新生成成功 → notice 消失（用正常输出覆盖）
skill_runner.chat_with_tools = lambda msgs, tools, ex, model=None, temperature=1.0: full
data = skill_runner.run_chapter(6, subtitles=None, materials_path=None, project_id=PID, pack_id=None)
saved = json.loads((pdir / "ch6.json").read_text(encoding="utf-8-sig"))
check("场景C-成功后notice消失", not saved.get("generation_notice"), str(saved.get("generation_notice")))
check("场景C-partial消失", not saved.get("partial"))

# —— ④ 预览接口回归 ——
from fastapi.testclient import TestClient
from backend.main import app
from backend.services.auth import issue_token
client = TestClient(app)
H = {"Authorization": "Bearer " + issue_token(1, "admin", "admin")}
r = client.get("/api/skills/chapter/2/preview", headers=H, params={"project_id": "1"})
check("预览接口200", r.status_code == 200, str(r.status_code) + r.text[:200])
d = r.json()
check("预览含gate_warnings字段", isinstance(d.get("gate_warnings"), list), str(list(d.keys())))
check("预览旧字段保留", d.get("status") == "ok" and "has_content" in d)
r = client.get("/api/skills/chapter/2/content", headers=H, params={"project_id": "1"})
check("content接口回归200", r.status_code == 200 and "generation_notice" in r.json(), str(r.status_code))

# —— 清理 ——
shutil.rmtree(pdir, ignore_errors=True)
check("清理-临时项目目录已删", not pdir.exists())

print("\n结论:", "全部通过" if not fails else f"失败项: {fails}")
sys.exit(1 if fails else 0)
