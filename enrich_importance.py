# -*- coding: utf-8 -*-
"""给 kb.json 每条记录计算 importance，写回 kb.json，供静态站点按重要性排序。

排序意图：部门权重 + 质量 + 时效 + **水相关度(water_rel)**。
低相关度（同"水生态环境"关系不大，如泛环保但无关水的稿件）记录：有效质量逐档下调，
并对最终 importance 乘惩罚系数，避免低质噪声进入高优先级展示。
实现方式（幂等）：首次运行把原始质量存入 quality_src，之后每次基于 quality_src + water_rel 重算。
update.py 会在 merge 之后自动调用本脚本。
"""
import os, json, datetime, sys


# ——— 原子安全写 JSON（自动注入，勿手改）———
# 背景：直接用 open(path,"w") + json.dump 有两个致命问题：
#   ① open("w") 会**先清空文件**，若写入中抛异常（如遇到孤立 Unicode 代理码位 \ud835），
#      会留下**半截无效 JSON**；下一步读取失败若又兜底为 []，就会把整库写成空数组（两次线上事故的根因）。
#   ② 非原子写，并发/中断都可能损坏文件。
# 本函数：清洗代理码位与控制字符 → 写临时文件 → os.replace 原子替换。原文件要么不变，要么完整。
def _safe_dump(path, obj):
    import json as _j, os as _o, re as _r, tempfile as _t
    _SURR = _r.compile(r"[\ud800-\udfff]")

    def _clean(x):
        if isinstance(x, str):
            return "".join(c for c in _SURR.sub("", x) if c in "\n\t" or ord(c) >= 32)
        if isinstance(x, list):
            return [_clean(i) for i in x]
        if isinstance(x, tuple):
            return [_clean(i) for i in x]
        if isinstance(x, dict):
            return {k: _clean(v) for k, v in x.items()}
        return x

    obj = _clean(obj)
    d = _o.path.dirname(_o.path.abspath(path)) or "."
    fd, tmp = _t.mkstemp(dir=d, suffix=".tmp")
    try:
        with _o.fdopen(fd, "w", encoding="utf-8") as f:
            _j.dump(obj, f, ensure_ascii=False, indent=1)
        _o.replace(tmp, path)
    except Exception:
        try:
            _o.unlink(tmp)
        except Exception:
            pass
        raise
# ——— 注入结束 ———



BASE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(BASE, "kb", "kb.json")

sys.path.insert(0, BASE)
try:
    import stage as _stage
    norm_dept = _stage.norm_dept
    norm_region = _stage.norm_region
except Exception:
    def norm_dept(s):
        return (s or "其他").strip() or "其他"
    def norm_region(s):
        return (s or "全国(部委)").strip() or "全国(部委)"

# 部门权重（水生态条线优先，水利/住建/发改/农业农村为重点）
DEPT = {"生态环境": 5, "水利": 4, "住建": 3, "发改": 3, "农业农村": 2, "其他": 1}
QUAL = {"A": 3, "B": 2, "C": 1}

# 强水信号词（标题命中即判为高相关）
WATER_CORE = ["水生态", "水环境", "地表水", "饮用水", "水源地", "河湖", "流域", "水体", "水质",
              "排污口", "入河排污口", "黑臭", "断面", "蓝藻", "富营养化", "水华", "美丽河湖",
              "五水共治", "河湖长", "河长制", "生态补偿", "再生水", "污水处理", "水污染物",
              "水功能区", "生态流量", "水生物多样性", "千岛湖", "钱塘江", "苕溪", "运河", "供水",
              "节水", "水安全", "湿地", "缓冲带"]
# 支撑词（弱信号）：与水治理相关但非专指
WATER_SUPPORT = ["治水", "水动力", "水葫芦", "底泥", "清淤", "面源", "氮磷", "水模型", "水监测",
                 "水十条", "水修复", "亲水", "海绵", "排水", "取水量", "水华", "水生", "水草"]


def _count(text, terms):
    return sum(text.count(t) for t in terms)


def water_rel(rec):
    """水相关度 ∈ [0,1]。标题命中核心水词=强信号；否则按正文水词密度打分。"""
    title = rec.get("title") or ""
    summary = rec.get("summary") or ""
    content = rec.get("content") or ""
    if _count(title, WATER_CORE) >= 1:
        return 1.0
    body = title + " " + summary + " " + content
    core = _count(body, WATER_CORE)
    support = _count(body, WATER_SUPPORT)
    raw = core * 1.0 + support * 0.4
    if raw >= 4:
        return 1.0
    if raw <= 0:
        return 0.3
    return round(0.3 + 0.7 * (raw / 4.0), 2)


def _downgrade(q, steps):
    order = ["A", "B", "C"]
    try:
        i = order.index(q)
    except ValueError:
        i = 1
    return order[min(2, i + steps)]


def timeliness(date_str):
    try:
        d = datetime.date.fromisoformat(date_str)
    except Exception:
        return 0
    days = (datetime.date.today() - d).days
    if days <= 365:
        return 2
    if days <= 730:
        return 1
    return 0


def importance(rec):
    dept = norm_dept(rec.get("department"))
    rec["department"] = dept
    region = norm_region(rec.get("region"))
    rec["region"] = region

    src_q = rec.get("quality_src") or rec.get("quality") or "B"
    if src_q not in QUAL:
        src_q = "B"
    rec["quality_src"] = src_q

    rel = water_rel(rec)
    rec["water_rel"] = rel

    # 低相关度：有效质量下调
    if rel < 0.35:
        q = _downgrade(src_q, 2)
    elif rel < 0.6:
        q = _downgrade(src_q, 1)
    else:
        q = src_q
    rec["quality"] = q

    base = DEPT.get(dept, 1) + QUAL.get(q, 1) * 0.7 + timeliness(rec.get("date") or rec.get("added_at") or "")
    factor = 0.4 + 0.6 * rel
    return round(base * factor, 2)


def main():
    data = json.load(open(KB, encoding="utf-8"))
    lowrel = 0
    for r in data:
        r["importance"] = importance(r)
        if r.get("water_rel", 1) < 0.6:
            lowrel += 1
    _safe_dump(KB, data)
    print("enriched importance for", len(data), "records; low-water-relevance demoted:", lowrel)


if __name__ == "__main__":
    main()
