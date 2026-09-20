# -*- coding: utf-8 -*-
"""
水生态环境知识图谱构建器

把 kb.json 转成结构化实体-关系网络（kb/graph.json），替换原先混入学科分类词的 kg_terms 噪音。

产出：
  nodes: [{id, name, type, cnt}]
  edges: [{s, t, rel, w}]
  stats: {节点数, 边数, 各类型节点数, 各关系边数}

设计（对齐 kg_schema.json）：
  · 只对「文件类」记录建 regulation 节点（研究文献/资讯动态不入图，避免学术噪声淹没图谱）
  · 实体抽取=词表精确匹配；关系=规则（发文机关/依据链/适用水体）+ 同记录共现
  · 剪枝：度数 <2 且只靠共现连接的节点不进入主图
用法：python3 kg_build.py [--kb kb/kb.json] [--out kb/graph.json]
"""
import argparse
import hashlib
import json
import os
import re
from collections import Counter, defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))
FULLDIR = os.path.join(BASE, "kb", "full")

FILE_LEVELS = {"法律", "行政法规", "部门规章", "规范性文件", "国家标准", "行业标准",
               "地方性法规", "省政府规章", "地方政府规章", "地方标准", "国家规划",
               "省级规划", "市级规划", "技术导则", "公报报告"}


def nid(tp, name):
    h = hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
    return "%s_%s" % (tp, h)


def load_json(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def ext_text(rec, n=4000):
    c = rec.get("content") or ""
    if not c and rec.get("cid"):
        p = os.path.join(FULLDIR, str(rec["cid"]) + ".txt")
        try:
            if os.path.exists(p):
                c = open(p, encoding="utf-8", errors="ignore").read(n)
        except Exception:
            c = ""
    return c[:n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", default=os.path.join(BASE, "kb", "kb.json"))
    ap.add_argument("--schema", default=os.path.join(BASE, "kg_schema.json"))
    ap.add_argument("--out", default=os.path.join(BASE, "kb", "graph.json"))
    a = ap.parse_args()

    kb = load_json(a.kb)
    schema = load_json(a.schema)
    E = schema["entities"]
    R = schema["relations"]
    ISSUES = schema["issues"]["values"]

    # 实体词表（长词优先，避免"总氮"先命中"氮"）
    vocab = {}
    for tp, cfg in E.items():
        for v in cfg.get("values", []):
            vocab.setdefault(v, []).append(tp)
    for v in ISSUES:
        vocab.setdefault(v, []).append("issue")
    keys = sorted(vocab.keys(), key=len, reverse=True)

    node_meta = {}          # id -> {name,type,cnt}
    edges = defaultdict(int)  # (s,t,rel) -> weight
    reg_titles = {}          # 规范标题 -> 节点 id（用于依据链）

    def add_node(tp, name):
        i = nid(tp, name)
        if i not in node_meta:
            node_meta[i] = {"id": i, "name": name, "type": tp, "cnt": 0}
        node_meta[i]["cnt"] += 1
        return i

    def add_edge(s, t, rel, w=1):
        if not s or not t or s == t:
            return
        k, v = (s, t, rel), (t, s, rel)
        use = k if k not in edges and v not in edges else (k if k in edges else v)
        edges[use] += w

    # ---------- 第一遍：为文件类记录建 regulation 节点 ----------
    regs = []
    for r in kb:
        lv = r.get("content_type") or ""
        if lv not in FILE_LEVELS:
            continue
        title = re.sub(r"\s+", " ", (r.get("title") or "")).strip()
        if len(title) < 4:
            continue
        i = add_node("regulation", title)
        reg_titles[title] = i
        regs.append((i, r, title))

    # ---------- 第二遍：抽实体与关系 ----------
    for i, r, title in regs:
        hay = " ".join([title, r.get("summary") or "", ext_text(r)])
        found = defaultdict(list)
        for k in keys:
            if k in hay:
                for tp in vocab[k]:
                    found[tp].append(k)

        # 1) 发文机关 -> issued_by
        src = (r.get("source") or "") + " " + (r.get("issuer") or "")
        for org in E["org"]["values"]:
            if org in src or org in title:
                o = add_node("org", org)
                add_edge(i, o, "issued_by", 3)
                break

        # 2) 适用水体 -> applies_to（权重 3，标题命中记 4）
        for wb in found.get("waterbody", []):
            w = add_node("waterbody", wb)
            add_edge(i, w, "applies_to", 4 if wb in title else 3)

        # 3) 管控对象 -> targets
        for tp in ("pollutant", "facility"):
            for v in found.get(tp, []):
                n = add_node(tp, v)
                add_edge(i, n, "targets", 3)

        # 4) 解决问题 -> addresses
        for v in found.get("issue", []):
            n = add_node("issue", v)
            add_edge(i, n, "addresses", 3)

        # 5) 指标 -> monitors（技术/设施→指标；文件类记为 targets 的弱化）
        for v in found.get("indicator", []):
            n = add_node("indicator", v)
            add_edge(i, n, "monitors", 2)

        # 6) 技术/行动 -> 与文件的关联（用于流域全景与热点）
        for tp in ("tech", "action"):
            for v in found.get(tp, []):
                n = add_node(tp, v)
                add_edge(i, n, "co_occurs", 2)

    # ---------- 第三遍：依据链 based_on / revises ----------
    text_index = {}
    for i, r, title in regs:
        text_index[i] = " ".join([title, r.get("summary") or "", ext_text(r, 2500)])

    title_list = list(reg_titles.keys())
    for i, r, title in regs:
        body = text_index[i]
        # 依据《X》
        for m in re.finditer(r"依据《([^》]{3,60})》", body):
            ref = m.group(1).strip()
            for t2 in title_list:
                if ref in t2 and reg_titles[t2] != i:
                    add_edge(i, reg_titles[t2], "based_on", 4)
                    break
        # 修订/替代
        if re.search(r"(废止|替代|修订)", title):
            core = re.sub(r"[（(].*?[)）]", "", title)
            core = re.sub(r"(修订|废止|替代|的通知|的决定)", "", core).strip()
            if len(core) >= 6:
                for t2 in title_list:
                    if reg_titles[t2] != i and core in t2:
                        add_edge(i, reg_titles[t2], "revises", 3)
                        break

    # ---------- 剪枝 ----------
    deg = Counter()
    for (s, t, rel) in edges:
        deg[s] += 1
        deg[t] += 1
    keep = set()
    for (s, t, rel) in edges:
        if rel != "co_occurs" or deg[s] >= 3 or deg[t] >= 3:
            keep.add((s, t, rel))
    # 只保留 regulation 节点 + 与之相连的实体
    nodes_out = {}
    for (s, t, rel) in keep:
        for n in (s, t):
            if n not in nodes_out and n in node_meta:
                nodes_out[n] = node_meta[n]
    edges_out = [{"s": s, "t": t, "rel": rel, "w": w} for (s, t, rel), w in edges.items()
                 if (s, t, rel) in keep and s in nodes_out and t in nodes_out]

    stats = {
        "nodes": len(nodes_out),
        "edges": len(edges_out),
        "by_type": dict(Counter(v["type"] for v in nodes_out.values())),
        "by_rel": dict(Counter(e["rel"] for e in edges_out)),
        "regulation_total": len(regs),
    }
    out = {"nodes": sorted(nodes_out.values(), key=lambda x: -x["cnt"]),
           "edges": edges_out, "stats": stats,
           "schema_version": schema.get("version")}
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print("[kg] 节点 %d 边 %d（入图文件 %d 条）" % (stats["nodes"], stats["edges"], len(regs)))
    print("[kg] 节点类型:", json.dumps(stats["by_type"], ensure_ascii=False))
    print("[kg] 关系类型:", json.dumps(stats["by_rel"], ensure_ascii=False))
    print("[kg] Top 枢纽节点:")
    for n in out["nodes"][:12]:
        print("    %-10s %-28s 度=%d" % (n["type"], n["name"][:28], n["cnt"]))
    print("[kg] -> %s" % a.out)


if __name__ == "__main__":
    main()
