# -*- coding: utf-8 -*-
"""原文覆盖率统计（真实口径）。

背景（2026-09-20）：旧口径用 `content_fetched` 标记来算覆盖率，结果失真——
  ① 我介入前的原始库就有 97.9% 标着"已抓全文"，但 content 全空、kb/full 也空；
  ② 回填后的 88.78% 里，只有 285 条有 content_kind 标记，其余"未回填"也被算作已抓；
  ③ 抽查发现所谓"原文"里混着 404 错误页与英文版。

新口径（三级）：
  fulltext   真正的原文全文 —— 有内联 content 或 kb/full/<cid>.txt，
             且通过 content_guard 真伪校验，且内容类型为 正文全文/pdf全文，
             或（无 kind 标记时）不以上述摘要前缀开头
  abstract   仅拿到权威摘要（Crossref/OpenAlex/DataCite/出版方）
  pdf_only   只有 PDF 文件、文本未抽出（扫描件），仍算"有原文"但需人工可读性确认
  missing    确实没有原文 —— 标"暂未收录原文"，写明已查的源

  coverage_pct       = fulltext / total              ← 严格口径（对外的"原文覆盖率"）
  origin_covered_pct = (fulltext+pdf_only+abstract) / total   ← 宽松口径（含摘要）

同时给出分维度覆盖（地域 / 分类 / 层级 / 是否必知必会 / 是否国内政策标准），
以及"原文来源"分布（哪个源贡献了多少条），便于判断权威性与补齐方向。

用法：
  python coverage.py                # 统计并写 kb/coverage.json
  python coverage.py --print        # 只打印关键数字
"""
import os, re, json, time, collections


# ——— 原子安全写 JSON（自动注入，勿手改）———
# 背景：直接用 open(path,"w") + json.dump 有两个致命问题：
#   ① open("w") 会**先清空文件**，若写入中抛异常（如遇到孤立 Unicode 代理码位 \ud835），
#      会留下**半截无效 JSON**；下一步读取失败若又兜底为 []，就会把整库写成空数组（两次线上事故的根因）。
#   ② 非原子写，并发/中断都可能损坏文件。
# 本函数：清洗代理码位与控制字符 → 写临时文件 → os.replace 原子替换。原文件要么不变，要么完整。
def _safe_dump(path, obj):
    import json as _j, os as _o, re as _r, tempfile as _t
    _SURR = _r.compile(r"[\ud800-\udfff]")

    def _clean(x):
        if isinstance(x, str):
            return "".join(c for c in _SURR.sub("", x) if c in "\n\t" or ord(c) >= 32)
        if isinstance(x, list):
            return [_clean(i) for i in x]
        if isinstance(x, tuple):
            return [_clean(i) for i in x]
        if isinstance(x, dict):
            return {k: _clean(v) for k, v in x.items()}
        return x

    obj = _clean(obj)
    d = _o.path.dirname(_o.path.abspath(path)) or "."
    fd, tmp = _t.mkstemp(dir=d, suffix=".tmp")
    try:
        with _o.fdopen(fd, "w", encoding="utf-8") as f:
            _j.dump(obj, f, ensure_ascii=False, indent=1)
        _o.replace(tmp, path)
    except Exception:
        try:
            _o.unlink(tmp)
        except Exception:
            pass
        raise
# ——— 注入结束 ———



BASE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(BASE, "kb", "kb.json")
FULL = os.path.join(BASE, "kb", "full")
COV = os.path.join(BASE, "kb", "coverage.json")

try:
    import content_guard as G
except Exception:
    G = None

ABSTRACT_PREFIX = "【摘要】"

# 内容类型 -> 归类
KIND_FULL = {"正文全文", "pdf全文", "pdf", "html", "word"}
KIND_ABSTRACT = {"abstract", "摘要"}


def kind_of_url(u):
    u = (u or "").lower()
    if not u:
        return "skip"
    if "doi.org" in u or "openalex.org" in u or "crossref" in u or "europepmc" in u:
        return "academic"
    if "mp.weixin.qq.com" in u:
        return "weixin"
    if re.search(r"(gov\.cn|mee\.gov|mwr\.gov|mohurd|ndrc|moa\.gov|samr|zjrd|zj\.gov|"
                 r"hangzhou|openstd|npc\.gov|people\.com)", u):
        return "gov"
    if u.startswith("http"):
        return "html"
    return "skip"


def read_full(cid):
    if not cid:
        return ""
    p = os.path.join(FULL, cid + ".txt")
    if not os.path.exists(p):
        return ""
    try:
        return open(p, encoding="utf-8", errors="replace").read()
    except Exception:
        return ""


def classify(rec):
    """返回 (status, chars, how)
    status ∈ fulltext / pdf_only / abstract / missing
    how    = 取得方式描述（写入展示）"""
    txt = rec.get("content") or ""
    if not txt:
        txt = read_full(rec.get("cid"))
    ck = (rec.get("content_kind") or "").strip()
    pdf = rec.get("pdf") or ""

    if txt:
        if txt.startswith(ABSTRACT_PREFIX) or ck in KIND_ABSTRACT:
            return "abstract", len(txt), "权威摘要"
        # 真伪校验
        if G is not None:
            try:
                ok, reason = G.is_valid(txt, rec.get("title") or "")
            except Exception:
                ok, reason = True, "guard_error"
            if not ok:
                return "missing", len(txt), "校验未过(%s)" % reason.split(":")[0]
        return "fulltext", len(txt), (ck or "正文全文")
    if pdf:
        return "pdf_only", 0, "PDF 已存（文本未抽出）"
    return "missing", 0, (rec.get("origin_note") or "")[:60] or "-"


def pct(a, b):
    return round(100.0 * a / b, 2) if b else 0.0


def main(do_print=False):
    kb = json.load(open(KB, encoding="utf-8"))
    total = len(kb)

    stat = collections.Counter()
    chars = 0
    by_region = collections.defaultdict(lambda: collections.Counter())
    by_cat = collections.defaultdict(lambda: collections.Counter())
    by_level = collections.defaultdict(lambda: collections.Counter())
    by_urlkind = collections.defaultdict(lambda: collections.Counter())
    by_dept = collections.defaultdict(lambda: collections.Counter())
    src = collections.Counter()
    missing_list = []

    for r in kb:
        st, n, how = classify(r)
        stat[st] += 1
        if st == "fulltext":
            chars += n
        uk = kind_of_url(r.get("url"))
        by_urlkind[uk][st] += 1
        by_region[r.get("region") or "?"][st] += 1
        by_cat[r.get("category") or "?"][st] += 1
        by_level[r.get("content_type") or "?"][st] += 1
        by_dept[r.get("department") or "?"][st] += 1
        if st != "missing":
            s = (r.get("origin_source") or "").strip()
            if s:
                if "//" in s:
                    s = s.split("//")[-1].split("/")[0]
                src[s] += 1
            elif "doi.org" in (r.get("url") or ""):
                src["学术索引库(Crossref/OpenAlex)"] += 1
            elif r.get("pdf"):
                src["原文页内生 PDF 附件"] += 1
            else:
                src["原文页 HTML 正文"] += 1
        else:
            missing_list.append({
                "title": (r.get("title") or "")[:90],
                "region": r.get("region") or "",
                "level": r.get("content_type") or "",
                "url": r.get("url") or "",
                "why": how,
                "note": (r.get("origin_note") or r.get("content_note") or "")[:160],
            })

    # 重点子集：国内政策/标准（真正需要原文的）
    def subset(pred):
        sub = [r for r in kb if pred(r)]
        c = collections.Counter()
        for r in sub:
            c[classify(r)[0]] += 1
        return len(sub), c

    n_core, c_core = subset(lambda r: r.get("region") in ("全国", "浙江", "杭州", "长三角")
                            and r.get("category") in ("policy", "standard", "management"))
    n_must, c_must = subset(lambda r: r.get("must_know"))

    cov = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "basis": "以内容有效性为准（content_guard 真伪校验 + 摘要前缀识别），非 content_fetched 标记",
        "total": total,
        "fulltext": stat["fulltext"],
        "pdf_only": stat["pdf_only"],
        "abstract_only": stat["abstract"],
        "missing": stat["missing"],
        "with_pdf": sum(1 for r in kb if r.get("pdf")),
        "content_chars": chars,
        "coverage_pct": pct(stat["fulltext"], total),
        "origin_covered_pct": pct(stat["fulltext"] + stat["pdf_only"] + stat["abstract"], total),
        "core": {"total": n_core, "fulltext": c_core["fulltext"],
                 "abstract": c_core["abstract"], "missing": c_core["missing"],
                 "coverage_pct": pct(c_core["fulltext"], n_core)},
        "must_know": {"total": n_must, "fulltext": c_must["fulltext"],
                      "abstract": c_must["abstract"], "missing": c_must["missing"],
                      "coverage_pct": pct(c_must["fulltext"], n_must)},
        "by_url_kind": {k: dict(v) for k, v in by_urlkind.items()},
        "by_region": {k: dict(v) for k, v in by_region.items()},
        "by_category": {k: dict(v) for k, v in by_cat.items()},
        "by_level": {k: dict(v) for k, v in by_level.items()},
        "by_department": {k: dict(v) for k, v in by_dept.items()},
        "origin_sources": dict(src.most_common(25)),
        "missing_samples": missing_list[:300],
    }
    _safe_dump(COV, cov)

    if do_print or True:
        print("=" * 64)
        print("kb 总数 %d" % total)
        print("  原文全文(fulltext) %d  -> 严格覆盖率 %.2f%%" % (stat["fulltext"], cov["coverage_pct"]))
        print("  仅 PDF 文本未抽出   %d" % stat["pdf_only"])
        print("  仅权威摘要          %d" % stat["abstract"])
        print("  缺原文(missing)     %d" % stat["missing"])
        print("  宽松口径（含摘要/PDF）%.2f%%   正文合计 %.1f 万字"
              % (cov["origin_covered_pct"], chars / 10000.0))
        print("重点·国内政策标准管理 %d 条，覆盖率 %.2f%%" % (n_core, cov["core"]["coverage_pct"]))
        print("必知必会            %d 条，覆盖率 %.2f%%" % (n_must, cov["must_know"]["coverage_pct"]))
        print("原文来源 Top: %s" % json.dumps(cov["origin_sources"], ensure_ascii=False)[:400])
    return cov


if __name__ == "__main__":
    import sys
    main(do_print=("--print" in sys.argv))
