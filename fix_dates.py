# -*- coding: utf-8 -*-
"""回填修正：将首轮回填中按收录日(2026-07-17)统一打戳的历史条目，
改回其真实发布日期(added_at=date)，使历史知识可分日期回看；
2026年内的当日收录条目保持 added_at=2026-07-17（构成"今日推送"）。
用法：python fix_dates.py
"""
import json, os

BASE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(BASE, "kb", "kb.json")
TODAY = "2026-07-17"

kb = json.load(open(KB, encoding="utf-8"))
n_hist = n_keep = 0
for r in kb:
    d = r.get("date", "")
    # 原回填把历史条目都打成了 2026-07-17；将其还原为真实发布日期
    if r.get("added_at") == TODAY and d and d < "2026-01-01":
        r["added_at"] = d
        n_hist += 1
    else:
        n_keep += 1
json.dump(kb, open(KB, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("re-dated historical:", n_hist, "| kept as today:", n_keep, "| total:", len(kb))
