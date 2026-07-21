# -*- coding: utf-8 -*-
"""采集开源学术文献库（OpenAlex）中与水生态环境高度相关的论文，归一化后追加到 inbox.json。
OpenAlex 为完全开放的学术图谱 API（无需密钥），可按关键词/概念检索、带摘要与机构国家。
用法：python crawl_academic.py [额外关键词 ...]   环境变量 PAGES 控制每词翻页数(默认6)
"""
import os, sys, json, time, ssl, urllib.request, urllib.parse, re, datetime
BASE = os.path.dirname(os.path.abspath(__file__))
INBOX = os.path.join(BASE, "kb", "inbox.json")
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
QUERIES = ["water environment", "surface water quality", "river basin management", "eutrophication",
           "cyanobacteria bloom", "drinking water source", "wastewater treatment", "water ecological restoration",
           "non-point source pollution", "constructed wetland", "water reuse", "lake restoration"]
PAGES = int(os.environ.get("PAGES", "6"))
DCC = {"生态环境": "生态环境", "水利": "水利", "科技": "科技", "环境": "生态环境"}

def get_json(u):
    for _ in range(4):
        try:
            req = urllib.request.Request(u, headers=UA)
            with urllib.request.urlopen(req, timeout=25, context=CTX) as r:
                return json.load(r)
        except Exception as e:
            time.sleep(2)
    return None

def norm_url(u):
    u = (u or "").strip()
    u = re.split(r"[?#]", u)[0]
    return u.rstrip("/").lower()

def abstract_text(inv):
    if not inv: return ""
    words = []
    for w, pos in inv.items():
        for p in pos:
            words.append((p, w))
    words.sort()
    return " ".join(w for _, w in words)

def classify(title, abstract):
    t = (title + " " + abstract).lower()
    if re.search(r"standard|规范|guideline|指南|method", t): return "standard"
    if re.search(r"technology|technolog|技术|material|monitor|监测|equipment|装备|wetland|生态修复", t): return "tech"
    if re.search(r"management|governance|管理|policy|政策|补偿|institution", t): return "management"
    if re.search(r"basin|river|lake|流域|湖|河|watershed", t): return "basin_eng"
    return "literature"

def main():
    existing = set()
    kb = json.load(open(os.path.join(BASE, "kb", "kb.json"), encoding="utf-8")) if os.path.exists(os.path.join(BASE, "kb", "kb.json")) else []
    for r in kb:
        if r.get("url"): existing.add(norm_url(r["url"]))
        if r.get("title"): existing.add(r["title"].strip().lower())
    inbox = json.load(open(INBOX, encoding="utf-8")) if os.path.exists(INBOX) else []
    for r in inbox:
        if r.get("url"): existing.add(norm_url(r["url"]))
        if r.get("title"): existing.add(r["title"].strip().lower())

    extra = sys.argv[1:]
    queries = QUERIES + extra
    added = 0
    for q in queries:
        for pg in range(1, PAGES + 1):
            url = ("https://api.openalex.org/works?search=%s&per-page=50&page=%d"
                   "&filter=from_publication_date:2018-01-01,has_abstract:true"
                   "&sort=publication_date:desc") % (urllib.parse.quote(q), pg)
            d = get_json(url)
            if not d or not d.get("results"): break
            for w in d["results"]:
                title = w.get("title") or (w.get("display_name") or "")
                if not title or len(title) < 8: continue
                if title.strip().lower() in existing: continue
                doi = w.get("doi") or ""
                link = ("https://doi.org/" + doi) if doi else (w.get("id") or "")
                if link and norm_url(link) in existing: continue
                # 保留真实发表年（含未来预发表年份，如 2027）；统计时间由前端按 effDate 回退到入库日
                date = w.get("publication_date") or ""
                abs = abstract_text(w.get("abstract_inverted_index"))
                concepts = [c["display_name"] for c in (w.get("concepts") or [])[:6] if c.get("display_name")]
                # 机构国家 → 地域
                cn = False
                for au in (w.get("authorships") or []):
                    for inst in (au.get("institutions") or []):
                        if (inst.get("country_code") or "") == "CN":
                            cn = True; break
                    if cn: break
                region = "全国" if cn else "国际"
                dept = "科技"
                cat = classify(title, abs)
                summary = abs[:240] if abs else (title)
                rec = {
                    "title": title.strip(),
                    "url": link,
                    "source": ((w.get("primary_location") or {}).get("source") or {}).get("display_name") or "OpenAlex 学术文献",
                    "date": date,
                    "category": cat,
                    "department": dept,
                    "region": region,
                    "quality": "A",
                    "summary": summary,
                    "tags": concepts[:5],
                    "content": abs[:3000] if abs else "",
                    "content_fetched": bool(abs),
                    "added_at": datetime.date.today().isoformat(),
                }
                inbox.append(rec); existing.add(title.strip().lower())
                if link: existing.add(norm_url(link))
                added += 1
            time.sleep(0.5)
        print("q=%s done, cumulative added=%d" % (q, added))
    json.dump(inbox, open(INBOX, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("crawl_academic: added=%d, inbox_total=%d" % (added, len(inbox)))

if __name__ == "__main__":
    main()
