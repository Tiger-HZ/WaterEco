# -*- coding: utf-8 -*-
"""存量知识原文回填：给 kb.json 里**已有的**条目补原文。

解决的痛点：全库 3405 条此前 content_fetched=0%、pdf=0，连《生态环境法典》这类
根本大法都只有条目没有原文。本脚本按「重要性优先」逐条回填：

  1) 页面内挂有 PDF 原文（标准/条例/规划常见，如生态环境部标准页）-> 下载 PDF 到
     kb/pdf/<cid>.pdf，用 pypdf 抽文本作为 content，记录 pdf 字段；
  2) 无 PDF 的 -> 从 HTML 正文抽取全文（政策/法规解读页）；
  3) 两者都拿不到 -> 跳过（不空耗），下次再试。

幂等可续跑：已 content_fetched 的跳过；每 SAVE_EVERY 条落盘一次；BATCH 控制本次上限。
用法：
  python crawl_pdf_attach.py                 # 默认 BATCH=300
  BATCH=1200 python crawl_pdf_attach.py      # 大批量推进
  ONLY="法典|条例" python crawl_pdf_attach.py
"""
import json, os, re, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdfutil
from fetch_fulltext import extract_main_text

BASE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(BASE, "kb", "kb.json")

BATCH = int(os.environ.get("BATCH", "300"))
SAVE_EVERY = int(os.environ.get("SAVE_EVERY", "20"))
ONLY = os.environ.get("ONLY", "").strip()
PREFER_PDF = os.environ.get("PREFER_PDF", "1") == "1"

# 这些 URL 只是官方栏目页/首页，抓不到原文，直接跳过
SKIP_URL = re.compile(r"(/sylf/fggg/?$|www\.zj\.gov\.cn/?$|www\.hangzhou\.gov\.cn/?$|"
                      r"std\.samr\.gov\.cn/db/?$|www\.mee\.gov\.cn/?$|www\.hzrd\.gov\.cn/?$)")


def norm_prio(r):
    try:
        mr = int(r.get("must_rank") or 999)
    except Exception:
        mr = 999
    try:
        imp = float(r.get("importance") or 0)
    except Exception:
        imp = 0.0
    return (mr, -imp)


def main():
    kb = json.load(open(KB, encoding="utf-8"))
    todo = [r for r in kb if not r.get("content_fetched") and (r.get("url") or "").startswith("http")]
    if ONLY:
        rx = re.compile(ONLY)
        todo = [r for r in todo if rx.search(r.get("title") or "")]
    todo = [r for r in todo if not SKIP_URL.search((r.get("url") or "").rstrip("/") + "/")]
    todo.sort(key=norm_prio)
    print("待回填 %d 条，本次上限 %d" % (len(todo), BATCH))

    n_pdf = n_text = n_fail = 0
    processed = 0
    t0 = time.time()
    for r in todo:
        if processed >= BATCH:
            break
        url = r.get("url") or ""
        cid = r.get("cid") or ""
        html = pdfutil.fetch_html(url, timeout=20)
        if not html:
            n_fail += 1
            processed += 1
            time.sleep(0.2)
            continue

        got = False
        if PREFER_PDF:
            links = pdfutil.find_pdf_links(html, url)
            if links:
                pdfutil.ensure_dir()
                dest = os.path.join(pdfutil.PDF_DIR, cid + ".pdf")
                for pu in links[:2]:
                    if pdfutil.download_binary(pu, dest):
                        txt = pdfutil.extract_pdf_text(dest)
                        r["pdf"] = "kb/pdf/" + cid + ".pdf"
                        if txt:
                            r["content"] = txt
                            r["content_fetched"] = True
                            n_text += 1
                        else:
                            # 扫描版：保留 PDF，正文用页面文本兜底
                            pt = extract_main_text(html) or ""
                            if len(pt) > 300:
                                r["content"] = pt
                                r["content_fetched"] = True
                        n_pdf += 1
                        note = r.get("note") or ""
                        tag = "已抓取 PDF 原文：kb/pdf/%s.pdf" % cid
                        if tag not in note:
                            r["note"] = (note + "；" if note else "") + tag
                        got = True
                        break
                    time.sleep(0.2)

        if not got:
            pt = extract_main_text(html) or ""
            pt = "\n".join(l for l in pt.split("\n") if len(l) >= 4)
            if len(pt) >= 200:
                r["content"] = pt
                r["content_fetched"] = True
                n_text += 1
                got = True

        if not got:
            n_fail += 1
        processed += 1
        if processed % 20 == 0:
            print("  ..%d/%d  pdf=%d text=%d fail=%d  %.1f min"
                  % (processed, min(BATCH, len(todo)), n_pdf, n_text, n_fail, (time.time() - t0) / 60))
        if processed % SAVE_EVERY == 0:
            json.dump(kb, open(KB, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        time.sleep(0.25)

    json.dump(kb, open(KB, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    total_cf = sum(1 for x in kb if x.get("content_fetched"))
    total_pdf = sum(1 for x in kb if x.get("pdf"))
    print("=" * 50)
    print("crawl_pdf_attach: 处理=%d PDF=%d 文本=%d 失败=%d" % (processed, n_pdf, n_text, n_fail))
    print("全库现状: content_fetched=%d/%d  pdf=%d" % (total_cf, len(kb), total_pdf))


if __name__ == "__main__":
    main()
