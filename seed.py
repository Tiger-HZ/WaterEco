# -*- coding: utf-8 -*-
"""将首期 data/2026-07-17.json 转为知识库记录，写入 kb/inbox.json 待合并。"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from merge import event_key

BASE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(BASE, "data", "2026-07-17.json")
OUT = os.path.join(BASE, "kb", "inbox.json")


def main():
    data = json.load(open(SRC, encoding="utf-8"))
    items = data.get("items", [])
    out = []
    for it in items:
        rec = dict(it)
        rec["content"] = it.get("summary", "")
        rec["content_fetched"] = False
        rec["added_at"] = data.get("date", "2026-07-17")
        rec["_ev"] = event_key(it.get("title", ""))
        out.append(rec)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("seeded", len(out))


if __name__ == "__main__":
    main()
