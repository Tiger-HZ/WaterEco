# -*- coding: utf-8 -*-
"""AI 网页入库：抓取 → 分析 → 智能分类 → 入知识库。
用户在门户「📥 手动入库 → 网页交给 AI」粘贴链接并提交后，助手在本机运行本脚本，
复用 fetch_fulltext 的正文抽取，基于关键词映射做智能分类（部门/地域/分类/质量/标签），
提取摘要，追加到 kb/inbox.json，再调用 update.py 合并渲染、gh_deploy.py 部署上线。
用法：
  python ingest_url.py "https://..." ["备注文字"]
  python ingest_url.py --stdin        # 从标准输入读 URL（每行一个，可带备注用 | 分隔）
  python ingest_url.py "https://..." --no-deploy   # 仅入库不部署
"""
import json, os, re, sys, time, subprocess, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
INBOX = os.path.join(BASE, "kb", "inbox.json")
sys.path.insert(0, BASE)
import fetch_fulltext as F   # 复用 download / extract_main_text / fetch_one

# ============ 智能分类关键词映射 ============
DEPT_MAP = [
    ("生态环境部门", ["减污降碳", "协同", "生态环境", "生态", "气候", "碳污", "污碳", "环境", "低碳",
                   "适应", "生态保护", "生态文明", "碳汇", "林业碳汇", "应对气候"]),
    ("发展改革委", ["碳市场", "碳排放权", "碳排放", "能耗", "双控", "节能", "用能权", "发改", "规划",
                 "宏观调控", "非化石", "可再生", "能源", "电力", "煤炭", "油气", "绿电", "绿证", "配额"]),
    ("经信(工信)", ["工业", "绿色制造", "技改", "数字化", "工信", "经信", "产业结构", "节能降碳改造",
                 "数据中心", "园区", "企业"]),
    ("市场监管局", ["标准", "认证", "计量", "质量", "市场监管", "合格评定", "标识", "检测"]),
    ("其他相关部门", ["住建", "建筑", "城乡建设", "交通", "交通运输", "农业", "农村", "科技", "金融",
                   "财政", "商务", "公共机构", "机关事务"]),
]
CAT_MAP = [
    ("policy", ["规划", "方案", "意见", "通知", "政策", "顶层设计", "目标", "路线图", "行动", "部署", "举措"]),
    ("standard", ["标准", "法规", "法律", "条例", "规范", "办法", "规定", "准则", "强制性"]),
    ("carbon_market", ["碳市场", "碳排放权", "碳交易", "碳价", "配额", "气候投融资", "CCER", "绿电", "绿证", "碳金融", "碳账户"]),
    ("synergy", ["减污降碳", "协同", "多污染物", "大气", "污碳", "蓝天"]),
    ("industry", ["行业", "工业", "钢铁", "水泥", "化工", "建材", "有色", "节能", "能效", "建筑", "交通", "煤炭消费"]),
    ("tech", ["技术", "创新", "产品", "工艺", "装备", "研发", "专利", "数字化", "智能化"]),
    ("literature", ["研究", "论文", "文献", "报告", "综述", "期刊", "白皮书", "课题", "调研"]),
    ("intl_region", ["国际", "全球", "欧盟", "美国", "联合国", "IPCC", "巴黎协定", "国外", "区域", "亚太"]),
]
# 地域：优先浙江/杭州，其次其他省份，默认全国，含国外信号则国外
ZHE = ["杭州", "宁波", "温州", "嘉兴", "湖州", "绍兴", "金华", "衢州", "舟山", "台州", "丽水"]
PROV = {"北京": "北京", "上海": "上海", "广东": "广东", "江苏": "江苏", "山东": "山东", "四川": "四川",
        "福建": "福建", "湖北": "湖北", "湖南": "湖南", "河南": "河南", "河北": "河北", "天津": "天津",
        "重庆": "重庆", "陕西": "陕西", "安徽": "安徽", "江西": "江西", "广西": "广西", "云南": "云南",
        "贵州": "贵州", "山西": "山西", "辽宁": "辽宁", "吉林": "吉林", "黑龙江": "黑龙江",
        "甘肃": "甘肃", "青海": "青海", "内蒙古": "内蒙古", "宁夏": "宁夏", "新疆": "新疆", "西藏": "西藏"}
FOREIGN = ["united states", "europe", "european", "iea", "ipcc", "united nations", "white house",
           "https://", "http://"]  # 仅作占位，下面用 domain 判断
FOREIGN_DOMAINS = ["gov.uk", "europa.eu", "whitehouse.gov", "un.org", "iea.org", "epa.gov",
                   "ec.europa.eu", "gov", "iea", ".edu", "nature.com", "sciencedirect", "arxiv"]
QUAL_GOV = ("gov.cn", "gov", "ndrc", "mee.gov", "miit.gov", "samr.gov", "china.gov", "xinhua",
            "people.com", "cntv", "cnr", "gov.cn")
STOP = set("的 了 在 是 和 与 及 对 为 以 等 中 其 该 此 个 也 并 将 把 被 由 从 向 于 到 上 下 内 外 "
           "我们 他们 可以 进行 通过 根据 按照 经过 已经 目前 相关 方面 工作 要求 提出 表示 指出 强调 "
           "一个 一种 一些 这个 那个 这些 那些 以及 或者 如果 由于 因此 所以 但是 然而 此外 同时 目前 "
           "年 月 日 号 时 分 公里 亿元 万元 百分比 %".split())

# 双碳领域词典：用于抽取高质量标签/关键词（长度 2-6 优先最长匹配）
DOMAIN_TERMS = ["减污降碳协同", "减污降碳", "污碳协同", "碳达峰", "碳中和", "碳排放权", "碳排放",
    "碳市场", "碳交易", "碳价", "碳配额", "碳汇", "林业碳汇", "碳污", "污碳", "碳足迹", "碳核算", "碳标签",
    "能耗双控", "碳排放双控制度", "双控", "节能降碳", "节能", "能效", "绿电", "绿证", "CCER", "气候投融资",
    "适应气候", "气候", "绿色制造", "数字化转型", "技改", "产业结构", "新型电力系统", "可再生能源",
    "非化石能源", "光伏", "风电", "储能", "氢能", "绿氢", "生态环境", "生态", "环境", "生态文明",
    "污染治理", "大气", "蓝天", "水环境", "标准", "认证", "计量", "合格评定", "质量",
    "工业", "钢铁", "水泥", "化工", "建材", "有色", "建筑", "交通", "交通运输",
    "技术", "创新", "研发", "专利", "工艺", "装备", "智能化", "人工智能",
    "政策", "规划", "方案", "意见", "通知", "条例", "法规", "规范", "办法", "白皮书", "路线图", "试点", "示范",
    "浙江", "杭州", "宁波", "温州", "嘉兴", "湖州", "绍兴", "金华", "衢州", "舟山", "台州", "丽水",
    "全国", "国外", "国际", "全球", "欧盟", "美国", "产品", "园区", "企业", "公共机构"]
# 虚词二元组黑名单（兜底高频二元组时排除）
FUNCTION_BI = set("推进 进行 明确 印发 开展 实施 提出 建设 探索 形成 能力 机制 经验 制度 体系 项目 "
    "为了 通过 根据 按照 经过 已经 目前 相关 方面 工作 要求 表示 指出 强调 一个 一种 一些 这个 这些 "
    "以及 或者 由于 因此 所以 但是 然而 此外 同时 我们 他们 可以 对于 关于 在于 在于 作为 成为".split())


def tokenize(text):
    text = re.sub(r"\s+", "", text or "")
    toks = re.findall(r"[a-zA-Z0-9_]{2,}", text)
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    bigrams = ["".join(cjk[i:i + 2]) for i in range(len(cjk) - 1)]
    return toks + bigrams


def classify_dept(text):
    best, best_n = "其他相关部门", 0
    for name, kws in DEPT_MAP:
        n = sum(text.count(k) for k in kws)
        if n > best_n:
            best, best_n = name, n
    return best


def classify_cat(text):
    best, best_n = "policy", 0
    for key, kws in CAT_MAP:
        n = sum(text.count(k) for k in kws)
        if n > best_n:
            best, best_n = key, n
    # 兜底：若几乎无命中，按内容长度归到文献/政策
    return best


def classify_region(text, url):
    low = (url or "").lower()
    if any(d in low for d in ("gov.uk", "europa.eu", "whitehouse.gov", "un.org", "iea.org",
                              "epa.gov", ".edu", "nature.com", "science.org", "arxiv.org",
                              "reuters.com", "bbc.com", "cnn.com")):
        return "国外"
    if "浙江" in text or "浙江省" in text:
        for c in ZHE:
            if c in text:
                return c
        return "浙江"
    for p, name in PROV.items():
        if p in text:
            return name
    if any(w in text.lower() for w in ("united states", "european union", "europe", "america", "global", "international")):
        return "国外"
    return "全国"


def classify_quality(url, text, title):
    low = (url or "").lower()
    if any(g in low for g in QUAL_GOV):
        return "A"
    if any(o in low for o in (".edu.cn", "research", "ac.cn", "org.cn", "association", "学会", "协会", "研究院")):
        return "B"
    if len(text) < 300:
        return "C"
    return "B"


def extract_tags(text, n=6):
    tags, seen = [], set()
    for t in DOMAIN_TERMS:
        if t in text and t not in seen:
            tags.append(t); seen.add(t)
    if len(tags) < n:
        cnt = {}
        for b in tokenize(text):
            if len(b) != 2:
                continue
            if b in STOP or b in seen or b in FUNCTION_BI:
                continue
            if b[-1] in "省市区县":   # 地名切出的垃圾二元组
                continue
            cnt[b] = cnt.get(b, 0) + 1
        for b, _ in sorted(cnt.items(), key=lambda x: -x[1]):
            if b not in seen:
                tags.append(b); seen.add(b)
            if len(tags) >= n:
                break
    return tags[:n]


def make_summary(text, n=200):
    text = re.sub(r"\s+", " ", text or "").strip()
    # 取前两个句子或前 n 字
    sents = re.split(r"(?<=[。！？])", text)
    out = ""
    for s in sents:
        if len(out) + len(s) > n:
            break
        out += s
    if not out:
        out = text[:n]
    return out[:n].strip()


def ingest(url, note="", deploy=True, do_update=True):
    print("── 抓取：", url)
    html = F.download(url)
    if not html:
        print("   ✗ 下载失败（网络/拦截）")
        return None
    text = F.extract_main_text(html)
    text = "\n".join(l for l in text.split("\n") if len(l) >= 4)
    if len(text) < 120:
        # 退一步：用 fetch_one 的兜底
        t2 = F.fetch_one(url)
        if t2 and len(t2) > len(text):
            text = t2
    if len(text) < 120:
        print("   ✗ 正文抽取不足（%d 字），可能是登录页/PDF/镜像，建议人工处理" % len(text))
        return None
    title_m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    title = re.sub(r"\s+", " ", title_m.group(1)).strip() if title_m else (text[:40])
    title = re.sub(r"[_\-|—].*$", "", title)[:80] or text[:40]
    # 去站点后缀
    title = re.split(r"[_\-—|∷]", title)[0].strip()[:80]

    rec = {
        "title": title,
        "url": url,
        "source": re.sub(r"^https?://", "", url).split("/")[0],
        "department": classify_dept(text + " " + title),
        "region": classify_region(text + " " + title, url),
        "category": classify_cat(text + " " + title),
        "quality": classify_quality(url, text, title),
        "date": datetime.date.today().isoformat(),
        "added_at": datetime.date.today().isoformat(),
        "tags": extract_tags(title + " " + text),
        "summary": make_summary(text),
        "content": text,
        "content_fetched": True,
        "note": note or "",
        "ingested_by": "ai-ingest_url",
    }
    # 写 inbox（去重）
    inbox = []
    if os.path.exists(INBOX):
        try:
            inbox = json.load(open(INBOX, encoding="utf-8"))
        except Exception:
            inbox = []
    if not isinstance(inbox, list):
        inbox = []
    if any(r.get("url") == url for r in inbox):
        print("   ✓ 已在 inbox，跳过重复")
    else:
        inbox.append(rec)
        json.dump(inbox, open(INBOX, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("   ✓ 已写入 inbox.json（共 %d 条待合并）" % len(inbox))
    print("   分类：%s ｜ 地域：%s ｜ 分类标签：%s ｜ 质量：%s"
          % (rec["department"], rec["region"], rec["category"], rec["quality"]))
    print("   标题：", rec["title"])
    print("   标签：", "、".join(rec["tags"]))
    # 合并渲染
    if do_update:
        print("── 合并渲染（update.py）")
        subprocess.run([sys.executable, os.path.join(BASE, "update.py")], check=False)
    # 部署
    if deploy:
        print("── 部署（gh_deploy.py）")
        subprocess.run([sys.executable, os.path.join(BASE, "gh_deploy.py")], check=False)
    return rec


if __name__ == "__main__":
    args = sys.argv[1:]
    deploy = "--no-deploy" not in args
    do_update = "--no-update" not in args
    args = [a for a in args if a not in ("--no-deploy", "--no-update")]
    items = []
    if "--stdin" in args:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            if "|" in line:
                u, n = line.split("|", 1)
                items.append((u.strip(), n.strip()))
            else:
                items.append((line, ""))
    elif args:
        note = args[1] if len(args) > 1 else ""
        items.append((args[0], note))
    if not items:
        print("用法：python ingest_url.py <URL> [备注]  |  --stdin  |  --no-deploy")
        raise SystemExit(1)
    for u, n in items:
        ingest(u, n, deploy=deploy, do_update=do_update)
        time.sleep(0.5)
    print("\n=== 完成：%d 个网页已处理 ===" % len(items))
