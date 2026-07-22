# -*- coding: utf-8 -*-
"""媒体资源抓取（图片 / PDF）：
- fetch_images(page_url, container, cid): 从正文容器抽取 <img> 并下载到 kb/img/<cid>_N.<ext>
- fetch_pdf(pdf_url, cid): 下载开放获取(OA)全文 PDF 到 kb/pdf/<cid>.pdf（带大小/超时保护，失败返回 None）
供 crawl_gov(图片) / crawl_crossref|crawl_europepmc|crawl_academic(PDF) 复用。
注意：二进制资源会随仓库增长；已做大小上限与 OA 优先，避免仓库膨胀过快。
"""
import os, ssl, urllib.request, urllib.parse, re, time

BASE = os.path.dirname(os.path.abspath(__file__))
IMG_DIR = os.path.join(BASE, "kb", "img")
PDF_DIR = os.path.join(BASE, "kb", "pdf")

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Referer": "https://www.gov.cn/"}

IMG_MAX_BYTES = 700 * 1024      # 单图上限 700KB（避免大图撑爆仓库）
PDF_MAX_BYTES = 25 * 1024 * 1024  # 单 PDF 上限 25MB
MAX_IMAGES = 10


def ensure_dirs():
    os.makedirs(IMG_DIR, exist_ok=True)
    os.makedirs(PDF_DIR, exist_ok=True)


def download_binary(url, dest, max_bytes=IMG_MAX_BYTES, timeout=30):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
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
            if not data:
                return False
            with open(dest, "wb") as f:
                f.write(data)
            return True
    except Exception:
        return False


def _ext(url, content_type):
    u = (url or "").lower()
    if u.endswith(".png"):
        return "png"
    if u.endswith(".jpg") or u.endswith(".jpeg"):
        return "jpg"
    if u.endswith(".gif"):
        return "gif"
    if u.endswith(".webp"):
        return "webp"
    ct = (content_type or "").lower()
    if "png" in ct:
        return "png"
    if "jpeg" in ct or "jpg" in ct:
        return "jpg"
    if "gif" in ct:
        return "gif"
    return "jpg"


_DECOR = re.compile(r"logo|icon|banner|foot|header|avatar|qr|二维码|watermark|advert|spacer|pixel", re.I)


def _is_decorative(im):
    hay = " ".join([str(im.get("class") or ""), str(im.get("alt") or ""), str(im.get("src") or "")])
    if _DECOR.search(hay):
        return True
    for a in ("width", "height"):
        try:
            if int(im.get(a) or 0) and int(im.get(a)) <= 48:
                return True
        except Exception:
            pass
    return False


def fetch_images(page_url, container, cid, max_n=MAX_IMAGES, skip_decorative=False):
    """从 BeautifulSoup 容器抽取图片并落盘，返回相对路径列表（如 kb/img/<cid>_0.jpg）。
    skip_decorative=True 时跳过 logo/icon/banner/二维码等装饰图（整页回退抓取时用）。"""
    out = []
    if not container:
        return out
    imgs = container.find_all("img")[: max_n * 2]
    host = "/".join(page_url.split("/")[:3])  # https://www.gov.cn
    i = 0
    for im in imgs:
        if i >= max_n:
            break
        if skip_decorative and _is_decorative(im):
            continue
        src = im.get("src") or im.get("data-src") or im.get("data-original") or ""
        if not src or src.startswith("data:"):
            continue
        if src.startswith("//"):
            src = "https:" + src
        elif src.startswith("/"):
            src = host + src
        elif not src.startswith("http"):
            src = urllib.parse.urljoin(page_url, src)
        ext = _ext(src, im.get("content-type") or "")
        dest = os.path.join(IMG_DIR, "%s_%d.%s" % (cid, i, ext))
        if download_binary(src, dest, max_bytes=IMG_MAX_BYTES):
            out.append("kb/img/%s_%d.%s" % (cid, i, ext))
            i += 1
        time.sleep(0.15)
    return out


def fetch_pdf(pdf_url, cid, max_bytes=PDF_MAX_BYTES):
    """下载 OA 全文 PDF；已存在则复用；失败返回 None。返回相对路径 kb/pdf/<cid>.pdf。"""
    if not pdf_url:
        return None
    dest = os.path.join(PDF_DIR, cid + ".pdf")
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return "kb/pdf/" + cid + ".pdf"
    if download_binary(pdf_url, dest, max_bytes=max_bytes):
        return "kb/pdf/" + cid + ".pdf"
    return None
