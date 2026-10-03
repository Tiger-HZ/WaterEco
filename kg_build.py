# -*- coding: utf-8 -*-
"""水生态环境知识图谱构建器 v2（2026-10-03 专业化改造）

用户反馈：「关系图谱还是有点太乱，实体最好是专业或领域内的实体，提升专业性」。

v1 的问题：
  · 把 692 个「法规标题」也做成节点（占全图 85%），图上堆满文件名 → 看不清专业关系
  · 边过多（2357 条）且多为「法规→实体」的挂载式连接，语义单一

v2 的做法（专业实体网络）：
  · 法规不再作为节点，而是作为「共现容器」—— 同一份法规里同时出现的专业实体之间连边，
    表示「该法规同时规范这两者」（如 总氮 ↔ 污水处理厂、太湖 ↔ 富营养化）
  · 节点只保留专业实体：水体 / 污染物 / 技术 / 设施 / 行动 / 指标 / 问题 / 机构
  · 剪枝：节点须出现在 ≥2 份文件中；边须共现 ≥2 次，去掉偶然同现
  · 另保留少量「旗舰法规」节点（被 ≥8 份文件提及者，如生态环境法典）作为骨架锚点

产出格式不变（nodes / edges / stats），门户无需改动。
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

# 入图的专业实体类型（法规不再作为节点）
ENTITY_TYPES = ["waterbody", "pollutant", "tech", "facility", "action", "indicator", "issue", "org"]
# 机构实体过滤（2026-10-03）：泛发文机关（国务院/各部委）在图上出现几百次但信息量低，
# 会让图谱变成「发文关系图」而非「专业实体网络」。只保留业务性机构（流域管理机构、
# 监测总站、科研院所等），体现「谁主管该专业领域」。
ORG_EXCLUDE = {"国务院", "国务院办公厅", "全国人民代表大会常务委员会", "全国人大",
               "中共中央", "中共中央办公厅", "生态环境部", "水利部", "住房和城乡建设部",
               "国家发展改革委", "农业农村部", "自然资源部", "国家卫生健康委员会",
               "国家市场监督管理总局", "财政部", "工业和信息化部", "交通运输部",
               "应急管理部", "国家能源局", "教育部", "科技部", "公安部", "司法部",
               "中国人民银行", "国家统计局", "中国气象局", "国家林业和草原局"}
MIN_NODE_FILES = 2      # 节点须出现在 >=2 份文件
MIN_EDGE_W = 2          # 边须共现 >=2 次
FLAGSHIP_DEG = 8        # 旗舰法规：被提及 >=8 次


def nid(tp, name):
    return "%s_%s" % (tp, hashlib.sha1(name.encode("utf-8")).hexdigest()[:10])


def load_json(p, default=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def ext_text(rec, n=4000):
    c = rec.get("content") or ""
    if not c and rec.get("cid"):
        fp = os.path.join(FULLDIR, str(rec["cid"]) + ".txt")
        try:
            if os.path.exists(fp):
                c = open(fp, encoding="utf-8", errors="ignore").read(n)
        except Exception:
            c = ""
    return c[:n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", default=os.path.join(BASE, "kb", "kb.json"))
    ap.add_argument("--schema", default=os.path.join(BASE, "kg_schema.json"))
    ap.add_argument("--out", default=os.path.join(BASE, "kb", "graph.json"))
    a = ap.parse_args()

    kb = load_json(a.kb, [])
    schema = load_json(a.schema, {})
    E = schema.get("entities", {})
    ISSUES = schema.get("issues", {}).get("values", [])

    # 实体词表（长词优先匹配）
    vocab = {}
    for tp in ENTITY_TYPES:
        for v in (E.get(tp) or {}).get("values", []):
            vocab.setdefault(v, []).append(tp)
    for v in ISSUES:
        vocab.setdefault(v, []).append("issue")
    keys = sorted(vocab.keys(), key=len, reverse=True)

    node_files = defaultdict(set)
    node_meta = {}
    edge_w = defaultdict(int)
    regs = []
    for r in kb:
        if (r.get("content_type") or "") not in FILE_LEVELS:
            continue
        title = re.sub(r"\s+", " ", (r.get("title") or "")).strip()
        if len(title) < 4:
            continue
        regs.append((r, title))

    print("[kg] 入图文件 %d 条，开始抽专业实体…" % len(regs))

    for idx, (r, title) in enumerate(regs):
        hay = " ".join([title, r.get("summary") or "", ext_text(r)])
        found = defaultdict(list)
        for k in keys:
            if k in hay:
                for tp in vocab[k]:
                    found[tp].append(k)
        ents = []
        for tp in ENTITY_TYPES:
            for v in found.get(tp, []):
                if tp == "org" and v in ORG_EXCLUDE:
                    continue
                i = nid(tp, v)
                node_files[i].add(idx)
                if i not in node_meta:
                    node_meta[i] = {"id": i, "name": v, "type": tp, "cnt": 0}
                node_meta[i]["cnt"] += 1
                ents.append((tp, v, i))
        ents = list({e[2]: e for e in ents}.values())
        for x in range(len(ents)):
            for y in range(x + 1, len(ents)):
                i1, i2 = ents[x][2], ents[y][2]
                if i1 == i2:
                    continue
                key = (i1, i2) if i1 < i2 else (i2, i1)
                edge_w[key] += 1

    # 旗舰法规：被 >=FLAGSHIP_DEG 份其他文件标题提及
    flagship = Counter()
    for r, title in regs:
        core = re.sub(r"[（(].*?[)）]", "", title)
        core = re.sub(r"^(中华人民共和国|浙江省|杭州市)", "", core).strip()
        if len(core) < 4:
            continue
        cnt = 0
        for r2, t2 in regs:
            if t2 != title and core in t2:
                cnt += 1
        if cnt >= FLAGSHIP_DEG:
            flagship[title] = cnt

    nodes_out = {i: m for i, m in node_meta.items() if len(node_files[i]) >= MIN_NODE_FILES}
    edges_out = [{"s": i1, "t": i2, "rel": "co_occurs", "w": w}
                 for (i1, i2), w in edge_w.items()
                 if w >= MIN_EDGE_W and i1 in nodes_out and i2 in nodes_out]
    for title, n in flagship.most_common(12):
        i = nid("regulation", title)
        nodes_out[i] = {"id": i, "name": title, "type": "regulation", "cnt": n}

    stats = {
        "nodes": len(nodes_out), "edges": len(edges_out),
        "by_type": dict(Counter(v["type"] for v in nodes_out.values())),
        "by_rel": dict(Counter(e["rel"] for e in edges_out)),
        "regulation_total": len(regs),
        "note": "v2 专业化：法规不再作为节点，改为同文件内专业实体共现网络；旗舰法规保留为骨架锚点",
    }
    out = {"nodes": sorted(nodes_out.values(), key=lambda x: -x["cnt"]),
           "edges": edges_out, "stats": stats, "schema_version": "2026.10"}
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print("[kg] 节点 %d 边 %d" % (stats["nodes"], stats["edges"]))
    print("[kg] 节点类型:", json.dumps(stats["by_type"], ensure_ascii=False))
    print("[kg] Top 枢纽（专业实体）:")
    for n in out["nodes"][:20]:
        print("    %-10s %-26s 出现 %d 次" % (n["type"], n["name"][:26], n["cnt"]))
    print("[kg] -> %s" % a.out)


if __name__ == "__main__":
    main()
