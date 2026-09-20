# -*- coding: utf-8 -*-
"""抓取生态环境部「水环境保护」标准库的真实详情页地址（标准名/标准号 -> URL）。

用途：修正知识库中一批**不可访问或被编造**的标准链接
（如 openstd.samr.gov.cn/bzgk/gb/newGbInfo?hcno=<编造值>），
改为生态环境部标准库的可访问详情页（页内挂 PDF 原文附件）。

输出：water-eco/out/std_index.json
  [{"code":"GB 3838-2002","name":"地表水环境质量标准","url":"...","impl":"2002-06-01","cat":"shjzlbz"}, ...]
"""
import os, re, sys, json, time, urllib.request, ssl
from urllib.parse import urljoin

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(BASE), "out", "std_index.json")

CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

ROOT = "https://www.mee.gov.cn/ywgz/fgbz/bz/bzwb/shjbh/"
CATS = {"shjzlbz": "水环境质量标准", "swrwpfbz": "水污染物排放标准", "xgbzh": "相关标准"}

CODE_RE = re.compile(r"\b(GB|HJ|SL|GB/T|HJ/T|GBZ|CJ|JT)\s*[\d.]+(?:[—–\-－]\s*\d{4})?")
DATE_RE = re.compile(r"(19|20)\d{2}[-年./]\d{1,2}[-月./]\d{1,2}")
DETAIL_RE = re.compile(r'href=["\'](\.?/?t\d{8}_\d+\.shtml|\./\d{6}/t\d{8}_\d+\.shtml)["\'][^>]*>(.*?)</a>', re.S | re.I)


def fetch(url, timeout=25, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as x:
                raw = x.read()
            for enc in ("utf-8", "gb18030"):
                try:
                    return raw.decode(enc)
                except Exception:
                    pass
            return raw.decode("utf-8", "replace")
        except Exception:
            if i == tries - 1:
                return ""
            time.sleep(1.5)


def clean(s):
    s = re.sub(r"<[^>]+>", "", s or "")
    s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&ldquo;", "“").replace("&rdquo;", "”")
    return re.sub(r"\s+", " ", s).strip()


def scan_cat(cat, max_pages=20):
    items = []
    for i in range(max_pages):
        u = ROOT + cat + "/" if i == 0 else ROOT + cat + "/index_%d.shtml" % i
        h = fetch(u)
        if not h:
            break
        found = 0
        for m in DETAIL_RE.finditer(h):
            href, txt = m.group(1), clean(m.group(2))
            if len(txt) < 6:
                continue
            full = urljoin(u, href)
            if any(x["url"] == full for x in items):
                continue
            cm = CODE_RE.search(txt)
            dm = DATE_RE.search(txt)
            items.append({
                "name": txt, "url": full, "cat": cat,
                "code": (cm.group(0).replace("－", "-").replace("—", "-").replace("–", "-").strip() if cm else ""),
                "impl": (dm.group(0).replace("年", "-").replace("月", "-").replace("日", "").replace(".", "-").replace("/", "-") if dm else ""),
            })
            found += 1
        if found == 0:
            break
        time.sleep(0.3)
    return items


def main():
    allitems = []
    for cat, label in CATS.items():
        its = scan_cat(cat)
        print("%s(%s): %d 条" % (cat, label, len(its)))
        allitems += its
    # 去重（同名同链接）
    seen, uniq = set(), []
    for it in allitems:
        k = it["url"]
        if k in seen:
            continue
        seen.add(k)
        uniq.append(it)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(uniq, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("合计 %d 条 -> %s" % (len(uniq), OUT))
    # 打印旗舰标准
    key = ["GB 3838", "GB 18918", "GB 8978", "GB/T 14848", "GB 5749", "GB 5084",
           "GB 18596", "GB/T 31962", "HJ 1295", "HJ 91.2", "GB 3552", "GB 18466"]
    print("\n--- 旗舰标准命中 ---")
    for k in key:
        hit = [x for x in uniq if k in x["name"] or k in x["code"]]
        for h in hit[:2]:
            print("  %-14s %s" % (k, h["url"]))
            print("      %s" % h["name"][:90])


if __name__ == "__main__":
    main()
