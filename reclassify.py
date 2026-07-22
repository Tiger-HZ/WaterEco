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
    json.dump(kb, open(KB, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("reclassify: 修正 %d 条 / 共 %d 条" % (n, len(kb)))
    print("新分类分布:", dict(sorted(cats.items(), key=lambda x: -x[1])))

if __name__ == "__main__":
    main()
