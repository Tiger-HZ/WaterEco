# -*- coding: utf-8 -*-
"""一次性回填：对“已入库但只有摘要片段”的政策原文，重新抓取全文并覆盖 kb/full/<cid>.txt。
原因：早期 crawl_gov 只存了搜索摘要片段；本次修复后新采集会带全文，但历史条目需回填。
仅对 gov.cn 政策详情页（#UCAP-CONTENT）生效；命中即覆盖，未命中则保留原文件。
用法：python refetch_full.py
"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from crawl_gov import fetch_full

BASE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(BASE, "kb", "kb.json")
FULL = os.path.join(BASE, "kb", "full")

def main():
    kb = json.load(open(KB, encoding="utf-8"))
    os.makedirs(FULL, exist_ok=True)
    n = 0
    for r in kb:
        url = r.get("url") or ""
        if "gov.cn" not in url:
            continue
        if "zhengce" not in url and "www.gov.cn" not in url:
            continue
        cid = r.get("cid")
        if not cid:
            continue
        fp = os.path.join(FULL, cid + ".txt")
        existing_len = os.path.getsize(fp) if os.path.exists(fp) else 0
        full = fetch_full(url)
        time.sleep(0.3)
        if not full:
            continue
        # 仅当抓到更长正文时覆盖（避免把全文覆盖成空/更短）
        if full and len(full) > existing_len + 100:
            with open(fp, "w", encoding="utf-8") as f:
                f.write(full)
            r["content_fetched"] = True
            n += 1
        elif full and existing_len == 0:
            with open(fp, "w", encoding="utf-8") as f:
                f.write(full)
            r["content_fetched"] = True
            n += 1
    json.dump(kb, open(KB, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("refetch_full: 覆盖全文=%d / 候选 gov 条目已处理" % n)

if __name__ == "__main__":
    main()
