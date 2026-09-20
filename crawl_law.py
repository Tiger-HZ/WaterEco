# -*- coding: utf-8 -*-
"""
法律法规原文抓取与校核器（crawl_law）

解决的核心问题（用户指出）：**条目有链接，但链接指向通知/解读/新闻页，没有法规原文全文**。
本工具做两件事：

  ① --scan  全库扫描：对「法律/行政法规/部门规章/地方性法规/省政府规章/地方政府规章」类条目，
            检查其原文是否为**法规全文**（判据：含"第一条" 且 不同条文数 ≥15 且 文本 ≥3000 字），
            不合格者列出清单（即"有链接无全文"的真实缺口）。

  ② 抓取修复：按 `law_sources.json`（人工核实、实测可抓全文的权威出处）重新抓取并回填，
            校验通过才写入；不通过则标记 `origin_status="暂未收录原文全文"` 并在 note 说明，
            **绝不用通知页/解读页冒充原文**。

用法：
  python3 crawl_law.py --scan                 # 只扫描，出缺口清单
  python3 crawl_law.py --apply                # 按映射表抓取并回填
  python3 crawl_law.py --apply --limit 5      # 限制本次处理条数
"""
import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
      "Accept-Language": "zh-CN,zh;q=0.9"}
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

# 需要"法规全文"的文件层级
LAW_LEVELS = {"法律", "行政法规", "部门规章", "地方性法规", "省政府规章", "地方政府规章"}

ART_RE = re.compile(r"第[一二三四五六七八九十百零〇\d]+条")
MIN_ARTS = 15
MIN_CHARS = 3000


def load_json(p, default=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def fetch(url, timeout=30):
    try:
        r = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(r, timeout=timeout, context=CTX) as x:
            raw = x.read()
        for enc in ("utf-8", "gb18030"):
            try:
                return raw.decode(enc)
            except Exception:
                continue
        return raw.decode("utf-8", "replace")
    except Exception as e:
        return ""


def to_text(html):
    if not html:
        return ""
    t = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    t = re.sub(r"<style.*?</style>", " ", t, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = (t.replace("&nbsp;", " ").replace("&ldquo;", "“").replace("&rdquo;", "”")
         .replace("&mdash;", "—").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">"))
    t = re.sub(r"[ \t\u3000]+", " ", t)
    t = re.sub(r"\n{2,}", "\n", t)
    return t.strip()


def fetch_docx(url, timeout=40):
    """docx 兜底解析（需 python-docx；不可用则返回空）"""
    try:
        import docx  # type: ignore
    except Exception:
        return ""
    try:
        import tempfile
        r = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(r, timeout=timeout, context=CTX) as x:
            raw = x.read()
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
            f.write(raw)
            p = f.name
        d = docx.Document(p)
        txt = "\n".join(par.text for par in d.paragraphs if par.text.strip())
        os.unlink(p)
        return txt
    except Exception:
        return ""


def fetch_pdf_text(url, timeout=60):
    """PDF 全文：下载后用 pypdf 抽文本（人大法律库的 PDF 版走这里）"""
    try:
        sys.path.insert(0, BASE)
        import pdfutil
        import tempfile
        r = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(r, timeout=timeout, context=CTX) as x:
            raw = x.read()
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(raw)
            p = f.name
        txt = pdfutil.extract_pdf_text(p) or ""
        try:
            os.unlink(p)
        except Exception:
            pass
        return txt
    except Exception as e:
        print("      (PDF 解析失败：%s)" % repr(e)[:60])
        return ""


def law_quality(text):
    """返回 (是否法规全文, 条文数, 字数)"""
    n = len(set(ART_RE.findall(text or "")))
    ok = ("第一条" in (text or "")) and n >= MIN_ARTS and len(text or "") >= MIN_CHARS
    return ok, n, len(text or "")


def norm_title(t):
    return re.sub(r"[\s《》（）()【】\[\]]", "", (t or ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", default=os.path.join(BASE, "kb", "kb.json"))
    ap.add_argument("--map", default=os.path.join(BASE, "law_sources.json"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--report", default="")
    a = ap.parse_args()
    # 流水线内自动修复：LAW_APPLY=1 时默认执行抓取回填
    if os.environ.get("LAW_APPLY") == "1":
        a.apply = True

    kb = load_json(a.kb, [])
    mp = load_json(a.map, {})
    sources = mp.get("sources", {})

    # ---------- 模式一：全库扫描 ----------
    if a.scan or not a.apply:
        rows, ok_n = [], 0
        for r in kb:
            if (r.get("content_type") or "") not in LAW_LEVELS:
                continue
            cid = r.get("cid") or ""
            body = r.get("content") or ""
            if not body:
                fp = os.path.join(BASE, "kb", "full", cid + ".txt")
                if os.path.exists(fp):
                    try:
                        body = open(fp, encoding="utf-8", errors="ignore").read()
                    except Exception:
                        body = ""
            good, n_art, n_char = law_quality(body)
            if good:
                ok_n += 1
            else:
                rows.append({"title": (r.get("title") or "")[:70], "level": r.get("content_type"),
                             "url": (r.get("url") or "")[:96], "arts": n_art, "chars": n_char,
                             "in_map": any(norm_title(k) in norm_title(r.get("title") or "") for k in sources)})
        total = ok_n + len(rows)
        print("[scan] 法规类条目 %d 条：原文达标 %d（%.1f%%），**缺全文 %d**"
              % (total, ok_n, 100.0 * ok_n / max(1, total), len(rows)))
        print("\n[scan] 缺全文清单（按层级）：")
        from collections import Counter
        for k, v in Counter(x["level"] for x in rows).most_common():
            print("   %-12s %d" % (k, v))
        print("\n[scan] 明细（前 30，★=映射表已有权威出处可修）：")
        for x in rows[:30]:
            print("   %s [%s] %s" % ("★" if x["in_map"] else " ", x["level"], x["title"]))
            print("        条文 %d / %d 字  %s" % (x["arts"], x["chars"], x["url"]))
        if a.report:
            json.dump({"total": total, "ok": ok_n, "missing": rows},
                      open(a.report, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print("\n[scan] 报告 -> %s" % a.report)
        if not a.apply:
            return

    # ---------- 模式二：按映射抓取回填 ----------
    print("\n[law] 按映射表抓取（%d 条）..." % len(sources))
    idx = {}
    for r in kb:
        idx.setdefault(norm_title(r.get("title")), r)
    fixed = failed = 0
    for name, cfg in sources.items():
        if a.limit and fixed >= a.limit:
            break
        url = cfg.get("url") or ""
        if not url:
            continue
        # 匹配 kb 条目（标题包含或相等）
        n = norm_title(name)
        rec = idx.get(n)
        if rec is None:
            for r in kb:
                rt = norm_title(r.get("title"))
                if n and (n in rt or rt in n) and len(rt) >= 6:
                    rec = r
                    break
        if rec is None:
            print("   ⊘ 库中未找到：%s" % name)
            continue
        print("   → %s" % (rec.get("title") or "")[:44])
        body = ""
        if url.lower().endswith(".docx"):
            body = fetch_docx(url)
        elif url.lower().endswith(".pdf"):
            body = fetch_pdf_text(url)
        if not body:
            body = to_text(fetch(url))
        good, n_art, n_char = law_quality(body)
        if good:
            rec["url"] = url
            rec["content"] = body[:200000]
            rec["content_fetched"] = True
            rec["content_kind"] = "law_fulltext"
            rec["origin_status"] = "原文已核"
            rec["origin_source"] = cfg.get("publisher") or ""
            rec["source"] = cfg.get("publisher") or rec.get("source")
            rec["note"] = ((rec.get("note") or "").split("；原文")[0]
                           + "；原文全文已入库（%d 字 / %d 条），出处：%s"
                           % (n_char, n_art, cfg.get("publisher") or url))
            fixed += 1
            print("      ✔ 全文入库：%d 字 / %d 条" % (n_char, n_art))
        else:
            rec["origin_status"] = "暂未收录"
            rec["gate_reason"] = "目标页非法规全文（条文 %d / %d 字）" % (n_art, n_char)
            rec["note"] = ((rec.get("note") or "").split("；原文")[0]
                           + "；暂未收集到原文全文（目标页为简介/通知类，非法规全文），待补")
            failed += 1
            print("      ✗ 校验未通过（条文 %d / %d 字）→ 标记待补" % (n_art, n_char))
        time.sleep(0.6)

    print("\n[law] 完成：成功 %d，未通过 %d" % (fixed, failed))
    if a.apply:
        json.dump(kb, open(a.kb, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("[law] 已写回 %s" % a.kb)


if __name__ == "__main__":
    main()
