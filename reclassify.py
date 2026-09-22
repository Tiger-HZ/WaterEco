# -*- coding: utf-8 -*-
"""一次性重分类：用改进的分类规则（crawl_common.classify_cat / classify_academic）
对 kb.json 中全部存量条目重新判定 category，修正“意见/批复/通知被误分为标准”等问题。
- 政府政策库 / 微信来源 → classify_cat（政策感知：意见/批复/通知/规划/方案→政策；仅明确标准/规范→标准）
- 学术来源（OpenAlex/Crossref/EuropePMC/期刊） → classify_academic（收紧：默认 literature，仅明确标准→标准）
用法：python reclassify.py
"""
import os, sys, json, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import crawl_common as C


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

ACADEMIC_MARK = ['openalex', 'crossref', 'europe pmc', '学术文献', 'journal', 'sciencedirect',
                 'springer', 'wiley', 'mdpi', 'arxiv', 'cairn', 'goldsmiths', 'birbeck',
                 'univ', 'university', 'elsevier', 'frontiers', 'tandfonline', 'nature',
                 'acs', 'rsc', 'ieee', '.edu']

def is_academic(r):
    s = (r.get("source") or "").lower()
    return any(k in s for k in ACADEMIC_MARK)

def main():
    kb = json.load(open(KB, encoding="utf-8"))
    n = 0
    cats = {}
    for r in kb:
        title = r.get("title") or ""
        summary = r.get("summary") or ""
        cat = C.classify_academic(title, summary) if is_academic(r) else C.classify_cat(title, summary, False)
        cats[cat] = cats.get(cat, 0) + 1
        if cat != r.get("category"):
            r["category"] = cat
            n += 1
    _safe_dump(KB, kb)
    print("reclassify: 修正 %d 条 / 共 %d 条" % (n, len(kb)))
    print("新分类分布:", dict(sorted(cats.items(), key=lambda x: -x[1])))

if __name__ == "__main__":
    main()
