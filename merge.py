# -*- coding: utf-8 -*-
"""双碳知识库 去重合并工具。
将 kb/inbox.json 中的新采集记录合并进 kb/kb.json：
 - 同一规范URL -> 视为同一篇，跳过；
 - 同一「事件指纹」(如政策文号/规范标题) -> 只保留质量更高的一条；
 - 否则新增。
用法：python merge.py
"""
import json, os, re

BASE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(BASE, "kb", "kb.json")
INBOX = os.path.join(BASE, "kb", "inbox.json")

QUAL = {"A": 3, "B": 2, "C": 1}


def qval(q):
    return QUAL.get(q, 1)


def norm_url(u):
    u = (u or "").strip()
    u = re.sub(r"#.*$", "", u)
    u = re.sub(r"\?.*$", "", u)
    u = u.rstrip("/")
    return u.lower()


def event_key(title):
    t = (title or "")
    # 提取政策/标准文号作为强事件指纹
    m = re.search(r"((国发|发改|工信部|环资|银保|央行|国标|gb|gbt|gb/t)[^ ]*\d{3,}[号〕)\-])",
                  t, re.I)
    if m:
        return "ev:" + m.group(1).lower()
    s = re.sub(r"[^\w\u4e00-\u9fff]+", " ", t).strip().lower()
    s = re.sub(r"\s+", " ", s)
    return "ti:" + s[:18]


def load(p):
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return []
    return []


def main():
    kb = load(KB)
    inbox = load(INBOX)
    if not inbox:
        print("no inbox")
        return
    by_url = {}
    by_ev = {}
    for r in kb:
        nu = norm_url(r.get("url", ""))
        if nu:
            by_url[nu] = r
        ev = r.get("_ev")
        if ev:
            by_ev.setdefault(ev, []).append(r)
    added = skipped = replaced = 0
    for r in inbox:
        nu = norm_url(r.get("url", ""))
        ev = r.get("_ev") or event_key(r.get("title", ""))
        r["_ev"] = ev
        if nu and nu in by_url:
            skipped += 1
            continue
        if ev in by_ev:
            old = by_ev[ev][0]
            if qval(r.get("quality")) > qval(old.get("quality")):
                for i, x in enumerate(kb):
                    if x is old or (x.get("_ev") == ev and x.get("url") == old.get("url")):
                        kb[i] = r
                        break
                by_ev[ev] = [r]
                replaced += 1
            else:
                skipped += 1
            continue
        kb.append(r)
        if nu:
            by_url[nu] = r
        by_ev[ev] = [r]
        added += 1
    os.makedirs(os.path.dirname(KB), exist_ok=True)
    json.dump(kb, open(KB, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump([], open(INBOX, "w", encoding="utf-8"), ensure_ascii=False)
    print("added=%d skipped=%d replaced=%d total=%d" % (added, skipped, replaced, len(kb)))


if __name__ == "__main__":
    main()
