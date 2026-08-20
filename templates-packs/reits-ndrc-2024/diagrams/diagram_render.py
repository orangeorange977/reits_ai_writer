# -*- coding: utf-8 -*-
"""
运行时示意图渲染器：把 reading skill 产出的结构化关系图数据渲染成 PNG 字节，
供 skill_runner 在生成落盘前物化成"图块标记段落"，再由 web_render 插入 Word/预览。

不依赖 Graphviz 等外部程序，只用 matplotlib。中文字体优先用系统已装的 CJK 字体；
生产容器没装中文字体时回退到 PyMuPDF 自带的 CJK 字体（Droid Sans Fallback，
从 fitz.Font("china-s").buffer 取出落临时文件供 matplotlib 加载）。

输入 spec（dict，字段均可缺省）：
{
  "nodes":  [{"id": "n1", "text": "方框文字\\n可多行", "dashed": false}, ...]   # 可选
  "edges":  [{"from": "n1", "to": "n2", "label": "50%",
              "style": "dashed",        # 虚线（运营管理这类非持股关系）
              "relation": "peer"}, ...] # peer=横向平级关系（不参与层级计算）
  "groups": [{"label": "实际控制人", "members": ["n1", "n2"],
              "label_side": "top"}, ...] # left=标签竖排在框左侧
}
- 没有 nodes 时，节点从 edges 两端自动收集（id=文字本身）。
- edges 的 from/to 既可指向节点 id/文字，也可指向 group 的 label（连线从分组框边缘出发）。
- 节点上下层级由 hierarchy 边自动推算（无入边者在最上层）；同层左右顺序按边首次出现顺序。

用法：render_diagram_png(spec) -> bytes（PNG）。
"""
import os
import tempfile
from collections import defaultdict, deque
from io import BytesIO

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib.lines import Line2D
from matplotlib.font_manager import FontProperties

_FONT_CACHE = {}


def _get_font():
    """CJK 字体：系统字体优先，缺则用 PyMuPDF 自带字体落临时文件（容器无中文字体兜底）。"""
    if "fp" in _FONT_CACHE:
        return _FONT_CACHE["fp"]
    fp = None
    try:
        from matplotlib import font_manager as fm
        available = {f.name for f in fm.fontManager.ttflist}
        for name in ("Microsoft YaHei", "SimHei", "PingFang SC", "Heiti SC",
                     "Noto Sans CJK SC", "WenQuanYi Zen Hei", "SimSun"):
            if name in available:
                fp = FontProperties(family=name)
                break
    except Exception:
        fp = None
    if fp is None:
        try:
            import fitz
            path = os.path.join(tempfile.gettempdir(), "reits_diagram_cjk.ttf")
            if not os.path.exists(path):
                with open(path, "wb") as f:
                    f.write(fitz.Font("china-s").buffer)
            fp = FontProperties(fname=path)
        except Exception:
            fp = None
    _FONT_CACHE["fp"] = fp
    return fp


def _resolve_spec(spec):
    """归一化 spec -> (nodes{id:dict}, edges[list], groups[list])，members 归一到节点 id。"""
    spec = spec or {}
    nodes = {}

    def add_node(nid, text=None, dashed=False):
        if nid and nid not in nodes:
            nodes[nid] = {"id": nid, "text": text or nid, "dashed": bool(dashed)}

    for nd in spec.get("nodes") or []:
        if not isinstance(nd, dict):
            continue
        nid = str(nd.get("id") or nd.get("text") or "").strip()
        add_node(nid, str(nd.get("text") or nid).strip(), nd.get("dashed"))

    groups = []
    for g in spec.get("groups") or []:
        if not isinstance(g, dict):
            continue
        label = str(g.get("label") or "").strip()
        if not label:
            continue
        groups.append({"label": label, "members": [],
                       "side": "left" if str(g.get("label_side") or "") == "left" else "top"})

    group_labels = {g["label"] for g in groups}

    def node_key(name):
        """名字 -> 节点 id（id 或 text 匹配）；是分组标签则返回 None。"""
        if name in group_labels:
            return None
        if name in nodes:
            return name
        for nid, nd in nodes.items():
            if nd["text"] == name:
                return nid
        return name  # 尚未登记的新节点

    edges = []
    for e in spec.get("edges") or []:
        if not isinstance(e, dict):
            continue
        a = str(e.get("from") or "").strip()
        b = str(e.get("to") or "").strip()
        if not a or not b:
            continue
        for name in (a, b):
            if name not in group_labels:
                add_node(node_key(name))
        edges.append({"from": a, "to": b, "label": str(e.get("label") or "").strip(),
                      "style": str(e.get("style") or "").strip(),
                      "relation": "peer" if str(e.get("relation") or "") == "peer" else "hierarchy"})

    # groups 的 members 归一到节点 id（未登记的成员补登记，保证分组框里看得到）
    for g in spec.get("groups") or []:
        if not isinstance(g, dict):
            continue
        label = str(g.get("label") or "").strip()
        tgt = next((x for x in groups if x["label"] == label), None)
        if tgt is None:
            continue
        for m in g.get("members") or []:
            name = str(m).strip()
            if not name:
                continue
            key = node_key(name)
            if key is None:
                continue
            add_node(key, name)
            if key not in tgt["members"]:
                tgt["members"].append(key)
    return nodes, edges, [g for g in groups if g["members"]]


def _layout(nodes, edges, groups):
    """层级推算 + 同层排序，返回 (level{id:int}, order{level:[ids]})。
    无任何上下级边的孤立节点（平级关系端点、分组连线成员的端点）跟随锚点同层，
    并在同层里挪到锚点旁边（分组成员在锚点左侧、平级端点在右侧）。"""
    children = defaultdict(list)
    indeg = defaultdict(int)
    hier_deg = defaultdict(int)
    for e in edges:
        if e["relation"] != "hierarchy":
            continue
        a, b = e["from"], e["to"]
        if a not in nodes or b not in nodes:
            continue
        children[a].append(b)
        indeg[b] += 1
        hier_deg[a] += 1
        hier_deg[b] += 1
    q = deque([n for n in nodes if indeg[n] == 0])
    topo = []
    work = dict(indeg)
    while q:
        n = q.popleft()
        topo.append(n)
        for b in children[n]:
            work[b] -= 1
            if work[b] == 0:
                q.append(b)
    for n in nodes:  # 有环兜底
        if n not in topo:
            topo.append(n)
    parents = defaultdict(list)
    for e in edges:
        if e["relation"] == "hierarchy" and e["from"] in nodes and e["to"] in nodes:
            parents[e["to"]].append(e["from"])
    level = {}
    for n in topo:
        level[n] = (max((level[p] for p in parents[n]), default=-1) + 1)

    # 孤立节点跟随锚点同层：平级边另一端 / 分组连线指向的目标节点
    anchor_of = {}
    for e in edges:
        a, b = e["from"], e["to"]
        if e["relation"] == "peer" and a in nodes and b in nodes:
            for iso, other in ((a, b), (b, a)):
                if hier_deg[iso] == 0 and hier_deg[other] > 0:
                    anchor_of.setdefault(iso, (other, "after"))
        lbl = a if a not in nodes else (b if b not in nodes else None)
        tgt = b if lbl == a else (a if lbl == b else None)
        if lbl and tgt in nodes:
            g = next((x for x in groups if x["label"] == lbl), None)
            if g:
                for m in g["members"]:
                    if m in nodes and hier_deg[m] == 0:
                        anchor_of.setdefault(m, (tgt, "before"))
                        level[m] = level.get(tgt, 0)
    for iso, (other, _side) in anchor_of.items():
        if other in level:
            level[iso] = level[other]

    # 同层顺序：按上层顺序做 BFS 展开（子随父聚拢），剩余节点按首次出现顺序补
    by_level = defaultdict(list)
    placed = set()
    for lvl in sorted(set(level.values())):
        order = []
        if lvl == 0:
            order = [n for n in nodes if level[n] == 0]
        else:
            for p in by_level[lvl - 1]:
                for c in children[p]:
                    if level.get(c) == lvl and c not in placed:
                        order.append(c)
                        placed.add(c)
            for n in nodes:
                if level.get(n) == lvl and n not in placed:
                    order.append(n)
                    placed.add(n)
        by_level[lvl] = order
    # 孤立节点挪到锚点旁边
    for iso, (other, side) in anchor_of.items():
        order = by_level.get(level.get(iso))
        if not order or iso not in order or other not in order:
            continue
        order.remove(iso)
        order.insert(order.index(other) + (0 if side == "before" else 1), iso)
    return level, by_level


def _box_size(text, cw=0.175, pad=0.28, lh=0.26):
    lines = str(text).split("\n")
    w = max((len(ln) for ln in lines), default=1) * cw + pad * 2
    return max(w, 1.5), 0.34 + lh * len(lines)


def render_diagram_png(spec, dpi=180):
    """结构化关系图 -> PNG 字节。无节点时抛 ValueError。"""
    nodes, edges, groups = _resolve_spec(spec)
    if not nodes:
        raise ValueError("diagram 没有节点")
    fp = _get_font()
    level, by_level = _layout(nodes, edges, groups)

    size = {n: _box_size(nodes[n]["text"]) for n in nodes}
    h_gap, v_gap = 0.55, 1.05
    rows_w = {lvl: sum(size[n][0] for n in order) + h_gap * (len(order) - 1)
              for lvl, order in by_level.items()}
    fig_w = max(rows_w.values()) + 1.0
    cx0 = fig_w / 2

    pos = {}
    y = 0.6
    for lvl in sorted(by_level.keys()):
        order = by_level[lvl]
        x = cx0 - rows_w[lvl] / 2
        row_h = max(size[n][1] for n in order)
        for n in order:
            w, h = size[n]
            pos[n] = [x + w / 2, y + row_h / 2, w, h]
            x += w + h_gap
        y += row_h + v_gap
    fig_h = y + 0.2

    group_boxes = {}
    for g in groups:
        xs = [pos[m] for m in g["members"] if m in pos]
        if not xs:
            continue
        pad = 0.22
        left = min(p[0] - p[2] / 2 for p in xs) - pad
        right = max(p[0] + p[2] / 2 for p in xs) + pad
        top = min(p[1] - p[3] / 2 for p in xs) - pad
        bottom = max(p[1] + p[3] / 2 for p in xs) + pad
        if g["side"] == "left":
            left -= 0.55  # 竖排标签占位
        group_boxes[g["label"]] = (left, top, right, bottom)

    # 画布范围：含分组框外扩
    min_x, max_x = 0.0, fig_w
    min_y, max_y = 0.0, fig_h
    for left, top, right, bottom in group_boxes.values():
        min_x, max_x = min(min_x, left - 0.2), max(max_x, right + 0.2)
        min_y, max_y = min(min_y, top - 0.35), max(max_y, bottom + 0.2)

    fig, ax = plt.subplots(figsize=(max_x - min_x, max_y - min_y))
    ax.set_xlim(min_x, max_x)
    ax.set_ylim(min_y, max_y)
    ax.invert_yaxis()
    ax.axis("off")

    def txt(x, s, **kw):
        kw.setdefault("fontsize", 10.5)
        kw.setdefault("ha", "center")
        kw.setdefault("va", "center")
        if fp is not None:
            kw["fontproperties"] = fp
        return ax.text(x if not isinstance(x, tuple) else x[0],
                       x[1] if isinstance(x, tuple) else x, s, **kw)

    # ---- 分组虚线框 + 标签 ----
    for g in groups:
        box = group_boxes.get(g["label"])
        if not box:
            continue
        left, top, right, bottom = box
        ax.add_patch(Rectangle((left, top), right - left, bottom - top,
                               linewidth=1.1, edgecolor="black",
                               facecolor="none", linestyle="--"))
        if g["side"] == "left":
            txt((left + 0.28, (top + bottom) / 2), g["label"], rotation=90,
                fontsize=9.5, bbox=dict(facecolor="white", edgecolor="none", pad=1))
        else:
            txt(((left + right) / 2, top), g["label"], fontsize=10,
                bbox=dict(facecolor="white", edgecolor="none", pad=1))

    # ---- 节点方框 ----
    for n, (x, yv, w, h) in pos.items():
        ls = "--" if nodes[n]["dashed"] else "-"
        ax.add_patch(Rectangle((x - w / 2, yv - h / 2), w, h, linewidth=1.2,
                               edgecolor="black", facecolor="white", linestyle=ls))
        txt((x, yv), nodes[n]["text"], fontsize=10.5)

    def group_anchor(label, tx, ty):
        left, top, right, bottom = group_boxes[label]
        gx, gy = (left + right) / 2, (top + bottom) / 2
        dx, dy = tx - gx, ty - gy
        if abs(dy) >= abs(dx):
            return (gx, top if dy < 0 else bottom)
        return (left if dx < 0 else right, gy)

    def draw_edge(e):
        a, b = e["from"], e["to"]
        pa, pb = pos.get(a), pos.get(b)
        if pa is None and a not in group_boxes:
            return
        if pb is None and b not in group_boxes:
            return
        if pa is not None:
            sx, sy = pa[0], pa[1] + pa[3] / 2  # 底边中点
        else:
            tb = pb or group_boxes[b]
            tcx, tcy = (tb[0], tb[1]) if pb else ((tb[0] + tb[2]) / 2, (tb[1] + tb[3]) / 2)
            sx, sy = group_anchor(a, tcx, tcy)
        if pb is not None:
            ex, ey = pb[0], pb[1] - pb[3] / 2  # 顶边中点
        else:
            ex, ey = group_anchor(b, sx, sy)
        def _v_clear(x, y0, y1):
            """垂直段 x∈(y0,y1) 不被其它方框挡住。"""
            for n, (nx, ny, nw, nh) in pos.items():
                if n in (a, b):
                    continue
                if (ny - nh / 2 < max(y0, y1) + 0.05 and ny + nh / 2 > min(y0, y1) - 0.05
                        and abs(nx - x) < nw / 2 + 0.05):
                    return False
            return True

        ls = "--" if e["style"] in ("dashed", "虚线") else "-"
        color = "black"
        if e["relation"] == "peer":
            # 横向平级：同层时左右边缘之间水平箭头；不同层时正交 L 形
            # （水平走到目标中心列，再垂直到目标底/顶边，如"廊坊平台→20%→京津冀"）
            if pa is not None and pb is not None and abs(pa[1] - pb[1]) < 1e-6:
                if pb[0] >= pa[0]:
                    sx, sy = pa[0] + pa[2] / 2, pa[1]
                    ex, ey = pb[0] - pb[2] / 2, pb[1]
                else:
                    sx, sy = pa[0] - pa[2] / 2, pa[1]
                    ex, ey = pb[0] + pb[2] / 2, pb[1]
                _poly_arrow(ax, [(sx, sy), (ex, ey)], ls, color, arrow=True)
                if e["label"]:
                    txt(((sx + ex) / 2, sy - 0.14), e["label"],
                        fontsize=9.5, ha="center",
                        bbox=dict(facecolor="white", edgecolor="none", pad=0.5))
                return
            if pa is not None and pb is not None:
                sx = pa[0] + (pa[2] / 2 if pb[0] >= pa[0] else -pa[2] / 2)
                sy = pa[1]
                up = pb[1] < pa[1]
                ey = pb[1] + (pb[3] / 2 if up else -pb[3] / 2)
                # 水平段若被同层中间方框挡住，改走层间空白带：
                # 源框侧缝垂直出 -> 空白带水平到目标列 -> 垂直接进入目标底/顶边
                lo, hi = min(sx, pb[0]), max(sx, pb[0])
                h_blocked = any(
                    n not in (a, b) and abs(ny - sy) < nh / 2 + 0.05
                    and nx + nw / 2 > lo + 0.05 and nx - nw / 2 < hi - 0.05
                    for n, (nx, ny, nw, nh) in pos.items())
                if h_blocked:
                    gx = pa[0] + (pa[2] / 2 + 0.275 if pb[0] >= pa[0] else -pa[2] / 2 - 0.275)
                    gap_y = ((pb[1] + pb[3] / 2) + (pa[1] - pa[3] / 2)) / 2 if up \
                        else ((pa[1] + pa[3] / 2) + (pb[1] - pb[3] / 2)) / 2
                    entry_x = pb[0] + (-pb[2] / 4 if pb[0] >= pa[0] else pb[2] / 4)
                    pts = [(sx, sy), (gx, sy), (gx, gap_y), (entry_x, gap_y), (entry_x, ey)]
                    _poly_arrow(ax, pts, ls, color, arrow=True)
                    if e["label"]:
                        txt(((gx + entry_x) / 2, gap_y - 0.12), e["label"],
                            fontsize=9.5, ha="center",
                            bbox=dict(facecolor="white", edgecolor="none", pad=0.5))
                    return
                _poly_arrow(ax, [(sx, sy), (pb[0], sy), (pb[0], ey)], ls, color, arrow=True)
                if e["label"]:
                    txt(((sx + pb[0]) / 2, sy - 0.14), e["label"],
                        fontsize=9.5, ha="center",
                        bbox=dict(facecolor="white", edgecolor="none", pad=0.5))
                return
            return
        # 上下级/分组连线：正交折线（垂直-水平-垂直），箭头在末端
        pts = [(sx, sy)]
        label_pos = None
        if abs(sx - ex) < 1e-6:
            # 垂直直线：若中途被其它方框挡住，从源框右缘出发右侧绕行、水平进入目标框右缘
            blocked_right = None
            for n, (nx, ny, nw, nh) in pos.items():
                if n in (a, b):
                    continue
                if min(sy, ey) - 0.05 < ny < max(sy, ey) + 0.05 and abs(nx - sx) < nw / 2 + 0.05:
                    blocked_right = max(blocked_right or 0, nx + nw / 2)
            if blocked_right is not None and pa is not None and pb is not None:
                dx_ = blocked_right + 0.45
                pts = [(pa[0] + pa[2] / 2, pa[1]), (dx_, pa[1]),
                       (dx_, pb[1]), (pb[0] + pb[2] / 2, pb[1])]
                label_pos = (dx_ + 0.07, (pa[1] + pb[1]) / 2)
            else:
                pts.append((ex, ey))
        else:
            # 水平段走在目标层邻接空白带里，保证不穿任何方框
            gap_y = ey - 0.5 if ey > sy else ey + 0.5
            vx = sx
            if not _v_clear(sx, sy, gap_y):
                if _v_clear(ex, sy + 0.22, gap_y):
                    vx = ex
                else:
                    # 扫描区间内挡路方框的水平间隙，取最宽间隙中心列
                    ivs = sorted((nx - nw / 2, nx + nw / 2)
                                 for n, (nx, ny, nw, nh) in pos.items()
                                 if n not in (a, b)
                                 and ny - nh / 2 < max(sy, gap_y)
                                 and ny + nh / 2 > min(sy, gap_y))
                    prev, best = min_x - 0.3, None
                    for l, r in ivs + [(max_x + 0.3, max_x + 0.3)]:
                        if l - prev > 0.25 and (best is None or l - prev > best[1]):
                            best = ((prev + l) / 2, l - prev)
                        prev = max(prev, r)
                    if best:
                        vx = best[0]
            pts = [(sx, sy)]
            if abs(vx - sx) > 1e-6:
                stub_y = sy + (0.22 if gap_y > sy else -0.22)
                pts += [(sx, stub_y), (vx, stub_y)]
            pts += [(vx, gap_y), (ex, gap_y), (ex, ey)]
        _poly_arrow(ax, pts, ls, color, arrow=True)
        if e["label"]:
            if label_pos is None:
                label_pos = (sx + 0.07, sy + 0.28 if len(pts) > 2 else (sy + ey) / 2)
            txt(label_pos, e["label"], fontsize=9.5, ha="left",
                bbox=dict(facecolor="white", edgecolor="none", pad=0.5))

    for e in edges:
        try:
            draw_edge(e)
        except Exception:
            continue

    buf = BytesIO()
    fig.savefig(buf, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def _poly_arrow(ax, pts, ls, color, arrow=True):
    """折线 + 末端箭头（最后一段用箭头线，其余普通线段）。"""
    for i in range(len(pts) - 2):
        ax.add_line(Line2D((pts[i][0], pts[i + 1][0]), (pts[i][1], pts[i + 1][1]),
                           linewidth=1.1, color=color, linestyle=ls))
    (x1, y1), (x2, y2) = pts[-2], pts[-1]
    dx, dy = x2 - x1, y2 - y1
    norm = (dx * dx + dy * dy) ** 0.5 or 1.0
    hl = min(0.16, norm * 0.4)  # 箭头长度
    ax.add_line(Line2D((x1, x2 - dx / norm * hl * 0.8), (y1, y2 - dy / norm * hl * 0.8),
                       linewidth=1.1, color=color, linestyle=ls))
    # 箭头两翼
    ux, uy = dx / norm, dy / norm
    wx, wy = -uy, ux
    for s in (1, -1):
        ax.add_line(Line2D((x2, x2 - ux * hl + s * wx * hl * 0.45),
                           (y2, y2 - uy * hl + s * wy * hl * 0.45),
                           linewidth=1.1, color=color, linestyle=ls))
