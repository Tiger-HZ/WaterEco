# -*- coding: utf-8 -*-
"""存量媒体回填：对 kb.json 中“已有知识”补抓配图与开放获取全文 PDF。
- 政策/研究类（gov.cn、mp.weixin.qq.com 等）缺配图的 → 重抓详情页抽取正文图片入库
- 学术文献（doi.org）缺 PDF 的 → 查 Crossref/OpenAlex 的 OA 全文 PDF 并下载入库
幂等：已有 images/pdf 的跳过；按 BATCH 上限分批（重跑继续推进，自然消化存量）。
用法：
  python refetch_media.py                 # 默认按 BATCH=300 推进
  BATCH=800 FETCH_PDF=1 FETCH_IMAGES=1 python refetch_media.py
（一般放在每日深度采集之后/之前运行，由 auto.yml 调度；沙箱受限时放在 GitHub Runner 跑。）
"""
import os, sys, json, time, ssl, urllib.request, urllib.parse, re

import crawl_common as C
import media
import crawl_gov
import crawl_weixin
import crawl_crossref

BASE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(BASE, "kb", "kb.json")
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
UA = {"User-Agent": "Mozilla/5.0 (WaterEcoBot; +https://github.com/Tiger-HZ/WaterEco)"}

BATCH = int(os.environ.get("BATCH", "300"))
FETCH_IMAGES = os.environ.get("FETCH_IMAGES", "1") == "1"
FETCH_PDF = os.environ.get("FETCH_PDF", "1") == "1"
MAIL = os.environ.get("OA_MAIL", "water-eco-bot@users.noreply.github.com")


def oa_pdf_for_doi(doi):
    """查 Crossref(优先) / OpenAlex 的 OA 全文 PDF 链接。"""
    # 1) Crossref
    try:
        url = "https://api.crossref.org/works/%s?mailto=%s" % (
            urllib.parse.quote(doi, safe=""), urllib.parse.quote(MAIL))
        req = urllib.request.Request(url, headers=C.UA)
        with urllib.request.urlopen(req, timeout=25, context=CTX) as r:
            d = json.loads(r.read().decode("utf-8"))
        it = (d.get("message") or {})
        pu = crawl_crossref.find_pdf_url(it)
        if pu:
            return pu
    except Exception:
        pass
    # 2) OpenAlex 回退
    try:
        url = "https://api.openalex.org/works/https://doi.org/%s" % urllib.parse.quote(doi, safe="")
        req = urllib.request.Request(url, headers=C.UA)
        with urllib.request.urlopen(req, timeout=25, context=CTX) as r:
            d = json.loads(r.read().decode("utf-8"))
        res = (d.get("results") or [d])[0] if isinstance(d.get("results"), list) else d
        pu = ((res.get("open_access") or {}).get("pdf_url") or "")
        if pu:
            return pu
    except Exception:
        pass
    return None


def main():
    kb = json.load(open(KB, encoding="utf-8"))
    n_img = n_pdf = 0
    processed = 0
    for r in kb:
        if processed >= BATCH:
            break
        cid = r.get("cid") or C.make_cid(r)
        r["cid"] = cid
        url = r.get("url") or ""
        changed = False

        # —— 配图回填（gov / 微信等详情页含图）——
        if FETCH_IMAGES and not r.get("images") and ("gov.cn" in url or "mp.weixin.qq.com" in url):
            container = soup = None
            if "gov.cn" in url:
                _, container, soup = crawl_gov.fetch_full(url)
            elif "mp.weixin.qq.com" in url:
                _, container, soup = crawl_weixin.fetch_weixin_full(url)
            if container:
                imgs = media.fetch_images(url, container, cid)
                if not imgs and soup:
                    imgs = media.fetch_images(url, soup, cid, skip_decorative=True)
                if imgs:
                    r["images"] = imgs
                    n_img += 1
                    changed = True
            time.sleep(0.3)
            processed += 1

        # —— PDF 回填（学术 doi 文献）——
        if FETCH_PDF and not r.get("pdf") and "doi.org" in url:
            m = re.search(r"doi\.org/(.+)$", url)
            doi = m.group(1) if m else ""
            if doi:
                pu = oa_pdf_for_doi(doi)
                if pu:
                    pdf = media.fetch_pdf(pu, cid)
                    if pdf:
                        r["pdf"] = pdf
                        n_pdf += 1
                        changed = True
                time.sleep(0.3)
                processed += 1

    json.dump(kb, open(KB, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("[refetch_media] 处理上限=%d 新增配图=%d 新增PDF=%d" % (BATCH, n_img, n_pdf))


if __name__ == "__main__":
    main()
