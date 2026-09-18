# -*- coding: utf-8 -*-
"""水生态环境知识库 去重合并工具。
将 kb/inbox.json 中的新采集记录合并进 kb/kb.json：
 - 同一「事件指纹」(规范化全标题 sha1) -> 只保留质量更高的一条；
 - 同一规范 URL -> 视为同一篇，跳过；**但仅当两个 URL 都不含 query 参数时**才据此判重，
   避免 openstd.samr.gov.cn/bzgk/gb/newGbInfo?hcno=xxx 这类「同页不同标准」被误判为同一篇；
 - 否则新增。

【2026-09-18 修订】原实现有两个误杀：
 1) norm_url 会截掉 ?query，导致 24 个国家标准（openstd hcno 详情页）互相判重，只剩 1 条；
 2) event_key 只取标题前 18 字，导致《水生生物水质基准推导基本数据集 第7~16部分》
    这类「长公共前缀 + 尾部区分」的标题互相判重，只剩 1 条。
 现改为：指纹 = 规范化全标题 sha1 前 16 位；URL 判重仅在不含 query 时生效。

用法：python merge.py
"""
import json, os, re, hashlib

BASE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(BASE, "kb", "kb.json")
INBOX = os.path.join(BASE, "kb", "inbox.json")

QUAL = {"A": 3, "B": 2, "C": 1}

NORM_CHARS = r"[\s《》〈〉【】\[\]()（）\"'“”‘’·、,，。.：:;；!！?？—\-_/\\]+"


def qval(q):
    return QUAL.get(q, 1)


def norm_title(t):
    t = (t or "").strip().lower()
    return re.sub(NORM_CHARS, "", t)


def norm_url(u):
    """用于 URL 判重：去 fragment、去末尾斜杠、小写。保留 query（由调用方判定是否可比）。"""
    u = (u or "").strip()
    u = re.sub(r"#.*$", "", u)
    return u.rstrip("/").lower()


def event_key(title):
    """事件指纹 = 规范化全标题的 sha1 前 16 位（完全同名才算同一篇）。"""
    return "ti:" + hashlib.sha1(norm_title(title).encode("utf-8")).hexdigest()[:16]


def url_comparable(u):
    """只有当 URL 不含 query 时，才参与「同篇」判重。"""
    return bool(u) and "?" not in u


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
        if url_comparable(nu):
            by_url[nu] = r
        ev = r.get("_ev") or event_key(r.get("title", ""))
        by_ev.setdefault(ev, []).append(r)
    added = skipped = replaced = 0
    for r in inbox:
        nu = norm_url(r.get("url", ""))
        ev = event_key(r.get("title", ""))
        r["_ev"] = ev
        if url_comparable(nu) and nu in by_url:
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
        if url_comparable(nu):
            by_url[nu] = r
        by_ev[ev] = [r]
        added += 1
    os.makedirs(os.path.dirname(KB), exist_ok=True)
    json.dump(kb, open(KB, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump([], open(INBOX, "w", encoding="utf-8"), ensure_ascii=False)
    print("added=%d skipped=%d replaced=%d total=%d" % (added, skipped, replaced, len(kb)))


if __name__ == "__main__":
    main()
