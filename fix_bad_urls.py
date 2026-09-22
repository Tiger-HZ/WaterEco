# -*- coding: utf-8 -*-
"""坏链修正器：把知识库里"不可访问 / 被编造 / 语种不对 / 只是首页"的原文链接换成真实来源。

背景（2026-09-20 排查「必知必会」91 条缺原文）发现四类坏链：
  ① **编造的标准链接**：`openstd.samr.gov.cn/bzgk/gb/newGbInfo?hcno=<编造十六进制>`
     —— 早期生成时为了让链接"看起来可访问"而拼的 hcno。实测该站是 SPA，
       连编造值也返回 HTTP 200 空壳页（长度固定 18559），既抓不到原文也违背准确性。
  ② **繁体代理链接**：`big5.www.gov.cn/gate/big5/www.gov.cn/...`、`cpc.people.com.cn/BIG5/...`
     —— 抓到的是繁体版，会被 content_guard 判为异常。
  ③ **英文版链接**：`npc.gov.cn/englishnpc/...`（长江保护法）。
  ④ **指向站点首页的占位链接**（`https://www.mee.gov.cn/`、`https://www.hzrd.gov.cn/`）。

修正策略（按优先级）：
  P1 `std_index.json`（生态环境部标准库 146 个真实详情页）替换：
     先按**标准号**精确匹配；再按**标准名称**匹配（本批记录 doc_no 多为空，
     标题里也没有标准号，只能靠名称）。命中则一并补全 doc_no。
  P2 繁体/代理前缀还原为简体主站地址。
  P3 英文版按已知对应关系换成简体正文页。
  P4 仍无解 -> 换成**按 region 分流的官方落点**（国家/浙江/杭州各自的官方检索入口），
     置 `origin_status='暂未收录'` 并在 note 写明"原文链接待补：可在 XX 按名称检索全文"，
     **绝不留下看似可用的假链**。

用法：
  python fix_bad_urls.py --kb <kb.json> [--std <std_index.json>] [--dry]
"""
import os, re, sys, json, time, urllib.request, urllib.error, ssl


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



CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

FAKE_HCNO = re.compile(r"openstd\.samr\.gov\.cn/bzgk/gb/newGbInfo\?hcno=([0-9A-F]{20,})", re.I)
BIG5_GATE = re.compile(r"^https?://big5\.www\.gov\.cn/gate/big5/(www\.gov\.cn/.*)$", re.I)
PROXY_PREFIX = re.compile(r"^https?://[a-z0-9.]*big5[a-z0-9.]*/", re.I)
HOME_ONLY = re.compile(r"^https?://[^/]+/?$")

LANG_FIX = {
    "npc.gov.cn/englishnpc": {"长江保护法": "http://www.npc.gov.cn/npc/c2/c30834/202012/t20201227_309594.html"},
}

# 人工核实过的"标准 -> 真实原文/权威来源"对照表（2026-09-20 逐条检索确认）
# 这些标准不在生态环境部「水环境保护」标准栏目内（多为住建部/卫健委归口），
# 因此无法由 std_index 自动匹配，必须显式指定，否则宁可标注"待补"也不留假链。
KNOWN_ORIGIN = [
    # (标题关键词, 真实URL, 来源说明)
    ("合成树脂工业污染物排放标准",
     "https://www.mee.gov.cn/ywgz/fgbz/bz/bzwb/dqhjbh/dqgdwrywrwpfbz/201505/t20150505_300691.shtml",
     "生态环境部标准页（含 GB 31572-2015 正文 PDF）"),
    ("农用污泥污染物控制标准",
     "https://std.samr.gov.cn/gb/search/gbDetailed?id=71F772D82C13D3A7E05397BE0A0AB82A",
     "全国标准信息公共服务平台（GB 4284-2018，住房城乡建设部归口）"),
    ("城市杂用水水质",
     "https://openstd.samr.gov.cn/bzgk/gb/newGbInfo?hcno=9825347B5A474612C6C3FE86323428C0",
     "国家标准全文公开系统（GB/T 18920-2020，真实 hcno，含全文）"),
    ("景观环境用水水质",
     "https://std.samr.gov.cn/gb/search/gbDetailed?id=8AA1F5D36E31A8DBE05397BE0A0AB19B",
     "全国标准信息公共服务平台（GB/T 18921-2019）"),
    ("污水排入城镇下水道水质标准",
     "https://std.samr.gov.cn/gb/search/gbDetailed?id=71F772D80A3AD3A7E05397BE0A0AB82A",
     "全国标准信息公共服务平台（GB/T 31962-2015）"),
    ("生活饮用水卫生标准",
     "https://iehs.chinacdc.cn/fgbz/jscl/202210/P020221028629144460624.pdf",
     "中国疾控中心环境与健康相关产品安全所（GB 5749-2022 官方全文 PDF）"),
    ("地下水质量标准",
     "https://slt.ah.gov.cn/ssltOldFiles/attachment/58edb317ceab06952d69cc36/201809/20180927151303634_3HQM2JX8.pdf",
     "安徽省水利厅（GB/T 14848-2017 全文 PDF，省级官方公开）"),
]

# P4：按 region 分流的官方落点（真实页面）
FALLBACK = {
    "全国": ("https://www.mee.gov.cn/ywgz/fgbz/bz/bzwb/shjbh/",
             "生态环境部「水环境保护」标准栏目"),
    "浙江": ("https://www.zj.gov.cn/col/col1229019366/index.html",
             "浙江省政府政策文件栏目"),
    "杭州": ("https://www.hangzhou.gov.cn/col/col1229063388/index.html",
             "杭州市政府规章与规范性文件栏目"),
}
DEFAULT_FB = ("https://www.mee.gov.cn/ywgz/fgbz/bz/bzwb/shjbh/", "生态环境部「水环境保护」标准栏目")

_CODE_STRIP = re.compile(r"\b(GB/T|GB|HJ/T|HJ|SL/T|SL|DB\d{2}/T?|CJ|JT|NY|NY/T)\s*[\d.]+(?:\s*[—\-–]\s*\d{4})?", re.I)


def fetch(url, timeout=18, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                code = r.status
                raw = r.read(200000)
            for enc in ("utf-8", "gb18030"):
                try:
                    return code, raw.decode(enc)
                except Exception:
                    pass
            return code, raw.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, ""
        except Exception:
            if i == tries - 1:
                return 0, ""
            time.sleep(1.2)
    return 0, ""


def reachable(url):
    return fetch(url)[0] == 200


def norm_name(s):
    """标准名称归一化：去括号注释、去标准号、去标点，仅留中英文数字。"""
    s = re.sub(r"[（(].{0,30}?[)）]", "", s or "")
    s = _CODE_STRIP.sub(" ", s)
    s = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", "", s)
    return s.lower()


def std_code_of(rec):
    for src in (rec.get("doc_no") or "", rec.get("title") or ""):
        m = re.search(r"\b(GB/T|GB|HJ/T|HJ|SL/T|SL)\s*(\d{2,6})(?:\s*[-—–]\s*(\d{4}))?", src, re.I)
        if m:
            return m.group(1).upper().replace(" ", "") + " " + m.group(2), (m.group(3) or "")
    return "", ""


def main():
    args = sys.argv[1:]
    kb_path = args[args.index("--kb") + 1] if "--kb" in args else None
    std_path = args[args.index("--std") + 1] if "--std" in args else None
    DRY = "--dry" in args
    if not kb_path:
        print("用法: python fix_bad_urls.py --kb <kb.json> [--std std_index.json] [--dry]")
        return
    kb = json.load(open(kb_path, encoding="utf-8"))
    std_index = json.load(open(std_path, encoding="utf-8")) if std_path and os.path.exists(std_path) else []

    by_code, by_name = {}, {}
    for it in std_index:
        c = (it.get("code") or "").upper().replace("-", " ").strip()
        m = re.match(r"(GB/T|GB|HJ/T|HJ|SL/T|SL)\s*(\d+)", c)
        if m:
            by_code.setdefault(m.group(1) + " " + m.group(2), []).append(it)
        n = norm_name(it.get("name"))
        if len(n) >= 4:
            by_name.setdefault(n, it)

    def match_std(rec):
        code, yr = std_code_of(rec)
        if code and code in by_code:
            return by_code[code][0]
        t = norm_name(rec.get("title"))
        if len(t) < 4:
            return None
        if t in by_name:
            return by_name[t]
        # 名称包含匹配：取"名字最短（最贴近查询名）"的那个，避免被超长名带偏
        cands = [it for k, it in by_name.items() if (t in k or k in t) and len(t) >= 5]
        if cands:
            cands.sort(key=lambda x: len(norm_name(x.get("name"))))
            return cands[0]
        return None

    stat = {"known": 0, "std_code": 0, "std_name": 0, "big5_fixed": 0, "lang_fixed": 0,
            "fallback": 0, "home_fixed": 0, "ok": 0, "checked": 0}
    report = []
    for r in kb:
        u = (r.get("url") or "").strip()
        if not u.startswith("http"):
            continue

        r0 = None  # noqa
        # ——— P0 人工核实对照表（最高优先级）———
        for kw, tgt, src in KNOWN_ORIGIN:
            if kw in (r.get("title") or ""):
                if u.rstrip("/") != tgt.rstrip("/"):
                    old = u
                    r["url"] = tgt
                    if not (r.get("source") or "").strip() or "未知" in (r.get("source") or ""):
                        r["source"] = src
                    r["note"] = (r.get("note") or "").rstrip("；; ") + "；原文来源已更正：%s" % src
                    r["content_fetched"] = False
                    r.pop("content_reject", None)
                    stat["known"] += 1
                    report.append(("known", r.get("title"), old, tgt))
                    u = tgt
                break

        # ——— P2 繁体/代理前缀还原 ———
        m = BIG5_GATE.match(u)
        if m or ("big5" in u.lower() and "gov.cn" in u.lower()):
            nu = BIG5_GATE.sub(lambda mm: "https://" + mm.group(1), u) if m else PROXY_PREFIX.sub("https://", u)
            if nu != u:
                r["url"] = nu
                r["note"] = (r.get("note") or "").rstrip("；; ") + "；原为繁体/代理链接，已还原为简体主站地址"
                stat["big5_fixed"] += 1
                report.append(("big5", r.get("title"), u, nu))
                u = nu

        # ——— P3 英文版 ———
        for pat, mapping in LANG_FIX.items():
            if pat in u:
                for kw, tgt in mapping.items():
                    if kw in (r.get("title") or ""):
                        r["url"] = tgt
                        r["note"] = (r.get("note") or "").rstrip("；; ") + "；原链接为英文版，已换为简体正文页"
                        stat["lang_fixed"] += 1
                        report.append(("lang", r.get("title"), u, tgt))
                        u = tgt
                        break
                break

        # ——— P1 生态环境部标准库（先标准号，再标准名称）———
        it = match_std(r)
        if it:
            need = ("openstd.samr.gov.cn" in u) or ("std.samr.gov.cn" in u) or ("mee.gov.cn" not in u)
            # std_index 的 URL 全部来自生态环境部栏目页（真实），只做形态校验，避免网络抖动误判
            if need and re.search(r"mee\.gov\.cn/.+/t\d{8}_\d+\.shtml$", it["url"]):
                old = u
                r["url"] = it["url"]
                r["content_fetched"] = False
                r.pop("content_reject", None)
                # 顺手补全 doc_no（本批记录普遍缺失）
                if not (r.get("doc_no") or "").strip() and it.get("code"):
                    r["doc_no"] = it["code"]
                if it.get("impl"):
                    r["effective_date"] = it["impl"]
                r["note"] = (r.get("note") or "").rstrip("；; ") + "；原文链接已更正为生态环境部标准库详情页"
                code_now, _ = std_code_of(r)
                stat["std_code" if code_now in by_code else "std_name"] += 1
                report.append(("std", r.get("title"), old, it["url"]))
                u = it["url"]

        # ——— P4a 首页占位 -> 按 region 分流落点 ———
        if HOME_ONLY.match(u):
            fb, label = FALLBACK.get(r.get("region") or "", DEFAULT_FB)
            r["url"] = fb
            r["origin_status"] = "暂未收录"
            r["content_fetched"] = False
            r["note"] = ((r.get("note") or "").rstrip("；; ")
                         + "；原文链接待补：原链接仅为站点首页，可在%s按名称检索全文" % label)
            stat["home_fixed"] += 1
            report.append(("home", r.get("title"), u, fb))
            u = fb

        # ——— P4b 编造 hcno 且确属空壳页 -> 落点 + 标注 ———
        if FAKE_HCNO.search(u):
            stat["checked"] += 1
            c, h = fetch(u)
            shell = (c != 200) or (h and len(h) == 18559)   # 18559 = 该站无效 hcno 的空壳页长度
            if shell:
                fb, label = FALLBACK.get(r.get("region") or "", DEFAULT_FB)
                r["url"] = fb
                r["origin_status"] = "暂未收录"
                r["content_fetched"] = False
                r["note"] = ((r.get("note") or "").rstrip("；; ")
                             + "；原文链接待补：原链接在国家标准全文公开系统中无对应内容，"
                             + "可在%s按标准名称检索" % label)
                stat["fallback"] += 1
                report.append(("fake", r.get("title"), u, fb))
            else:
                stat["ok"] += 1

    print("修正统计:", stat)
    print("\n明细（前 50）：")
    for kind, title, old, new in report[:50]:
        print("  [%-5s] %s" % (kind, (title or "")[:44]))
        print("        %s" % old[:96])
        print("     -> %s" % new[:96])

    # ——— 仍留在"不可靠来源"的条目：列出，供定向人工检索 ———
    manual = []
    for r in kb:
        u = r.get("url") or ""
        if "openstd.samr.gov.cn" in u or re.search(r"std\.samr\.gov\.cn/gb/search/gbDetailed", u):
            manual.append({"title": r.get("title"), "url": u, "region": r.get("region"),
                           "doc_no": r.get("doc_no")})
    print("\n仍需定向检索的标准（%d 条）：" % len(manual))
    for m in manual:
        print("   - %s | %s" % ((m["title"] or "")[:52], m["url"][:80]))
    if not DRY:
        _safe_dump(kb_path, kb)
        out = os.path.join(os.path.dirname(os.path.abspath(kb_path)), "url_fix_report.json")
        json.dump([{"kind": k, "title": t, "old": o, "new": n} for k, t, o, n in report],
                  open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("\n已写回 %s\n报告 %s" % (kb_path, out))
    else:
        print("\n(dry-run，未写回)")


if __name__ == "__main__":
    main()
