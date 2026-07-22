# -*- coding: utf-8 -*-
"""采集开源学术文献库（OpenAlex）中与水生态环境高度相关的论文，归一化后追加到 inbox.json。
OpenAlex 为完全开放的学术图谱 API（无需密钥），可按关键词/概念检索、带摘要与机构国家。
用法：python crawl_academic.py [额外关键词 ...]   环境变量 PAGES 控制每词翻页数(默认6)
"""
import os, sys, json, time, ssl, urllib.request, urllib.parse, re, datetime
import crawl_common as C
import media  # 学术 OA 全文 PDF 入库

FETCH_PDF = os.environ.get("FETCH_PDF", "1") == "1"
BASE = os.path.dirname(os.path.abspath(__file__))
INBOX = os.path.join(BASE, "kb", "inbox.json")
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
QUERIES = [
    # 基础水环境 / 水质
    "water environment", "surface water quality", "groundwater quality", "drinking water source",
    "water quality standards", "nutrient pollution", "total nitrogen total phosphorus",
    # 富营养化 / 蓝藻
    "eutrophication", "cyanobacteria bloom", "harmful algal bloom", "lake trophic status",
    "cyanotoxin microcystin", "algae control",
    # 流域 / 河川管理
    "river basin management", "watershed management", "river restoration", "lake restoration",
    "stream ecological restoration", "riparian buffer", "river connectivity", "environmental flow",
    "ecological flow", "hydrological alteration",
    # 城市水 / 污水 / 再生水
    "wastewater treatment", "municipal wastewater", "wastewater reuse", "water reuse",
    "reclaimed water", "sewage treatment plant", "combined sewer overflow", "sponge city",
    # 非点源 / 农业面源
    "non-point source pollution", "agricultural runoff", "diffuse pollution", "best management practices",
    # 修复 / 湿地
    "water ecological restoration", "constructed wetland", "riverine wetland", "aquatic vegetation restoration",
    "benthic macroinvertebrate", "bioindicator water",
    # 黑臭 / 内源
    "black odorous water", "urban black water", "sediment remediation", "internal phosphorus loading",
    # 水源 / 饮用水安全
    "drinking water safety", "source water protection", "drinking water treatment", "disinfection byproduct",
    # 水生态 / 生物多样性
    "aquatic ecosystem", "fish assemblage", "macroinvertebrate community", "aquatic biodiversity",
    "water ecological integrity", "ecological health assessment",
    # 监测 / 模型
    "water quality monitoring", "remote sensing water", "water quality model", "machine learning water quality",
    "sensor network water",
    # 治理机制 / 政策
    "water governance", "water pollution prevention", "pollutant discharge permit", "total maximum daily load",
    "water ecological compensation", "payment for ecosystem services water", "transboundary water",
    # 国际 / 区域
    "EU water framework directive", "clean water act", "water framework directive implementation",
    "lake taihu eutrophication", "lake chao eutrophication",
]
PAGES = int(os.environ.get("PAGES", "6"))
DCC = {"生态环境": "生态环境", "水利": "水利", "科技": "科技", "环境": "生态环境"}

# OpenAlex 礼貌池标识（提升配额、降低被限流概率）
OA_MAIL = os.environ.get("OA_MAIL", "water-eco-bot@users.noreply.github.com")
_last_req = [0.0]
def _ratelimit(min_gap=0.25):
    # 全局最小请求间隔，避免触发限流
    while True:
        now = time.time()
        left = _last_req[0] + min_gap - now
        if left <= 0:
            _last_req[0] = now
            return
        time.sleep(min(left, 0.5))

def get_json(u):
    # OpenAlex 礼貌池：附 mailto 提升配额；遇 429 指数退避重试
    if 'openalex.org' in u and 'mailto=' not in u:
        u = u + ('&' if '?' in u else '?') + 'mailto=' + urllib.parse.quote(OA_MAIL)
    backoff = 5
    for attempt in range(8):
        _ratelimit()
        try:
            req = urllib.request.Request(u, headers=UA)
            with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
                if r.status == 429:
                    time.sleep(backoff); backoff = min(backoff * 2, 90); continue
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(backoff); backoff = min(backoff * 2, 90); continue
            time.sleep(2)
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
    # 学术文献分类（收紧）：默认 literature；仅明确讨论标准(含代号)才归 standard
    return C.classify_academic(title, abstract)

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
    # 回溯游标：每次抓取比上一次“更旧”的批次，单调增长、零重复
    cur = C.load_cursor()
    until = (cur.get("openalex") or {}).get("until") or datetime.date.today().isoformat()
    min_date = until
    for q in queries:
        for pg in range(1, PAGES + 1):
            # 时间窗：2000 起 ~ until（游标上界），配合更大 per_page 覆盖海量水生态文献
            # SORT 可配置：desc=最新优先(小时级定时用)，asc=最旧优先(批量回填历史文献用)
            fdate = os.environ.get("FROM_DATE", "2000-01-01")
            sort = os.environ.get("SORT", "publication_date:desc")
            url = ("https://api.openalex.org/works?search=%s&per-page=200&page=%d"
                   "&filter=from_publication_date:%s,to_publication_date:%s,has_abstract:true"
                   "&sort=%s") % (urllib.parse.quote(q), pg, fdate, until, sort)
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
                if date and date > until: continue
                if date and date < min_date: min_date = date
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
                cid = C.make_cid({"url": link, "title": title, "date": date})
                pdf = None
                if FETCH_PDF:
                    pu = (w.get("open_access") or {}).get("pdf_url") or ""
                    if pu:
                        pdf = media.fetch_pdf(pu, cid)
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
                    "pdf": pdf,
                    "cid": cid,
                    "added_at": datetime.date.today().isoformat(),
                }
                inbox.append(rec); existing.add(title.strip().lower())
                if link: existing.add(norm_url(link))
                added += 1
            time.sleep(0.5)
        print("q=%s done, cumulative added=%d" % (q, added))
    if min_date < until:
        cur["openalex"] = {"until": min_date}
        C.save_cursor(cur)
        print("openalex 游标推进 -> %s" % min_date)
    total = C.append_inbox(inbox)
    print("crawl_academic: added=%d, inbox_total=%d" % (added, total))

if __name__ == "__main__":
    main()
