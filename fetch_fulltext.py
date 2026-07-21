# -*- coding: utf-8 -*-
"""全文抓取模块：从政策/文献原文 URL 下载 HTML 并抽取正文。
纯标准库实现（urllib + re），不依赖第三方包，供每日自动化增量补全文库使用。
用法：
  python fetch_fulltext.py            # 对 kb.json 中尚未抓取且 URL 可抓取者批量补全文
  python fetch_fulltext.py --only "标题关键词"   # 仅抓取标题命中的若干条
  python fetch_fulltext.py --test URL  # 测试单条 URL 抽取效果
"""
import json, os, re, sys, time, html, datetime, urllib.request, urllib.error, urllib.parse
from urllib.parse import urljoin

BASE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(BASE, "kb", "kb.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# 内容容器候选（按优先级匹配 class/id 关键字）
CONTENT_KEYS = ["TRS_Editor", "article", "article-con", "art_content", "artContent",
                "content", "detail", "newscon", "newsCon", "news_text", "newsText",
                "main", "body", "pages_content", "pagesContent", "zoom", "view",
                "fontbox", "TRS_UEDITOR", "u-editor", "edit", "cont"]

BOILER = re.compile(r"(版权所有|ICP备|网站标识|京公网安|首页|登录|注册|无障碍|繁體|English|"
                    r"纠错|责任编辑|来源：|发布时间|点击：|打印|关闭|分享到|扫一扫|关注我们|"
                    r"上一篇|下一篇|相关阅读|推荐阅读|主办单位|技术支持|政府网站)")


def download(url, timeout=12, retries=1):
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                        "Accept": "text/html,application/xhtml+xml"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            enc = "utf-8"
            m = re.search(r"charset=([\w-]+)", r.headers.get("Content-Type", ""), re.I)
            if m:
                enc = m.group(1).lower()
            try:
                return data.decode(enc, "ignore")
            except LookupError:
                return data.decode("utf-8", "ignore")
        except Exception as e:
            last = e
            time.sleep(1.0)
    return None


def _clean(txt):
    txt = html.unescape(txt)
    txt = re.sub(r"&emsp;|&ensp;|&thinsp;|&nbsp;", " ", txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt


def extract_main_text(html):
    """从 HTML 抽取正文纯文本（尽力而为）。"""
    if not html:
        return ""
    # 去 head/script/style/comment
    html = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    html = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I | re.S)
    html = re.sub(r"<style[\s\S]*?</style>", " ", html, flags=re.I | re.S)
    html = re.sub(r"<head[\s\S]*?</head>", " ", html, flags=re.I | re.S)
    html = re.sub(r"<noscript[\s\S]*?</noscript>", " ", html, flags=re.I | re.S)

    # 1) 尝试定位内容容器
    best = ""
    for key in CONTENT_KEYS:
        # class*=key 或 id*=key
        for pat in (r"class=[\"'][^\"']*%s[^\"']*[\"']" % re.escape(key),
                    r"id=[\"']%s[\"']" % re.escape(key)):
            m = re.search(pat, html, re.I)
            if m:
                # 找该标签闭合块
                start = m.start()
                # 回溯到标签起点
                ot = html.rfind("<", 0, start)
                eb = html.find(">", ot) + 1
                block = _grab_block(html, ot)
                if block:
                    text = _block_text(block)
                    if len(text) > len(best):
                        best = text
    if len(best) > 300:
        return best

    # 2) 退而求其次：取 body 内最长连续有效文本段
    body = re.sub(r"<body[\s\S]*?</body>", lambda m: m.group(0), html, flags=re.I | re.S)
    if not body or "body" not in body.lower():
        body = html
    # 取所有标签外文本片段
    texts = re.findall(r">([^<>]+)<", body)
    texts = [_clean(t) for t in texts if _clean(t)]
    # 过滤样板行，拼接最长连续有效段
    kept = [t for t in texts if not BOILER.search(t) and len(t) >= 4]
    # 用空行/短分隔合并：直接拼成一大段也可，这里返回过滤后的非空行
    longs = [t for t in kept if len(t) >= 15]
    if longs:
        return "\n".join(longs)
    return "\n".join(kept)


def _grab_block(html, open_idx):
    """从 open_idx（'<') 开始，匹配到对应闭合标签，返回内部 HTML。"""
    m = re.match(r"<\s*([a-zA-Z0-9]+)", html[open_idx:])
    if not m:
        return None
    tag = m.group(1).lower()
    i = open_idx
    depth = 0
    n = len(html)
    while i < n:
        lt = html.find("<", i)
        if lt < 0:
            break
        if html[lt + 1:lt + 2] == "/":
            # 闭合
            et = html.find(">", lt)
            ctag = re.match(r"</\s*([a-zA-Z0-9]+)", html[lt:et + 1])
            if ctag and ctag.group(1).lower() == tag:
                depth -= 1
                if depth == 0:
                    return html[open_idx:et + 1]
            i = et + 1
        else:
            # 开放
            et = html.find(">", lt)
            otag = re.match(r"<\s*([a-zA-Z0-9]+)", html[lt:et + 1])
            if otag and otag.group(1).lower() == tag:
                depth += 1
            i = et + 1
    return html[open_idx:]


def _block_text(block):
    # 去所有标签，保留换行
    text = re.sub(r"<br\s*/?>", "\n", block, flags=re.I)
    text = re.sub(r"</p>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    lines = [_clean(t) for t in text.split("\n")]
    lines = [t for t in lines if t and not BOILER.search(t) and len(t) >= 4]
    return "\n".join(lines)


def extract_images(html, base_url=""):
    """从正文容器内抽取图片绝对 URL（最多 8 张），过滤图标/追踪像素。"""
    if not html:
        return []
    container = html
    best = ""
    for key in CONTENT_KEYS:
        for pat in (r"class=[\"'][^\"']*%s[^\"']*[\"']" % re.escape(key),
                    r"id=[\"']%s[\"']" % re.escape(key)):
            m = re.search(pat, html, re.I)
            if m:
                ot = html.rfind("<", 0, m.start())
                block = _grab_block(html, ot)
                if block and len(block) > len(best):
                    best = block
    if best:
        container = best
    out = []
    seen = set()
    for m in re.finditer(r"<img\b[^>]*>", container, re.I):
        tag = m.group(0)
        sm = re.search(r"src=[\"']([^\"']+)[\"']", tag, re.I)
        if not sm:
            continue
        src = sm.group(1).strip()
        if not src or src.startswith("data:") or src.startswith("javascript:"):
            continue
        low = src.lower()
        # 跳过明显的装饰/图标/追踪图（仅当不是 jpg/png 等正文图时）
        if (re.search(r"(pixel|spacer|icon|logo|arrow|tracking|bg\.|background)", low)
                and not re.search(r"\.(jpg|jpeg|png|webp)", low)):
            continue
        if re.search(r"(weixin\.qq\.com|qpic\.cn|qq\.com/q\?|mpvote|head_img)", low):
            # 微信头像/投票/二维码图，非正文
            continue
        abs_src = urljoin(base_url, src)
        if abs_src in seen:
            continue
        seen.add(abs_src)
        out.append(abs_src)
        if len(out) >= 8:
            break
    return out


def fetch_one(url):
    html = download(url)
    if not html:
        return None, []
    text = extract_main_text(html)
    # 去除与正文无关的超短噪声
    text = "\n".join(l for l in text.split("\n") if len(l) >= 4)
    imgs = extract_images(html, url)
    return (text if len(text) >= 120 else None), imgs


def run_batch(only=None, mode="images", recent=0):
    kb = json.load(open(KB, encoding="utf-8"))
    if mode == "text":
        todo = [r for r in kb if not r.get("content_fetched")]
    elif mode == "all":
        # 全量补：正文缺失或图片缺失都处理
        todo = [r for r in kb if (not r.get("content_fetched") or not r.get("images"))]
    else:
        # 默认(images)：仅对「已有正文(已验证可下载)」的存量记录补图片，
        # 避免对不可达站点空等超时，卡住批量任务。
        todo = [r for r in kb if (not r.get("images") and r.get("content_fetched"))]
    if recent > 0:
        cut = (datetime.date.today() - datetime.timedelta(days=recent)).isoformat()
        todo = [r for r in todo if (r.get("added_at") or "") >= cut]
    if only:
        rx = re.compile(only)
        todo = [r for r in todo if rx.search(r.get("title", ""))]
    done = 0
    fail = 0
    saved = 0
    for r in todo:
        url = r.get("url", "")
        if not url or not url.startswith("http"):
            continue
        if any(b in url for b in ("people.com.cn", "163.com", "nfnews.com",
                                  ".pdf", "wulanchabu", "qpic.cn")):
            # 这些镜像/聚合/PDF 站点正文或图片抽取不稳定，跳过（留待人工或后续补）
            continue
        try:
            txt, imgs = fetch_one(url)
        except Exception:
            txt, imgs = None, []
        changed = False
        if txt and not r.get("content_fetched"):
            r["content"] = txt
            r["content_fetched"] = True
            changed = True
            print("OK  [%d字] %s" % (len(txt), r.get("title", "")[:40]))
        if imgs:
            r["images"] = imgs
            changed = True
            print("IMG x%d %s" % (len(imgs), r.get("title", "")[:36]))
        if changed:
            done += 1
        else:
            fail += 1
            print("SKIP %s" % r.get("title", "")[:40])
        time.sleep(0.3)
        saved += 1
        # 增量落盘：每 25 条写一次，保证中断也可恢复（已写入图片的记录下次自动跳过）
        if saved % 25 == 0:
            json.dump(kb, open(KB, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
            print("  ..saved %d/%d (images so far: %d)" % (saved, len(todo), sum(1 for x in kb if x.get("images"))))
    json.dump(kb, open(KB, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("----\nbatch done: filled=%d skipped=%d total=%d (images: %d)" % (done, fail, len(kb), sum(1 for x in kb if x.get("images"))))
    return done


def _acquire_lock():
    """互斥锁：防止多个 fetch_fulltext 实例并发写 kb.json（曾因并发覆盖导致数据丢失）。
    基于 PID 的锁文件：若锁存在且持有者进程仍存活，则本实例直接退出；否则清理失效锁后占位。"""
    LOCK = os.path.join(BASE, "kb", ".fetch_lock")
    try:
        if os.path.exists(LOCK):
            pid = 0
            try:
                with open(LOCK) as f:
                    pid = int((f.read() or "0").strip() or 0)
            except Exception:
                pid = 0
            if pid:
                try:
                    os.kill(pid, 0)  # 进程存活则不抛异常
                    print("[lock] fetch_fulltext 已在运行 (pid %d)，本实例退出以避免并发写 kb.json" % pid)
                    sys.exit(0)
                except Exception:
                    pass  # 进程已死 -> 锁失效，清理后继续
            try:
                os.remove(LOCK)
            except Exception:
                pass
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
    except FileExistsError:
        print("[lock] fetch_fulltext 锁文件竞争，本实例退出")
        sys.exit(0)
    import atexit
    atexit.register(_release_lock, LOCK)
    return LOCK


def _release_lock(LOCK):
    try:
        os.remove(LOCK)
    except Exception:
        pass


if __name__ == "__main__":
    _acquire_lock()
    args = sys.argv[1:]
    if args and args[0] == "--test":
        u = args[1]
        t, imgs = fetch_one(u)
        print("TEXT_LEN", len(t) if t else 0)
        print(t[:1500] if t else "(none)")
        print("IMAGES", len(imgs), imgs[:5])
    elif args and args[0] == "--only":
        run_batch(args[1] if len(args) > 1 else None)
    elif args and args[0] == "--text":
        run_batch(mode="text")
    elif args and args[0] == "--all":
        # 全量补正文+图片（耗时较长，建议后台运行）
        run_batch(mode="all")
    elif args and args[0] == "--recent":
        n = int(args[1]) if len(args) > 1 and args[1].isdigit() else 2
        run_batch(mode="all", recent=n)
    else:
        # 默认：为已有正文的存量记录补图片（快速、不空等不可达站点）
        run_batch()
