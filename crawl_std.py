# -*- coding: utf-8 -*-
"""生态环境部「水环境保护」标准库采集器（含 **PDF 原文抓取**）。

数据源：https://www.mee.gov.cn/ywgz/fgbz/bz/bzwb/shjbh/{shjzlbz|swrwpfbz|xgbzh}/
       栏目页 index.shtml / index_1.shtml / ... 每页 15 条标准详情页
流程：栏目翻页 -> 详情页 -> 解析 名称/标准号/实施日期 -> **下载页面内 PDF 原文**
     -> 抽取 PDF 纯文本作为 content -> 去重入库 kb/inbox.json
用法：
  python crawl_std.py                 # 全部三个栏目
  MAX_PAGES=3 python crawl_std.py     # 限制每栏目翻页数（调试用）
"""
import json, os, re, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdfutil
import crawl_common as C


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
INBOX = os.path.join(BASE, "kb", "inbox.json")

ROOT = "https://www.mee.gov.cn/ywgz/fgbz/bz/bzwb/shjbh/"
CATS = [("shjzlbz", "水环境质量标准"), ("swrwpfbz", "水污染物排放标准"), ("xgbzh", "水环境相关标准")]
MAX_PAGES = int(os.environ.get("MAX_PAGES", "12"))

CODE_RE = re.compile(r"\b(GB|GB/T|GB/T|HJ|HJ/T|SL|SL/T|DB\d{2}(?:/T)?)\s*[0-9]+(?:\.\d+)?\s*[—\-–]\s*\d{4}\b")
DATE_RE = re.compile(r"(\d{4})[-./年](\d{1,2})[-./月](\d{1,2})")
IMPL_RE = re.compile(r"(\d{4}-\d{2}-\d{2})\s*实施")


def norm(u):
    u = (u or "").strip().lower()
    return re.sub(r"#.*$", "", u).rstrip("/")


def load(p):
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return []
    return []


def parse_listing(html, page_url):
    """解析栏目页，返回 [(detail_url, 名称, 标准号, 实施日期)]。"""
    out = []
    # 形如： <a href="./202605/t20260514_1152270.shtml">名称 GB xxxx—2026 …</a>2026-09-01 实施
    for m in re.finditer(r'<a[^>]+href=["\']([^"\']*t\d{8}_\d+\.shtml)["\'][^>]*>(.*?)</a>(.{0,120})',
                         html, re.S | re.I):
        href, txt, tail = m.group(1), m.group(2), m.group(3)
        name = re.sub(r"<[^>]+>", "", txt)
        name = re.sub(r"&nbsp;|\s+", " ", name).strip()
        if not name:
            continue
        from urllib.parse import urljoin
        url = urljoin(page_url, href)
        code = ""
        cm = CODE_RE.search(clean_name(name))
        if cm:
            code = cm.group(0).replace("－", "-").replace("—", "-").replace("–", "-")
            code = re.sub(r"\s+", " ", code).strip()
        impl = ""
        im = IMPL_RE.search(tail or "")
        if im:
            impl = im.group(1)
        out.append((url, name, code, impl))
    return out


def classify_level(code):
    c = (code or "").upper()
    if c.startswith("GB"):
        return "国家标准", "standard", 550
    if c.startswith("HJ"):
        return "行业标准", "standard", 600
    if c.startswith("SL"):
        return "行业标准", "standard", 600
    if c.startswith("DB"):
        return "地方标准", "standard", 850
    return "行业标准", "standard", 600


FLAGSHIP = ["GB 3838", "GB 18918", "GB 8978", "GB/T 14848", "GB 5749", "GB 5084",
            "GB 18596", "GB/T 31962", "GB 50014", "GB 50201", "GB/T 51345",
            "GB/T 25173", "GB 3097", "GB 3552"]
FLAG_RANK = {k: 501 + i for i, k in enumerate(FLAGSHIP)}


def clean_name(n):
    """清洗标准名称：去掉"代替旧标准"后缀、压缩空白、去掉首尾括号。"""
    n = (n or "").strip()
    n = re.split(r"代替|替代", n)[0]
    n = re.sub(r"\s+", " ", n).strip(" 《》")
    return n


def pick_code(name):
    """取「标准名称之后」的那个标准号（而不是"代替"后面的旧标准号）。"""
    base = clean_name(name)
    codes = CODE_RE.findall(base) if isinstance(CODE_RE.findall(base), list) else []
    m = list(CODE_RE.finditer(base))
    if not m:
        return ""
    c = m[0].group(0)
    c = c.replace("－", "-").replace("—", "-").replace("–", "-")
    c = re.sub(r"\s+", "", c)
    c = re.sub(r"^(GB/T|GB|HJ/T|HJ|SL/T|SL|DB\d{2}/T|DB\d{2})(?=[0-9])", r"\1 ", c)
    return c


def clean_cjk(t):
    """修复 pypdf 抽出的"中 文 之 间 被 插 空 格"问题。"""
    if not t:
        return t
    for _ in range(2):
        t = re.sub(r"(?<=[\u4e00-\u9fff])[ ](?=[\u4e00-\u9fff])", "", t)
    t = re.sub(r"[ \t\u3000]{2,}", " ", t)
    t = re.sub(r"\n{2,}", "\n", t)
    return t.strip()


def make_summary(name, code, impl, pdf_text, pdf_rel, page_text):
    base = clean_name(name)
    tail = ("，%s 起实施" % impl) if impl else ""
    if pdf_text:
        t = clean_cjk(re.sub(r"\s+", " ", pdf_text))[:400]
        t = re.sub(r"^(ICS[^ ]*\s*[\d.]+\s*)+", "", t)
        return ("《%s》**原文全文已入库**（PDF，约 %d 字）。标准号 %s%s。正文要点：%s"
                % (base, len(pdf_text), code or "待核", tail, t[:200]))
    if pdf_rel:
        return ("《%s》**原文 PDF 已入库**（%s），但该 PDF 为扫描/图文版，文本抽取受限，"
                "可下载原件查阅。标准号 %s%s。" % (base, pdf_rel, code or "待核", tail))
    if page_text and len(page_text) > 300:
        return ("《%s》标准文本（约 %d 字，取自生态环境部标准页）。标准号 %s%s。"
                % (base, len(page_text), code or "待核", tail))
    return ("《%s》标准条目（标准号 %s%s）。该页未直接提供 PDF 原文，已记录详情页链接，供后续补齐原文。"
            % (base, code or "待核", tail))


def main():
    kb = load(KB)
    inbox = load(INBOX)
    seen = set()
    for r in kb + inbox:
        u = norm(r.get("url"))
        if u:
            seen.add(u)
    print("已有 %d 条，去重基准 %d" % (len(kb) + len(inbox), len(seen)))

    added = 0
    with_pdf = 0
    for cat_id, cat_name in CATS:
        pages_done = 0
        for pi in range(0, MAX_PAGES):
            page_url = ROOT + cat_id + "/" if pi == 0 else ROOT + cat_id + "/index_%d.shtml" % pi
            html = pdfutil.fetch_html(page_url, timeout=25)
            if not html:
                print("  [warn] %s 第%d页 抓取失败" % (cat_name, pi))
                break
            rows = parse_listing(html, page_url)
            if not rows:
                break
            pages_done += 1
            for url, name, _c0, impl in rows:
                nu = norm(url)
                if nu in seen:
                    continue
                name = clean_name(name)
                code = pick_code(name)
                rec_html = pdfutil.fetch_html(url, timeout=25)
                pdf_text, page_text = "", ""
                pdf_rel = None
                if rec_html:
                    links = pdfutil.find_pdf_links(rec_html, url)
                    if links:
                        cid_tmp = C.make_cid({"url": url, "title": name})
                        dest = os.path.join(pdfutil.PDF_DIR, cid_tmp + ".pdf")
                        pdfutil.ensure_dir()
                        for pu in links[:2]:
                            if pdfutil.download_binary(pu, dest):
                                pdf_rel = "kb/pdf/" + cid_tmp + ".pdf"
                                pdf_text = pdfutil.extract_pdf_text(dest)
                                break
                            time.sleep(0.2)
                    if not pdf_text:
                        page_text = _page_text(rec_html)
                level, cat, rank = classify_level(code)
                for k, v in FLAG_RANK.items():
                    if k in (name + " " + (code or "")):
                        rank = v
                        break
                cid = C.make_cid({"url": url, "title": name})
                rec = {
                    "title": name,
                    "url": url,
                    "source": "生态环境部",
                    "date": impl or "",
                    "category": cat,
                    "content_type": level,
                    "department": "生态环境",
                    "region": "全国",
                    "quality": "A",
                    "quality_src": "A",
                    "visibility": "官方权威",
                    "summary": make_summary(name, code, impl, pdf_text, pdf_rel, page_text),
                    "tags": ["水生态环境", "标准规范", code or level, cat_name],
                    "doc_no": code,
                    "status": "有效",
                    "content": clean_cjk(pdf_text) or page_text,
                    "content_fetched": bool(pdf_text or page_text),
                    "images": [],
                    "added_at": "2026-09-18",
                    "cid": cid,
                    "must_know": 1,
                    "must_rank": rank,
                    "_ev": "ti:" + re.sub(r"\s+", "", name)[:18].lower(),
                    "note": ("已抓取 PDF 原文：%s" % pdf_rel) if pdf_rel else "该页未直接提供 PDF 原文链接",
                    "pdf": pdf_rel,
                }
                inbox.append(rec)
                seen.add(nu)
                added += 1
                if pdf_rel:
                    with_pdf += 1
                time.sleep(0.25)
            print("  [%s] 第%d页 +%d (累计 %d)，含PDF %d" % (cat_name, pi, len(rows), added, with_pdf))
            time.sleep(0.3)

    _safe_dump(INBOX, inbox)
    print("=" * 50)
    print("crawl_std: added=%d, with_pdf=%d, inbox_total=%d" % (added, with_pdf, len(inbox)))


def _page_text(html):
    html = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    html = re.sub(r"<style[\s\S]*?</style>", " ", html, flags=re.I)
    txt = re.sub(r"<[^>]+>", "\n", html)
    lines = [re.sub(r"\s+", " ", l).strip() for l in txt.split("\n")]
    lines = [l for l in lines if len(l) >= 12 and not re.search(r"版权所有|ICP备|网站标识|首页|上一页|下一页", l)]
    return "\n".join(lines[:400])


if __name__ == "__main__":
    main()
