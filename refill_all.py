# -*- coding: utf-8 -*-
"""全库原文分级回填引擎（refill_all）。

目标：把知识库里**每一条**能拿到原文的条目都补上正文，拿不到全文的至少补权威摘要。
按来源类型走不同策略（这是关键——全库 68% 是 doi.org，用它自己的元数据接口最快）：

  A. 学术（doi.org / openalex.org / 期刊域名）
     1) Crossref API（一次请求拿到 abstract + 可能的 PDF 链接）
     2) 无 PDF 时查 OpenAlex open_access.pdf_url
     3) 仍无 -> 用 Crossref/OpenAlex 摘要作为 content（标注 content_kind='摘要'）
  B. 政府/标准（gov.cn、mee.gov.cn、openstd.samr.gov.cn、mwr/mohurd/zjrd/zj/hangzhou 等）
     1) 抓页面 HTML -> 抽正文（>=200 字即入库）
     2) 页面内挂 PDF 附件 -> 下载存 kb/pdf/<cid>.pdf 并抽文本（优先用 PDF 文本）
  C. 微信（mp.weixin.qq.com）-> 复用 crawl_weixin.fetch_weixin_full
  D. 其他 HTML -> 通用正文抽取
  E. 官方栏目页/首页（抓不到原文）-> 跳过，不空耗

幂等可续跑：`content_fetched` 为真则跳过；每 SAVE_EVERY 条落盘；BATCH 控制单次上限。
同时产出 kb/coverage.json（覆盖率看板），供门户「系统管理」展示。

用法：
  python refill_all.py                 # BATCH=600
  BATCH=1500 python refill_all.py      # 大批量推进
  KIND=academic python refill_all.py   # 只处理学术类
"""
import json, os, re, sys, time, urllib.parse, urllib.request, ssl
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdfutil
import content_guard
from fetch_fulltext import extract_main_text

BASE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(BASE, "kb", "kb.json")
COV = os.path.join(BASE, "kb", "coverage.json")

BATCH = int(os.environ.get("BATCH", "600"))
SAVE_EVERY = int(os.environ.get("SAVE_EVERY", "25"))
ONLY_KIND = os.environ.get("KIND", "").strip()
MAIL = os.environ.get("OA_MAIL", "water-eco-bot@users.noreply.github.com")
MIN_TEXT = int(os.environ.get("MIN_TEXT", "180"))

CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
UA = {"User-Agent": "Mozilla/5.0 (WaterEcoBot; +https://github.com/Tiger-HZ/WaterEco)",
      "Accept": "application/json, text/html;q=0.9, */*;q=0.8"}

GOV_RE = re.compile(r"(gov\.cn|npc\.gov\.cn|\.gov\.|mee\.gov|mwr\.gov|mohurd|ndrc|moa\.gov|"
                    r"openstd\.samr|std\.samr|zjrd|zj\.gov|hangzhou\.gov|chinacourt|people\.com\.cn|"
                    r"xinhuanet|xinhua|paper\.people)")
ACAD_RE = re.compile(r"(doi\.org|openalex\.org|arxiv\.org|springer|sciencedirect|wiley|tandfonline|"
                     r"mdpi\.com|frontiersin|nature\.com|acs\.org|rsc\.org|sagepub|ieee|pubmed|"
                     r"ncbi\.nlm\.nih\.gov|europepmc|cnki)")
SKIP_RE = re.compile(r"(/sylf/fggg/?$|^https?://www\.zj\.gov\.cn/?$|^https?://www\.hangzhou\.gov\.cn/?$|"
                     r"^https?://std\.samr\.gov\.cn/db/?$|^https?://www\.mee\.gov\.cn/?$|"
                     r"^https?://www\.hzrd\.gov\.cn/?$)")
ABSTRACT_LABEL = "【摘要】本库未获取到该文献全文，以下为出版方/索引库提供的官方摘要：\n\n"
_DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/)+", re.I)


def bare_doi(u):
    """从任意写法里取出裸 DOI（修复 'https://doi.org/https://doi.org/10.x' 双重前缀）。"""
    m = re.search(r"doi\.org/(.+)$", u or "", re.I)
    s = m.group(1) if m else (u or "")
    s = urllib.parse.unquote(s).strip()
    s = re.sub(r"^doi:\s*", "", s, flags=re.I)
    s = _DOI_PREFIX.sub("", s)
    return s.strip()


def host_of(u):
    m = re.match(r"https?://([^/]+)", u or "")
    return m.group(1).lower() if m else ""


def kind_of(url):
    h = host_of(url)
    if not url.startswith("http"):
        return "skip"
    if SKIP_RE.search(url.rstrip("/") + "/"):
        return "skip"
    if "doi.org" in h or "openalex.org" in h or ACAD_RE.search(h):
        return "academic"
    if "mp.weixin.qq.com" in h:
        return "weixin"
    if GOV_RE.search(h):
        return "gov"
    return "html"


def get_json(url, timeout=25):
    for i in range(3):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception:
            time.sleep(1.0 + i)
    return None


def get_pdf_bytes(url, timeout=45, max_bytes=30 * 1024 * 1024):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
            cl = int(r.headers.get("Content-Length", "0") or 0)
            if cl and cl > max_bytes:
                return None
            data = b""
            while True:
                c = r.read(65536)
                if not c:
                    break
                data += c
                if len(data) > max_bytes:
                    return None
        return data if data[:5].startswith(b"%PDF") else None
    except Exception:
        return None


def openalex_abstract(inv):
    """OpenAlex abstract_inverted_index -> 正常语序文本。"""
    if not isinstance(inv, dict):
        return ""
    pos = []
    for w, idxs in inv.items():
        for i in idxs:
            pos.append((i, w))
    pos.sort()
    return " ".join(w for _, w in pos)


def strip_jats(s):
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def datacite_abstract(doi):
    """DataCite（机构库/仓储 DOI，如 10.25602/gold.*）摘要。"""
    d = get_json("https://api.datacite.org/dois/%s" % urllib.parse.quote(doi, safe=""))
    if not d:
        return ""
    attrs = (d.get("data") or {}).get("attributes") or {}
    for desc in (attrs.get("descriptions") or []):
        if (desc.get("descriptionType") or "").lower() == "abstract" and desc.get("description"):
            return strip_jats(desc["description"])
    for desc in (attrs.get("descriptions") or []):
        if desc.get("description"):
            return strip_jats(desc["description"])
    return ""


def unpaywall_pdf(doi):
    """Unpaywall：最权威的开放获取全文 PDF 定位。"""
    d = get_json("https://api.unpaywall.org/v2/%s?email=%s"
                 % (urllib.parse.quote(doi, safe=""), urllib.parse.quote(MAIL)))
    if not d:
        return ""
    best = d.get("best_oa_location") or {}
    if best.get("url_for_pdf"):
        return best["url_for_pdf"]
    for loc in (d.get("oa_locations") or []):
        if loc.get("url_for_pdf"):
            return loc["url_for_pdf"]
    return ""


def save_pdf(rec, data):
    pdfutil.ensure_dir()
    cid = rec.get("cid") or "x"
    dest = os.path.join(pdfutil.PDF_DIR, cid + ".pdf")
    with open(dest, "wb") as f:
        f.write(data)
    t = pdfutil.extract_pdf_text(dest)
    if len(t) >= MIN_TEXT:
        return t, "kb/pdf/%s.pdf" % cid
    return None, None


def refill_academic(rec):
    """学术文献：Unpaywall/Crossref/OpenAlex 找 OA 全文 PDF；否则补权威摘要。"""
    url = rec.get("url") or ""
    doi = bare_doi(url) if "doi.org" in url.lower() else ""

    # —— openalex.org 直接走 OpenAlex ——
    if not doi and "openalex.org" in url:
        m = re.search(r"openalex\.org/(W\d+)", url)
        if m:
            d = get_json("https://api.openalex.org/works/%s" % m.group(1))
            if d:
                doi = (d.get("doi") or "").replace("https://doi.org/", "")
                pu = ((d.get("open_access") or {}).get("pdf_url") or "")
                if pu:
                    b = get_pdf_bytes(pu)
                    if b:
                        t, p = save_pdf(rec, b)
                        if t:
                            return t, "pdf", p
                ab = openalex_abstract(d.get("abstract_inverted_index"))
                if len(ab) >= MIN_TEXT:
                    return ABSTRACT_LABEL + ab, "abstract", None
        return None, None, None
    if not doi:
        return None, None, None

    # ① Unpaywall -> 最靠谱的 OA 全文 PDF
    pu = unpaywall_pdf(doi)
    if pu:
        b = get_pdf_bytes(pu)
        if b:
            t, p = save_pdf(rec, b)
            if t:
                return t, "pdf", p

    # ② Crossref（摘要 + link[pdf]）与 OpenAlex 并行补齐
    cm = get_json("https://api.crossref.org/works/%s?mailto=%s"
                  % (urllib.parse.quote(doi, safe=""), urllib.parse.quote(MAIL)))
    msg = (cm or {}).get("message") or {}
    for lk in (msg.get("link") or []):
        if "pdf" in (lk.get("content-type") or "").lower() and lk.get("URL"):
            b = get_pdf_bytes(lk["URL"])
            if b:
                t, p = save_pdf(rec, b)
                if t:
                    return t, "pdf", p
            break
    ab = strip_jats(msg.get("abstract"))
    if len(ab) >= MIN_TEXT:
        return ABSTRACT_LABEL + ab, "abstract", None

    oa = get_json("https://api.openalex.org/works/https://doi.org/%s" % urllib.parse.quote(doi, safe=""))
    if oa:
        pu2 = ((oa.get("open_access") or {}).get("pdf_url") or "")
        if pu2:
            b = get_pdf_bytes(pu2)
            if b:
                t, p = save_pdf(rec, b)
                if t:
                    return t, "pdf", p
        ab2 = openalex_abstract(oa.get("abstract_inverted_index"))
        if len(ab2) >= MIN_TEXT:
            return ABSTRACT_LABEL + ab2, "abstract", None

    # ③ DataCite（机构库/仓储 DOI，Crossref 查不到的走这里）
    ab3 = datacite_abstract(doi)
    if len(ab3) >= MIN_TEXT:
        return ABSTRACT_LABEL + ab3, "abstract", None
    return None, None, None


def refill_web(rec, weixin=False):
    """政府/普通网页：先 PDF 附件，再 HTML 正文。"""
    url = rec.get("url") or ""
    cid = rec.get("cid") or ""
    if weixin:
        try:
            import crawl_weixin
            txt, _, _ = crawl_weixin.fetch_weixin_full(url)
            if txt and len(txt) >= MIN_TEXT:
                return txt, "正文", None
        except Exception:
            pass
        return None, None, None
    html = pdfutil.fetch_html(url, timeout=20)
    if not html:
        return None, None, None
    links = pdfutil.find_pdf_links(html, url)
    for pu in links[:2]:
        b = get_pdf_bytes(pu)
        if b:
            pdfutil.ensure_dir()
            dest = os.path.join(pdfutil.PDF_DIR, cid + ".pdf")
            open(dest, "wb").write(b)
            t = pdfutil.extract_pdf_text(dest)
            if len(t) >= MIN_TEXT:
                return t, "pdf全文", "kb/pdf/%s.pdf" % cid
    t = extract_main_text(html) or ""
    t = "\n".join(l for l in t.split("\n") if len(l) >= 4)
    if len(t) >= MIN_TEXT:
        return t, "正文全文", None
    return None, None, None


def write_coverage(kb):
    """统一走 coverage.py 的真实口径（按内容有效性，而非 content_fetched 标记）。

    旧口径的问题（2026-09-20 复盘）：content_fetched 长期不可信 ——
    我介入前的原始库就有 97.9% 标着"已抓全文"而 content 全空；
    回填后 88.78% 里 5127 条其实"未回填"也被算作已抓。
    """
    try:
        import coverage as COV_MOD
        cov = COV_MOD.main(do_print=False)
        # 兼容旧字段，避免门户/其他脚本读不到
        cov["fetched"] = cov["fulltext"] + cov["pdf_only"]
        cov["content_kind"] = {
            "正文全文/pdf全文": cov["fulltext"],
            "仅PDF": cov["pdf_only"],
            "仅摘要": cov["abstract_only"],
            "未回填": cov["missing"],
        }
        cov["url_kind_total"] = cov["by_url_kind"]
        json.dump(cov, open(COV, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return cov
    except Exception as e:
        print("[refill_all] coverage 模块不可用(%r)，回退简易口径" % (e,))
        from collections import Counter as C
        n = len(kb)
        ft = sum(1 for r in kb if r.get("content_fetched") and (r.get("content") or ""))
        cov = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "total": n, "fetched": ft, "fulltext": ft,
            "with_pdf": sum(1 for r in kb if r.get("pdf")),
            "content_kind": dict(C((r.get("content_kind") or "未回填") for r in kb)),
        }
        cov["coverage_pct"] = round(100.0 * ft / max(1, n), 2)
        cov["origin_covered_pct"] = cov["coverage_pct"]
        json.dump(cov, open(COV, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return cov


def main():
    kb = json.load(open(KB, encoding="utf-8"))
    todo = [r for r in kb if not r.get("content_fetched") and (r.get("url") or "").startswith("http")]
    if ONLY_KIND:
        todo = [r for r in todo if kind_of(r.get("url") or "") == ONLY_KIND]
    # 优先级：必知必会/高重要性先行
    todo.sort(key=lambda r: (int(r.get("must_rank") or 999), -(float(r.get("importance") or 0))))
    print("待回填 %d 条，本次上限 %d" % (len(todo), BATCH))

    stats = Counter()
    t0 = time.time()
    processed = 0
    for r in todo:
        if processed >= BATCH:
            break
        k = kind_of(r.get("url") or "")
        if k == "skip":
            stats["skip"] += 1
            continue
        if k == "academic":
            txt, ck, pdf = refill_academic(r)
        elif k == "weixin":
            txt, ck, pdf = refill_web(r, weixin=True)
        else:
            txt, ck, pdf = refill_web(r)
        if txt:
            ok, reason = content_guard.is_valid(txt, r.get("title") or "")
            if not ok:
                r["content_fetched"] = False
                r["content_reject"] = reason
                stats["reject:" + reason.split(":")[0]] += 1
                processed += 1
                if processed % SAVE_EVERY == 0:
                    json.dump(kb, open(KB, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                time.sleep(0.15)
                continue
            r["content"] = txt
            r["content_fetched"] = True
            r["content_kind"] = ck
            r.pop("content_reject", None)
            if pdf:
                r["pdf"] = pdf
            stats[ck or k] += 1
        else:
            stats["fail:" + k] += 1
        processed += 1
        if processed % 25 == 0:
            el = (time.time() - t0) / 60.0
            print("  ..已处理 %d 条  %.1f min  分项=%s"
                  % (processed, el, dict(stats)), flush=True)
        if processed % SAVE_EVERY == 0:
            json.dump(kb, open(KB, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        time.sleep(0.15)

    json.dump(kb, open(KB, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    cov = write_coverage(kb)
    print("=" * 56)
    print("refill_all: 处理=%d  %s" % (processed, dict(stats)))
    print("覆盖率: %s/%s = %s%%   含PDF %s  PDF/摘要分项=%s"
          % (cov["fetched"], cov["total"], cov["coverage_pct"], cov["with_pdf"], cov["content_kind"]))
    print("按 URL 类型: 总=%s 已回填=%s" % (cov["url_kind_total"], cov["url_kind_fetched"]))


if __name__ == "__main__":
    main()
