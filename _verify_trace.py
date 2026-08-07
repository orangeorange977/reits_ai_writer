"""临时验证脚本：溯源 HTML 双向转换 + 上传保留目录结构（跑完即删）"""
import sys
sys.path.insert(0, '.')

# 1) 块级 src 渲染 + HTML 反解析回 src
from backend.services import skill_runner as sr

sections = [{
    "id": "1-1", "title": "（一）测试小节",
    "blocks": [
        {"type": "p", "text": "本公司成立于2020年。", "src": "申报材料：4-承诺函/承诺函1.pdf"},
        {"type": "kv", "caption": "表#  基本信息",
         "rows": [{"label": "注册资本", "value": "10000.00万元"}],
         "src": "摘要表：注册资本"},
        {"type": "grid", "caption": "表#  股东情况",
         "headers": ["股东", "比例"], "rows": [["甲公司", "51%"]],
         "src": "天眼查：get_shareholder_info（甲公司）"},
        {"type": "p", "text": "以上信息真实有效。"},
    ],
}]
htmls = sr._sections_to_html(sections)
html = htmls[0]["html"]
assert html.count('class="doc-src"') == 3, f"应有3个溯源行, 实际 {html.count('doc-src')}"
assert "📎 依据：申报材料：4-承诺函/承诺函1.pdf" in html
print("[1] 渲染 src 行 OK")

parser = sr._HTMLToBlocks()
parser.feed(html)
blocks = parser.result()
assert len(blocks) == 4, f"块数应为4, 实际 {len(blocks)}"
assert blocks[0]["src"] == "申报材料：4-承诺函/承诺函1.pdf", blocks[0]
assert blocks[1]["src"] == "摘要表：注册资本", blocks[1]
assert blocks[2]["src"] == "天眼查：get_shareholder_info（甲公司）", blocks[2]
assert blocks[1]["rows"] == [{"label": "注册资本", "value": "10000.00万元"}], blocks[1]
assert blocks[2]["rows"] == [["甲公司", "51%"]], blocks[2]
assert "依据" not in blocks[3].get("src", ""), blocks[3]
print("[2] HTML 反解析回 src OK（含表格结构无损）")

# 2) 用户上传修改后的溯源行也能回读
edited = '<p>改写后的正文</p><div class="doc-src">📎 依据：人工核对：营业执照扫描件</div>'
p2 = sr._HTMLToBlocks()
p2.feed(edited)
assert p2.result()[0]["src"] == "人工核对：营业执照扫描件", p2.result()
print("[3] 编辑后的溯源行回读 OK")

# 3) 上传路径净化
from backend.routers.projects import _rel_parts
assert _rel_parts("sub dir/report.pdf") == ["sub dir", "report.pdf"]
assert _rel_parts("../evil/x.pdf") == ["evil", "x.pdf"]
assert _rel_parts("a/./b/../c.pdf") == ["a", "b", "c.pdf"]  # '..' 直接丢弃不回退，无法穿越
assert _rel_parts("普通文件名.docx") == ["普通文件名.docx"]
print("[4] 上传路径净化 OK")

print("ALL PASS")
