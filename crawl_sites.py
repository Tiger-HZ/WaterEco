# -*- coding: utf-8 -*-
"""多站点栏目采集器（crawl_sites）。

为什么需要它：`crawl_gov` 只能查"国务院政策文件库"，覆盖面窄、重复率高，
单次采集到不了 2000 条。本采集器直接**逐栏目翻页**抓各部委/省级生态环境部门的
政策文件、部令、规范性文件与司局动态，是新条目的主要来源。

设计要点：
 - 栏目配置放在 `sites.json`（可随时增删，无需改代码）；
 - 通用列表解析：抽 <a href> + 锚文本，按"像不像一篇公文"过滤（长度、路径含日期、
   锚文本含水生态关键词或栏目本身是水务专属）；
 - 每篇详情页：抽 HTML 正文 + 抓页面内 PDF 附件（复用 pdfutil）；
 - 相关性严格过滤（复用 crawl_gov 的 REL_STRONG 思路，标题命中 or 摘要≥2 命中）；
 - 与 kb + inbox 去重（URL + 标题指纹），增量写 kb/inbox.json。

用法：
  python crawl_sites.py                      # 全部栏目
  MAX_PAGES=3 python crawl_sites.py          # 限制每栏目页数
  ONLY="水生态环境司" python crawl_sites.py    # 只跑匹配的栏目
"""
import json, os, re, sys, time
from urllib.parse import urljoin, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdfutil
import crawl_common as C
from fetch_fulltext import extract_main_text

BASE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(BASE, "kb", "kb.json")
INBOX = os.path.join(BASE, "kb", "inbox.json")
SITES = os.path.join(BASE, "sites.json")

MAX_PAGES = int(os.environ.get("MAX_PAGES", "99"))
ONLY = os.environ.get("ONLY", "").strip()
FETCH_PDF = os.environ.get("FETCH_PDF", "1") == "1"

STRONG = ["水生态", "水环境", "地表水", "饮用水源", "饮用水水源", "水源地", "河湖", "流域", "水体",
          "水质", "排污口", "入河排污口", "黑臭", "断面", "蓝藻", "富营养化", "水华", "美丽河湖",
          "幸福河湖", "五水共治", "河湖长", "河长", "生态补偿", "再生水", "污水处理", "水污染物",
          "水功能区", "生态流量", "千岛湖", "钱塘江", "苕溪", "运河", "供水", "节水", "水安全",
          "湿地", "缓冲带", "地下水", "排水", "海绵城市", "水资源", "水生态修复", "尾水", "农田退水",
          "水域", "河道", "湖泊", "水污染", "水十条", "碧水", "海洋生态", "近岸海域"]
WEAK_STRONG = len(STRONG)

# 从 URL 提取发布日期（t20260817_xxx / /202608/ / W020260817…）
URL_DATE_RE = re.compile(r"(?:t|W0)(\d{4})(\d{2})(\d{2})")


def date_from_url(u):
    m = URL_DATE_RE.search(u or "")
    if m:
        y, mo, d = m.group(1), m.group(2), m.group(3)
        if 2000 <= int(y) <= 2100 and 1 <= int(mo) <= 12 and 1 <= int(d) <= 31:
            return "%s-%s-%s" % (y, mo, d)
    m = re.search(r"/(20\d{2})(\d{2})/", u or "")
    if m:
        return "%s-%s-01" % (m.group(1), m.group(2))
    # Hanweb CMS：/col/col1692364/art/2026/art_<hash>.html -> 仅知年份
    m = re.search(r"/art/(20\d{2})/", u or "")
    if m:
        return "%s-01-01" % m.group(1)
    return ""


DATE_IN_HREF = re.compile(r"(t\d{8}_|/20\d{2}[-/]?\d{0,2}/|content_\d{6,}|W020\d{12,})")
TAG_RE = re.compile(r"<[^>]+>")
ANCHOR_RE = re.compile(r'<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.S | re.I)
# 源码中的文章路径（兼容 JS 渲染站：如浙江省人大 /202609/t20260918_265081.shtml，
# 页面没有可用的 <a href>，但路径字符串在脚本里；亦覆盖 Hanweb CMS 的 art_<hash>.html）
ART_PATH_RE = re.compile(
    r"(/[A-Za-z0-9_\-./]*(?:t\d{8}_\d+|art_[0-9a-f]{16,}|content_\d{6,})\.s?html)", re.I)
ZH_TITLE_RE = re.compile(r"[\u4e00-\u9fa5][\u4e00-\u9fa5，、（）《》“”：；·\-—0-9A-Za-z\s]{7,60}")
NOISE_TXT = re.compile(r"^(更多|首页|上一页|下一页|尾页|返回|登录|注册|网站地图|联系我们|English|"
                       r"简体|繁体|无障碍|打印|关闭|分享|收藏|下载|查看|详情|>>|«|»)")


def water_hits(*parts):
    hay = " ".join(p or "" for p in parts).lower()
    n = sum(1 for k in STRONG if k.lower() in hay)
    return n


def clean_txt(s):
    s = TAG_RE.sub("", s or "")
    s = s.replace("&nbsp;", " ").replace("&emsp;", " ").replace("&amp;", "&")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def load(p, default):
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return default
    return default


def extract_items(html, page_url, site):
    """从栏目页抽取候选条目 [(url, title)]。"""
    out, seen = [], set()
    for m in ANCHOR_RE.finditer(html or ""):
        href, txt = m.group(1), clean_txt(m.group(2))
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        if len(txt) < 8 or len(txt) > 130 or NOISE_TXT.match(txt):
            continue
        u = urljoin(page_url, href)
        host = urlparse(u).netloc.lower()
        if site["url"].split("/")[2].split(":")[0] not in host:
            continue
        # 像一篇文章：路径含日期/文章号，或扩展名为 .htm/.shtml/.html 且不是栏目首页
        if not (DATE_IN_HREF.search(u) or re.search(r"\.(s?html?|jsp|php)$", u, re.I)):
            continue
        if u.rstrip("/") == site["url"].rstrip("/"):
            continue
        if u in seen:
            continue
        seen.add(u)
        out.append((u, txt))
    # —— 通道 B：源码文章路径（无 <a> 标签的 JS 渲染站）——
    if len(out) < 3:
        base_host = urlparse(site["url"]).netloc
        for m in ART_PATH_RE.finditer(html or ""):
            path = m.group(1)
            u = urljoin(site["url"], path)
            if urlparse(u).netloc != base_host:
                continue
            if u in seen:
                continue
            # 就近取中文标题（路径前后 300 字符窗口内最长的中文串）
            a, b = max(0, m.start() - 300), min(len(html or ""), m.end() + 300)
            win = TAG_RE.sub(" ", html[a:b])
            cands = [c.strip() for c in ZH_TITLE_RE.findall(win)]
            cands = [c for c in cands if 8 <= len(c) <= 70 and not NOISE_TXT.match(c)]
            title = max(cands, key=len) if cands else ""
            if not title:
                continue
            seen.add(u)
            out.append((u, title))
    return out


def build_record(url, title, site):
    html = pdfutil.fetch_html(url, timeout=20)
    text = ""
    pdf_rel = None
    if html:
        pdfutil.ensure_dir()
        cid = C.make_cid({"url": url, "title": title})
        if FETCH_PDF:
            for pu in pdfutil.find_pdf_links(html, url)[:2]:
                dest = os.path.join(pdfutil.PDF_DIR, cid + ".pdf")
                if pdfutil.download_binary(pu, dest):
                    t = pdfutil.extract_pdf_text(dest)
                    if len(t) >= 300:
                        text, pdf_rel = t, "kb/pdf/%s.pdf" % cid
                    break
                time.sleep(0.15)
        if not text:
            t = extract_main_text(html) or ""
            text = "\n".join(l for l in t.split("\n") if len(l) >= 4)
        # 标题兜底：用 <title> 或正文首行
        if len(title) < 10 and html:
            m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
            if m:
                tt = clean_txt(m.group(1))
                if len(tt) >= 8:
                    title = tt[:120]
    else:
        cid = C.make_cid({"url": url, "title": title})

    title = C.sanitize(title)
    text = C.sanitize(text)
    cat = C.classify_cat(title, text[:500] if text else "")
    rhay = title + " " + site["name"]
    region = site["region"]
    if "浙江" in rhay:
        region = "浙江"
    elif "杭州" in rhay:
        region = "杭州"
    rec = {
        "title": title,
        "url": url,
        "source": site["name"].split("·")[0],
        "date": date_from_url(url),
        "category": cat,
        "content_type": "政策文件",
        "department": site["dept"],
        "region": region,
        "quality": "A" if site["weight"] >= 0.9 else "B",
        "quality_src": "A" if site["weight"] >= 0.9 else "B",
        "visibility": "官方权威",
        "summary": (re.sub(r"\s+", " ", text)[:220] if text else ("%s发布的《%s》。" % (site["name"], title))),
        "tags": ["水生态环境", site["name"]],
        "status": "有效",
        "images": [],
        "added_at": time.strftime("%Y-%m-%d"),
        "cid": cid,
        "pdf": pdf_rel,
        "_ev": "ti:" + re.sub(r"\s+", "", title)[:18].lower(),
        "note": ("已抓取 PDF 原文：%s" % pdf_rel) if pdf_rel else "待原文审查",
        "origin_status": "待审查",
    }
    if text:
        rec["content"] = text
        rec["content_fetched"] = True
        rec["content_kind"] = "pdf全文" if pdf_rel else "正文全文"
    return rec


def main():
    sites = load(SITES, [])
    if ONLY:
        sites = [s for s in sites if ONLY in s["name"]]
    kb = load(KB, [])
    inbox = load(INBOX, [])
    seen_url, seen_ev = set(), set()
    for r in kb + inbox:
        u = (r.get("url") or "").strip().lower().rstrip("/")
        if u:
            seen_url.add(u)
        if r.get("_ev"):
            seen_ev.add(r["_ev"])
    print("站点栏目 %d 个；去重基准 %d" % (len(sites), len(seen_url)))

    added = 0
    for site in sites:
        got_site = 0
        pages = [site["url"]] + ([site["page_tpl"] % i for i in range(1, site["max_pages"] + 1)]
                                 if site.get("page_tpl") else [])
        for pi, page_url in enumerate(pages[: (MAX_PAGES if site.get("page_tpl") else 1) + 1]):
            html = pdfutil.fetch_html(page_url, timeout=20)
            if not html:
                print("  [%s] 第%d页 抓取失败" % (site["name"], pi))
                break
            items = extract_items(html, page_url, site)
            if not items:
                break
            new_here = 0
            for url, title in items:
                nu = url.strip().lower().rstrip("/")
                if nu in seen_url:
                    continue
                # 相关性：栏目为水务专属则放宽；否则标题需命中强水生态词
                wh = water_hits(title)
                if not site.get("water_only") and wh == 0:
                    continue
                rec = build_record(url, title, site)
                ev = rec["_ev"]
                if ev in seen_ev:
                    continue
                seen_url.add(nu)
                seen_ev.add(ev)
                inbox.append(rec)
                added += 1
                new_here += 1
                got_site += 1
                time.sleep(0.2)
            print("  [%s] 第%d页 候选%d 新增%d（累计%d）" % (site["name"], pi, len(items), new_here, added))
            if len(items) == 0:
                break
            time.sleep(0.3)
        print("== %s 完成，本次新增 %d" % (site["name"], got_site))
        # 落盘前清洗代理字符（否则 json.dump 会抛 UnicodeEncodeError 导致整个采集崩溃）
        json.dump(C.sanitize_record(inbox), open(INBOX, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)

    json.dump(C.sanitize_record(inbox), open(INBOX, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    with_pdf = sum(1 for r in inbox if r.get("pdf"))
    print("=" * 52)
    print("crawl_sites: 新增=%d, inbox_total=%d, 其中含PDF=%d" % (added, len(inbox), with_pdf))


if __name__ == "__main__":
    main()
