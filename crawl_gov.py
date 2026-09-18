# -*- coding: utf-8 -*-
"""国务院政策文件库(sousuo.www.gov.cn)双碳政策批量采集。

数据源：https://sousuo.www.gov.cn/search-gov/data?t=zhengcelibrary&...
返回 searchVO.catMap.{gongwen,bumenfile,gongbao,otherfile}.listVO[]，
每条含 title/url/pcode(文号)/pubtimeStr/puborg/summary/childtype。

流程：多关键词 × 多类目 × 多页 → 清洗 → 水生态相关性过滤 → 分类(部门/类别/地域/质量)
     → 与 kb.json+inbox.json 去重(norm_url + 文号事件指纹) → 追加写入 kb/inbox.json。
随后由 update.py 合并渲染。

用法：
  python crawl_gov.py                # 默认全部关键词，每类每词抓 PAGES 页
  python crawl_gov.py 千岛湖 水源    # 仅指定关键词
"""
import json, os, re, ssl, sys, time, urllib.parse, urllib.request
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from merge import norm_url, event_key  # 复用去重逻辑
import crawl_common as C  # 复用政策感知分类 + 共享 make_cid
import media  # 政策/研究配图入库

FETCH_IMAGES = os.environ.get("FETCH_IMAGES", "1") == "1"

BASE = os.path.dirname(os.path.abspath(__file__))
INBOX = os.path.join(BASE, "kb", "inbox.json")
KB = os.path.join(BASE, "kb", "kb.json")
STATE = os.path.join(BASE, "kb", "gov_crawl_state.json")

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
      "Referer": "https://sousuo.www.gov.cn/"}

API = ("https://sousuo.www.gov.cn/search-gov/data?t=zhengcelibrary"
       "&q=%s&p=%d&n=50&type=gwyzcwjk&sort=&sortType=1&searchfield=")

# 水生态环境关键词（覆盖政策/标准/流域/管理/技术/国际等）
QUERIES = [
    "水生态环境保护", "饮用水水源", "入河排污口", "黑臭水体", "美丽河湖", "流域治理",
    "水污染防治", "排污许可", "水功能区", "生态流量", "水生态补偿", "再生水",
    "污水处理", "海绵城市", "地下水", "河长制", "水十条", "节水",
    "水环境质量标准", "蓝藻 富营养化", "水生态修复", "水质监测", "千岛湖 水源",
]
CATS_KEEP = ["gongwen", "bumenfile", "gongbao"]   # 公文/部门文件/公报；otherfile(解读)另计
CATS_LIT = ["otherfile"]                          # 政策解读 -> literature

# —— 大批量模式（BULK=1）启用：显著扩面的水生态环境检索词 ——
QUERIES_BULK = QUERIES + [
    "水環境", "水生态保护", "河湖保护", "幸福河湖", "江河保护治理", "水资源刚性约束",
    "用水权交易", "节约用水", "节水产业", "供水安全", "农村供水", "城市供水",
    "污水资源化利用", "再生水利用", "城镇污水", "污泥处置", "排水管网", "雨季溢流",
    "水环境综合治理", "水生态修复", "河湖生态缓冲带", "生态沟渠", "人工湿地",
    "滨水空间", "清水绿岸", "水质改善", "断面水质", "水功能区", "水域纳污能力",
    "入海河流", "总氮", "总磷", "氨氮", "化学需氧量", "富营养化", "水华", "蓝藻",
    "饮用水安全", "水源地保护", "水源保护区", "地下水保护", "地下水超采",
    "海水养殖", "养殖尾水", "水产养殖污染", "畜禽粪污", "农业面源污染",
    "农田退水", "农村生活污水", "农村黑臭水体", "农村人居环境",
    "小水电清理", "岸线保护", "河道采砂", "河湖长制", "河湖四乱",
    "水土保持", "生态清洁小流域", "湿地保护", "国际湿地城市",
    "流域生态补偿", "生态综合补偿", "生态环境分区管控", "三线一单",
    "美丽中国先行区", "美丽海湾", "海洋生态保护", "近岸海域",
    "排污口整治", "排污许可制", "水污染物排放标准", "水生态监测", "水生生物多样性",
    "底栖动物", "河湖健康评价", "数字孪生流域", "智慧水务", "水质自动监测",
    "水生态环境保护规划", "重点流域", "长江保护修复", "黄河生态保护",
    "太湖治理", "巢湖治理", "滇池治理", "洞庭湖", "鄱阳湖",
]
QUERIES = QUERIES_BULK if os.environ.get("BULK", "0") == "1" else QUERIES

# 大批量模式下可关闭"逐条抓详情页全文"以提速（全文由 crawl_pdf_attach 统一回填）
FETCH_FULL = os.environ.get("FETCH_FULL", "1") == "1"

# 水生态环境相关性词表（标题或摘要需命中，过滤宽泛查询带来的噪声）
REL = ["水生态", "水环境", "地表水", "饮用水", "水源地", "河湖", "流域", "水体", "水质",
       "排污口", "入河排污口", "黑臭", "断面", "蓝藻", "富营养化", "水华", "美丽河湖",
       "五水共治", "河湖长", "生态补偿", "再生水", "污水处理", "水污染物", "水功能区",
       "生态流量", "水生物多样性", "千岛湖", "钱塘江", "苕溪", "运河", "供水", "节水",
       "水安全", "湿地", "缓冲带", "治水", "地下水", "排水", "海绵", "水资源"]

DEPTS = ["生态环境", "水利", "住建", "发改", "农业农村", "自然资源", "市场监管", "其他"]

# 发布机构 / 标题关键词 -> 受控部门词表
DEPT_RULES = [
    (("生态环境部", "环境保护", "水生态", "流域"), "生态环境"),
    (("水利部", "水利", "水务", "水文", "水资源"), "水利"),
    (("住房和城乡建设", "住建", "城乡建设", "城市供水", "排水"), "住建"),
    (("发展改革委", "发改委", "国家发展和改革"), "发改"),
    (("农业农村", "林业", "农业", "农村"), "农业农村"),
    (("自然资源", "国土", "空间规划"), "自然资源"),
    (("市场监督管理", "市场监管", "标准委", "国家标准"), "市场监管"),
]

# 类别关键词 -> 受控 category（对齐水生态环境 7 类）
CAT_RULES = [
    (("标准", "技术规范", "指南", "排放标准", "GB", "HJ"), "standard"),
    (("生态补偿", "横向补偿", "流域协作", "跨省"), "management"),
    (("黑臭", "流域治理", "美丽河湖", "水源地", "水生态修复", "河湖", "治理工程"), "basin_eng"),
    (("河长制", "排污许可", "断面考核", "再生水", "数字治水", "水源保护"), "management"),
    (("技术", "监测", "装备", "材料", "智慧水务"), "tech"),
    (("国际", "全球", "欧盟", "美国", "莱茵", "经验"), "intl_region"),
    (("研究", "论文", "调查", "评估", "承载力"), "literature"),
]


def get(u, timeout=25, retries=3):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(u, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:
            last = e
            time.sleep(1.2 * (i + 1))
    raise last


def clean(s):
    s = s or ""
    s = re.sub(r"<[^>]+>", "", s)              # 去 <em>/<br/> 等标签
    s = s.replace("\u3000", " ").replace("\r", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", s).strip()


def fetch_full(url):
    """抓取政策详情页全文（中国政府网 zhengce/政库页正文位于 #UCAP-CONTENT）。
    返回 (清洗后纯文本, 正文容器soup, 整页soup)；任一失败对应位置返回空串/None
    （调用方回退到摘要片段；容器用于抽取正文配图，整页作为配图回退源）。"""
    if not url:
        return "", None, None
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=25, context=ctx) as r:
            html = r.read().decode("utf-8", "replace")
        soup = BeautifulSoup(html, "html.parser")
        for sel in ["#UCAP-CONTENT", ".pages_content", "#p_content", "div.article", "#content"]:
            el = soup.select_one(sel)
            if el:
                txt = el.get_text("\n")
                txt = re.sub(r"\n{2,}", "\n", txt)
                txt = "\n".join(l.strip() for l in txt.split("\n") if l.strip())
                if len(txt) > 200:
                    return txt, el, soup
    except Exception:
        return "", None, None
    return "", None, None


def to_date(it):
    ds = it.get("pubtimeStr") or ""
    m = re.search(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})", ds)
    if m:
        return "%04d-%02d-%02d" % (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    for k in ("pubtime", "ptime"):
        v = it.get(k)
        if isinstance(v, (int, float)) and v > 1_000_000_000_000:
            t = time.localtime(v / 1000.0)
            return time.strftime("%Y-%m-%d", t)
    return ""


def classify_dept(title, puborg):
    hay = (puborg or "") + " " + (title or "")
    for keys, dept in DEPT_RULES:
        if any(k in hay for k in keys):
            return dept
    return "其他"


def classify_cat(title, summary, is_lit):
    # 复用 crawl_common 的政策感知分类（意见/批复/通知/规划/方案→政策；仅明确标准/规范→标准）
    return C.classify_cat(title, summary, is_lit)


# 强水生态信号词（收严版）：标题命中即入库；否则要求摘要命中 >=2 个
REL_STRONG = ["水生态", "水环境", "地表水", "饮用水源", "饮用水水源", "水源地", "河湖", "流域",
              "水体", "水质", "排污口", "入河排污口", "黑臭", "断面", "蓝藻", "富营养化",
              "水华", "美丽河湖", "幸福河湖", "五水共治", "河湖长", "河长", "生态补偿",
              "再生水", "污水处理", "水污染物", "水功能区", "生态流量", "千岛湖", "钱塘江",
              "苕溪", "运河", "供水", "节水", "水安全", "湿地", "缓冲带", "地下水", "排水",
              "海绵城市", "水资源", "水生态修复", "尾水", "农田退水", "水域", "河道", "湖泊"]


def is_relevant(title, summary):
    """收严相关性：标题含强水生态词即可；否则摘要需命中 >=2 个强水生态词。
    （原实现用宽泛词表+任一命中，导致"邯郸市城市总体规划""无人驾驶航空器"等噪声入库。）"""
    t = (title or "").lower()
    s = (summary or "").lower()
    if any(k.lower() in t for k in REL_STRONG):
        return True
    return sum(1 for k in REL_STRONG if k.lower() in s) >= 2


def quality_of(cat, dept, is_lit):
    if is_lit:
        return "B"
    # 国家级政策/部门文件/公报默认高质量
    return "A"


def build_record(it, is_lit):
    title = clean(it.get("title"))
    if not title:
        return None
    url = (it.get("url") or "").strip()
    if not url:
        return None
    summary = clean(it.get("summary"))
    puborg = clean(it.get("puborg"))
    pcode = clean(it.get("pcode"))
    if not is_relevant(title, summary):
        return None
    date = to_date(it)
    dept = classify_dept(title, puborg)
    cat = classify_cat(title, summary, is_lit)
    qual = quality_of(cat, dept, is_lit)
    if not summary:
        summary = ("%s发布%s。" % (puborg or "国家有关部门", title)) + (("文号：%s。" % pcode) if pcode else "")
    # 区域识别：命中浙江/杭州/长三角高亮，默认全国(部委)
    rhay = (title or "") + " " + (puborg or "")
    if "杭州" in rhay:
        region = "杭州"
    elif "浙江" in rhay or "长三角" in rhay:
        region = "浙江" if "浙江" in rhay else "长三角"
    else:
        region = "全国"
    tags = ["水生态环境", "国家政策"]
    if pcode:
        tags.append(pcode)
    if puborg:
        tags.append(puborg)
    ev = ("ev:" + pcode.lower()) if pcode else event_key(title)
    # 抓取政策详情页全文（而非仅搜索摘要片段），确保拿到原文全部内容。
    # FETCH_FULL=0 时只入条目（大批量采集提速，全文交给 crawl_pdf_attach 统一回填）。
    full, container, soup = fetch_full(url) if FETCH_FULL else ("", None, None)
    time.sleep(0.3)
    content = full if full else summary
    cid = C.make_cid({"url": url, "title": title, "date": date})
    # 抽取正文配图入库（政策规划类/研究类配图常含关键信息，用户要求一并收集）
    images = []
    if FETCH_IMAGES and container:
        images = media.fetch_images(url, container, cid)
        # 正文容器无图时，回退到整页（过滤 logo/二维码等装饰图）
        if not images and soup:
            images = media.fetch_images(url, soup, cid, skip_decorative=True)
    rec = {
        "title": title,
        "url": url,
        "source": (puborg or "中国政府网 · 国务院政策文件库"),
        "date": date,
        "category": cat,
        "department": dept,
        "region": region,
        "quality": qual,
        "summary": summary,
        "tags": tags,
        "content": content,
        "content_fetched": bool(full),
        "images": images,
        "cid": cid,
        "added_at": date or "",   # 用发布日期归档，历史条目分布到各自时间轴
        "_ev": ev,
    }
    return rec


def load(p):
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return []
    return []


def main():
    queries = sys.argv[1:] or QUERIES
    pages = int(os.environ.get("GOV_PAGES", "3"))

    kb = load(KB)
    inbox = load(INBOX)

    # 去重索引：kb + 现有 inbox
    seen_url, seen_ev = set(), set()
    for r in kb + inbox:
        nu = norm_url(r.get("url", ""))
        if nu:
            seen_url.add(nu)
        ev = r.get("_ev")
        if ev:
            seen_ev.add(ev)

    added = 0
    per_year = {}
    for q in queries:
        qq = urllib.parse.quote(q)
        cat_plan = [(c, False) for c in CATS_KEEP] + [(c, True) for c in CATS_LIT]
        for pg in range(1, pages + 1):
            try:
                raw = get(API % (qq, pg))
                d = json.loads(raw)
            except Exception as e:
                print("  [warn] q=%s p=%d %s" % (q, pg, type(e).__name__))
                break
            cm = (d.get("searchVO") or {}).get("catMap") or {}
            got_any = False
            for cat_key, is_lit in cat_plan:
                lv = (cm.get(cat_key) or {}).get("listVO") or []
                for it in lv:
                    got_any = True
                    rec = build_record(it, is_lit)
                    if not rec:
                        continue
                    nu = norm_url(rec["url"])
                    if nu and nu in seen_url:
                        continue
                    if rec["_ev"] in seen_ev:
                        continue
                    seen_url.add(nu)
                    seen_ev.add(rec["_ev"])
                    inbox.append(rec)
                    added += 1
                    yr = (rec["date"] or "")[:4] or "?"
                    per_year[yr] = per_year.get(yr, 0) + 1
            if not got_any:
                break
            time.sleep(0.25)
        print("q=%s done, cumulative added=%d" % (q, added))

    json.dump(inbox, open(INBOX, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("=" * 50)
    print("crawl_gov: added=%d, inbox_total=%d" % (added, len(inbox)))
    print("by year:", dict(sorted(per_year.items(), reverse=True)))


if __name__ == "__main__":
    main()
