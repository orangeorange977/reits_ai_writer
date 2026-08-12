# -*- coding: utf-8 -*-
"""阶段3验证：财务表勾稽校验器。
① fixture 单测：三条规则各自"有问题报得出、没问题不誤报"；
② 真实数据回归：项目1已生成的 ch1/ch2 跑校验不崩、返回结构正确；
③ 接口回归：/chapter/{n}/content 返回 table_check 字段、旧字段不变。"""
import json
import sys
sys.path.insert(0, '.')

from backend.services import table_check as tc

fails = []
def check(name, cond, detail=""):
    print(f"[{'OK' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail and not cond else ""))
    if not cond:
        fails.append(name)

# —— 数值解析基础 ——
check("千分位解析", tc._parse_num("1,234.56") == 1234.56)
check("万元后缀", tc._parse_num("5,000万元") == 5000)
check("百分号", tc._parse_num("56.7%") == 56.7)
check("括号负数", tc._parse_num("（1,000）") == -1000)
check("备注不解析", tc._parse_num("【注：待核实】") is None)
check("横线不解析", tc._parse_num("—") is None)
check("文字不解析", tc._parse_num("不适用") is None)

def grid(caption, headers, rows):
    return {"type": "grid", "caption": caption, "headers": headers, "rows": rows}
def sec(title, *blocks):
    return {"title": title, "blocks": list(blocks)}

# —— 规则1：资产负债率 ——
good = grid("表13 主要财务指标", ["项目", "2024年", "2023年"],
            [["资产总额（万元）", "10,000", "8,000"],
             ["负债总额（万元）", "6,000", "4,000"],
             ["资产负债率", "60%", "50%"]])
bad = grid("表13 主要财务指标", ["项目", "2024年"],
           [["资产总额（万元）", "10,000"],
            ["负债总额（万元）", "6,000"],
            ["资产负债率", "55%"]])          # 实际应为60%
ratio_dec = grid("表A", ["项目", "2024年"],
                 [["资产总额（万元）", "10,000"],
                  ["负债总额（万元）", "6,000"],
                  ["资产负债率", "0.6"]])     # 小数写法，等价60%，不应报
check("规则1-正确数据不误报", tc.check_sections([sec("（一）财务", good)]) == [])
iss = tc.check_sections([sec("（一）财务", bad)])
check("规则1-错误比率报出", len(iss) == 1 and "60.00%" in iss[0]["message"], str(iss))
check("规则1-提示为业务语言", "请核对" in iss[0]["message"] and "rule" not in json.dumps(iss).lower())
check("规则1-小数写法兼容", tc.check_sections([sec("（一）财务", ratio_dec)]) == [])

# —— 规则2：列数一致 ——
cols = grid("表14 收入结构", ["项目", "2024年", "2023年", "2022年"],
            [["机柜租赁收入", "1,000", "900"]])   # 少一列
iss = tc.check_sections([sec("（二）收入", cols)])
check("规则2-缺列报出", len(iss) == 1 and "不一致" in iss[0]["message"], str(iss))
ok_cols = grid("表14", ["项目", "2024年", "2023年"], [["收入", "1,000", "900"]])
check("规则2-列数一致不误报", tc.check_sections([sec("（二）收入", ok_cols)]) == [])

# —— 规则3：跨表一致 ——
t1 = grid("表13 主要财务指标", ["项目", "2024年"], [["营业收入", "5,000"]])
t2 = grid("表14 收入构成", ["项目", "2024年"], [["营业收入", "5,200"]])
iss = tc.check_sections([sec("（一）财务", t1), sec("（二）收入", t2)])
cross = [i for i in iss if "跨表不一致" in i["message"]]
check("规则3-跨表不一致报出", len(cross) == 1 and "营业收入" in cross[0]["message"], str(iss))
t2b = grid("表14 收入构成", ["项目", "2024年"], [["营业收入", "5,000"]])
iss = tc.check_sections([sec("（一）财务", t1), sec("（二）收入", t2b)])
check("规则3-一致不误报", not any("跨表不一致" in i["message"] for i in iss), str(iss))
# 不同期间不算不一致
t3 = grid("表15", ["项目", "2023年"], [["营业收入", "4,800"]])
iss = tc.check_sections([sec("（一）财务", t1), sec("（二）收入", t3)])
check("规则3-不同期间不误报", not any("跨表不一致" in i["message"] for i in iss), str(iss))

# —— 容错：畸形结构不崩 ——
weird = [{"title": "x", "blocks": [{"type": "grid", "caption": "坏表", "headers": None, "rows": [["a"]]}]},
         {"title": "y"}, None]
try:
    r = tc.check_sections(weird)
    check("畸形结构不崩", isinstance(r, list), str(r))
except Exception as e:
    check("畸形结构不崩", False, str(e))
check("空输入返回空列表", tc.check_sections([]) == [])

# —— 真实数据回归：项目1 已生成章节跑一遍不崩 ——
import pathlib
for ch in ("ch1.json", "ch2.json"):
    p = pathlib.Path("workspace/projects/1") / ch
    if p.exists():
        d = json.loads(p.read_text(encoding="utf-8-sig"))
        r = tc.check_sections(d.get("sections", []))
        check(f"真实{ch}跑校验不崩", isinstance(r, list), str(r)[:200])
        print(f"    {ch} 提示数: {len(r)}" + (f" -> {r[0]['message'][:60]}..." if r else ""))

# —— 接口回归：content 返回 table_check，旧字段不变 ——
from fastapi.testclient import TestClient
from backend.main import app
from backend.services.auth import issue_token
client = TestClient(app)
H = {"Authorization": "Bearer " + issue_token(1, "admin", "admin")}
r = client.get("/api/skills/chapter/2/content", headers=H, params={"project_id": "1"})
d = r.json()
check("接口200", r.status_code == 200, str(r.status_code))
check("含table_check字段", isinstance(d.get("table_check"), list), str(list(d.keys())))
check("旧字段保留(source/sections/refs)", all(k in d for k in ("source", "sections", "refs")))
r = client.get("/api/skills/chapter/6/content", headers=H, params={"project_id": "1"})
check("第六章content仍200", r.status_code == 200, str(r.status_code))

print("\n结论:", "全部通过" if not fails else f"失败项: {fails}")
sys.exit(1 if fails else 0)
