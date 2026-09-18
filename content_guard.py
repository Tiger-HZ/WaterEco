# -*- coding: utf-8 -*-
"""原文内容校验闸门（content guard）。

背景（2026-09-18）：回填后发现部分条目的 content 是**错误页文案**，例如生态环境部
某栏目页 404 后的提示「抱歉，您访问的地址有错或页面不存在…」（211 字），却被判为
"已抓原文"；另有《长江保护法》抓到的是 npc.gov.cn 的**英文版**。
这说明缺一道"内容真伪/相关性"校验。

本模块提供：
  is_valid(text, title)  -> (bool, reason)     内容真伪与相关性判定
  validate_record(rec, text)                   记录级封装
  scan_and_reset(kb, full_dir, dry)            扫描 kb/full/*.txt，把不合格的标记为
                                               待重取（content_fetched=False 并删除坏文件）

判定规则（任一不满足即不合格）：
  1) 错误页/风控页特征词命中 -> reject:error_page
  2) 长度 < MIN_LEN（默认 600 字） -> reject:too_short
  3) 标题与正文关键词重合度太低（标题里的中文实词基本没出现）-> reject:title_mismatch
  4) 正文中文占比过低（对中文标题而言）-> reject:not_chinese
"""
import os, re, json

MIN_LEN = int(os.environ.get("GUARD_MIN_LEN", "600"))
MIN_TITLE_HIT = float(os.environ.get("GUARD_MIN_TITLE_HIT", "0.34"))
MIN_CJK_RATIO = float(os.environ.get("GUARD_MIN_CJK_RATIO", "0.25"))

ERROR_MARKERS = [
    "页面不存在", "访问的地址有错", "您访问的页面不存在", "抱歉，您访问", "404", "Not Found",
    "无法访问此网站", "无法找到该页", "系统繁忙", "请稍后再试", "出错了", "请求被拒绝",
    "验证码", "安全验证", "请开启JavaScript", "请开启 JavaScript", "浏览器版本过低",
    "该内容已被删除", "内容不存在", "没有找到", "无权访问", "404 Not Found",
    "the page you requested", "page not found", "access denied", "403 forbidden",
]
RISK_MARKERS = ["访问过于频繁", "您的访问", "请进行人机验证", "正在验证您是否是", "Cloudflare"]

CJK = re.compile(r"[\u4e00-\u9fff]")
STOP = set("的了和与及在是为对中关于及其等一二三四五六七八九十一二三号中华人民共和国实施办法通知公告印发")


def _cjk_ratio(t):
    if not t:
        return 0.0
    n = len(CJK.findall(t))
    return n / max(1, len(t))


def _title_tokens(title):
    t = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", " ", title or "")
    out = []
    for w in t.split():
        if len(w) >= 2 and w not in STOP:
            out.append(w)
    # 长标题再切 2-gram 提升召回
    for w in list(out):
        if len(w) >= 6:
            out += [w[i:i + 3] for i in range(0, len(w) - 2, 3)]
    return list(dict.fromkeys(out))[:24]


def is_valid(text, title=""):
    t = (text or "").strip()
    if not t:
        return False, "empty"
    low = t.lower()
    for m in ERROR_MARKERS:
        if m.lower() in low:
            return False, "error_page:" + m
    for m in RISK_MARKERS:
        if m.lower() in low:
            return False, "risk_page:" + m
    if len(t) < MIN_LEN:
        return False, "too_short:%d" % len(t)
    if CJK.search(title or ""):
        if _cjk_ratio(t) < MIN_CJK_RATIO:
            return False, "not_chinese:%.2f" % _cjk_ratio(t)
        toks = _title_tokens(title)
        if toks:
            hit = sum(1 for w in toks if w in t)
            ratio = hit / float(len(toks))
            if ratio < MIN_TITLE_HIT:
                return False, "title_mismatch:%.2f" % ratio
    return True, "ok"


def scan_and_reset(kb, full_dir, dry=False):
    """扫描 kb/full/<cid>.txt 与内联 content，把不合格的标记为待重取。
    返回统计字典。"""
    import collections
    stat = collections.Counter()
    for r in kb:
        cid = r.get("cid") or ""
        fpath = os.path.join(full_dir, cid + ".txt") if cid else ""
        text = r.get("content") or ""
        if not text and fpath and os.path.exists(fpath):
            try:
                text = open(fpath, encoding="utf-8", errors="replace").read()
            except Exception:
                text = ""
        if not text:
            if r.get("content_fetched"):
                r["content_fetched"] = False
                r.pop("content_kind", None)
                r["content_note"] = "无正文文件，标记为重取"
                stat["missing_file"] += 1
            continue
        ok, reason = is_valid(text, r.get("title") or "")
        if ok:
            stat["ok"] += 1
            continue
        stat["reset:" + reason.split(":")[0]] += 1
        r["content_fetched"] = False
        r["content_note"] = "内容校验不合格（%s），已标记重取" % reason
        r["content_reject"] = reason
        r.pop("content_kind", None)
        if "content" in r:
            del r["content"]
        if fpath and os.path.exists(fpath) and not dry:
            try:
                os.remove(fpath)
            except Exception:
                pass
    return dict(stat)


if __name__ == "__main__":
    # 自测
    cases = [
        ("抱歉，您访问的地址有错或页面不存在。如您是在地址栏输入网址的，请确认其拼写和大小写" * 8,
         "重点流域水生态环境保护规划"),
        ("第一条 为了保护生态环境，防治污染和其他公害，保障公众健康和生态环境权益，维护生态安全，"
         "推动绿色低碳发展，建设生态文明，全面推进美丽中国建设。" * 6, "中华人民共和国生态环境法典"),
    ]
    for txt, title in cases:
        print(is_valid(txt, title))
