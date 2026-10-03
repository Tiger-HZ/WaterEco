# -*- coding: utf-8 -*-
"""学术文献全文获取（多源）—— 2026-10-03 按用户要求新增

用户要求：「学术的也可以，但要把全文或内容都要收录进来，不能仅只有摘要等简单的信息」。

背景：crawl_academic / crawl_crossref / crawl_europepmc 原来只存摘要（OpenAlex 的
abstract_inverted_index），实测 2621 条科研文献里 content 字段有内容的为 0 —— 等于只有题录。

本模块按优先级尝试拿**全文**：
  ① EuropePMC fullTextXML（有 PMCID 的开放获取论文，全文 XML 最完整）
  ② Unpaywall（按 DOI 查 best_oa_location，覆盖最广）
  ③ OpenAlex open_access.oa_url / best_oa_location
  ④ DOI 直连（部分出版商开放 HTML/PDF）
拿到的 PDF 用 pdfutil 抽文本、HTML 用正文抽取；**不足 MIN_FULLTEXT 字视为失败**，
调用方据此决定是否入库（宁缺毋滥，符合「不要只有摘要」的要求）。

用法：
  from academic_fulltext import fetch_fulltext
  text, src = fetch_fulltext(doi=..., pmcid=..., oa_url=...)
"""
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "WaterEcoBot/1.0 (mailto:water-eco-bot@users.noreply.github.com)",
      "Accept": "application/json,text/html,application/xml;q=0.9,*/*;q=0.8"}
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
EMAIL = os.environ.get("OA_MAIL", "water-eco-bot@users.noreply.github.com")

MIN_FULLTEXT = int(os.environ.get("MIN_FULLTEXT", "3000"))   # 全文最低字数

_last = [0.0]


def _rate(min_gap=0.3):
    d = time.time() - _last[0]
    if d < min_gap:
        time.sleep(min_gap - d)
    _last[0] = time.time()


def _get(url, timeout=30, binary=False):
    try:
        _rate()
        r = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(r, timeout=timeout, context=CTX) as x:
            raw = x.read()
        if binary:
            return raw
        for enc in ("utf-8", "gb18030"):
            try:
                return raw.decode(enc)
            except Exception:
                continue
        return raw.decode("utf-8", "replace")
    except Exception:
        return None


def _clean(t):
    t = re.sub(r"<script.*?</script>", " ", t or "", flags=re.S | re.I)
    t = re.sub(r"<style.*?</style>", " ", t, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = (t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">"))
    t = re.sub(r"[ \t\u3000]+", " ", t)
    t = re.sub(r"\n{2,}", "\n", t)
    return t.strip()


def _pdf_text(url):
    try:
        sys.path.insert(0, BASE)
        import pdfutil, tempfile
        raw = _get(url, timeout=60, binary=True)
        if not raw or len(raw) < 2000:
            return ""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(raw)
            tmp = f.name
        txt = pdfutil.extract_pdf_text(tmp) or ""
        try:
            os.unlink(tmp)
        except Exception:
            pass
        return txt
    except Exception:
        return ""


def from_europepmc(pmcid):
    if not pmcid:
        return "", ""
    xml = _get("https://www.ebi.ac.uk/europepmc/webservices/rest/%s/fullTextXML" % pmcid)
    if not xml or "<body" not in xml.lower():
        return "", ""
    body = xml[xml.lower().find("<body"):]
    txt = _clean(body)
    if len(txt) >= MIN_FULLTEXT:
        return txt, "EuropePMC 全文XML"
    return "", ""


def from_unpaywall(doi):
    if not doi:
        return "", ""
    d = doi.replace("https://doi.org/", "").strip()
    j = _get("https://api.unpaywall.org/v2/%s?email=%s" % (urllib.parse.quote(d), EMAIL))
    if not j:
        return "", ""
    try:
        import json as _j
        o = _j.loads(j)
        loc = o.get("best_oa_location") or {}
        for u in [loc.get("url_for_pdf"), loc.get("url")]:
            if not u:
                continue
            if u.lower().endswith(".pdf"):
                t = _pdf_text(u)
            else:
                t = _clean(_get(u, timeout=40))
            if len(t) >= MIN_FULLTEXT:
                return t, "Unpaywall(%s)" % ("PDF" if u.lower().endswith(".pdf") else "HTML")
    except Exception:
        pass
    return "", ""


def from_url(u):
    if not u:
        return "", ""
    if u.lower().endswith(".pdf"):
        t = _pdf_text(u)
    else:
        t = _clean(_get(u, timeout=40))
    if len(t) >= MIN_FULLTEXT:
        return t, "OA 链接"
    return "", ""


def fetch_fulltext(doi="", pmcid="", oa_url="", oa_pdf=""):
    """按优先级尝试获取全文。返回 (text, source)；失败返回 ("", "")"""
    for fn, arg in ((from_europepmc, pmcid), (from_unpaywall, doi),
                    (from_url, oa_pdf), (from_url, oa_url)):
        try:
            t, s = fn(arg)
            if t:
                return t, s
        except Exception:
            continue
    return "", ""


if __name__ == "__main__":
    print("[academic_fulltext] 自测（MIN_FULLTEXT=%d）" % MIN_FULLTEXT)
    for name, kw in [("EuropePMC PMC 示例", dict(pmcid="PMC1010101")),
                     ("Unpaywall 示例", dict(doi="10.1016/j.watres.2020.115999"))]:
        t, s = fetch_fulltext(**kw)
        print("  %-22s -> %s 字  %s" % (name, len(t), s or "未获取到"))
