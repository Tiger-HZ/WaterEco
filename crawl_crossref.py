# -*- coding: utf-8 -*-
"""采集 Crossref 开放学术文献（无需密钥，礼貌池 mailto 提升配额）。
按水生态环境关键词检索，回溯游标（kb/cursor.json）保证每次运行抓取“更旧”的批次，
单调增长、零重复，可经 cron 持续累积至几十万级。
用法：python crawl_crossref.py   环境变量 PAGES 控制每词翻页数(默认2)
"""
import os, sys, json, re, datetime, urllib.parse
import crawl_common as C

MAIL = os.environ.get("OA_MAIL", "water-eco-bot@users.noreply.github.com")
PAGES = int(os.environ.get("PAGES", "2"))
ROWS = 200
QUERIES = [
    "water quality", "surface water quality", "groundwater quality", "drinking water source",
    "water quality standards", "nutrient pollution", "eutrophication", "cyanobacteria bloom",
    "harmful algal bloom", "lake trophic status", "microcystin",
    "river basin management", "watershed management", "river restoration", "lake restoration",
    "stream ecological restoration", "riparian buffer", "environmental flow", "ecological flow",
    "wastewater treatment", "municipal wastewater", "wastewater reuse", "water reuse",
    "reclaimed water", "combined sewer overflow", "sponge city",
    "non-point source pollution", "agricultural runoff", "diffuse pollution", "best management practices",
    "water ecological restoration", "constructed wetland", "aquatic vegetation restoration",
    "benthic macroinvertebrate", "sediment remediation", "internal phosphorus loading",
    "drinking water safety", "source water protection", "disinfection byproduct",
    "aquatic ecosystem", "fish assemblage", "macroinvertebrate community", "aquatic biodiversity",
    "water ecological integrity", "ecological health assessment",
    "water quality monitoring", "remote sensing water", "water quality model",
    "water governance", "water pollution prevention", "pollutant discharge permit",
    "total maximum daily load", "water ecological compensation", "transboundary water",
    "water framework directive", "clean water act", "lake Taihu eutrophication", "lake Chao eutrophication",
]


def strip_xml(s):
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def date_parts(item):
    for key in ("published", "published-print", "issued"):
        dp = (item.get(key) or {}).get("date-parts")
        if dp and dp[0] and dp[0][0]:
            y, m, d = (dp[0] + [None, None])[:3]
            try:
                return "%04d-%02d-%02d" % (int(y), int(m or 1), int(d or 1))
            except Exception:
                return "%04d-01-01" % int(y)
    return ""


def is_china(item, title, abstract):
    if C.looks_china(title, abstract):
        return True
    for a in (item.get("author") or []):
        for aff in (a.get("affiliation") or []):
            nm = (aff.get("name") or "") if isinstance(aff, dict) else str(aff)
            if re.search(r"china|中国|中华", nm, re.I):
                return True
    return False


def main():
    cur = C.load_cursor()
    state = cur.get("crossref", {})
    until = state.get("until") or C.today()
    print("crossref cursor until=%s" % until)

    kb = json.load(open(os.path.join(C.BASE, "kb", "kb.json"), encoding="utf-8")) if os.path.exists(os.path.join(C.BASE, "kb", "kb.json")) else []
    inbox0 = json.load(open(C.INBOX, encoding="utf-8")) if os.path.exists(C.INBOX) else []
    existing = set()
    for r in list(kb) + list(inbox0):
        if r.get("url"):
            existing.add(r["url"].strip().lower())
        if r.get("title"):
            existing.add(r["title"].strip().lower())

    recs = []
    min_date = until
    added = 0
    for q in QUERIES:
        qrecs = []
        for pg in range(PAGES):
            off = pg * ROWS
            url = ("https://api.crossref.org/works?query=%s&rows=%d&offset=%d"
                   "&filter=from-pub-date:2000-01-01,until-pub-date:%s"
                   "&sort=published&order=desc&mailto=%s") % (
                urllib.parse.quote(q), ROWS, off, until, urllib.parse.quote(MAIL))
            data = C.safe_get(url, mail=MAIL)
            if not data:
                break
            items = (data.get("message") or {}).get("items") or []
            if not items:
                break
            for it in items:
                doi = it.get("DOI")
                if not doi:
                    continue
                title = (it.get("title") or [""])[0]
                if not title:
                    continue
                abstract = strip_xml(it.get("abstract"))
                d = date_parts(it)
                if not d:
                    continue
                if d > until:
                    continue
                if d < min_date:
                    min_date = d
                key = (doi.strip().lower(), title.strip().lower())
                if key[0] in existing or key[1] in existing:
                    continue
                existing.add(key[0]); existing.add(key[1])
                region = "全国" if is_china(it, title, abstract) else "国际"
                cat = C.classify_academic(title, abstract)
                quality = "A" if abstract else "B"
                cited = int(it.get("is-referenced-by-count") or 0)
                recs.append({
                    "title": title.strip(),
                    "url": it.get("URL") or ("https://doi.org/" + doi),
                    "source": (it.get("container-title") or ["Crossref 学术文献"])[0] or "Crossref 学术文献",
                    "date": d,
                    "category": cat,
                    "department": "科技",
                    "region": region,
                    "quality": quality,
                    "summary": (abstract[:240] if abstract else title) ,
                    "tags": [],
                    "content": abstract[:800] if abstract else "",
                    "content_fetched": bool(abstract),
                    "importance": min(5, 1 + cited // 50),
                    "added_at": C.today(),
                })
                added += 1
                qrecs.append(recs[-1])
        C.append_inbox(qrecs)
        print("  q=%s done, cumulative=%d" % (q, added))

    if min_date < until:
        cur["crossref"] = {"until": min_date}
        C.save_cursor(cur)
        print("crossref 游标推进 -> %s" % min_date)
    total = len(json.load(open(C.INBOX, encoding="utf-8")) if os.path.exists(C.INBOX) else [])
    print("crawl_crossref: added=%d, inbox_total=%d" % (added, total))


if __name__ == "__main__":
    main()
