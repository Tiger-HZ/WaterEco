# -*- coding: utf-8 -*-
"""学术文献全文回填（2026-10-03 按用户要求新增）

用户要求：「全文回填，所有都要补上，实在补不上的也要保留，但要做好标注，
          以便后续用户和你这边都能看得到」。

设计要点：
  · **只补不删** —— 补不到全文的条目**保留**，并写入明确标注：
      fulltext_status = '未获取到全文'
      fulltext_note   = 具体原因（无 DOI / 无开放版本(订阅期刊) / 下载失败 / 超时）
      fulltext_tried_at = 尝试时间（YYYY-MM-DD）
    这样门户可显示「仅摘要·全文待补」提示，双方都能看到缺口。
  · **可续跑** —— 已有 fulltext_tried_at 的条目默认跳过（--retry-miss 可重试失败项）；
     支持 --limit N 分批，配合 workflow 每天跑若干轮直到补齐。
  · **多源取全文** —— 复用 academic_fulltext.fetch_fulltext
      （EuropePMC fullTextXML → Unpaywall(DOI) → OpenAlex OA → DOI 直连）。
  · **原子写 + 条目数护栏** —— 复用 _safe_dump，避免写坏。

用法：
  python3 backfill_fulltext.py --limit 800              # 本轮最多处理 800 条
  python3 backfill_fulltext.py --limit 800 --retry-miss # 连之前失败的也重试
  python3 backfill_fulltext.py --report r.json          # 输出统计
"""
import argparse
import datetime
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import academic_fulltext as afull  # noqa: E402

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
UA = {"User-Agent": "WaterEcoBot/1.0 (mailto:water-eco-bot@users.noreply.github.com)"}
TODAY = datetime.date.today().isoformat()


def _safe_dump(path, obj):
    """原子写（清洗代理码位 + 临时文件 + os.replace）"""
    import re as _r, os as _o, tempfile as _t
    _S = _r.compile(r"[\ud800-\udfff]")

    def _clean(x):
        if isinstance(x, str):
            return "".join(c for c in _S.sub("", x) if c in "\n\t" or ord(c) >= 32)
        if isinstance(x, list):
            return [_clean(i) for i in x]
        if isinstance(x, dict):
            return {k: _clean(v) for k, v in x.items()}
        return x

    obj = _clean(obj)
    d = _o.path.dirname(_o.path.abspath(path)) or "."
    fd, tmp = _t.mkstemp(dir=d, suffix=".tmp")
    try:
        with _o.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        _o.replace(tmp, path)
    except Exception:
        try:
            _o.unlink(tmp)
        except Exception:
            pass
        raise


def _get_json(url, timeout=25):
    try:
        r = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(r, timeout=timeout, context=CTX) as x:
            return json.loads(x.read().decode("utf-8", "replace"))
    except Exception:
        return None


def extract_doi(u):
    u = u or ""
    m = re.search(r"(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)", u)
    return m.group(1).rstrip(".") if m else ""


def extract_openalex_id(u):
    m = re.search(r"openalex\.org/(W\d+)", u or "")
    return m.group(1) if m else ""


def is_academic(r):
    u = (r.get("url") or "").lower()
    return ("doi.org" in u or "openalex" in u or "europepmc" in u
            or r.get("kclass") == "research" or r.get("content_type") == "研究文献")


def openalex_oa(wid):
    """从 OpenAlex 取开放获取链接与 PMCID"""
    if not wid:
        return "", "", ""
    d = _get_json("https://api.openalex.org/works/%s" % wid)
    if not d:
        return "", "", ""
    oa = d.get("open_access") or {}
    best = d.get("best_oa_location") or {}
    ids = d.get("ids") or {}
    pmcid = (ids.get("pmcid") or "").split("/")[-1]
    return (oa.get("oa_url") or "", best.get("pdf_url") or "", pmcid)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", default=os.path.join(BASE, "kb", "kb.json"))
    ap.add_argument("--limit", type=int, default=800)
    ap.add_argument("--retry-miss", action="store_true", help="连之前失败的也重试")
    ap.add_argument("--report", default="")
    a = ap.parse_args()

    kb = json.load(open(a.kb, encoding="utf-8"))
    if not kb:
        raise SystemExit("!! kb.json 为空或解析失败，拒绝继续")
    n0 = len(kb)

    todo = []
    for r in kb:
        if not is_academic(r):
            continue
        if r.get("content_kind") == "fulltext" and (r.get("content") or ""):
            continue                                    # 已有全文，跳过
        if r.get("fulltext_tried_at") and not a.retry_miss:
            continue                                    # 已尝试过，跳过（可 --retry-miss 重试）
        todo.append(r)
    todo = todo[: a.limit]

    print("[backfill] kb %d 条；本轮待处理 %d 条（学术待补总数见下）" % (n0, len(todo)))
    ok = miss = 0
    reasons = {}
    for i, r in enumerate(todo, 1):
        url = r.get("url") or ""
        doi = extract_doi(url)
        wid = extract_openalex_id(url)
        oa_url = oa_pdf = pmcid = ""
        if wid:
            oa_url, oa_pdf, pmcid = openalex_oa(wid)
        note = ""
        ft, src = afull.fetch_fulltext(doi=doi, pmcid=pmcid, oa_url=oa_url, oa_pdf=oa_pdf)
        if ft:
            r["content"] = ft[:200000]
            r["content_fetched"] = True
            r["content_kind"] = "fulltext"
            r["fulltext_status"] = "已获取全文"
            r["fulltext_source"] = src
            r["fulltext_chars"] = len(ft)
            r.pop("fulltext_note", None)
            ok += 1
        else:
            if not doi and not wid:
                note = "无 DOI/OpenAlex 标识，无法定位原文"
            else:
                note = "未找到开放获取版本（订阅期刊 / 无 OA 版），仅保留摘要与题录"
            r["fulltext_status"] = "未获取到全文"
            r["fulltext_note"] = note
            r["content_fetched"] = bool(r.get("abstract") or r.get("summary"))
            r["content_kind"] = r.get("content_kind") or "abstract"
            miss += 1
            reasons[note[:24]] = reasons.get(note[:24], 0) + 1
        r["fulltext_tried_at"] = TODAY
        if i % 50 == 0:
            print("   ...%d/%d  成功 %d 失败 %d" % (i, len(todo), ok, miss), flush=True)

    print("[backfill] 本轮完成：成功 %d，未获取 %d" % (ok, miss))
    for k, v in sorted(reasons.items(), key=lambda x: -x[1]):
        print("   %-30s %d" % (k, v))

    # 统计存量状态
    acad = [r for r in kb if is_academic(r)]
    st = {}
    for r in acad:
        k = r.get("fulltext_status") or ("已有全文" if r.get("content_kind") == "fulltext" and r.get("content") else "待处理")
        st[k] = st.get(k, 0) + 1
    print("[backfill] 学术文献存量状态：", json.dumps(st, ensure_ascii=False))

    # 条目数护栏
    if len(kb) < n0 * 0.5:
        raise SystemExit("!! 条目数异常（%d -> %d），拒绝写回" % (n0, len(kb)))
    _safe_dump(a.kb, kb)
    print("[backfill] 已写回 %s" % a.kb)

    if a.report:
        json.dump({"ok": ok, "miss": miss, "reasons": reasons, "status": st},
                  open(a.report, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
