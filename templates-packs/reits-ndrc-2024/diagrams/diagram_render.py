# -*- coding: utf-8 -*-
"""
运行时示意图渲染器：把 reading skill 产出的结构化关系图数据渲染成 PNG 字节，
供 skill_runner 在生成落盘前物化成"图块标记段落"，再由 web_render 插入 Word/预览。

不依赖 Graphviz 等外部程序，只用 matplotlib。中文字体优先用系统已装的 CJK 字体；
生产容器没装中文字体时回退到 PyMuPDF 自带的 CJK 字体（Droid Sans Fallback，
从 fitz.Font("china-s").buffer 取出落临时文件供 matplotlib 加载）。

输入 spec（dict，字段均可缺省）：
{
  "nodes":  [{"id": "n1", "text": "方框文字\\n可多行", "dashed": false,
              "shape": "ellipse"}, ...]  # shape=ellipse 画椭圆框（如"专项计划"）
  "edges":  [{"from": "n1", "to": "n2", "label": "50%",
              "style": "dashed",        # 虚线（资金流/运营管理这类关系）
              "relation": "peer",       # peer=横向平级关系（不参与层级计算）
              "side": "left"}, ...]     # side=left/right：peer 边里孤立端点固定在
                                        # 锚点左/右侧（如税务机关挂左侧、募投项目挂右侧）
                                        # flow=反向回流边（只画线不参与层级计算，
                                        # 与正向边构成平行双箭头，如"证券/收益分配"）
  "groups": [{"label": "实际控制人", "members": ["n1", "n2"],
              "label_side": "top",      # left=标签竖排在框左侧
              "place": "above"}, ...]   # place=above=成员置于连线目标的上一层
                                        # （如"基金投资人"分组在基金上一层）；默认同层
  "legend": [{"style": "solid", "text": "业务关系"},
             {"style": "dashed", "text": "资金流"}]  # 左上角图例（solid/dashed 箭头+文字）
}
- 没有 nodes 时，节点从 edges 两端自动收集（id=文字本身）。
- edges 的 from/to 既可指向节点 id/文字，也可指向 group 的 label（连线从分组框边缘出发）。
- 节点上下层级由 hierarchy 边自动推算（无入边者在最上层）；同层左右顺序按边首次出现顺序。
- 同一对主体之间的反向两条边（正向边默认、反向边用 relation=flow，如"认购"向上、
  "分配"向下）自动画成平行双箭头，不重叠。

用法：render_diagram_png(spec) -> bytes（PNG）；
      render_diagram_drawio_xml(spec) -> str（draw.io 可编辑源码，同一套布局）。
"""
import os
import tempfile
from collections import defaultdict, deque
from io import BytesIO

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, Ellipse
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

    def add_node(nid, text=None, dashed=False, shape=None):
        if nid and nid not in nodes:
            nodes[nid] = {"id": nid, "text": text or nid, "dashed": bool(dashed),
                          "shape": "ellipse" if str(shape or "").strip() == "ellipse" else ""}

    for nd in spec.get("nodes") or []:
        if not isinstance(nd, dict):
            continue
        nid = str(nd.get("id") or nd.get("text") or "").strip()
        add_node(nid, str(nd.get("text") or nid).strip(), nd.get("dashed"), nd.get("shape"))

    groups = []
    for g in spec.get("groups") or []:
        if not isinstance(g, dict):
            continue
        label = str(g.get("label") or "").strip()
        if not label:
            continue
        groups.append({"label": label, "members": [],
                       "side": "left" if str(g.get("label_side") or "") == "left" else "top",
                       "place": "above" if str(g.get("place") or "").strip() == "above" else "same"})

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
                      "relation": str(e.get("relation") or "").strip()
                      if str(e.get("relation") or "").strip() in ("peer", "flow")
                      else "hierarchy",
                      "side": str(e.get("side") or "").strip()
                      if str(e.get("side") or "").strip() in ("left", "right") else ""})

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
    """层级推算 + 同层排序，返回 (level{id:int}, order{level:[ids]}, forced{iso:(anchor,side)}).
    无任何上下级边的孤立节点（平级关系端点、分组连线成员的端点）跟随锚点同层。
    平级孤立端点默认在行内挪到锚点旁边（分组成员同样在行内）；
    仅当锚点两侧外挂槽都被 side 显式占用、行内无处可放时才改为外挂堆叠。
    forced 节点不参与行居中，由渲染阶段外挂到锚点左/右外侧（同侧依次向外堆叠），
    保证主链垂直居中不被挤偏。"""
    children = defaultdict(list)
    indeg = defaultdict(int)
    hier_deg = defaultdict(int)
    # place=above 分组：其 hierarchy 连线视同每个成员直连目标（成员自然落在目标上一层）
    g_by_label = {g["label"]: g for g in groups}
    for e in edges:
        if e["relation"] != "hierarchy":
            continue
        g = g_by_label.get(e["from"])
        if g and g["place"] == "above" and e["to"] in nodes:
            for m in g["members"]:
                if m in nodes:
                    children[m].append(e["to"])
                    indeg[e["to"]] += 1
                    hier_deg[m] += 1
                    hier_deg[e["to"]] += 1
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
    for p, cs in children.items():
        for c in cs:
            parents[c].append(p)
    level = {}
    for n in topo:
        level[n] = (max((level[p] for p in parents[n]), default=-1) + 1)

    # 孤立节点跟随锚点同层：平级边另一端 / 分组连线指向的目标节点
    anchor_of = {}
    forced = {}
    taken = defaultdict(set)
    anchor_side_cnt = defaultdict(int)
    for e in edges:
        a, b = e["from"], e["to"]
        if e["relation"] == "peer" and a in nodes and b in nodes:
            for iso, other in ((a, b), (b, a)):
                if hier_deg[iso] == 0 and hier_deg[other] > 0 and iso not in anchor_of:
                    # side 指定优先（如税务机关挂左、募投项目挂右），外挂不参与行居中；
                    # 未指定时交替选边在行内挂锚点左/右，但避让该锚点已被外挂占用
                    # 的侧（外挂槽与行内邻格同几何，同侧必重叠）；两侧都被占用、
                    # 行内无处可放时改为外挂，同侧依次向外堆叠
                    if e["side"]:
                        side = "after" if e["side"] == "right" else "before"
                        forced[iso] = (other, side)
                        taken[other].add(side)
                    else:
                        pref = "after" if anchor_side_cnt[other] % 2 == 0 else "before"
                        alt = "before" if pref == "after" else "after"
                        if pref not in taken[other]:
                            side = pref
                        elif alt not in taken[other]:
                            side = alt
                        else:
                            side = pref
                            forced[iso] = (other, side)
                    anchor_side_cnt[other] += 1
                    anchor_of[iso] = (other, side)
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
    return level, by_level, forced


def _box_size(text, cw=0.175, pad=0.28, lh=0.26):
    lines = str(text).split("\n")
    w = max((len(ln) for ln in lines), default=1) * cw + pad * 2
    return max(w, 1.5), 0.34 + lh * len(lines)


def _geometry(spec):
    """布局几何计算（PNG 渲染与 draw.io 编辑源码生成共用）：
    返回 nodes/edges/groups/pos/group_boxes/画布边界等，画布单位、y 向下。"""
    nodes, edges, groups = _resolve_spec(spec)
    if not nodes:
        raise ValueError("diagram 没有节点")
    level, by_level, forced = _layout(nodes, edges, groups)

    size = {}
    for n in nodes:
        w, h = _box_size(nodes[n]["text"])
        if nodes[n]["shape"] == "ellipse":
            w, h = w * 1.35, h * 1.45  # 椭圆内接文字需要更大外框
        size[n] = (w, h)
    h_gap, v_gap = 0.55, 1.25
    # 行宽只算主链节点（side 外挂节点不参与居中，避免挤偏垂直主链）
    rows_w = {lvl: sum(size[n][0] for n in order if n not in forced)
              + h_gap * (max(0, len([n for n in order if n not in forced]) - 1))
              for lvl, order in by_level.items()}
    fig_w = max(rows_w.values()) + 1.0
    cx0 = fig_w / 2

    pos = {}
    y = 0.6
    for lvl in sorted(by_level.keys()):
        order = by_level[lvl]
        core = [n for n in order if n not in forced] or order
        row_h = max(size[n][1] for n in order)
        x = cx0 - rows_w[lvl] / 2
        for n in core:
            w, h = size[n]
            pos[n] = [x + w / 2, y + row_h / 2, w, h]
            x += w + h_gap
        # side 外挂节点：挂在锚点该侧最外行内节点之外（该侧无行内节点则贴锚点
        # 缘），同侧多个依次向外排——避免外挂槽与行内邻格同位重叠
        outer = {}
        for n in order:
            if n not in forced:
                continue
            other, side = forced[n]
            pa = pos.get(other)
            if pa is None:
                continue
            w, h = size[n]
            key = (other, side)
            idx = order.index(other)
            if side == "before":
                base = pa[0] - pa[2] / 2
                for m in order[idx - 1::-1]:
                    if m in forced or m not in pos:
                        continue
                    pm = pos[m]
                    base = pm[0] - pm[2] / 2
                edge = outer.get(key, base)
                xc = edge - h_gap - w / 2
                outer[key] = xc - w / 2
            else:
                base = pa[0] + pa[2] / 2
                for m in order[idx + 1:]:
                    if m in forced or m not in pos:
                        continue
                    pm = pos[m]
                    base = pm[0] + pm[2] / 2
                edge = outer.get(key, base)
                xc = edge + h_gap + w / 2
                outer[key] = xc + w / 2
            pos[n] = [xc, y + row_h / 2, w, h]
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

    # 画布范围：含分组框外扩 + side 外挂节点外扩
    min_x, max_x = 0.0, fig_w
    for xc, yv, w, h in pos.values():
        min_x, max_x = min(min_x, xc - w / 2 - 0.3), max(max_x, xc + w / 2 + 0.3)
    min_y, max_y = 0.0, fig_h
    for left, top, right, bottom in group_boxes.values():
        min_x, max_x = min(min_x, left - 0.2), max(max_x, right + 0.2)
        min_y, max_y = min(min_y, top - 0.35), max(max_y, bottom + 0.2)

    # 左上角图例预留顶部空白带
    legend = [l for l in (spec.get("legend") or [])
              if isinstance(l, dict) and str(l.get("text") or "").strip()]
    if legend:
        min_y -= 0.5 * len(legend) + 0.4

    return {"nodes": nodes, "edges": edges, "groups": groups, "pos": pos,
            "group_boxes": group_boxes, "forced": forced,
            "min_x": min_x, "max_x": max_x, "min_y": min_y, "max_y": max_y,
            "legend": legend}


def render_diagram_png(spec, dpi=180):
    """结构化关系图 -> PNG 字节。无节点时抛 ValueError。"""
    g = _geometry(spec)
    nodes, edges, groups = g["nodes"], g["edges"], g["groups"]
    pos, group_boxes = g["pos"], g["group_boxes"]
    min_x, max_x, min_y, max_y = g["min_x"], g["max_x"], g["min_y"], g["max_y"]
    legend = g["legend"]
    fp = _get_font()

    fig, ax = plt.subplots(figsize=(max_x - min_x, max_y - min_y))
    ax.set_xlim(min_x, max_x)
    ax.set_ylim(min_y, max_y)
    ax.invert_yaxis()
    ax.axis("off")
    _drawn_labels = []   # 已画标签包围盒（用于候选位置避让）

    def _lab_wh(s, fs_pt):
        """标签包围盒宽高估计：画布 1 数据单位=1 英寸，按字号精确换算（CJK 字宽≈字号）。"""
        fd = fs_pt / 72.0
        lines = str(s).split("\n")
        w = max((len(l) for l in lines), default=1) * fd + 0.12
        h = len(lines) * fd * 1.3 + 0.06
        return w, h

    def txt(x, s, **kw):
        kw.setdefault("fontsize", 10.5)
        kw.setdefault("ha", "center")
        kw.setdefault("va", "center")
        if fp is not None:
            kw["fontproperties"] = fp
        # 登记标签包围盒，供后续标签候选位置避让（所有标签都算障碍）
        w, h = _lab_wh(s, kw.get("fontsize", 10.5))
        px = x[0] if isinstance(x, tuple) else x
        py = x[1] if isinstance(x, tuple) else x
        ha = kw.get("ha", "center")
        if ha == "center":
            x0, x1 = px - w / 2, px + w / 2
        elif ha == "left":
            x0, x1 = px, px + w
        else:
            x0, x1 = px - w, px
        _drawn_labels.append((x0, x1, py - h / 2, py + h / 2))
        return ax.text(x if not isinstance(x, tuple) else x[0],
                       x[1] if isinstance(x, tuple) else x, s, **kw)

    def lab_avoid(cands, s, ha="center"):
        """按候选顺序选第一个不与已画标签相交的位置画标签；
        候选全相交时在该候选上做垂直微调逃逸，仍无解用末位候选。"""
        w, h = _lab_wh(s, 9.5)

        def box_at(cx, cy):
            if ha == "center":
                x0, x1 = cx - w / 2, cx + w / 2
            elif ha == "left":
                x0, x1 = cx, cx + w
            else:
                x0, x1 = cx - w, cx
            return (x0, x1, cy - h / 2, cy + h / 2)

        def free(b):
            return not any(b[0] < o[1] and b[1] > o[0] and b[2] < o[3] and b[3] > o[2]
                           for o in _drawn_labels)

        pick = None
        for cx, cy in cands:
            for dy in (0, 0.12, -0.12, 0.25, -0.25, 0.45, -0.45, 0.7, -0.7):
                if free(box_at(cx, cy + dy)):
                    pick = (cx, cy + dy)
                    break
            if pick:
                break
        if pick is None:
            pick = cands[-1]
        txt(pick, s, fontsize=9.5, ha=ha,
            bbox=dict(facecolor="white", edgecolor="none", pad=0.5))

    # ---- 左上角图例 ----
    if legend:
        ly = min_y + 0.35
        for l in legend:
            ls = "--" if str(l.get("style") or "") in ("dashed", "虚线") else "-"
            _poly_arrow(ax, [(min_x + 0.25, ly), (min_x + 1.05, ly)], ls, "black", arrow=True)
            txt((min_x + 1.2, ly), str(l["text"]).strip(), fontsize=9.5, ha="left")
            ly += 0.5

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
        if nodes[n]["shape"] == "ellipse":
            ax.add_patch(Ellipse((x, yv), w, h, linewidth=1.2,
                                 edgecolor="black", facecolor="white", linestyle=ls))
        else:
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

    def draw_edge(e, off=0.0):
        a, b = e["from"], e["to"]
        pa, pb = pos.get(a), pos.get(b)
        if pa is None and a not in group_boxes:
            return
        if pb is None and b not in group_boxes:
            return

        def _center(p, name):
            if p is not None:
                return p[0], p[1]
            g = group_boxes[name]
            return ((g[0] + g[2]) / 2, (g[1] + g[3]) / 2)

        scx, scy = _center(pa, a)
        tcx, tcy = _center(pb, b)
        down = tcy >= scy  # 目标在源下方：从源底边出、进目标顶边；反之走顶边/底边
        if pa is not None:
            sx, sy = pa[0], pa[1] + (pa[3] / 2 if down else -pa[3] / 2)
        else:
            sx, sy = group_anchor(a, tcx, tcy)
        if pb is not None:
            ex, ey = pb[0], pb[1] - (pb[3] / 2 if down else -pb[3] / 2)
        else:
            ex, ey = group_anchor(b, sx, sy)
        sx += off  # 反向平行边错开，避免重叠
        ex += off
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
                # 水平直箭头若被同层中间方框挡住、或标签比两框间隙还长，改走本行上方空白带绕行
                lo, hi = min(sx, ex), max(sx, ex)
                label_w = len(e["label"]) * 0.17 + 0.3 if e["label"] else 0.0
                if label_w > (hi - lo) or any(n not in (a, b) and abs(ny - sy) < nh / 2 + 0.05
                       and nx + nw / 2 > lo + 0.05 and nx - nw / 2 < hi - 0.05
                       for n, (nx, ny, nw, nh) in pos.items()):
                    gap_y = pa[1] - pa[3] / 2 - 0.62
                    pts = [(pa[0], pa[1] - pa[3] / 2), (pa[0], gap_y),
                           (pb[0], gap_y), (pb[0], pb[1] - pb[3] / 2)]
                    _poly_arrow(ax, pts, ls, color, arrow=True)
                    if e["label"]:
                        # 标签候选位置避让：线上居中→偏源端→偏目标端→线下居中→线下偏目标端
                        mid = (pa[0] + pb[0]) / 2
                        src_b = pa[0] + (pb[0] - pa[0]) * 0.2
                        tgt_b = pb[0] + (pa[0] - pb[0]) * 0.2
                        lab_avoid([(mid, gap_y - 0.12), (src_b, gap_y - 0.12),
                                   (tgt_b, gap_y - 0.12), (mid, gap_y + 0.16),
                                   (tgt_b, gap_y + 0.16)], e["label"])
                    return
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
                        lab_avoid([((gx + entry_x) / 2, gap_y - 0.12),
                                   ((gx + entry_x) / 2, gap_y + 0.16),
                                   (gx, gap_y - 0.12)], e["label"])
                    return
                _poly_arrow(ax, [(sx, sy), (pb[0], sy), (pb[0], ey)], ls, color, arrow=True)
                if e["label"]:
                    txt((pb[0] + 0.07, (sy + ey) / 2), e["label"],
                        fontsize=9.5, ha="left",
                        bbox=dict(facecolor="white", edgecolor="none", pad=0.5))
                return
            return
        # 上下级/分组连线：正交折线（垂直-水平-垂直），箭头在末端
        # 分组与目标同层（如"股权激励分组→上市公司 0.04%"）：水平间距太窄放不下标签，
        # 改为从分组右缘垂直上到目标框上方空白带，水平到目标列再垂直进入顶边
        if pa is None and pb is not None and abs(sy - pb[1]) < 0.3:
            gap_y = (pb[1] - pb[3] / 2) - 0.5
            pts = [(sx, sy), (sx, gap_y), (ex, gap_y), (ex, ey)]
            _poly_arrow(ax, pts, ls, color, arrow=True)
            if e["label"]:
                txt((sx - 0.05, gap_y), e["label"], fontsize=9.5, ha="right",
                    bbox=dict(facecolor="white", edgecolor="none", pad=0.5))
            return
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
                # 平行错开的边：标签向外侧伸展，避免互相叠压；向上边标签放源框上方
                if len(pts) > 2:
                    lyv = sy + (0.28 if down else -0.28)
                else:
                    lyv = (sy + ey) / 2
                if off < 0:
                    label_pos = (sx - 0.07, lyv)
                else:
                    label_pos = (sx + 0.07, lyv)
            txt(label_pos, e["label"], fontsize=9.5,
                ha="right" if off < 0 else "left",
                bbox=dict(facecolor="white", edgecolor="none", pad=0.5))

    # 同一对主体间的多条非平级边（认购/分配这类反向对）按序错开成平行线
    pair_idx = defaultdict(list)
    for i, e in enumerate(edges):
        if e["relation"] != "peer":
            pair_idx[tuple(sorted((e["from"], e["to"])))].append(i)
    edge_off = {}
    for idxs in pair_idx.values():
        if len(idxs) > 1:
            for j, i in enumerate(idxs):
                edge_off[i] = (j - (len(idxs) - 1) / 2) * 0.35
    for i, e in enumerate(edges):
        try:
            draw_edge(e, edge_off.get(i, 0.0))
        except Exception:
            continue

    buf = BytesIO()
    fig.savefig(buf, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def render_diagram_drawio_xml(spec):
    """结构化关系图 -> draw.io XML（可编辑源码）。
    与 render_diagram_png 用同一套布局几何，点击图块在 draw.io 里打开后
    节点位置/文字与 PNG 一致，可自由拖动、改字、加连线。
    平行双向边（如认购/分配）用出/入点百分比错开，与 PNG 的偏移同逻辑。"""
    g = _geometry(spec)
    nodes, edges, groups = g["nodes"], g["edges"], g["groups"]
    pos, group_boxes = g["pos"], g["group_boxes"]
    S = 180.0  # 画布单位 -> draw.io px（与 PNG dpi=180 一致，框体字号视觉接近）

    def px(v):
        return round((v - g["min_x"]) * S, 1)

    def py(v):
        return round((v - g["min_y"]) * S, 1)

    def esc(s):
        return (str(s).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;")
                .replace("\n", "&#10;"))

    cells = []
    cid_of = {}
    for i, (n, nd) in enumerate(nodes.items()):
        if n not in pos:
            continue
        cid = f"n{i + 2}"
        cid_of[n] = cid
        x, yv, w, h = pos[n]
        style = ("ellipse;" if nd["shape"] == "ellipse" else "rounded=0;") \
            + ("whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#000000;"
               "fontFamily=SimSun;fontSize=14;")
        if nd["dashed"]:
            style += "dashed=1;"
        cells.append(
            f'<mxCell id="{cid}" value="{esc(nd["text"])}" style="{style}" '
            f'vertex="1" parent="1"><mxGeometry x="{px(x - w / 2)}" y="{py(yv - h / 2)}" '
            f'width="{round(w * S, 1)}" height="{round(h * S, 1)}" as="geometry"/></mxCell>')

    for i, grp in enumerate(groups):
        box = group_boxes.get(grp["label"])
        if not box:
            continue
        cid = f"g{i + 2}"
        cid_of[grp["label"]] = cid
        left, top, right, bottom = box
        style = ("rounded=0;whiteSpace=wrap;html=1;fillColor=none;dashed=1;"
                 "strokeColor=#000000;fontFamily=SimSun;fontSize=13;")
        style += ("horizontal=0;verticalAlign=middle;" if grp["side"] == "left"
                  else "verticalAlign=top;")
        cells.append(
            f'<mxCell id="{cid}" value="{esc(grp["label"])}" style="{style}" '
            f'vertex="1" parent="1"><mxGeometry x="{px(left)}" y="{py(top)}" '
            f'width="{round((right - left) * S, 1)}" height="{round((bottom - top) * S, 1)}" '
            f'as="geometry"/></mxCell>')

    # 同一对主体间的多条非平级边（认购/分配这类反向对）按序错开成平行线（同 PNG）
    pair_idx = defaultdict(list)
    for i, e in enumerate(edges):
        if e["relation"] != "peer":
            pair_idx[tuple(sorted((e["from"], e["to"])))].append(i)
    edge_off = {}
    for idxs in pair_idx.values():
        if len(idxs) > 1:
            for j, i in enumerate(idxs):
                edge_off[i] = (j - (len(idxs) - 1) / 2) * 0.35

    def center(name):
        p = pos.get(name)
        if p is not None:
            return p[0], p[1]
        box = group_boxes.get(name)
        if box:
            return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        return None

    for i, e in enumerate(edges):
        a, b = e["from"], e["to"]
        sa, sb = cid_of.get(a), cid_of.get(b)
        ca, cb = center(a), center(b)
        if sa is None or sb is None or ca is None or cb is None:
            continue
        pa, pb = pos.get(a), pos.get(b)
        style = ("edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;strokeColor=#000000;"
                 "fontFamily=SimSun;fontSize=13;labelBackgroundColor=#FFFFFF;"
                 "endArrow=block;endFill=0;")
        if e["style"] in ("dashed", "虚线"):
            style += "dashed=1;"
        down = cb[1] >= ca[1]
        dx, dy = cb[0] - ca[0], cb[1] - ca[1]
        if e["relation"] == "peer" and pa is not None and pb is not None \
                and abs(pa[1] - pb[1]) < 1e-6:
            # 同层横向：两端从左右边缘进出（绕行交给 draw.io 正交路由）
            if pb[0] >= pa[0]:
                style += "exitX=1;exitY=0.5;entryX=0;entryY=0.5;"
            else:
                style += "exitX=0;exitY=0.5;entryX=1;entryY=0.5;"
        elif e["relation"] == "peer":
            # 跨层平级：源框横向出，目标框顶/底边进
            style += (f"exitX={1 if dx >= 0 else 0};exitY=0.5;"
                      f"entryX=0.5;entryY={0 if down else 1};")
        elif pa is None or pb is None:
            # 分组框端点：按中心连线主导方向决定出入边
            if abs(dy) >= abs(dx):
                style += (f"exitX=0.5;exitY={1 if down else 0};"
                          f"entryX=0.5;entryY={0 if down else 1};")
            else:
                style += (f"exitX={1 if dx >= 0 else 0};exitY=0.5;"
                          f"entryX={0 if dx >= 0 else 1};entryY=0.5;")
        else:
            # 上下级：底/顶边进出；平行边用出入点百分比错开（与 PNG 偏移同逻辑）
            off = edge_off.get(i, 0.0)
            ex = min(0.92, max(0.08, 0.5 + off / pa[2]))
            nx = min(0.92, max(0.08, 0.5 + off / pb[2]))
            style += (f"exitX={ex:.2f};exitY={1 if down else 0};"
                      f"entryX={nx:.2f};entryY={0 if down else 1};")
        cells.append(
            f'<mxCell id="e{i + 2}" value="{esc(e["label"])}" style="{style}" '
            f'edge="1" parent="1" source="{sa}" target="{sb}">'
            f'<mxGeometry relative="1" as="geometry"/></mxCell>')

    # 左上角图例：隐形端点 + 样式箭头 + 文字（与 PNG 图例同位置，保存后不丢）
    for li, l in enumerate(g["legend"]):
        ly = g["min_y"] + 0.35 + li * 0.5
        inv = "fillColor=none;strokeColor=none;opacity=0;"
        cells.append(f'<mxCell id="lv{li}" value="" style="{inv}" vertex="1" parent="1">'
                     f'<mxGeometry x="{px(g["min_x"] + 0.25)}" y="{py(ly)}" '
                     f'width="1" height="1" as="geometry"/></mxCell>')
        cells.append(f'<mxCell id="lw{li}" value="" style="{inv}" vertex="1" parent="1">'
                     f'<mxGeometry x="{px(g["min_x"] + 1.05)}" y="{py(ly)}" '
                     f'width="1" height="1" as="geometry"/></mxCell>')
        ls = "dashed=1;" if str(l.get("style") or "") in ("dashed", "虚线") else ""
        cells.append(f'<mxCell id="le{li}" value="" style="html=1;strokeColor=#000000;'
                     f'{ls}endArrow=block;endFill=0;" edge="1" parent="1" '
                     f'source="lv{li}" target="lw{li}">'
                     f'<mxGeometry relative="1" as="geometry"/></mxCell>')
        cells.append(f'<mxCell id="lt{li}" value="{esc(l["text"])}" '
                     f'style="text;html=1;align=left;verticalAlign=middle;resize=0;'
                     f'fontFamily=SimSun;fontSize=13;" vertex="1" parent="1">'
                     f'<mxGeometry x="{px(g["min_x"] + 1.2)}" y="{py(ly - 0.2)}" '
                     f'width="{round(3.5 * S)}" height="{round(0.4 * S)}" as="geometry"/></mxCell>')

    W = round((g["max_x"] - g["min_x"]) * S, 1)
    H = round((g["max_y"] - g["min_y"]) * S, 1)
    return ('<mxfile host="reit-ai"><diagram id="reits-diagram" name="示意图">'
            f'<mxGraphModel dx="{int(W)}" dy="{int(H)}" grid="0" gridSize="10" guides="1" '
            f'tooltips="1" connect="1" arrows="1" fold="1" page="0" pageScale="1" '
            f'pageWidth="{int(W) + 80}" pageHeight="{int(H) + 80}" math="0" shadow="0">'
            '<root><mxCell id="0"/><mxCell id="1" parent="0"/>'
            + "".join(cells) + '</root></mxGraphModel></diagram></mxfile>')


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
