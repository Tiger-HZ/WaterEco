# -*- coding: utf-8 -*-
"""PDF 附件工具：从详情页找 PDF 原文 -> 下载 -> 抽取纯文本。

背景（2026-09-18）：
  知识库此前只对学术 doi.org 抓过 OA PDF，**政府/标准详情页里挂的 PDF 原文完全没抓**。
  而恰恰这些 PDF 才是真正要沉淀的原文，例如
  https://www.mee.gov.cn/ywgz/fgbz/bz/bzwb/shjbh/xgbzh/202605/t20260514_1152270.shtml
  页面内即为《流域水生态环境质量标准制订技术导则》PDF 原文。

依赖：优先 pypdf（Actions 里 pip install pypdf）；不可用时仅保留 PDF 文件，
      文本抽取降级为空（记录仍标记 has_pdf）。
"""
import os, re, ssl, time, urllib.parse, urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
PDF_DIR = os.path.join(BASE, "kb", "pdf")
PDF_MAX_BYTES = 30 * 1024 * 1024

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

PDF_HREF = re.compile(r"""href\s*=\s*["']([^"']+?\.(?:pdf|PDF))["']""")
PDF_ANY = re.compile(r"""["']([^"']+?\.(?:pdf|PDF))["']""")
ATTACH_TEXT = re.compile(r"(附件|下载|全文|原文|\.pdf)", re.I)


def ensure_dir():
    os.makedirs(PDF_DIR, exist_ok=True)


def find_pdf_links(html, base_url):
    """从详情页 HTML 中抽取绝对 PDF 链接（优先 <a href>，其次任意引号内 .pdf）。"""
    if not html:
        return []
    out, seen = [], set()
    cands = PDF_HREF.findall(html) or []
    if not cands:
        cands = PDF_ANY.findall(html)
    for href in cands:
        href = (href or "").strip()
        if not href or href.startswith("data:"):
            continue
        if href.startswith("./"):
            href = href[2:]
        if href.startswith("//"):
            href = "https:" + href
        elif not href.startswith("http"):
            # 栏目页里附件常写作 ./W020260514xxxx.pdf，用页面 URL 目录拼接
            href = urllib.parse.urljoin(base_url, href)
        href = href.strip()
        if href in seen:
            continue
        seen.add(href)
        out.append(href)
    return out


def download_binary(url, dest, max_bytes=PDF_MAX_BYTES, timeout=60):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
            cl = int(r.headers.get("Content-Length", "0") or 0)
            if cl and cl > max_bytes:
                return False
            data = b""
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                data += chunk
                if len(data) > max_bytes:
                    return False
        if len(data) < 1024:
            return False
        # 简单校验：PDF 以 %PDF 开头
        if not data[:5].startswith(b"%PDF"):
            return False
        with open(dest, "wb") as f:
            f.write(data)
        return True
    except Exception:
        return False


def extract_pdf_text(path, max_chars=200000):
    """抽取 PDF 纯文本。无 pypdf 时返回空串（不报错）。"""
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:
        try:
            from PyPDF2 import PdfReader  # type: ignore
        except Exception:
            return ""
    try:
        reader = PdfReader(path)
        parts = []
        total = 0
        for pg in reader.pages:
            try:
                t = pg.extract_text() or ""
            except Exception:
                t = ""
            if t:
                parts.append(t)
                total += len(t)
            if total >= max_chars:
                break
        txt = "\n".join(parts)
        txt = re.sub(r"[ \t\u3000]+", " ", txt)
        txt = re.sub(r"\n{2,}", "\n", txt)
        return txt.strip()[:max_chars]
    except Exception:
        return ""


def fetch_pdf_for_record(rec, save_text=True):
    """给定记录（含 url/cid），抓取页面内 PDF 原文。
    返回 (rel_path or None, text or '')。已存在则复用。"""
    ensure_dir()
    cid = rec.get("cid") or ""
    if not cid:
        return None, ""
    dest = os.path.join(PDF_DIR, cid + ".pdf")
    rel = "kb/pdf/" + cid + ".pdf"
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        txt = extract_pdf_text(dest) if save_text else ""
        return rel, txt
    url = rec.get("url") or ""
    if not url.startswith("http"):
        return None, ""
    html = fetch_html(url)
    if not html:
        return None, ""
    links = find_pdf_links(html, url)
    for pu in links[:3]:
        if download_binary(pu, dest):
            txt = extract_pdf_text(dest) if save_text else ""
            return rel, txt
        time.sleep(0.2)
    return None, ""


def fetch_html(url, timeout=25, retries=2):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                raw = r.read()
            for enc in ("utf-8", "gb18030"):
                try:
                    return raw.decode(enc)
                except Exception:
                    continue
            return raw.decode("utf-8", "replace")
        except Exception as e:
            last = e
            time.sleep(1.0 * (i + 1))
    return None
