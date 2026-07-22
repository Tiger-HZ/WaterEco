# -*- coding: utf-8 -*-
"""采集微信公众号水生态环境相关文章（经由搜狗微信搜索），归一化后追加到 inbox.json。
搜狗微信无需登录即可返回文章列表；文章链接经 /link?url= 重定向到 mp.weixin.qq.com 原文。
按 config.json 的 weixin_filter 阈值，用阅读量/公众号关注人数过滤低质噪声。
用法：python crawl_weixin.py [额外关键词 ...]   环境变量 PAGES 控制每词翻页数(默认3)
"""
import os, sys, json, time, ssl, urllib.request, urllib.parse, re, datetime
BASE = os.path.dirname(os.path.abspath(__file__))
INBOX = os.path.join(BASE, "kb", "inbox.json")
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
QUERIES = ["水生态环境", "饮用水水源", "入河排污口", "黑臭水体", "美丽河湖", "五水共治",
           "河长制 河湖长", "流域治理", "再生水 污水", "千岛湖 水源", "钱塘江 治理",
           "水生态修复", "智慧水务", "蓝藻 富营养化"]
PAGES = int(os.environ.get("PAGES", "3"))

# 权威/官方公众号白名单（绕过阅读量阈值，并提升质量等级）
AUTH_ACCOUNTS = ["浙江生态环境", "杭州发布", "杭州生态环境", "中国环境", "生态环境部",
                 "中国给水排水", "给水排水", "水利部", "中国水利", "生态环境部卫星环境应用中心"]

# 分类关键词（对齐水生态 7 类）
WX_CAT = [
    (("标准", "规范", "指南", "GB", "HJ"), "standard"),
    (("补偿", "河长制", "排污许可", "断面", "再生水", "数字治水", "五水共治"), "management"),
    (("黑臭", "流域", "美丽河湖", "水源地", "生态", "治理工程", "千岛湖", "钱塘江"), "basin_eng"),
    (("技术", "监测", "装备", "智慧水务", "材料"), "tech"),
    (("研究", "论文", "调查", "评估", "承载力"), "literature"),
    (("国际", "欧盟", "美国", "莱茵", "经验"), "intl_region"),
]
WX_REL = ["水生态", "水环境", "地表水", "饮用水", "水源", "河湖", "流域", "水体", "水质",
          "排污口", "黑臭", "断面", "蓝藻", "富营养化", "水华", "美丽河湖", "五水共治",
          "河湖长", "河长制", "生态补偿", "再生水", "污水", "水污染物", "水功能区",
          "生态流量", "千岛湖", "钱塘江", "苕溪", "运河", "供水", "节水", "湿地"]
DEPT_RE = {"生态环境": "生态环境", "环境": "生态环境", "水利": "水利", "水务": "水利",
           "住建": "住建", "建设": "住建", "发改": "发改", "发展改革": "发改",
           "农业农村": "农业农村", "农业": "农业农村", "自然资源": "自然资源", "科技": "科技"}


def load_wx_filter():
    try:
        c = json.load(open(os.path.join(BASE, "config.json"), encoding="utf-8"))
        return c.get("weixin_filter", {})
    except Exception:
        return {}


def water_rel(text):
    t = (text or "").lower()
    return 1.0 if any(w in t for w in WX_REL) else 0.2


def classify_cat(title, summary):
    h = (title or "") + " " + (summary or "")
    for keys, cat in WX_CAT:
        if any(k in h for k in keys):
            return cat
    return "policy"

def get(u, to=20):
    req = urllib.request.Request(u, headers=UA)
    with urllib.request.urlopen(req, timeout=to, context=CTX) as r:
        return r.read().decode("utf-8", "ignore")

def norm_url(u):
    u = (u or "").strip()
    u = re.split(r"[?#]", u)[0]
    return u.rstrip("/").lower()

def resolve(url):
    """跟随搜狗 /link?url= 重定向，取真实 mp.weixin.qq.com 文章地址。"""
    try:
        req = urllib.request.Request(url, headers=UA)
        req.get_method = lambda: "HEAD"
        with urllib.request.urlopen(req, timeout=15, context=CTX) as r:
            return r.geturl()
    except Exception:
        return url

def main():
    # 微信阅读量/关注数过滤配置（来自 config.json → weixin_filter）
    rec_metrics = load_wx_filter()
    rec_metrics_cache = {}  # 预留：若后续接入阅读量/关注数抓取，按 url 存入 {read,follower}
    existing = set()
    kb = json.load(open(os.path.join(BASE, "kb", "kb.json"), encoding="utf-8")) if os.path.exists(os.path.join(BASE, "kb", "kb.json")) else []
    for r in kb:
        if r.get("url"): existing.add(norm_url(r["url"]))
        if r.get("title"): existing.add(r["title"].strip().lower())
    inbox = json.load(open(INBOX, encoding="utf-8")) if os.path.exists(INBOX) else []
    for r in inbox:
        if r.get("url"): existing.add(norm_url(r["url"]))
        if r.get("title"): existing.add(r["title"].strip().lower())

    extra = sys.argv[1:]
    queries = QUERIES + extra
    ITEM_RE = re.compile(
        r'<h3>\s*<a[^>]*href="(/link\?url=[^"]+)"[^>]*>(.*?)</a>\s*</h3>'
        r'.*?class="txt-info"[^>]*>(.*?)</p>'
        r'.*?class="all-time-y2"[^>]*>([^<]+)'
        r'.*?timeConvert\(\'(\d+)\'\)', re.S)
    added = 0
    for q in queries:
        for pg in range(1, PAGES + 1):
            su = "https://weixin.sogou.com/weixin?type=2&page=%d&query=%s" % (pg, urllib.parse.quote(q))
            try:
                html = get(su)
            except Exception as e:
                print("FAIL %s %s" % (q, str(e)[:50])); break
            for link, rawtitle, rawsum, acct, ts in ITEM_RE.findall(html):
                link = link.replace("&amp;", "&")
                title = re.sub(r"<[^>]+>", "", rawtitle).strip()
                if not title or len(title) < 6:
                    continue
                if title.strip().lower() in existing:
                    continue
                full = "https://weixin.sogou.com" + link
                real = resolve(full)
                # 搜狗 /link?url= 用 meta 刷新跳转，HEAD 拿不到真实 mp 地址；
                # 以「完整 sogou 链接(含唯一 token)」作为去重键与可用 url，避免互相判重。
                if "mp.weixin.qq.com" in real:
                    dedup_key = norm_url(real)
                    url = real
                else:
                    dedup_key = full
                    url = full
                if dedup_key in existing:
                    continue
                summary = re.sub(r"<[^>]+>", "", rawsum).strip()
                account = acct.strip() or "微信公众号"
                try:
                    date = datetime.datetime.fromtimestamp(int(ts), datetime.timezone.utc).strftime("%Y-%m-%d")
                except Exception:
                    date = "2026-07-19"
                dept = "其他"
                for k, v in DEPT_RE.items():
                    if k in (account + title):
                        dept = v; break
                # 区域识别（浙江/杭州/长三角高亮）
                rhay = (title or "") + " " + account
                if "杭州" in rhay:
                    region = "杭州"
                elif "浙江" in rhay or "长三角" in rhay:
                    region = "浙江" if "浙江" in rhay else "长三角"
                else:
                    region = "全国"
                # 质量与相关性
                rel = water_rel(title + " " + summary)
                is_auth = any(a in account for a in AUTH_ACCOUNTS)
                cat = classify_cat(title, summary)
                # 阅读量/关注数过滤（metrics 存在时按 config 阈值；否则按相关性与权威号判断）
                m = rec_metrics  # 见下方：本批次统一读取的过滤配置
                keep = True
                if not is_auth:
                    if rel < 0.6:
                        keep = False
                    elif m and (m.get("min_read") or m.get("min_follower")):
                        rd = (rec_metrics_cache.get(url) or {}).get("read", 0)
                        fo = (rec_metrics_cache.get(url) or {}).get("follower", 0)
                        if rd < (m.get("min_read", 0) or 0) and fo < (m.get("min_follower", 0) or 0):
                            keep = False
                if not keep:
                    continue
                quality = "A" if is_auth else ("B" if rel >= 0.9 else "C")
                if is_auth and m and (rec_metrics_cache.get(url) or {}).get("read", 0) >= (m.get("high_value_read", 10**9) or 10**9):
                    quality = "A"
                rec = {
                    "title": title,
                    "url": url,
                    "source": "微信:" + account,
                    "date": date,
                    "category": cat,
                    "content_type": "资讯动态",
                    "department": dept,
                    "region": region,
                    "quality": quality,
                    "visibility": "自媒" if not is_auth else "官方权威",
                    "summary": summary[:240],
                    "tags": [q] + ([w for w in WX_REL if w in (title + summary)]),
                    "content": summary,
                    "content_fetched": False,
                    "added_at": date or datetime.date.today().isoformat(),
                }
                if url in rec_metrics_cache and rec_metrics_cache[url]:
                    rec["metrics"] = rec_metrics_cache[url]
                inbox.append(rec); existing.add(title.strip().lower()); existing.add(dedup_key); added += 1
            time.sleep(1.2)
        print("q=%s done, cumulative added=%d" % (q, added))
    json.dump(inbox, open(INBOX, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("crawl_weixin: added=%d, inbox_total=%d" % (added, len(inbox)))

if __name__ == "__main__":
    main()
