# -*- coding: utf-8 -*-
"""采集 Europe PMC 开放学术文献（无需密钥，EBI 托管，约 4000 万条引文）。
按水生态环境关键词 + PUB_YEAR 检索；回溯游标（kb/cursor.json）按“年”推进：
每次运行抓取游标所指年份的批次，随后年份 -1，逐年向 2000 回溯，单调增长、零重复，
可经 cron 持续累积至几十万级。
用法：python crawl_europepmc.py   环境变量 PAGES 控制每词翻页数(默认2)
"""
import os, sys, json, re, datetime, urllib.parse
import crawl_common as C

PAGES = int(os.environ.get("PAGES", "2"))
ROWS = 200
BASE_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
MIN_YEAR = 2000
QUERIES = [
    "water quality", "surface water quality", "groundwater quality", "drinking water",
    "nutrient pollution", "eutrophication", "cyanobacteria bloom", "harmful algal bloom",
    "lake restoration", "river restoration", "watershed management", "river basin management",
    "wastewater treatment", "wastewater reuse", "water reuse", "reclaimed water",
    "sponge city", "non-point source pollution", "agricultural runoff", "diffuse pollution",
    "constructed wetland", "aquatic vegetation", "sediment remediation", "phosphorus loading",
    "drinking water safety", "source water protection", "disinfection byproduct",
    "aquatic ecosystem", "fish assemblage", "macroinvertebrate", "aquatic biodiversity",
    "ecological health assessment", "water quality monitoring", "remote sensing water",
    "water quality model", "water governance", "water pollution prevention",
    "water framework directive", "lake Taihu", "lake Chao",
]


def is_china(item, title, abstract):
    if C.looks_china(title, abstract):
        return True
    al = (item.get("authorList") or {}).get("author") or []
    for a in al:
        aff = a.get("affiliation") or ""
        if re.search(r"china|中国|中华", aff, re.I):
            return True
    return False


def main():
    cur = C.load_cursor()
    state = cur.get("europepmc", {})
    year = int(state.get("year") or datetime.date.today().year)
    print("europepmc cursor year=%d" % year)

    kb = json.load(open(os.path.join(C.BASE, "kb", "kb.json"), encoding="utf-8")) if os.path.exists(os.path.join(C.BASE, "kb", "kb.json")) else []
    inbox0 = json.load(open(C.INBOX, encoding="utf-8")) if os.path.exists(C.INBOX) else []
    existing = set()
    for r in list(kb) + list(inbox0):
        if r.get("url"):
            existing.add(r["url"].strip().lower())
        if r.get("title"):
            existing.add(r["title"].strip().lower())

    recs = []
    added = 0
    for q in QUERIES:
        qrecs = []
        cursor = "*"
        for _ in range(PAGES):
            fq = '(%s) AND PUB_YEAR:%d' % (q, year)
            url = ("%s?query=%s&format=json&resultType=core&pageSize=%d"
                   "&sort=P_PDATE_D desc&cursorMark=%s") % (
                BASE_URL, urllib.parse.quote(fq), ROWS, urllib.parse.quote(cursor))
            data = C.safe_get(url, min_gap=0.3)
            if not data:
                break
            results = (data.get("resultList") or {}).get("result") or []
            if not results:
                break
            for it in results:
                title = it.get("title") or ""
                if not title:
                    continue
                abstract = (it.get("abstractText") or "").strip()
                d = (it.get("firstPublicationDate") or "")[:10]
                doi = it.get("doi") or ""
                key_title = title.strip().lower()
                key_url = ("https://doi.org/" + doi) if doi else ""
                if key_title in existing:
                    continue
                if key_url and key_url in existing:
                    continue
                existing.add(key_title)
                if key_url:
                    existing.add(key_url)
                region = "全国" if is_china(it, title, abstract) else "国际"
                cat = C.classify_academic(title, abstract)
                quality = "A" if abstract else "B"
                cited = int(it.get("citedByCount") or 0)
                recs.append({
                    "title": title.strip(),
                    "url": key_url or ("https://europepmc.org/article/%s/%s" % (it.get("source", "MED"), it.get("id", ""))),
                    "source": (it.get("journalInfo") or {}).get("journal", {}).get("title") or "Europe PMC 学术文献",
                    "date": d or ("%d-01-01" % year),
                    "category": cat,
                    "department": "科技",
                    "region": region,
                    "quality": quality,
                    "summary": (abstract[:240] if abstract else title),
                    "tags": [],
                    "content": abstract[:800] if abstract else "",
                    "content_fetched": bool(abstract),
                    "importance": min(5, 1 + cited // 50),
                    "added_at": C.today(),
                })
                added += 1
                qrecs.append(recs[-1])
            nxt = data.get("nextCursorMark")
            if not nxt or nxt == cursor:
                break
            cursor = nxt
        C.append_inbox(qrecs)
        print("  q=%s done, cumulative=%d" % (q, added))

    # 年份游标推进：向 2000 回溯；到底则回到今年（拾取新发表）
    nxt_year = year - 1
    if nxt_year < MIN_YEAR:
        nxt_year = datetime.date.today().year
    cur["europepmc"] = {"year": nxt_year}
    C.save_cursor(cur)
    print("europepmc 年份游标推进 -> %d" % nxt_year)
    total = len(json.load(open(C.INBOX, encoding="utf-8")) if os.path.exists(C.INBOX) else [])
    print("crawl_europepmc: added=%d, inbox_total=%d" % (added, total))


if __name__ == "__main__":
    main()
